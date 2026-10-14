# -*- coding: utf-8 -*-
r"""
Trae 用量抓取（免手动复制 token）
====================================
自动从 Trae 客户端的本地落盘里找到凭证，调用官方用量接口，导出看板数据。

凭证来源（脚本全部扫描后**逐个实测**再选用）：
  ⭐ D. `logs/<会话>/Modular/ai-agent_*_stdout.log` 里的 `BootUserInfo.token`
     —— **当前主力来源**。每次 AI 会话启动，agent 进程会打印一行
     `user_info: Some(BootUserInfo { token: "..." })`，**没有掩码**。
     payload 里 `source=refresh_token`、14 天有效期，
     实测**能直接调通账单接口**（header 用 `Authorization: Cloud-IDE-JWT <token>`）。
  A. 客户端 logs/*/main.log 里的登录态 access token —— 14 天
     （⚠️ 2026-10-11 起新版日志里**只写 `******` 占位**，这条来源实际已失效）
  B. webview leveldb 的 `Cloud-IDE-Token` —— 8 小时，只在打开「套餐/用量」页时刷新
     （⚠️ 2026-10-11 起新版 webview leveldb 里**也不再有这个键**）
  C. `~/.trae-cn/trae-jwt-token` —— `GenerateTempToken` 写的「临时 token 文件」
     ⚠️ 实测**调不了账单接口**（CheckLogin 里 IsLogin=false，账单接口一律 401）。
     它是 temp token（ExpireDuration=10080 分钟 = 7 天），跟账单接口要的主凭证不是一回事。
     仍保留为候选，靠「实测筛选」自动淘汰 —— 万一哪天服务端放宽了，就自动生效。
     💡 它只认 `x-cloudide-token` header（能让 GetUserInfo 返回 200），
        但账单接口对它是 401 —— **两类 token 用的是两套 header**，别混。

⚠️ 2026-10-11：TraeWork(TRAE SOLO CN) 与 TraeCode(Trae CN) 合并为「全新 TRAE」。
   客户端目录名可能再变，所以**不再硬编码目录**，改成扫 %APPDATA% / %LOCALAPPDATA%
   下所有「长得像 Trae 客户端数据目录」的文件夹（含 logs / Partitions / Preferences / Local State）。

🟢 2026-10-12 打通记录（D 是怎么找到的）：
   10-11 的结论「磁盘上拿不到主凭证」其实是**漏了一类文件** ——
   AI agent 进程的 stdout 日志在 `logs/<会话>/Modular/ai-agent_*_stdout.log`，
   比 `logs/<会话>/*.log` 深一层，原先的 glob 没覆盖到。
   是靠客户端日志里露出的两个独特 ID（`ClientID` / `BoundDeviceID`）反查整个
   客户端目录才翻出来的。所以：**以后 Trae 又采不到数时，先搜 ai-agent 的 stdout 日志。**
   · 账单接口**必须**用 `Authorization: Cloud-IDE-JWT <token>`；
     `x-cloudide-token` 只对 /cloudide/* 类接口有效，对 /trae/api/*/pay/* 一律 401。
   · **重登客户端没用** —— 它只刷新 C 那种 temp token（重登后签发时间确实变了，
     但账单接口依旧 401）。
   · 也试过借浏览器（Edge）登录态，全部行不通：cookie 是 v20（App-Bound 加密）解不开；
     而 Edge 从 136 版起禁止在**默认** user-data-dir 上开远程调试
     （`--remote-debugging-port` 与 `--remote-debugging-pipe` 都被拒），
     用 profile 副本 / junction 又会因为 App-Bound 的熵包含原始路径而解不开密钥。
     ⇒ **不用动浏览器，D 这条路就够。**

用法：
  python trae_fetch.py                # 拉取并写入 trae-data.js
  python trae_fetch.py --days 90      # 指定窗口（默认 90 天）
  python trae_fetch.py --check        # 只检查 token 状态（含 CheckLogin 登录态自检）
  python trae_fetch.py --json out.json
"""
import json
import os
import re
import sys
import glob
import base64
import hashlib
import argparse
import urllib.request
import urllib.error
from datetime import datetime

# ---------------------------------------------------------------- 配置
# 明确兜底的目录（自动发现之外的保底项；不存在会被跳过）
#
# ⚠️ 别把 `C:\Users\<用户名>\AppData\Roaming\...` 写死在这里：
#    一是换台机器 / 换个用户名就失效，二是会把本机用户名提交进仓库。
#    改成从 APPDATA / LOCALAPPDATA 环境变量拼，同一份代码谁都能跑。
_CLIENT_DIR_NAMES = [
    "TRAE SOLO CN",   # 旧 TraeWork（合并后可能被卸载）
    "Trae CN",        # 旧 TraeCode，现在是合并后的主目录
    "Trae",
]
EXTRA_CLIENT_DIRS = [
    os.path.join(root, name)
    for root in (os.environ.get("APPDATA"), os.environ.get("LOCALAPPDATA"))
    if root
    for name in _CLIENT_DIR_NAMES
]
# 产品数据目录（新版客户端把临时 token 文件写在这里）
PRODUCT_DATA_DIRS = [
    os.path.join(os.path.expanduser("~"), ".trae-cn"),
    os.path.join(os.path.expanduser("~"), ".trae"),
]
API_HOST = "https://api.trae.cn"
API_USAGE = "/trae/api/v1/pay/query_user_usage_group_by_session"
API_CREDITS = "/trae/api/v2/pay/cn_credits_billing_status"
API_ENTITLEMENTS = "/trae/api/v2/pay/user_current_entitlement_list"
# 合并后新版客户端改用这两个（v2）；作为上面两个的兜底（返回结构兼容）
API_ENT_USAGE = "/trae/api/v2/pay/ide_user_ent_usage"
API_PAY_STATUS = "/trae/api/v2/pay/ide_user_pay_status"
# 登录态自检接口（客户端自己用的，用来区分「token 无效」与「登录态失效」）
API_CHECKLOGIN = "/cloudide/api/v3/trae/CheckLogin"
IDE_VERSION = "3.4.2"

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_JS = os.path.join(HERE, "trae-data.js")

JWT_RE = re.compile(rb"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}")
# ⭐ 2026-10-12 打通的关键：新版客户端把 main.log 里的 token 全掩码成 ******，
#    但 **AI agent 进程的 stdout 日志没掩码** —— 每次 AI 会话启动都会打印一行
#    `user_info: Some(BootUserInfo { token: "..." })`。
#    这个 token 的 payload 里 source=refresh_token、14 天有效期，
#    实测**能直接调通账单接口**，是当前的主力凭证来源（见 README）。
BOOT_TOKEN_RE = re.compile(rb'BootUserInfo\s*\{[^{}]{0,400}?token:\s*"([^"]+)"')

# 判定「这是个 Trae 客户端数据目录」的标志文件
_CLIENT_MARKS = ("logs", "Partitions", "Preferences", "Local State")


def discover_client_dirs():
    """
    自动发现本机所有 Trae 系列客户端数据目录。

    为什么要自动发现：2026-10-11 TraeWork + TraeCode 合并成「全新 TRAE」，
    客户端的数据目录名、安装路径都可能再变；硬编码列表一旦漏掉新名字，
    脚本就会「找不到 token」而静默失效。所以改成按特征扫。

    判定标准：目录名含 trae + 有 logs / Partitions / Preferences / Local State 之一。
    ⚠️ 特征检查对**所有**候选都生效（包括下面的保底列表）——
       否则 Windows 路径大小写不敏感，用户随便建的 `%APPDATA%\\TRAE`（放杂物用）
       会被保底列表匹配上，当成客户端目录扫一遍（实测踩到过）。
    去重用 normcase，避免 `Trae` / `TRAE` 这种同目录不同大小写被算两遍。
    """
    found = []
    seen = set()

    def add(p):
        if not p or not os.path.isdir(p):
            return
        key = os.path.normcase(os.path.abspath(p))
        if key in seen:
            return
        if not any(os.path.exists(os.path.join(p, m)) for m in _CLIENT_MARKS):
            return                      # 不像客户端数据目录，跳过
        seen.add(key)
        found.append(p)

    for root in (os.environ.get("APPDATA"), os.environ.get("LOCALAPPDATA")):
        if not root or not os.path.isdir(root):
            continue
        try:
            names = os.listdir(root)
        except Exception:
            continue
        for name in names:
            if "trae" in name.lower():
                add(os.path.join(root, name))

    # 已知目录保底（同样要过特征检查）
    for p in EXTRA_CLIENT_DIRS:
        add(p)
    return found




# ---------------------------------------------------------------- token
def decode_jwt(tok):
    try:
        p = tok.split(".")[1]
        p += "=" * (-len(p) % 4)
        return json.loads(base64.urlsafe_b64decode(p))
    except Exception:
        return None


# ------------------------------------------------- E. 加密 UserStorage
# 背景（2026-10-14 打通）：
#   新版客户端（1.107.1）不再往 ai-agent 的 stdout 写明文 token（D 类来源失效），
#   可用凭证被收进了 `User/globalStorage/storage.json` 里、键名形如
#   `iCubeAuthInfo://<authProviderId>` 的**加密**值。主进程自己用
#   `byteCrypto.js` 的 `oy()` 解它，所以脚本要读就得复刻同一套算法。
#
# 串的格式：[6B 头（'t','c',版本…）][32B 密钥材料][AES-128-CBC 密文]
# 明文格式：[64B SHA-512 校验和][JSON 正文]（PKCS#7 填充）
# 密钥推导：aesKey/iv = SHA512( SHA512(密钥材料) + 掩码 ) 的前 32 字节
#           （掩码是客户端内置常量表 ule/dle 逐字节异或的结果）
#
# 为什么要手写 AES：项目坚持零第三方依赖，而主 Python 环境没装
# cryptography / pycryptodome。这里只实现解密方向，够用。
_AES_SBOX = [
    0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
    0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0,
    0xb7, 0xfd, 0x93, 0x26, 0x36, 0x3f, 0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15,
    0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07, 0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75,
    0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0, 0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84,
    0x53, 0xd1, 0x00, 0xed, 0x20, 0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf,
    0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8,
    0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5, 0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2,
    0xcd, 0x0c, 0x13, 0xec, 0x5f, 0x97, 0x44, 0x17, 0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73,
    0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88, 0x46, 0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb,
    0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c, 0xc2, 0xd3, 0xac, 0x62, 0x91, 0x95, 0xe4, 0x79,
    0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a, 0xae, 0x08,
    0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f, 0x4b, 0xbd, 0x8b, 0x8a,
    0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e, 0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e,
    0xe1, 0xf8, 0x98, 0x11, 0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf,
    0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68, 0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16,
]
_INV_SBOX = [0] * 256
for _i, _v in enumerate(_AES_SBOX):
    _INV_SBOX[_v] = _i
_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1b, 0x36]

# 客户端内置的掩码常量表（byteCrypto.js 里的 ule / dle / cle / lle）
#
# ⚠️ 公开仓库不再内置这几张表。它们是 Trae 客户端 obfuscated 源码里的常量，
#    属于商业软件的内部实现细节，再分发一次不合适。
#    改成**运行时从同目录的 trae_crypto_table.py 导入** ——
#    那个文件已被 .gitignore 排除，使用者按 trae_crypto_table.py.example
#    自己从客户端源码里提取后填入即可。
#
#    导入失败也**不影响脚本运行**：只是会跳过「加密 UserStorage」这条凭证来源
#    （见 collect_user_storage_tokens），其余候选来源照常扫描。
try:
    from trae_crypto_table import ULE, DLE, CLE, LLE
    _BC_ULE, _BC_DLE, _BC_CLE, _BC_LLE = ULE, DLE, CLE, LLE
    HAS_CRYPTO_TABLE = True
except Exception:
    HAS_CRYPTO_TABLE = False

_BC_HH, _BC_NY, _BC_OO, _BC_EG = 64, 32, 64, 6     # 校验长 / 密钥材料长 / 掩码长 / 头部偏移


def _aes_xtime(a):
    a <<= 1
    return (a ^ 0x1b) & 0xff if a & 0x100 else a


def _aes_mul(a, b):
    r = 0
    while b:
        if b & 1:
            r ^= a
        a = _aes_xtime(a)
        b >>= 1
    return r


def _aes_expand_key(key):
    """AES-128 密钥扩展 → 11 组轮密钥（每组 16 字节）。"""
    w = [list(key[i * 4:i * 4 + 4]) for i in range(4)]
    for i in range(4, 44):
        t = list(w[i - 1])
        if i % 4 == 0:
            t = t[1:] + t[:1]                    # RotWord
            t = [_AES_SBOX[b] for b in t]        # SubWord
            t[0] ^= _RCON[i // 4 - 1]
        w.append([w[i - 4][j] ^ t[j] for j in range(4)])
    return [sum((w[r * 4 + c] for c in range(4)), []) for r in range(11)]


def _aes_decrypt_block(block, rk):
    s = list(block)

    def add(k):
        for i in range(16):
            s[i] ^= k[i]

    def inv_shift():
        # 行 r 右移 r（解密方向；加密是左移 r）
        for r in range(1, 4):
            row = [s[r + 4 * c] for c in range(4)]
            row = row[-r:] + row[:-r]
            for c in range(4):
                s[r + 4 * c] = row[c]

    add(rk[10])
    for rnd in range(9, 0, -1):
        inv_shift()
        for i in range(16):
            s[i] = _INV_SBOX[s[i]]
        add(rk[rnd])
        for c in range(4):
            a = s[4 * c:4 * c + 4]
            s[4 * c + 0] = _aes_mul(a[0], 14) ^ _aes_mul(a[1], 11) ^ _aes_mul(a[2], 13) ^ _aes_mul(a[3], 9)
            s[4 * c + 1] = _aes_mul(a[0], 9) ^ _aes_mul(a[1], 14) ^ _aes_mul(a[2], 11) ^ _aes_mul(a[3], 13)
            s[4 * c + 2] = _aes_mul(a[0], 13) ^ _aes_mul(a[1], 9) ^ _aes_mul(a[2], 14) ^ _aes_mul(a[3], 11)
            s[4 * c + 3] = _aes_mul(a[0], 11) ^ _aes_mul(a[1], 13) ^ _aes_mul(a[2], 9) ^ _aes_mul(a[3], 14)
    inv_shift()
    for i in range(16):
        s[i] = _INV_SBOX[s[i]]
    add(rk[0])
    return bytes(s)


def aes128_cbc_decrypt(key, iv, data):
    rk = _aes_expand_key(key)
    out, prev = bytearray(), iv
    for off in range(0, len(data), 16):
        blk = data[off:off + 16]
        dec = _aes_decrypt_block(blk, rk)
        out += bytes(a ^ b for a, b in zip(dec, prev))
        prev = blk
    return bytes(out)


def decode_user_storage(b64):
    """
    解 Trae userStorage 的加密串 → 明文 JSON 文本；解不开返回 None。

    ⚠️ 踩坑记录（2026-10-14）：解出来必须**手工去掉 PKCS#7 填充**。
       浏览器 / Node 的 WebCrypto 解密是自动去填充的，纯 Python 不会 ——
       不去的话尾部会多出几个 0x05，SHA-512 校验永远对不上，
       看起来"密钥推错了"，其实是填充没剥。
    """
    if not HAS_CRYPTO_TABLE:
        return None          # 没有自备常量表 → 这条来源直接跳过，不报错

    try:
        raw = base64.b64decode(b64)
    except Exception:
        return None
    if len(raw) < _BC_EG + _BC_NY + 16 or raw[0] != 116 or raw[1] != 99:   # 't', 'c'
        return None
    # 头部第 3、4 字节是版本标记；(32,32,2,3) 是 AES_PRIVATE，否则按 AES 试
    if raw[2] == 32 and raw[3] == 32 and raw[4] == 2 and raw[5] == 3:
        versions = [2]
    else:
        versions = [1, 2]
    key = raw[_BC_EG:_BC_EG + _BC_NY]
    ct = raw[_BC_EG + _BC_NY:]
    if len(key) != _BC_NY or not ct or len(ct) % 16:
        return None
    for ver in versions:
        mask = bytes((_BC_CLE[i] ^ _BC_LLE[i]) if ver == 2 else (_BC_ULE[i] ^ _BC_DLE[i])
                     for i in range(_BC_OO))
        n = hashlib.sha512(key).digest() + mask
        n = hashlib.sha512(n).digest() + n[_BC_HH:]
        pt = aes128_cbc_decrypt(n[:16], n[16:32], ct)
        # 手工剥 PKCS#7 填充
        if pt:
            pad = pt[-1]
            if 1 <= pad <= 16 and pt[-pad:] == bytes([pad]) * pad:
                pt = pt[:-pad]
        if len(pt) > _BC_HH and hashlib.sha512(pt[_BC_HH:]).digest() == pt[:_BC_HH]:
            try:
                return pt[_BC_HH:].decode("utf-8")
            except Exception:
                return None
    return None


def _iso_ts(s):
    """'2026-10-26T00:50:01.463Z' → unix 秒；失败返回 None。"""
    if not isinstance(s, str) or len(s) < 19:
        return None
    try:
        return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").timestamp()
    except Exception:
        return None


def collect_user_storage_tokens(client):
    """
    E 类来源：`<client>/User/globalStorage/storage.json` 里的加密 UserStorage。

    这是 2026-10-14 打通的新来源，也是**目前唯一稳定可用**的：
    客户端把主凭证（payload 里 source=refresh_token、14 天）加密存在这里，
    键名 `iCubeAuthInfo://<authProviderId>` —— 用户名不固定，所以按前缀扫所有键，
    逐个尝试解密，取解出来带 `token` 字段的那个。

    返回 [{"token", "path", "key", "expiredAt"}]。
    """
    out = []
    if not HAS_CRYPTO_TABLE:
        return out                  # 没自备常量表，这条来源整体跳过
    path = os.path.join(client, "User", "globalStorage", "storage.json")
    if not os.path.isfile(path):
        return out
    try:
        with open(path, "r", encoding="utf-8") as fh:
            store = json.load(fh)
    except Exception:
        return out
    if not isinstance(store, dict):
        return out
    for k, v in store.items():
        if not k.startswith("iCubeAuthInfo://") or not isinstance(v, str):
            continue
        try:
            txt = decode_user_storage(v)
        except Exception:
            txt = None              # 自备常量表填错（长度/类型不对）时别让整个脚本崩掉
        if not txt:
            continue
        try:
            info = json.loads(txt)
        except Exception:
            continue
        tok = info.get("token")
        if not isinstance(tok, str) or len(tok) < 60:
            continue
        out.append({"token": tok, "path": path, "key": k,
                    "expiredAt": info.get("expiredAt"),
                    "info": info})
    return out


def collect_token_candidates():
    """
    收集本机所有可用的 Trae 凭证候选（五类来源）：
      A. webview leveldb 的 Cloud-IDE-Token —— 8 小时短效，
         ⚠️ 实测只在打开「套餐/用量」webview 页面时才刷新，日常用 agent 不会刷新！
      B. 客户端 logs/*/main.log 里的登录态 access token —— 14 天有效期，
         新版已把值掩码成 ******，留着只为兼容旧版客户端
      C. ~/.trae-cn/trae-jwt-token —— 新版 TokenManager 写的**临时** token 文件
         ⚠️ 实测对账单接口一律 401（它只过 cloudide 类接口），由实测环节淘汰
      D. logs/*/Modular/ai-agent_*_stdout.log 里的 BootUserInfo.token
         —— 2026-10-12 曾靠它打通；⚠️ 2026-10-11 之后客户端**不再写**这个日志（全是 0 字节），
         旧日志目录也会被客户端定期清理，已经不可依赖。留着兜底。
      E. ⭐ `<client>/User/globalStorage/storage.json` 的**加密 UserStorage**
         —— 2026-10-14 打通，目前唯一的稳定来源：客户端把主凭证（source=refresh_token、
         14 天）加密存在这里，算法在 byteCrypto.js，见上面的 decode_user_storage()。
         它跟着登录态走，只要客户端还登录着就一直有效。

    返回候选列表（去重，按 (boot, exp) 从新到旧取前 16 个 —— 日志会累积历史 token，
    全量实测太慢）。
    """
    cands = {}
    scan_targets = []
    clients = discover_client_dirs()
    for client in clients:
        # A. webview leveldb
        for ldb in glob.glob(os.path.join(client, "Partitions", "*", "Local Storage", "leveldb", "*")):
            if os.path.isdir(ldb) or ldb.endswith(("LOCK", "LOG", "CURRENT", "MANIFEST-000001")):
                continue
            scan_targets.append((ldb, "webview"))
        # B. 日志（logs 子目录的 .log / .txt）
        for lf in glob.glob(os.path.join(client, "logs", "*", "*.log")) + \
                  glob.glob(os.path.join(client, "logs", "*", "*.txt")):
            scan_targets.append((lf, "logs"))
        # D. AI agent 的 stdout 日志（在 logs/<会话>/Modular/ 下，比 B 深一层）
        for lf in glob.glob(os.path.join(client, "logs", "*", "Modular", "*.log")):
            scan_targets.append((lf, "aiagent"))


    # C. 产品数据目录里的 token 文件（名字含 token 的，或已知的固定文件名）
    for pd in PRODUCT_DATA_DIRS:
        if not os.path.isdir(pd):
            continue
        for p in glob.glob(os.path.join(pd, "*token*")) + \
                 glob.glob(os.path.join(pd, "*", "*token*")):
            if os.path.isfile(p):
                scan_targets.append((p, "tokenfile"))

    MAX_READ = 8 * 1024 * 1024          # ai-agent 的 stdout 日志可以很大，只读前 8MB
    MAX_FILE = 64 * 1024 * 1024         # 更大的文件直接跳过
    for path, kind in scan_targets:
        try:
            if os.path.getsize(path) > MAX_FILE:
                continue
            with open(path, "rb") as fh:
                b = fh.read(MAX_READ)
        except Exception:
            continue
        # 先挑出 BootUserInfo 里的 token（这类才是有用的 refresh_token）
        boot_toks = {m.group(1).decode() for m in BOOT_TOKEN_RE.finditer(b)}
        for m in JWT_RE.finditer(b):
            tok = m.group(0).decode()
            js = decode_jwt(tok)
            if not js or "exp" not in js or "iat" not in js:
                continue
            keyed = b[max(0, m.start() - 120):m.start()].decode("utf-8", "ignore").find("Cloud-IDE-Token") >= 0
            if tok not in cands:
                cands[tok] = {
                    "token": tok, "path": path, "kind": kind, "keyed": keyed,
                    "payload": js, "exp": js["exp"], "iat": js["iat"],
                    "boot": tok in boot_toks,
                }
    # E. 加密 UserStorage（storage.json）—— 当前最可靠的来源，置 boot=True 优先实测
    for client in clients:
        for item in collect_user_storage_tokens(client):
            tok = item["token"]
            js = decode_jwt(tok)
            if not js or "exp" not in js:
                # 新版主凭证不是 JWT（是不透明串），过期时间得从 storage.json 字段取
                exp = _iso_ts(item.get("expiredAt"))
                if not exp:
                    continue
                iat = None
                info = item.get("info") or {}
                for f in ("tokenReleaseAt", "issuedAt", "refreshExpiredAt"):
                    iat = _iso_ts(info.get(f))
                    if iat:
                        break
                js = {"exp": int(exp), "iat": int(iat or exp - 7 * 86400), "iss": "trae",
                      "source": "userstorage"}
            if tok not in cands:
                cands[tok] = {
                    "token": tok, "path": item["path"], "kind": "userstorage", "keyed": True,
                    "payload": js, "exp": js["exp"], "iat": js["iat"],
                    "boot": True,
                }
    # 日志里会累积几十个历史 token，全量实测太慢 —— 按 (boot, exp) 取最新的 16 个
    out = sorted(cands.values(),
                 key=lambda c: (c.get("boot", False), c["exp"], c["iat"]),
                 reverse=True)
    return out[:16]


def verify_token(token, timeout=20):
    """
    实测一个 token 到底能不能调通账单接口。
    用最轻的接口（套餐积分状态）探一下：200 表示可用，401 表示不可用。
    ⚠️ 这一步很关键：新版客户端写的 `trae-jwt-token` 看着像凭证，
       但实测对账单接口一律 401；只按 exp 选会把这种「假凭证」选上来。
    返回 (是否可用, 状态码/说明)。
    """
    url = API_HOST + API_CREDITS
    data = json.dumps({}).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/plain, */*")
    req.add_header("Authorization", "Cloud-IDE-JWT " + token)
    req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200, "HTTP %s" % r.status
    except urllib.error.HTTPError as e:
        return False, "HTTP %s" % e.code
    except Exception as e:
        return False, "异常 %s" % str(e)[:60]


def check_login_state(token, timeout=15):
    """
    调客户端的 CheckLogin 接口，判断一个 token 到底还认不认。

    为什么要多这一步（2026-10-11 加）：
      只做 verify_token（打账单接口）时，401 的含义是含糊的 —— 可能是 token 无效，
      也可能是 token 有效但「登录态已失效」。这两种情况用户的处理动作完全不同。
      CheckLogin 会明确返回 `IsLogin: true/false`：
        - CheckLogin → IsLogin:true  但账单 401  → 凭证与接口版本不匹配（真异常，要排查）
        - CheckLogin → IsLogin:false 且账单 401  → 登录态确实失效了（让用户重登）
      实测：新版客户端把可用凭证存进了**加密的 UserStorage**（os_crypt 保护），
      磁盘上只剩 `~/.trae-cn/trae-jwt-token` 这个「看着像凭证、实际已失效」的文件，
      所以这里能明确告诉用户"是登录态没了"，而不是让人以为脚本坏了。

    返回 (是否登录, 说明)。
    """
    url = API_HOST + API_CHECKLOGIN
    data = json.dumps({"IDEVersion": IDE_VERSION}).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("x-cloudide-token", token)      # 客户端真实用的是这个 header（不是 Authorization）
    req.add_header("User-Agent", "Trae/%s" % IDE_VERSION)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            j = json.loads(r.read().decode("utf-8", "ignore"))
            is_login = bool((j.get("Result") or {}).get("IsLogin"))
            return is_login, "IsLogin=%s" % is_login
    except urllib.error.HTTPError as e:
        return None, "HTTP %s" % e.code
    except Exception as e:
        return None, "异常 %s" % str(e)[:60]


def alive_candidates():
    """
    返回「未过期」的候选，按 (boot, exp) 从新到旧排序。不做网络实测（省时）。

    boot 优先：来自 AI agent 日志的 refresh_token 才是能调账单接口的那种，
    先实测它就能少打几次无用的 401 请求。
    """
    cands = collect_token_candidates()
    now = datetime.now().timestamp()
    alive = [c for c in cands if c["exp"] > now]
    alive.sort(key=lambda c: (c.get("boot", False), c["exp"], c["iat"]), reverse=True)
    return alive, cands



def token_state(js):
    now = datetime.now().timestamp()
    return {
        "issued": datetime.fromtimestamp(js.get("iat", 0)),
        "expires": datetime.fromtimestamp(js["exp"]),
        "span_h": (js["exp"] - js.get("iat", 0)) / 3600.0,
        "left_h": (js["exp"] - now) / 3600.0,
        "alive": js["exp"] > now,
    }


# ---------------------------------------------------------------- API
def api_post(path, token, body=None, retries=2):
    """POST 到 Trae API；网络抖动自动重试（指数退避）。"""
    url = API_HOST + path
    data = json.dumps(body or {}).encode()
    last_err = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json, text/plain, */*")
        req.add_header("Authorization", "Cloud-IDE-JWT " + token)
        req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                raw = r.read().decode("utf-8", "ignore")
                try:
                    return r.status, json.loads(raw)
                except Exception:
                    return r.status, {"raw": raw}
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "ignore")
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, {"raw": raw}
        except Exception as e:
            last_err = e
            if attempt < retries:
                import time
                time.sleep(2 * (attempt + 1))
    return 0, {"error": str(last_err)}


# ⚠️ 服务端单页硬上限就是 20：page_size 传 100 会返回 HTTP 400 / code 9004
#    （"The submitted order parameters are incorrect"）。所以只能靠翻页取全量。
PAGE_SIZE = 20
MAX_PAGES = 50          # 50 × 20 = 1000 条上限，足够；纯属防死循环


def fetch_usage_all(token, start, end):
    """
    翻页取全部用量记录。

    ⚠️ 2026-10-10 修的 bug：旧版只发了一次 page_num=1，于是永远只有最近 20 条 ——
       看板因此看起来"只剩今天的数据"。接口会返回 `total`，实际数据远超 20 条。
       现在改为按 total 翻到取完；并对同一 session_id 去重（防翻页边界重叠）。
    返回 (records_raw, total, pages, err)。
    """
    out, seen, err = [], set(), None
    total = None
    for pn in range(1, MAX_PAGES + 1):
        status, r = api_post(API_USAGE, token, {
            "start_time": start, "end_time": end,
            "page_size": PAGE_SIZE, "page_num": pn, "usage_type": [7],
        })
        if status == 400:
            # 有些版本要求字符串型的 usage_type，降级重试一次
            status, r = api_post(API_USAGE, token, {
                "start_time": start, "end_time": end,
                "page_size": PAGE_SIZE, "page_num": pn, "usage_type": "7",
            })
        if status == 401:
            return out, total, pn - 1, "接口返回 401：凭证被拒（可能客户端已登出）。请打开 Trae 重新登录。"
        if status != 200:
            return out, total, pn - 1, "用量接口返回 %s：%s" % (
                status, json.dumps(r, ensure_ascii=False)[:200])

        t = r.get("total")
        if isinstance(t, int) and t > 0:
            total = t
        arr = r.get("user_usage_group_by_sessions") or []
        if not arr:
            return out, total, pn - 1, None          # 到底了
        for s in arr:
            sid = s.get("session_id")
            if sid and sid in seen:
                continue                              # 防御：翻页边界重复
            if sid:
                seen.add(sid)
            out.append(s)
        if total is not None and len(out) >= total:
            return out, total, pn, None               # 已取满
        if len(arr) < PAGE_SIZE:
            return out, total, pn, None               # 最后一页不满
    return out, total, MAX_PAGES, None                # 撞到 MAX_PAGES 上限



# ---------------------------------------------------------------- 主流程
def pick_usable_token():
    """
    从所有候选里挑一个**实测能调通账单接口**的 token。

    ⚠️ 为什么不能只看 exp：新版客户端写的 `~/.trae-cn/trae-jwt-token` 寿命 7 天、
       看起来最"新鲜"，但对账单接口**一律 401**（2026-10-11 实测）——
       只按 exp 选会把它选上来，然后在真正拉数据时报 401。
       所以这里按 exp 从晚到早**逐个实测**，第一个 HTTP 200 的胜出。

    返回 (token, 来源路径, payload, 候选清单, 备注)；没有可用时 token 为 None。
    """
    alive, allc = alive_candidates()
    if not alive:
        if allc:
            allc.sort(key=lambda c: (c["exp"], c["iat"]), reverse=True)
            return None, allc[0]["path"], allc[0]["payload"], allc, "全部候选均已过期"
        return None, None, None, [], "本机找不到任何 Trae token 候选"

    failed = []
    for c in alive:
        ok, detail = verify_token(c["token"])
        c["usable"] = ok
        c["detail"] = detail
        if ok:
            note = ""
            if failed:
                note = "已跳过 %d 个「未过期但接口拒绝」的候选：%s" % (
                    len(failed), "、".join("%s→%s" % (f["kind"], f["detail"]) for f in failed))
            return c["token"], c["path"], c["payload"], alive, note
        failed.append(c)

    return None, alive[0]["path"], alive[0]["payload"], alive, \
        "候选都未过期但账单接口全部拒绝：" + "、".join(
            "%s→%s" % (c["kind"], c["detail"]) for c in alive)


def fetch(days=90):
    token, src, js, cands, note = pick_usable_token()
    if token is None:
        if not cands:
            return None, "找不到本地 token：请确认 Trae 客户端处于登录状态，且至少打开过一次。"
        st = token_state(js)
        if not st["alive"]:
            return None, ("本机所有 token 候选均已过期（最晚的 %.2f 小时前过期，读自 %s）。\n"
                          "  → 打开一次 Trae 客户端并登录，等它写入新凭证后重试；\n"
                          "  → 或打开 Trae 内的「套餐/用量」页面，让 webview 刷新短效 token。"
                          % (-st["left_h"], src))
        return None, ("找到了 token 但账单接口拒绝使用（%s）。\n"
                      "  → 优先检查 E 类来源（storage.json 的加密 UserStorage）是否读到了：\n"
                      "     只要 Trae 客户端登录着，它每次启动都会刷新这份凭证。\n"
                      "  → 若只有 `~/.trae-cn/trae-jwt-token`（temp token，由 GenerateTempToken 签发，\n"
                      "     本来就是给 AI 补全用的、调不了账单接口）—— 说明客户端没在登录态，\n"
                      "     打开 Trae 并确认已登录后重跑本脚本。"
                      % note)

    st = token_state(js)
    now = int(datetime.now().timestamp())
    start = now - days * 86400

    sessions, total, pages, err = fetch_usage_all(token, start, now)
    if err:
        return None, err

    # 套餐额度：优先老接口，失败或没数据就退回合并后的新接口（v2，返回结构兼容）
    _, ents = api_post(API_ENTITLEMENTS, token)
    if not isinstance(ents, dict) or not ents.get("usage_summary"):
        _, alt = api_post(API_ENT_USAGE, token)
        if isinstance(alt, dict) and alt.get("usage_summary"):
            ents = alt
    # 注：cn_credits_billing_status 已经由 pick_usable_token() 的实测环节调过，这里不再重复请求。

    # ---- 归一成记录列表
    records = []
    for s in sessions:
        ei = s.get("extra_info") or {}
        t = s.get("usage_time") or 0
        dt = datetime.fromtimestamp(t) if t else None
        inp = ei.get("input_token", 0) or 0
        cac = ei.get("cache_read_token", 0) or 0
        # 项目名：Trae 的接口只在少数记录里给 `mode`（Auto 等），**大多数记录是空字符串**。
        # 空的时候退回用**模型名** —— 比原来的「Trae · 未知模式」可读得多
        # （125 条里 102 条为空，等于大半个面板都是「未知模式」；2026-10-12 和田确认改）。
        mode_raw = (s.get("mode") or "").strip()
        model_raw = (s.get("model_name") or "").strip()
        records.append({
            "ts": t * 1000,
            "time": dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "—",
            "date": dt.strftime("%Y-%m-%d") if dt else "—",
            "hour": dt.hour if dt else 0,
            "sessionId": s.get("session_id", ""),
            "session": (s.get("user_input_preview") or "").strip() or "（无预览）",
            "project": "Trae · " + (mode_raw or model_raw or "未知模式"),
            "kind": "Trae 会话",
            "mode": s.get("mode") or "—",
            "model": s.get("model_name") or (s.get("mode") or "—"),
            "calls": 1,
            "inputTokens": inp,
            "outputTokens": ei.get("output_token", 0) or 0,
            "totalTokens": inp + (ei.get("output_token", 0) or 0),
            "cachedTokens": cac,
            "cacheWriteTokens": ei.get("cache_write_token", 0) or 0,
            "cacheHitRate": (cac / inp) if inp else 0.0,
            "reasoningTokens": 0,
            "credit": round(s.get("credits_float", 0) or 0, 4),
            "costMoney": round(s.get("cost_money_float", 0) or 0, 6),
            "hasCredit": True,
            "contextUsed": None,
            "contextSize": None,
        })
    records.sort(key=lambda r: r["ts"])

    # ---- 汇总
    def s_(f):
        return sum(r[f] for r in records)

    tin, tout, tcac = s_("inputTokens"), s_("outputTokens"), s_("cachedTokens")
    ent = (ents or {}).get("usage_summary") or {}
    payload = {
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "trae",
        "tokenInfo": {
            "issued": st["issued"].strftime("%Y-%m-%d %H:%M:%S"),
            "expires": st["expires"].strftime("%Y-%m-%d %H:%M:%S"),
            "leftHours": round(st["left_h"], 2),
            "readFrom": src,
            # 本次是「实测可用」才选中的；note 记录跳过了哪些不可用的候选
            "verified": True,
            "note": note or "",
            "candidates": [
                {"kind": c["kind"], "path": c["path"],
                 "expires": datetime.fromtimestamp(c["exp"]).strftime("%Y-%m-%d %H:%M"),
                 "usable": c.get("usable"),          # True/False；未实测过则是 None
                 "probed": "usable" in c}            # 是否真的发过请求实测
                for c in cands
            ],
        },
        "range": {
            "start": records[0]["date"] if records else "—",
            "end": records[-1]["date"] if records else "—",
        },
        "paging": {
            "total": total,                 # 服务端报告的总条数（窗口内）
            "fetched": len(records),        # 实际取回的条数
            "pages": pages,                 # 翻了几页
            "pageSize": PAGE_SIZE,
        },
        "records": records,
        "summary": {
            "requests": len(records),
            "sessions": len({r["sessionId"] for r in records}),
            "credit": round(s_("credit"), 2),
            "inputTokens": tin,
            "outputTokens": tout,
            "totalTokens": tin + tout,
            "cachedTokens": tcac,
            "reasoningTokens": 0,
            "cacheHitRate": (tcac / tin) if tin else 0.0,
            "cost": round(s_("costMoney"), 4),
            "models": len({r["model"] for r in records}),
            "projects": len({r["project"] for r in records}),
        },
        "quota": {
            "consumed": ent.get("consumed_amount"),
            "total": ent.get("total_amount"),
            "ratio": ent.get("consumption_ratio"),
        },
    }
    return payload, None


def main():
    # 输出跟随当前控制台编码，不强行改成 UTF-8：中文 Windows 控制台是 cp936，
    # 若这里写成 utf-8，双击 bat 时 cmd 会按 936 解码 UTF-8 字节 → 中文提示全成乱码。
    # errors="replace" 兜住 GBK 编不出的字符（⚠️ ✅ ❌），避免直接抛 UnicodeEncodeError。
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--check", action="store_true", help="只看 token 状态")
    ap.add_argument("--json", help="额外导出成 JSON 文件")
    args = ap.parse_args()

    if args.check:
        print("=" * 62)
        print("Trae 本地凭证状态（全部候选 + 接口实测）")
        print("=" * 62)
        cands = collect_token_candidates()
        if not cands:
            print("  [×] 未找到任何 token。请确认 Trae 客户端已登录并至少打开过一次。")
            return 1
        now = datetime.now().timestamp()
        cands.sort(key=lambda c: (c["exp"], c["iat"]), reverse=True)
        kind_names = {"webview": "webview(8h短效)", "logs": "logs(14天登录态)",
                      "tokenfile": "token文件(新版客户端)",
                      "aiagent": "ai-agent日志(已停写)",
                      "userstorage": "storage.json加密存储(14天)⭐"}
        winner = None
        for i, c in enumerate(cands):
            st = token_state(c["payload"])
            kind = kind_names.get(c["kind"], c["kind"])
            line = "  [%d] %s %s" % (i + 1, kind, "✅未过期" if st["alive"] else "❌已过期")
            if st["alive"]:
                # 只有未过期的才值得花一次网络请求实测
                ok, detail = verify_token(c["token"])
                c["usable"], c["detail"] = ok, detail
                line += "  接口实测: %s" % ("✅可用" if ok else "❌被拒(%s)" % detail)
                if ok:
                    if winner is None:
                        winner = c
                        line += "  ←实际选用"
                    # 可用但不是首选（已有更晚过期的）→ 不用再多问 CheckLogin
                else:
                    # 被拒时再问一次 CheckLogin：区分「token 无效」与「登录态失效」
                    logged, lmsg = check_login_state(c["token"])
                    c["logged_in"], c["login_detail"] = logged, lmsg
            print(line)
            print("      签发 %s → 过期 %s（剩 %.1f 小时）" % (
                st["issued"].strftime("%m-%d %H:%M"),
                st["expires"].strftime("%m-%d %H:%M"), st["left_h"]))
            print("      来源: %s" % c["path"])
            if c.get("login_detail"):
                if c.get("logged_in") is False:
                    print("      登录态自检: ❌ CheckLogin 返回 IsLogin=false —— 这个 token 已不被服务端认可")
                elif c.get("logged_in") is True:
                    print("      登录态自检: ⚠ CheckLogin 说已登录，但账单接口仍拒绝（凭证与接口版本不匹配）")
                else:
                    print("      登录态自检: ? 无法判定（%s）" % c["login_detail"])
        if winner is None:
            print("\n  [×] 没有「接口实测可用」的凭证。")
            if not HAS_CRYPTO_TABLE:
                print("  → ① 先查这个：本机没有 trae_crypto_table.py，"
                      "「加密 UserStorage」这条主力来源已被跳过。")
                print("     复制 trae_crypto_table.py.example 并按 README 自备常量表即可启用。")
            if any(c.get("logged_in") is False for c in cands):
                print("  → 原因：能读到的凭证都「登录态失效」了（CheckLogin IsLogin=false）。")
                print("  → 处理：打开 Trae 客户端 → 确认已登录；若还不行，退出登录再重新登录一次。")
            else:
                print("  → 打开一次 Trae 客户端并登录，让它写入新的登录态后重试。")
            print("  → 注意：加密 UserStorage（storage.json）是当前主力来源，"
                  "它只在客户端**登录着**且至少启动过一次时才有值；")
            print("     若该文件里的 iCubeAuthInfo:// 条目解不出来，多半是客户端大版本换了加密格式，")
            print("     需要按 README「Trae 数据采集」一节重新对 byteCrypto.js 校准常量。")
            return 2
        if winner["kind"] == "webview":
            print("\n  ⚠ 当前只有 webview 短效 token 可用（剩 %.1f 小时）。" % winner["left_h"])
        if any(c.get("usable") is False for c in cands):
            print("  ℹ 有候选「未过期但接口拒绝」——这是正常的，脚本会自动跳过它们。")
        return 0

    payload, err = fetch(args.days)
    if err:
        print("[×]", err)
        return 1

    with open(OUT_JS, "w", encoding="utf-8") as fh:
        fh.write("// 由 trae_fetch.py 自动生成（数据来自 Trae 官方用量接口）\n")
        fh.write("window.TRAE_USAGE = ")
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write(";\n")

    if args.json:
        json.dump(payload, open(args.json, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)

    s = payload["summary"]
    q = payload["quota"]
    pg = payload.get("paging") or {}
    print("已生成:", OUT_JS)
    print(f"  窗口    : {payload['range']['start']} ~ {payload['range']['end']}")
    print(f"  会话数  : {s['requests']}", end="")
    if pg.get("total"):
        mark = "" if pg.get("fetched", 0) >= pg["total"] else "  ⚠ 未取满，请检查"
        print(f"  （服务端共 {pg['total']} 条，翻 {pg.get('pages')} 页取回 {pg.get('fetched')} 条）{mark}")
    else:
        print()
    print(f"  积分    : {s['credit']}", end="")
    if q.get("total"):
        print(f"  / 套餐 {q['total']:.0f}  ({(q.get('ratio') or 0) * 100:.2f}%)")
    else:
        print()
    print(f"  Token   : 输入 {s['inputTokens']:,} / 输出 {s['outputTokens']:,}")
    print(f"  缓存命中: {s['cacheHitRate'] * 100:.2f}%")
    print(f"  成本    : ${s['cost']}")
    ti = payload["tokenInfo"]
    print(f"  凭证    : 有效至 {ti['expires']}（剩 {ti['leftHours']} 小时，自动读取）")
    if ti.get("note"):
        print(f"  ⚠ 凭证说明: {ti['note']}")
    else:
        print(f"            来源 {ti['readFrom']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
