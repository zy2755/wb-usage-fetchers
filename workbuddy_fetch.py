# -*- coding: utf-8 -*-
"""
WorkBuddy 用量数据提取脚本
============================
数据来源（全部为 WorkBuddy 本机真实落盘数据）：
  1. ~/.workbuddy/projects/**/*.jsonl   —— 会话转录，含每次模型调用的 token 级用量
     providerData.usage = {inputTokens, outputTokens, totalTokens,
                           inputTokensDetails:[{cached_tokens}],
                           outputTokensDetails:[{reasoning_tokens}]}
  2. ~/.workbuddy/workbuddy.db 的 session_usage / sessions 表
     session_usage.credit_json = {conversationRequestId: 积分}  —— 积分账本
     sessions                  = 会话标题 / 项目路径 / 模型 / 模式

输出：usage-data.js  （挂到 window.WB_USAGE，供 index.html 直接 <script> 加载，
     避免 file:// 下的 fetch 跨域限制）
"""
import json
import glob
import os
import re
import sqlite3
import collections
from datetime import datetime

HOME = os.path.expanduser("~")
WB = os.path.join(HOME, ".workbuddy")
PROJ_GLOB = os.path.join(WB, "projects", "**", "*.jsonl")
DB = os.path.join(WB, "workbuddy.db")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "usage-data.js")

# ---------------------------------------------------------------- 工具函数
def ts_to_dt(ts):
    """毫秒时间戳 -> datetime"""
    return datetime.fromtimestamp(ts / 1000.0) if isinstance(ts, (int, float)) else None


def norm_model(raw, request_model_name):
    """把各种模型别名归一到可读名称"""
    m = (raw or request_model_name or "").strip()
    alias = {
        "fast-model": "快速模型",
        "快速": "快速模型",
        "auto": "自动选择",
        "": "未标注",
    }
    key = m.lower()
    if key in alias:
        return alias[key]
    # 统一 deepseek 系列大小写
    pretty = {
        "deepseek-v4-flash": "DeepSeek-V4-Flash",
        "deepseek-v4-pro": "DeepSeek-V4-Pro",
        "deepseek-v4.1-flash": "DeepSeek-V4.1-Flash",
        "hy4-preview": "Hunyuan4-Preview",
        "glm-5.3": "GLM-5.3",
        "glm-5.3-flash": "GLM-5.3-Flash",
        "kimi-k2.6": "Kimi-K2.6",
        "kimi-k2.7": "Kimi-K2.7",
        "space-bunny": "Space-Bunny",
    }
    return pretty.get(key, m)


def proj_label(cwd):
    """把项目路径转成可读标签。

    优先级：定时任务/Claw 这类特殊目录 -> 工作区里实际的工程目录名 -> 时间戳
    WorkBuddy 的会话目录多为 2026-09-21-14-29-57 这种时间戳名，直接当项目名可读性差，
    所以往工作区里看一眼有没有真实工程目录（anime-tracker / wb-usage-dashboard 等），
    有就拿它当项目名 —— 那才是"这是什么项目"的答案。
    """
    if not cwd:
        return "未知项目"
    c = cwd.replace("\\", "/").rstrip("/")
    if "automation-" in c:
        return "定时任务 · 睡前天气推送"
    if c.endswith("/Claw"):
        return "Claw 主会话"
    tail = c.split("/")[-1]

    # 时间戳式工作区：找找里面真正的工程目录
    if re.match(r"^\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}$", tail):
        try:
            subs = sorted(
                x for x in os.listdir(cwd)
                if os.path.isdir(os.path.join(cwd, x))
                and not x.startswith(".")
                and ".bak_" not in x
                and not x.startswith("_")
            )
        except Exception:
            subs = []
        if subs:
            # 多个工程时取第一个，并标注数量，避免名字掩盖别的项目
            name = subs[0]
            return name if len(subs) == 1 else f"{name} 等 {len(subs)} 个"
        # 没有工程目录，退回「月/日 时:分」
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-\d{2}$", tail)
        return f"{m.group(2)}/{m.group(3)} {m.group(4)}:{m.group(5)}"
    return tail or "未知项目"


# 平台注入的外壳标签，这些不是用户真正问的话
_INJECT_TAGS = ("system-reminder", "conversation_history_summary",
                "user_info", "additional_data", "project_context",
                "memory_and_skills_reminder", "product_identity")


def clean_prompt(text):
    """从一条 user 消息里剥出用户真正问的那句话。

    WorkBuddy 的 user 消息常被平台外壳包裹（<system-reminder> 环境信息、
    <conversation_history_summary> 摘要回灌等），直接拿 text 会得到
    "IDE Theme: light..." 这种噪声，必须剥掉。返回 "" 表示这条不是真提问。
    """
    if not text or not isinstance(text, str):
        return ""
    t = text
    # 逐个剥掉平台注入块（成对出现）
    for tag in _INJECT_TAGS:
        t = re.sub(r"<%s[^>]*>.*?</%s>" % (tag, tag), "", t, flags=re.S)
    # 平台外壳有时只开不闭，兜底砍掉残留的开标签及其内容
    t = re.sub(r"<(%s)[^>]*>.*$" % "|".join(_INJECT_TAGS), "", t, flags=re.S)
    # 剥掉 <user_query> 外壳但保留里面的正文
    m = re.search(r"<user_query>(.*?)</user_query>", t, flags=re.S)
    if m:
        t = m.group(1)
    else:
        t = re.sub(r"</?user_query>", "", t)
    t = re.sub(r"<[a-z_][a-z0-9_-]*>.*?</[a-z_][a-z0-9_-]*>", "", t, flags=re.S)
    t = re.sub(r"\s+", " ", t).strip()
    # 摘要/续跑这类机器话，不算用户提问
    if t.startswith("Please continue with the conversation"):
        return ""
    if t.startswith("<"):
        return ""
    return t


def clip_title(s, limit=42):
    """把提问首句压成标题：去换行、超长截断加省略号"""
    s = re.sub(r"\s+", " ", (s or "")).strip()
    s = re.sub(r"^(请|帮我|你|麻烦|我想要?|我想|能不能|可以|能不能帮)[，,、\s]*", "", s)
    if len(s) <= limit:
        return s
    return s[:limit].rstrip() + "…"


# Ardot 画布给会话打的分组头，形如 @scene#33:"移动端App"，不是人话
_SCENE_RE = re.compile(r'^@scene#\d+:(?:"([^"]*)"\s*)?')


def clean_title(t):
    """规范化会话标题：剥掉平台分组头、压缩空白。

    Ardot 场景会把标题写成 @scene#33:"移动端App" 参考小红书的设计风格，帮我设计…
    前面的 @scene#33: 对人是噪声，后面那句才是真正想干的事。
    """
    s = re.sub(r"\s+", " ", (t or "")).strip()
    m = _SCENE_RE.match(s)
    if m:
        scene, rest = m.group(1), s[m.end():].strip()
        if rest:
            # 有正文就用正文，场景名降级为前缀提示
            s = rest if not scene else f"{rest}"
        elif scene:
            s = scene
    return s


def is_subagent(sid):
    """子代理（subagent）会话：由主会话临时派生，不该当成独立会话列出来。

    识别方式：其转录位于 <父会话>/subagents/agent-<hash>.jsonl，
    sessionId 形如 agent-31103437；但实际用量常以父会话 id 记账（见下），
    所以这里服务于「给这类记录换个可读名字」。
    """
    return bool(sid and re.match(r"^agent-[0-9a-f]{6,}$", sid))


# ---------------------------------------------------------------- 1. 读数据库
sess_meta = {}
credit = {}          # conversationRequestId -> 积分
ctx_used = {}        # sessionId -> 上下文占用 token
ctx_size = {}        # sessionId -> 上下文窗口大小

if os.path.exists(DB):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    for r in con.execute(
        "select id,cwd,title,custom_title,created_at,last_activity_at,"
        "model,source_mode,mode,is_playground,is_background_automation from sessions"
    ):
        d = dict(r)
        sess_meta[d["id"]] = {
            "title": d["custom_title"] or d["title"] or "未命名会话",
            "cwd": d["cwd"] or "",
            "project": proj_label(d["cwd"]),
            "created_at": d["created_at"],
            "last_activity_at": d["last_activity_at"] or d["created_at"],
            "model": d["model"],
            "mode": d["mode"] or d["source_mode"] or "craft",
            "kind": (
                "定时任务" if d["is_background_automation"]
                else ("临时会话" if d["is_playground"] else "常规会话")
            ),
        }
    for sid, used, size, cj in con.execute(
        "select session_id, used, size, credit_json from session_usage"
    ):
        ctx_used[sid] = used
        ctx_size[sid] = size
        if cj:
            try:
                for k, v in json.loads(cj).items():
                    credit[k] = credit.get(k, 0.0) + float(v)
            except Exception:
                pass
    con.close()

# --------------------------------------- 1b. 运行日志里的积分（账本兜底）
# 平台还会把「这一轮扣了多少积分」打在会话日志里，一行一次模型调用：
#   [SessionManager][credit] Credit received: rootRequestId=<32位>, source=..., credit=1.23
#
# 为什么需要它：只有这一轮带了 usage.cost 时，客户端才写本地账本
# session_usage.credit_json。所以存在「平台确实扣了积分、本地账本却没有」的轮次
# —— 2026-10-03 实测 space-bunny 的全部 10 轮都是这样，看板一律显示「未计费」，
# 而日志里明明记着扣了多少。
#
# 可靠性对照（2026-10-03 实测）：账本 ∩ 日志 = 151 条，其中 110 条数值**完全一致**，
# 仅 1 条日志偏高 2.63 分；但也有 92 条账本有、日志没有。
# ⇒ 结论：**日志只能当「账本缺失轮次」的下限兜底，不能反过来覆盖账本**。
LOG_ROOT = os.path.join(WB, "logs")
_CREDIT_MARK = b"[SessionManager][credit] Credit received:"
_CREDIT_RE = re.compile(
    r"\[SessionManager\]\[credit\] Credit received: rootRequestId=(\w+), "
    r"source=\w+, credit=(\S+)")
credit_log = {}      # conversationRequestId -> 日志累计积分
_log_files = 0

if os.path.isdir(LOG_ROOT):
    for path in glob.glob(os.path.join(LOG_ROOT, "**", "*.log"), recursive=True):
        try:
            if os.path.getsize(path) > 400 * 1024 * 1024:
                continue
            with open(path, "rb") as fh:
                blob = fh.read()
        except Exception:
            continue
        # 先二进制粗筛（1.6GB 全量约 2 秒），没命中就不解码
        if _CREDIT_MARK not in blob:
            continue
        _log_files += 1
        for line in blob.decode("utf-8", "replace").splitlines():
            if "[SessionManager][credit]" not in line:
                continue
            m = _CREDIT_RE.search(line)
            if not m:
                continue
            v = m.group(2)
            # response_done 那一遍是 undefined，重复计入会翻倍，必须跳过
            if v in ("undefined", "null", ""):
                continue
            try:
                credit_log[m.group(1)] = credit_log.get(m.group(1), 0.0) + float(v)
            except ValueError:
                pass

# ------------------------------------------------- 2. 读会话转录，逐次调用汇总
# 同一 conversationRequestId 下会有多次模型调用（工具循环），
# 这里按 requestId 聚合为一条“轮次”；若缺失则退化为 (sessionId, 分钟) 归组。
groups = collections.defaultdict(lambda: {
    "inputTokens": 0, "outputTokens": 0, "totalTokens": 0,
    "cachedTokens": 0, "reasoningTokens": 0, "calls": 0,
    "ts": None, "sessionId": None, "model": None, "requestId": None,
})

# 转录兜底：sessions 表是靠不住的 —— 会话被删、或被平台停止登记之后，
# 只有转录里还留着「第一句提问」和「cwd」。用它们兜住标题和项目名。
tr_title = {}        # sessionId -> 第一句真实提问（最早时间戳优先）
tr_title_ts = {}     # sessionId -> 该提问的时间戳
tr_cwd = {}          # sessionId -> 转录里记录的 cwd
tr_usage = {}        # sessionId -> 是否出现过模型用量（过滤掉无消耗的空转录）
tr_sub = {}          # sessionId -> 父会话 id（转录位于 <父>/subagents/ 时）

for path in glob.glob(PROJ_GLOB, recursive=True):
    try:
        fh = open(path, encoding="utf-8")
    except Exception:
        continue
    for line in fh:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue

        ts = d.get("timestamp")
        sid = d.get("sessionId") or "unknown"

        # ---- 标记：这条转录来自某个主会话派生的子代理目录，名字该带上父会话
        if "/subagents/" in path.replace("\\", "/") and sid not in tr_sub:
            parent = os.path.basename(os.path.dirname(os.path.dirname(path.replace("\\", "/"))))
            tr_sub[sid] = parent

        # ---- 兜底素材 1：cwd（转录里每行都可能带，取到就用）
        cwd = d.get("cwd")
        if cwd and sid not in tr_cwd:
            tr_cwd[sid] = cwd

        # ---- 兜底素材 2：第一句真实提问（只认标了 role=user 的 message）
        # 只取最早 timestamp 的那条，避免被后续追问覆盖
        if d.get("type") == "message" and d.get("role") == "user" and sid not in tr_title:
            parts = d.get("content")
            if isinstance(parts, list):
                for c in parts:
                    if not isinstance(c, dict):
                        continue
                    q = clean_prompt(c.get("text"))
                    if q:
                        tr_title[sid] = q
                        tr_title_ts[sid] = ts or 0
                        break

        pd = d.get("providerData") or {}
        u = pd.get("usage")
        if not u:
            continue

        tr_usage[sid] = True
        rid = pd.get("conversationRequestId") or pd.get("traceId")
        if not rid:
            dt = ts_to_dt(ts)
            rid = "%s|%s" % (sid, dt.strftime("%Y%m%d%H%M") if dt else "na")

        g = groups[rid]
        g["requestId"] = rid
        g["sessionId"] = sid
        g["model"] = norm_model(pd.get("model"), pd.get("requestModelName"))
        if ts and (g["ts"] is None or ts < g["ts"]):
            g["ts"] = ts

        itd = u.get("inputTokensDetails") or [{}]
        otd = u.get("outputTokensDetails") or [{}]
        g["inputTokens"] += u.get("inputTokens") or 0
        g["outputTokens"] += u.get("outputTokens") or 0
        g["totalTokens"] += u.get("totalTokens") or 0
        g["cachedTokens"] += itd[0].get("cached_tokens", 0) or 0
        g["reasoningTokens"] += otd[0].get("reasoning_tokens", 0) or 0
        g["calls"] += 1
    fh.close()

# ---------------------------------------------------------------- 3. 逐条展开
records = []
for rid, g in groups.items():
    dt = ts_to_dt(g["ts"])
    if dt is None:
        continue
    sid = g["sessionId"]
    meta = sess_meta.get(sid, {})
    inp = g["inputTokens"]
    cached = g["cachedTokens"]

    # 标题三级兜底：sessions 表的标题 -> 转录里的第一句提问 -> 占位
    # 会话被删、或平台停止登记新会话时，前两级会依次失效
    guess = False
    title = clean_title(meta.get("title"))
    if not title:
        if sid in tr_sub:
            # 子代理会话：标成「子代理 · <父会话名>」，比甩一句原始提问可读
            pname = clean_title(sess_meta.get(tr_sub[sid], {}).get("title")) or "主会话"
            title = "子代理 · " + pname
        else:
            title = clip_title(tr_title.get(sid, ""))
        if not title:
            title = "未命名会话"
        guess = True

    # 项目名两级兜底：sessions 表的 cwd -> 转录里的 cwd -> 占位
    project = meta.get("project")
    if not project:
        project = proj_label(tr_cwd.get(sid, ""))

    # 积分三级来源：官方账本 -> 运行日志兜底 -> 未计费
    # 日志兜底只在账本完全没有这一轮时启用，绝不覆盖账本（账本更准，但会漏轮次）
    cr = credit.get(rid)
    from_log = False
    if cr is None:
        cr = credit_log.get(rid)
        from_log = cr is not None

    records.append({
        "ts": int(g["ts"]),
        "time": dt.strftime("%Y-%m-%d %H:%M:%S"),
        "date": dt.strftime("%Y-%m-%d"),
        "hour": dt.hour,
        "sessionId": sid,
        "session": title,
        "project": project,
        "kind": meta.get("kind", "常规会话"),
        "mode": meta.get("mode", "craft"),
        "model": g["model"],
        "calls": g["calls"],
        "inputTokens": inp,
        "outputTokens": g["outputTokens"],
        "totalTokens": g["totalTokens"],
        "cachedTokens": cached,
        # 缓存命中率 = 命中缓存的输入 token / 总输入 token
        "cacheHitRate": (cached / inp) if inp else 0.0,
        "reasoningTokens": g["reasoningTokens"],
        "credit": round(cr or 0.0, 4),
        "hasCredit": rid in credit,
        # 积分是否取自运行日志（平台没写本地账本）——看板据此标注来源
        "creditFromLog": from_log,
        "contextUsed": ctx_used.get(sid),
        "contextSize": ctx_size.get(sid),
        # 标题/项目名是否为转录兜底得来的（看板据此弱化显示，提醒是推断值）
        "titleGuess": guess,
    })

records.sort(key=lambda r: r["ts"])

# ---------------------------------------------------------------- 4. 统计输出
if records:
    d0 = min(r["date"] for r in records)
    d1 = max(r["date"] for r in records)
else:
    d0 = d1 = datetime.now().strftime("%Y-%m-%d")

total_credit = round(sum(r["credit"] for r in records), 2)
total_input = sum(r["inputTokens"] for r in records)
total_output = sum(r["outputTokens"] for r in records)
total_tokens = sum(r["totalTokens"] for r in records)
total_cached = sum(r["cachedTokens"] for r in records)
total_reasoning = sum(r["reasoningTokens"] for r in records)

payload = {
    "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "range": {"start": d0, "end": d1},
    "records": records,
    "summary": {
        "requests": len(records),
        "sessions": len({r["sessionId"] for r in records}),
        "credit": total_credit,
        "inputTokens": total_input,
        "outputTokens": total_output,
        "totalTokens": total_tokens,
        "cachedTokens": total_cached,
        "reasoningTokens": total_reasoning,
        "cacheHitRate": (total_cached / total_input) if total_input else 0.0,
        "models": len({r["model"] for r in records}),
        "projects": len({r["project"] for r in records}),
    },
}

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("// 由 build_data.py 自动生成，数据来自本机 WorkBuddy 真实使用记录\n")
    fh.write("window.WB_USAGE = ")
    json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    fh.write(";\n")

print("已生成:", OUT)
print("记录数: %d | 会话数: %d | 积分: %s" % (len(records), payload["summary"]["sessions"], total_credit))
print("Token: 输入 %s / 输出 %s / 合计 %s" % (f"{total_input:,}", f"{total_output:,}", f"{total_tokens:,}"))
print("缓存命中率: %.2f%%" % (payload["summary"]["cacheHitRate"] * 100))
print("时间范围: %s ~ %s" % (d0, d1))
print("模型: %s" % ", ".join(sorted({r["model"] for r in records})))

# 标题兜底情况：sessions 表靠不住时，这些数量会明显大于 0
_guess = {}
for r in records:
    if r.get("titleGuess"):
        _guess[r["sessionId"]] = r["session"]
if _guess:
    print("标题兜底: %d 个会话的标题取自转录（未登记在 sessions 表）" % len(_guess))
    for _sid, _nm in sorted(_guess.items(), key=lambda x: x[1]):
        print("   - %s" % _nm)

# 积分兜底情况：账本漏记的轮次有多少、从日志里补回多少分
_dash = [r for r in records if r["creditFromLog"]]
if _dash:
    _ledger_sum = sum(r["credit"] for r in records if r["hasCredit"])
    _log_sum = sum(r["credit"] for r in _dash)
    _by_sess = collections.defaultdict(lambda: [0, 0.0])
    for r in _dash:
        _by_sess[r["session"]][0] += 1
        _by_sess[r["session"]][1] += r["credit"]
    print("积分兜底: 扫描 %d 个含积分记录的日志文件，%d 轮账本漏记、从日志补回 %s 积分"
          % (_log_files, len(_dash), round(_log_sum, 2)))
    print("          账本自身记录 %.2f 分，合并后总计 %.2f 分"
          % (_ledger_sum, total_credit))
    for _nm, (_n, _v) in sorted(_by_sess.items(), key=lambda x: -x[1][1])[:8]:
        print("   - %-34s %2d 轮  %s 分" % (_nm[:34], _n, round(_v, 2)))
