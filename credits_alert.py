# -*- coding: utf-8 -*-
r"""
积分到期预警（WorkBuddy + Trae）
================================
回答一个和「用量」不同的问题：**还剩多少分、哪几笔快过期了、先花哪一笔。**

`workbuddy_fetch.py` / `trae_fetch.py` 算的是「已经花掉多少」（消耗流水，按时间窗口）；
本脚本算的是「账户里还躺着多少、它们各自的失效时刻」（余额快照 + 逐批到期）。
两者是互补的两本账，别混。

数据来源（全部是官方只读接口，只读自己的账号，不写任何数据）：

  WorkBuddy
    POST https://<domain>/billing/meter/get-user-resource-summary
         → 各资源包本周期总量 / 剩余
    POST https://<domain>/billing/meter/get-user-resource-free-packages
    POST https://<domain>/billing/meter/get-user-resource-paid-packages
         → 逐批明细：这一笔还剩多少、什么时候失效
    ⚠️ 网关校验请求来源，必须带 Origin / Referer / User-Agent / X-Requested-With，
       否则返回 403 code=10085「请求不合法」。
    ⚠️ 登录态不落在本仓库里。凭证是「可插拔自备」的，见「凭证怎么来」一节和
       credits_auth.py.example。本仓库不分发、不硬编码任何 token 或客户端密钥。

  Trae
    POST https://api.trae.cn/trae/api/v2/pay/user_current_entitlement_list
         → 当前权益包：额度上限 / 已用 / expire_time（主接口）
    POST https://api.trae.cn/trae/api/v2/pay/ide_user_ent_usage
         → 返回结构一致，主接口失败时兜底；另备 api.trae.com.cn 域名
    鉴权：Authorization: Cloud-IDE-JWT <token>
          （x-cloudide-token 只对 /cloudide/* 有效，用在 /trae/api/*/pay/* 一律 401）
    凭证直接复用同目录 trae_fetch.py 的 pick_usable_token()：它会自动发现客户端目录、
    解开加密 UserStorage、并**逐个实测**接口后才选一个真能用的。
    ⚠️ 因此 Trae 这边同样需要先自备 trae_crypto_table.py（见 README）。

产出：credits-data.js → window.CREDITS_ALERT
    generatedAt / thresholds / summary / alerts / sources[]
    sources[] 里每一项是该平台的余额、逐批 items[]、预警等级与最早到期时间。
    alerts[] 是跨两个平台、按到期时间排好序的「需要关注」批次。

用法：
    python credits_alert.py                       # 采集两家，写 credits-data.js 并打印预警
    python credits_alert.py --check              # 只体检凭证，不抓数据
    python credits_alert.py --warn-days 14       # 把「提醒」窗口从 7 天放宽到 14 天
    python credits_alert.py --json out.json      # 额外导出一份 JSON
    python credits_alert.py --alert-exit-code    # 有 3 天内到期时退出码 2（给计划任务用）

等级口径（可调）：
    daysLeft <= --urgent-days（默认 3） → urgent  紧急
    daysLeft <= --warn-days  （默认 7） → warn    提醒
    其余                                → normal  正常
    没有到期时间                        → unknown 未知（不会假装它安全）
"""
import argparse
import importlib.util
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_JS = os.path.join(HERE, "credits-data.js")

BROWSER_HEADERS = {
    "Accept-Language": "zh",
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"),
    "X-Requested-With": "XMLHttpRequest",
}

# 等级只用于「要不要提醒」，不参与任何金额计算。数值越大越急。
LEVEL_RANK = {"unknown": 0, "normal": 1, "warn": 2, "urgent": 3, "expired": 4}


# ---------------------------------------------------------------- 通用小工具
def http_post(url, body, headers, timeout=30, retries=2):
    """POST JSON，返回 (status, 解析后的 dict)。网络抖动做指数退避重试。

    HTTP 4xx/5xx 也照样把响应体带回来 —— 排障时要看的是 code=10085 这种业务码，
    直接抛异常反而把线索丢了。
    """
    data = json.dumps(body).encode("utf-8")
    last = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json, text/plain, */*")
        for k, v in headers.items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(raw)
            except Exception:
                return r.status, {"raw": raw[:300]}
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, {"raw": raw[:300]}
        except Exception as e:
            last = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    return 0, {"error": str(last)}


def num(v, default=0.0):
    """接口里的数字可能是 str / None / 空串，统一成 float，别让一处脏数据炸掉整轮。"""
    try:
        return float(v)
    except Exception:
        return default


def iso_to_ms(text):
    """'2026-10-15 23:59:59' / ISO 串 / 毫秒数字 → epoch ms（本地时区，和客户端一致）。"""
    if text is None or text == "":
        return None
    if isinstance(text, (int, float)) or str(text).isdigit():
        v = int(text)
        return v * 1000 if v < 1e11 else v      # 秒级时间戳补成毫秒
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%fZ",
                "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M"):
        try:
            return int(datetime.strptime(str(text), fmt).timestamp() * 1000)
        except Exception:
            continue
    return None


def fmt_ms(ms):
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M") if ms else None


# ================================================================ WorkBuddy
# 商品码（CommodityCode 枚举）：它们是**接口参数**，不是密钥、也不是客户端源码片段。
# 官方改版后可以改这两个列表，或设环境变量 WB_PACKAGE_CODES_JSON 覆盖，格式：
#   {"free": ["TCACA_code_001_..."], "paid": ["..."]}
WB_FREE_CODES = [
    "TCACA_code_001_PqouKr6QWV",  # 免费
    "TCACA_code_008_cfWoLwvjU4",  # 免费月包
    "TCACA_code_035_ArVxJcGDsm",  # 免费月包(国际)
    "TCACA_code_006_DbXS0lrypC",  # 赠送
    "TCACA_code_039_KRcQj7wUat",  # Pro 试用月
    "TCACA_code_040_mi9rCYg46x",  # Pro 试用年
    "TCACA_code_007_nzdH5h4Nl0",  # 活动
    "TCACA_code_028_NtpWi0jzXs",  # bonus28
    "TCACA_code_037_WxOD3MpI2o",  # bonus(国际)
    "TCACA_code_029_6wCGEWquYy",  # bonus29
    "TCACA_code_030_BjSt89qTvr",  # bonus30
]
WB_PAID_CODES = [
    "TCACA_code_002_AkiJS3ZHF5",  # Pro 月
    "TCACA_code_005_maRGyrHhw1",  # Pro 月 Plus
    "TCACA_code_003_FAnt7lcmRT",  # Pro 年
    "TCACA_code_023_4xbGhMrE6q",  # 青春版
    "TCACA_code_026_BaESVICNoi",  # 高级版
    "TCACA_code_027_0FCGVA6vSa",  # 旗舰版
    "TCACA_code_009_0XmEQc2xOf",  # 加量包
    "TCACA_code_038_OhvqZtiPKr",  # 加量包38
    "TCACA_code_036_lupO5WgNdG",  # 加量包(国际)
]

# 凭证来源（按优先级）：自备模块 → 环境变量 → 本机已装的积分技能。
# 任何一条都**不需要**把 token 写进仓库。
WB_SKILL_CANDIDATES = [
    os.path.expanduser("~/.workbuddy/skills/totorosir-workbuddy-score/scripts/buddy_station.py"),
    os.path.expanduser("~/.workbuddy/skills/totorosir-workbuddy-score__skillhub/scripts/buddy_station.py"),
]


def load_wb_auth():
    """返回 (token, domain, 来源说明)。取不到就抛异常，由 main 降级成 ok:false。

    为什么做成可插拔：WorkBuddy 5.6.2+ 把登录态里的 accessToken 换成了
    AES-256-GCM 信封，解密要绕 DPAPI / 进程内存，属于客户端内部实现细节 ——
    公开仓库不该分发、也不该复制那套东西。所以这里只**借用使用者本机的能力**：
    自备模块、环境变量，或你已安装的积分技能（按路径 import，不复制代码）。
    """
    # ① 自备模块 credits_auth.py（不入库，见 .example 模板）
    try:
        import credits_auth
        got = credits_auth.get_auth()
        if got and got[0]:
            return got[0], _norm_domain(got[1]), "credits_auth.py"
    except ImportError:
        pass
    except Exception as e:
        raise RuntimeError("credits_auth.py 里的 get_auth() 出错：%s" % e)

    # ② 环境变量（适合定时任务 / CI，不在磁盘留痕）
    tok = os.environ.get("WB_CREDITS_TOKEN")
    if tok:
        return tok, _norm_domain(os.environ.get("WB_CREDITS_DOMAIN")), "环境变量"

    # ③ 本机已安装的 WorkBuddy 积分技能：只按路径加载，不分发它的代码
    last = None
    for path in WB_SKILL_CANDIDATES:
        if not os.path.isfile(path):
            continue
        try:
            spec = importlib.util.spec_from_file_location("_wb_score_skill", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            # 客户端会不定时重写登录态文件，且不是原子写；撞上写入窗口会读到半截 JSON。
            # 这里重试 4 次，避免把「刚好在写」误报成「登录过期」。
            for delay in (0.6, 1.2, 2.0, 3.0, 0):
                try:
                    token, domain, _headers = mod._resolve_auth("auto")
                    if not token:
                        raise RuntimeError("只拿到 cookie 模式凭证，本脚本需要 Bearer token")
                    return token, _norm_domain(domain), "本机积分技能"
                except Exception as e:
                    last = e
                    if delay:
                        time.sleep(delay)
        except Exception as e:
            last = e
    raise RuntimeError(
        "取不到 WorkBuddy 登录态。任选其一：① 复制 credits_auth.py.example 为 "
        "credits_auth.py 并实现 get_auth()；② 设 WB_CREDITS_TOKEN / WB_CREDITS_DOMAIN；"
        "③ 安装本机积分技能。%s" % (("（最后错误：%s）" % last) if last else "")
    )


def _norm_domain(domain):
    d = (domain or "www.codebuddy.cn").strip()
    for p in ("https://", "http://"):
        if d.startswith(p):
            d = d[len(p):]
    return d.rstrip("/")


def wb_headers(token, domain):
    h = dict(BROWSER_HEADERS)
    h["Authorization"] = "Bearer " + token
    h["Origin"] = "https://" + domain
    h["Referer"] = "https://" + domain + "/"
    return h


def wb_collect():
    token, domain, auth_from = load_wb_auth()
    H = wb_headers(token, domain)
    base = "https://" + domain

    # --- 1) 资源汇总（401 时重取一次凭证：客户端可能刚轮换 token）
    body = None
    for attempt in (1, 2):
        st, body = http_post(base + "/billing/meter/get-user-resource-summary", {}, H)
        if st == 401 and attempt == 1:
            time.sleep(1.0)
            token, domain, auth_from = load_wb_auth()
            H = wb_headers(token, domain)
            continue
        break
    if st != 200 or (body or {}).get("code") != 0:
        raise RuntimeError("资源汇总接口失败：HTTP %s %s" % (st, str(body)[:200]))

    packages = ((body or {}).get("data") or {}).get("Packages") or []
    total_remain = sum(num(p.get("CycleRemainCapacity")) for p in packages)
    total_size = sum(num(p.get("CycleTotalCapacity")) for p in packages)
    pkg_cycle_remain = {}
    for p in packages:
        if p.get("PackageCode"):
            pkg_cycle_remain[p["PackageCode"]] = num(p.get("CycleRemainCapacity"))

    # --- 2) 逐批明细（免费包 + 付费包，都要翻页取全）
    today = datetime.now().strftime("%Y-%m-%d")
    free_codes, paid_codes = WB_FREE_CODES, WB_PAID_CODES
    override = os.environ.get("WB_PACKAGE_CODES_JSON")
    if override:
        try:
            cfg = json.loads(override)
            free_codes = cfg.get("free") or free_codes
            paid_codes = cfg.get("paid") or paid_codes
        except Exception:
            pass

    def fetch_pages(path, codes, extra):
        rows, total_count = [], None
        for page in range(1, 11):            # 10 页 × 200 = 2000 条上限，防死循环
            payload = {"PageNumber": page, "PageSize": 200,
                       "PackageCodes": codes, "IsDisplayTotalInfo": True}
            payload.update(extra)
            st2, b2 = http_post(base + path, payload, H)
            if st2 != 200 or (b2 or {}).get("code") != 0:
                break
            d = (b2 or {}).get("data") or {}
            batch = d.get("Accounts") or []
            rows.extend(batch)
            total_count = int(num(d.get("TotalCount"), len(rows)))
            if len(rows) >= total_count or not batch:
                break
        return rows, total_count

    free_rows, free_total = fetch_pages(
        "/billing/meter/get-user-resource-free-packages", free_codes,
        {"SlicePeriodStartTime": today + " 00:00:00",
         "SlicePeriodEndTime": today + " 23:59:59"})
    paid_rows, paid_total = fetch_pages(
        "/billing/meter/get-user-resource-paid-packages", paid_codes,
        {"NeedRenewInfo": True})

    items, warnings = [], []
    for rows, total_count, kind in ((free_rows, free_total, "免费/赠送"),
                                    (paid_rows, paid_total, "付费套餐")):
        if total_count and len(rows) < total_count:
            warnings.append("%s 明细截断：取到 %d / 共 %d 条" % (kind, len(rows), total_count))
        for r in rows:
            # ⚠️ 两套额度口径：Capacity*(累计) 与 Cycle*(本周期)。
            #   「每月刷新」的周期包只有 Cycle* 才是当月真剩；优先 Capacity* 会
            #   显示成「整包额度、用量 0」，和客户端弹窗对不上。
            cyc_size = num(r.get("CycleCapacitySizePrecise"), num(r.get("CycleCapacitySize")))
            cyc_remain = num(r.get("CycleCapacityRemainPrecise"), num(r.get("CycleCapacityRemain")))
            cyc_used = num(r.get("CycleCapacityUsedPrecise"), num(r.get("CycleCapacityUsed")))
            if cyc_size > 0:
                size, remain, used = cyc_size, cyc_remain, cyc_used
            else:
                size = num(r.get("CapacitySizePrecise"), num(r.get("CapacitySize")))
                remain = num(r.get("CapacityRemainPrecise"), num(r.get("CapacityRemain")))
                used = num(r.get("CapacityUsedPrecise"), num(r.get("CapacityUsed")))
            if r.get("Status") != 0 or remain <= 0:
                continue                      # 只看「还有余额、且未过期/未用完」的批次

            # ⚠️ 有效批次（Status=0）的 ExpiredTime 是空的，真正的到期时刻在
            #   CycleEndTime / DeductionEndTime；已过期批次才有 ExpiredTime。
            expire_text = r.get("ExpiredTime") or r.get("CycleEndTime") or ""
            expire_ms = iso_to_ms(expire_text)
            if expire_ms is None and r.get("DeductionEndTime"):
                expire_ms = iso_to_ms(r.get("DeductionEndTime"))
                expire_text = fmt_ms(expire_ms)
            attrs = {a.get("Key"): a.get("Value") for a in (r.get("AccountAttributes") or [])}
            items.append({
                "expireMs": expire_ms,
                "expireAt": expire_text or fmt_ms(expire_ms),
                "remain": round(remain, 2),
                "size": round(size, 2),
                "used": round(used, 2),
                "unit": r.get("CapacityUnit") or "credits",
                "name": r.get("PackageName") or "",
                "kind": kind,
                "reason": attrs.get("grantReason") or "",
                "pkgCode": r.get("PackageCode") or "",
            })

    items.sort(key=lambda x: (x["expireMs"] or 0))

    # --- 3) 明细与汇总对平：两次请求差几秒常有分的漂移，按包补到最早到期那笔上
    by_pkg = {}
    for it in items:
        by_pkg.setdefault(it["pkgCode"], []).append(it)
    for code, group in by_pkg.items():
        want = pkg_cycle_remain.get(code)
        if want is None:
            continue
        delta = round(want - sum(i["remain"] for i in group), 2)
        if abs(delta) < 0.01:
            continue
        target = group[0]
        if target["remain"] + delta < 0 or target["remain"] + delta > target["size"] + 0.01:
            warnings.append("%s 明细与汇总差 %.2f，无法自动校平" % (target["name"], delta))
            continue
        target["remain"] = round(target["remain"] + delta, 2)
        target["used"] = round(max(0.0, target["size"] - target["remain"]), 2)

    return {
        "ok": True,
        "platform": "WorkBuddy",
        "subtitle": "CodeBuddy 积分",
        "totalRemain": round(total_remain, 2),
        "totalSize": round(total_size, 2),
        "totalUsed": round(total_size - total_remain, 2),
        "packageCount": len(packages),
        "batchCount": len(items),
        "items": items,
        "warnings": warnings,
        "authFrom": auth_from,
    }


# ==================================================================== Trae
TRAE_HOSTS = ["https://api.trae.cn", "https://api.trae.com.cn"]
TRAE_PATHS = ["/trae/api/v2/pay/user_current_entitlement_list",
              "/trae/api/v2/pay/ide_user_ent_usage"]


def trae_token():
    """复用 trae_fetch.py 的凭证发现 + 实测，返回 (token, 来源)。"""
    try:
        import trae_fetch
    except Exception as e:
        raise RuntimeError("加载同目录 trae_fetch.py 失败：%s" % e)
    try:
        token, src, _js, _alive, _note = trae_fetch.pick_usable_token()
    except Exception as e:
        raise RuntimeError("Trae 凭证筛选失败：%s" % e)
    if not token:
        raise RuntimeError(
            "没找到接口实测可用的 Trae 凭证。先跑 `python trae_fetch.py --check`，"
            "最常见原因是没自备 trae_crypto_table.py（加密 UserStorage 被整条跳过）。")
    return token, src


def trae_collect():
    token, src = trae_token()
    req_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Authorization": "Cloud-IDE-JWT " + token,
        "User-Agent": BROWSER_HEADERS["User-Agent"],
    }
    body, last_err = None, None
    for host in TRAE_HOSTS:
        for path in TRAE_PATHS:
            st, cand = http_post(host + path, {}, req_headers, timeout=40, retries=1)
            if st == 200 and isinstance(cand, dict) and "user_entitlement_pack_list" in cand:
                body = cand
                break
            last_err = "%s%s → HTTP %s %s" % (host, path, st, str(cand)[:120])
        if body is not None:
            break
    if body is None:
        raise RuntimeError("权益接口都不可用：%s" % (last_err or "无响应"))

    packs = body.get("user_entitlement_pack_list") or []
    usage_summary = body.get("usage_summary") or {}
    items = []
    for p in packs:
        b = p.get("entitlement_base_info") or {}
        limit = num((b.get("quota") or {}).get("credits_limit"))
        if not limit:                       # 只带功能开关、不带额度的包跳过
            continue
        used = num((p.get("usage") or {}).get("credits_amount"))
        remain = max(0.0, limit - used)
        if remain <= 0:
            continue
        expire = p.get("expire_time") or b.get("end_time")
        expire_ms = iso_to_ms(expire)
        extra = (b.get("product_extra") or {}).get("package_extra") or {}
        items.append({
            "expireMs": expire_ms,
            "expireAt": fmt_ms(expire_ms),
            "remain": round(remain, 2),
            "size": round(limit, 2),
            "used": round(used, 2),
            "unit": "积分",
            "name": extra.get("package_name") or p.get("display_desc") or "积分包",
            "kind": p.get("group_name") or "权益",
            "reason": p.get("display_desc") or "",
        })

    # 同到期时刻 + 同名的包合并，避免 500+2000 那种同批多包刷屏
    merged = {}
    for it in items:
        key = (it["expireMs"], it["name"], it["kind"])
        if key in merged:
            m = merged[key]
            for f in ("remain", "size", "used"):
                m[f] = round(m[f] + it[f], 2)
            m["count"] = m.get("count", 1) + 1
        else:
            it["count"] = 1
            merged[key] = it
    out = sorted(merged.values(), key=lambda x: (x["expireMs"] or 0))

    total_amount = num(usage_summary.get("total_amount"))
    consumed = num(usage_summary.get("consumed_amount"))
    return {
        "ok": True,
        "platform": "Trae",
        "subtitle": "全链路开发平台",
        "totalRemain": round(total_amount - consumed, 2) if total_amount
                       else round(sum(i["remain"] for i in out), 2),
        "totalSize": round(total_amount, 2),
        "totalUsed": round(consumed, 2),
        "packageCount": len(packs),
        "batchCount": len(out),
        "items": out,
        "warnings": [],
        "authFrom": src,
    }


# ================================================================ 预警计算
def finalize(src, warn_days, urgent_days):
    """给每笔打等级，并汇总平台级 / 全局级的「快过期多少分」。

    daysLeft 用自然日（/86400）而不是日历日：晚上 23:00 抓到「明早 8:00 到期」
    应该算 0.4 天、立刻进紧急档，而不是「还有 1 天」。
    """
    now_ms = int(time.time() * 1000)
    expiring = 0.0
    urgent = 0.0
    worst = "normal"
    unknown = 0
    for it in src.get("items", []):
        ms = it.get("expireMs")
        if not ms:
            it["daysLeft"] = None
            it["level"] = "unknown"
            unknown += 1
            continue
        days = (ms - now_ms) / 86400000.0
        it["daysLeft"] = round(days, 2)
        if days < 0:
            level = "expired"
            src.setdefault("warnings", []).append(
                "%s 仍显示有余额却已过期（%s），可能是接口时区/状态不同步" % (it["name"], it["expireAt"]))
        elif days <= urgent_days:
            level = "urgent"
        elif days <= warn_days:
            level = "warn"
        else:
            level = "normal"
        it["level"] = level
        if level in ("expired", "urgent"):
            urgent += it["remain"]
        if level in ("expired", "urgent", "warn"):
            expiring += it["remain"]
        if LEVEL_RANK[level] > LEVEL_RANK[worst]:
            worst = level
    if unknown:
        src.setdefault("warnings", []).append("%d 笔接口没给到期时间，已标为「未知」而非「安全」" % unknown)

    soon = [i for i in src.get("items", []) if i.get("expireMs")]
    first = min(soon, key=lambda x: x["expireMs"]) if soon else None
    src["level"] = worst
    src["expiringSoon"] = round(expiring, 2)
    src["urgentRemain"] = round(urgent, 2)
    src["soonest"] = ({"expireAt": first["expireAt"], "daysLeft": first["daysLeft"],
                       "remain": first["remain"], "name": first["name"]} if first else None)
    return src


def load_previous():
    """读上一次生成的 credits-data.js，取出各平台**成功**的那份数据。

    用途：某家临时取不到登录态 / 接口抖动时，余额卡不该凭空消失。
    返回 ({platform: source}, 上次生成时间)；文件不存在/损坏就返回空，不影响主流程。
    """
    if not os.path.isfile(OUT_JS):
        return {}, None
    try:
        s = open(OUT_JS, encoding="utf-8").read()
        obj = json.loads(s[s.index("=") + 1:].strip().rstrip(";"))
        got = {x.get("platform"): x for x in (obj.get("sources") or []) if x.get("ok")}
        return got, obj.get("generatedAt")
    except Exception:
        return {}, None


def build_alerts(sources):
    """跨平台把「需要关注」的批次排成一条时间线 —— 先花哪笔一眼就看到。"""
    out = []
    for s in sources:
        if not s.get("ok"):
            continue
        for it in s.get("items", []):
            if it.get("level") in ("expired", "urgent", "warn"):
                out.append({
                    "platform": s["platform"],
                    "level": it["level"],
                    "name": it.get("name") or "",
                    "kind": it.get("kind") or "",
                    "remain": it["remain"],
                    "expireAt": it.get("expireAt"),
                    "expireMs": it.get("expireMs"),
                    "daysLeft": it.get("daysLeft"),
                })
    out.sort(key=lambda x: (x["expireMs"] or 0, -x["remain"]))
    return out


# ==================================================================== main
def main():
    # 控制台跟随系统编码（中文 Windows 是 cp936），errors=replace 兜住 GBK 编不出的字符。
    try:
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="WorkBuddy + Trae 积分到期预警")
    ap.add_argument("--check", action="store_true", help="只体检两边凭证，不抓数据")
    ap.add_argument("--warn-days", type=int, default=7, help="提醒窗口天数（默认 7）")
    ap.add_argument("--urgent-days", type=int, default=3, help="紧急窗口天数（默认 3）")
    ap.add_argument("--json", help="额外导出一份 JSON")
    ap.add_argument("--alert-exit-code", action="store_true",
                    help="有紧急/已过期批次时返回退出码 2（给计划任务弹通知用）")
    ap.add_argument("--only", choices=("workbuddy", "trae"), help="只跑一家")
    args = ap.parse_args()

    if args.urgent_days > args.warn_days:
        ap.error("--urgent-days 不能大于 --warn-days")

    if args.check:
        print("=" * 62)
        print("积分到期预警 · 凭证体检")
        print("=" * 62)
        try:
            token, domain, frm = load_wb_auth()
            print("[OK]   WorkBuddy 登录态可用（来源 %s，domain=%s，token 长度 %d）"
                  % (frm, domain, len(token)))
        except Exception as e:
            print("[ERR]  WorkBuddy 登录态：%s" % e)
        try:
            token, src = trae_token()
            print("[OK]   Trae 凭证可用（来源 %s，token 长度 %d）" % (src, len(token)))
        except Exception as e:
            print("[ERR]  Trae 凭证：%s" % e)
        print("-" * 62)
        print("体检只验证凭证，不请求账单接口，也不写任何文件。")
        return 0

    collectors = []
    if args.only != "trae":
        collectors.append((wb_collect, "WorkBuddy", "CodeBuddy 积分"))
    if args.only != "workbuddy":
        collectors.append((trae_collect, "Trae", "全链路开发平台"))

    sources = []
    for fn, name, sub in collectors:
        try:
            sources.append(finalize(fn(), args.warn_days, args.urgent_days))
        except Exception as e:
            # 一家挂了不能拖垮另一家：各自 ok:false，页面/看板据此降级显示
            sources.append({"ok": False, "platform": name, "subtitle": sub,
                            "error": str(e), "level": "unknown", "items": [],
                            "warnings": [], "expiringSoon": 0, "urgentRemain": 0})

    # ---- 某家临时失败：沿用上一次成功的数据，明确标 stale，别让余额卡凭空消失
    # 典型场景：登录态文件正被客户端重写、网络抖动、或脚本跑在受限环境里。
    # 等级要按「现在」重算（时间过去了，daysLeft 会变），所以再过一次 finalize()。
    prev, prev_at = load_previous()
    for i, s in enumerate(sources):
        if s.get("ok"):
            continue
        old = prev.get(s.get("platform"))
        if not old or not old.get("items"):
            continue
        merged = dict(old)
        merged["warnings"] = []
        merged = finalize(merged, args.warn_days, args.urgent_days)
        merged["stale"] = True
        merged["staleReason"] = s.get("error")
        # 一直指向最初那次真实采集，不要每次刷新都把时间改成「上次」
        merged["staleFrom"] = old.get("staleFrom") or prev_at or "上次采集"
        sources[i] = merged

    alerts = build_alerts(sources)
    ok_sources = [s for s in sources if s.get("ok")]
    level = max((s.get("level", "unknown") for s in ok_sources),
                key=lambda x: LEVEL_RANK.get(x, 0), default="unknown")
    payload = {
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "thresholds": {"warnDays": args.warn_days, "urgentDays": args.urgent_days},
        "summary": {
            "platforms": len(sources),
            "ok": len(ok_sources),                 # 有数据的平台数（含沿用上次的）
            "fresh": sum(1 for s in sources if s.get("ok") and not s.get("stale")),
            "stale": sum(1 for s in sources if s.get("stale")),
            "level": level,
            "totalRemain": round(sum(s.get("totalRemain", 0) for s in ok_sources), 2),
            "expiringSoon": round(sum(s.get("expiringSoon", 0) for s in ok_sources), 2),
            "urgentRemain": round(sum(s.get("urgentRemain", 0) for s in ok_sources), 2),
            "alertCount": len(alerts),
        },
        "alerts": alerts,
        "sources": sources,
    }

    with open(OUT_JS, "w", encoding="utf-8") as fh:
        fh.write("// 由 credits_alert.py 自动生成（官方只读接口；不含任何登录凭证）\n")
        fh.write("window.CREDITS_ALERT = ")
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write(";\n")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)

    print("已生成: %s" % OUT_JS)
    print("窗口: 紧急 <= %d 天 / 提醒 <= %d 天" % (args.urgent_days, args.warn_days))
    for s in sources:
        if not s.get("ok"):
            print("  [ERR]  %-10s %s" % (s["platform"], s.get("error")))
            continue
        soon = s.get("soonest") or {}
        print("  [OK]   %-10s 剩余 %s / 共 %s，%d 笔待到期；最近 %s（%.2f 天）"
              % (s["platform"], s["totalRemain"], s["totalSize"], s["batchCount"],
                 soon.get("expireAt", "未知"),
                 soon.get("daysLeft", 0)))
        print("         %s 窗口内有 %s 分到期，其中紧急 %s 分"
              % (args.warn_days, s["expiringSoon"], s["urgentRemain"]))
        for w in s.get("warnings", []):
            print("         [WARN] %s" % w)
        if s.get("stale"):
            print("         [WARN] 本次采集失败，沿用 %s 的上次数据（%s）"
                  % (s["staleFrom"], str(s.get("staleReason") or "")[:70]))

    if alerts:
        print("\n需要关注（按到期时间）：")
        for a in alerts[:10]:
            tag = {"expired": "已过期", "urgent": "紧急", "warn": "提醒"}.get(a["level"], a["level"])
            print("  [%s] %-10s %-22s %9s 分  %s（%.2f 天）"
                  % (tag, a["platform"], a["name"][:22], a["remain"],
                     a["expireAt"], a["daysLeft"] if a["daysLeft"] is not None else 0))
        if len(alerts) > 10:
            print("  ... 另有 %d 笔，见 credits-data.js 的 alerts[]" % (len(alerts) - 10))
    else:
        print("\n窗口内没有即将到期的批次。")

    if args.alert_exit_code and level in ("urgent", "expired"):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
