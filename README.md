# AI 编码工具用量抓取脚本（WorkBuddy + Trae）

两个**只读本机数据**的 Python 脚本，把 WorkBuddy 和 Trae 的用量消耗抓出来，导出成看板能直接加载的 `*-data.js`。

- **纯本地**：数据不出本机，不往任何地方上传。
- **零第三方依赖**：只用 Python 标准库（AES 那部分是手写的，就是为了不装包）。
- **只读**：不修改客户端任何文件，不做任何写操作以外的副作用。

| 脚本 | 抓谁 | 数据从哪来 | 联网 | 产出 |
|---|---|---|---|---|
| `workbuddy_fetch.py` | WorkBuddy | `~/.workbuddy/` 的会话转录 + 本地 SQLite 账本 | ❌ 完全离线 | `usage-data.js`（`window.WB_USAGE`） |
| `trae_fetch.py` | Trae | 本机凭证 + 官方账单接口 | ✅ 只调官方 API | `trae-data.js`（`window.TRAE_USAGE`） |

---

## 环境要求

- Windows（脚本按 Windows 的 `%APPDATA%` / `%LOCALAPPDATA%` 布局找客户端数据目录）
- Python **3.8+**，`trae_fetch.py` 无额外要求；两个脚本都不需要 `pip install` 任何东西

> Linux / macOS 目前没适配——主要差在客户端数据目录的位置。想移植的话改 `discover_client_dirs()` 里的根目录即可。

## 快速开始

```bash
# WorkBuddy：离线扫本机，产出 usage-data.js
python workbuddy_fetch.py

# Trae：自动找凭证 → 调账单接口，产出 trae-data.js（默认近 90 天）
python trae_fetch.py

# Trae 的可选参数
python trae_fetch.py --days 30        # 改窗口，默认 90 天
python trae_fetch.py --check          # 只体检：打印找到的凭证能不能用，不抓数据
python trae_fetch.py --json out.json  # 额外导出一份 JSON（方便自己写分析）
```

产出的 `*-data.js` 是 `window.XXX = {...}` 形式的 JS 文件，用 `<script src="...">` 直接引就行——
这样在 `file://` 下打开本地 HTML 也不会撞上 fetch 的跨域限制。

> ⚠️ 这两个 `*-data.js` 里是**你自己的真实用量**，仓库的 `.gitignore` 已经把它们排除了。别手动 `git add -f`。

---

## 数据源一：WorkBuddy（`workbuddy_fetch.py`）

**完全离线**，读的全是 WorkBuddy 自己落盘的东西：

| 路径 | 内容 |
|---|---|
| `~/.workbuddy/projects/**/*.jsonl` | 会话转录，每次模型调用都带 token 级用量 |
| `~/.workbuddy/workbuddy.db` → `session_usage` 表 | 积分账本（`credit_json` 字段） |
| `~/.workbuddy/workbuddy.db` → `sessions` 表 | 会话标题 / 项目路径 / 模型 / 模式 |

token 用量取自 JSONL 里的 `providerData.usage`：

```json
{ "inputTokens": 1234, "outputTokens": 567, "totalTokens": 1801,
  "inputTokensDetails": [{"cached_tokens": 900}],
  "outputTokensDetails": [{"reasoning_tokens": 120}] }
```

### 口径对齐（这几点很容易算错）

1. **`inputTokens` 是「未命中缓存」的输入**，不含缓存读。脚本统一成看板口径：
   `input = 未缓存 + 缓存读`，`cached = 缓存读`，这样「缓存命中率 = cached ÷ input」才和其它源可比。
2. **积分要从账本取，不要自己用 token 推算**——各模型的换算系数是服务端定的，本地算不准。
   账本里漏记的条目，脚本会退回运行日志兜底（README 原项目里有详细考证）。

---

## 数据源二：Trae（`trae_fetch.py`）

Trae 客户端本身显示的用量来自官方接口，所以脚本要做的其实是两件事：**找凭证** + **调接口**。

### 凭证是怎么拿到的

不用你手动去浏览器里复制 token。脚本会扫描本机所有「长得像 Trae 客户端数据目录」的文件夹
（目录名含 `trae` + 有 `logs` / `Partitions` / `Preferences` / `Local State` 之一），
从里面翻出多类候选凭证，然后**逐个实测**——谁能调通账单接口就用谁。

这么做是因为 Trae 客户端改版很频繁（2026-10-11 TraeWork 与 TraeCode 合并为「全新 TRAE」，
目录名和凭证存储位置都变过），**硬编码任何一条路径都会在某个版本后静默失效**。
所以策略是「多路候选 + 实测筛选」而不是「认准一条路」。

已知候选来源（脚本里都有注释）：

- AI agent 进程 stdout 日志里的 `BootUserInfo.token`
- 客户端 `main.log` 里的登录态 access token
- webview leveldb 里的 `Cloud-IDE-Token`
- `~/.trae-cn/trae-jwt-token` 临时 token 文件
- `User/globalStorage/storage.json` 里加密的 `iCubeAuthInfo://*`（用客户端内置的掩码常量表解 AES-128-CBC）

> 📌 **哪天突然抓不到数了，先搜 AI agent 的 `*_stdout.log`**——历史上两次断档都是靠翻这类日志重新打通的。

### 调的接口

```
GET /trae/api/v1/pay/query_user_usage_group_by_session   # 按会话聚合的用量
GET /trae/api/v2/pay/cn_credits_billing_status           # 积分账单
GET /trae/api/v2/pay/user_current_entitlement_list       # 当前套餐权益
GET /trae/api/v2/pay/ide_user_ent_usage                  # 新版 v2（兜底）
GET /trae/api/v2/pay/ide_user_pay_status                 # 新版 v2（兜底）
GET /cloudide/api/v3/trae/CheckLogin                     # 登录态自检（区分「token 无效」与「登录态失效」）
```

两个易踩的坑：

- 账单接口**必须**用 `Authorization: Cloud-IDE-JWT <token>`；
  `x-cloudide-token` 只对 `/cloudide/*` 类接口有效，用在 `/trae/api/*/pay/*` 上一律 401。
- 用量接口**要翻页**，`page`/`pageSize` 取全量，否则只拿回第一页、数字会偏小。

---

## 输出结构

两个脚本产出的结构基本一致（这样看板可以共用一套渲染逻辑）：

```js
window.WB_USAGE = {
  generated_at: "2026-10-15T01:20:00",
  days: [...],              // 逐日：日期 / 积分 / 输入 / 输出 / 缓存读 / 会话数
  by_model: [...],          // 按模型聚合
  by_project: [...],        // 按项目聚合
  sessions: [...],          // 会话明细
  totals: {...}             // 汇总 KPI
};
```

Trae 那一份会多带套餐额度（`entitlements`）相关的字段。

---

## 常见问题

**Q：`trae_fetch.py` 报「找不到可用凭证」？**

按顺序排查：
1. 客户端**现在是登录状态**吗？退出登录过就先重新登录一次。
2. 跑 `python trae_fetch.py --check`，它会逐个打印候选凭证的实测结果，看是哪一步挂了。
3. 客户端刚升级过？数据目录可能改名了。脚本是自动发现的，但如果新目录名里没有 `trae` 字样就会漏——
   这时手动把目录加进 `EXTRA_CLIENT_DIRS`。

**Q：`workbuddy_fetch.py` 抓出来是空的？**

确认 `~/.workbuddy/projects/` 下有 `.jsonl`、且 `~/.workbuddy/workbuddy.db` 存在。
没有的话说明 WorkBuddy 装在了别的用户目录，或者还没产生过会话。

**Q：能定时跑吗？**

可以。Windows 上建个计划任务，或者写个 `.bat` 调这两个脚本。注意用**绝对路径**指向 Python 解释器，
计划任务的环境里经常没有 `PATH`。

---

## 隐私说明

- 脚本**只在本机运行**，除了 Trae 那一份要调官方账单接口（拿你自己的用量）之外，不向任何第三方发送数据。
- 产出的 `*-data.js` 含你的逐日消耗、会话标题、本地项目路径，**属于个人信息，别提交到仓库**。
- 脚本里不含任何 token 明文——凭证都是运行时从本机读出来的。

## 免责声明

这是**个人自用的逆向工程实践**：两个工具都没有公开的用量查询 API，脚本依赖的是客户端本地数据格式
和未公开的内部接口，这些东西**随时可能因为官方改版而失效**（Trae 就变过好几次）。

- 本项目与 WorkBuddy / Trae 官方**无任何关联**，也不是官方认可的工具。
- 请勿用于绕过付费、篡改计费或任何违反上述产品服务条款的用途；仅用于查看你自己的用量。
- 接口失效属正常现象，欢迎提 issue / PR 一起维护，但别指望它永远可用。

## License

MIT — 见 [LICENSE](LICENSE)。
