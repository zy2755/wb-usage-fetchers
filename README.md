# AI 编码工具用量抓取 + 积分到期预警（WorkBuddy + Trae）

三个**只读本机数据**的 Python 脚本。前两个回答「**已经花了多少**」（消耗流水，按时间窗口），
第三个回答「**还剩多少、哪几笔快过期、该先花哪一笔**」（余额快照 + 逐批到期）——
这是互补的两本账，别混。

- **纯本地**：数据不出本机；需要联网的部分只调官方接口读你自己的账号。
- **零第三方依赖**：只用 Python 标准库（AES 那部分是手写的，就是为了不装包）。
- **只读**：不修改客户端任何文件，不在客户端里写东西。
- **不分发凭证**：仓库里没有任何 token、密钥或客户端内置常量，需要自备的都给了 `.example` 模板。

| 脚本 | 抓谁 | 回答什么 | 数据从哪来 | 联网 | 产出 |
|---|---|---|---|---|---|
| `workbuddy_fetch.py` | WorkBuddy | 逐轮消耗 | `~/.workbuddy/` 会话转录 + 本地 SQLite 账本 | ❌ 完全离线 | `usage-data.js`（`window.WB_USAGE`） |
| `trae_fetch.py` | Trae | 逐轮消耗 | 本机凭证 + 官方账单接口 | ✅ 只调官方 API | `trae-data.js`（`window.TRAE_USAGE`） |
| `credits_alert.py` | WorkBuddy + Trae | 余额与到期 | 官方 billing / 权益接口 | ✅ 只调官方 API | `credits-data.js`（`window.CREDITS_ALERT`） |

> 全文只围绕 **WorkBuddy** 和 **Trae** 这两个工具。其它 AI 编码工具的用量看板不在这个仓库里。

---

## 环境要求

- Windows（脚本按 `%APPDATA%` / `%LOCALAPPDATA%` 的布局找客户端数据目录）
- Python **3.8+**，三个脚本都不需要 `pip install` 任何东西

> Linux / macOS 目前没适配——差在客户端数据目录的位置与凭证存储方式。
> 想移植的话改 `discover_client_dirs()` 里的根目录，以及 `credits_alert.py` 的凭证通道即可。

## 快速开始

```bash
# 1) WorkBuddy：离线扫本机，产出 usage-data.js
python workbuddy_fetch.py

# 2) Trae：自动找凭证 → 调账单接口，产出 trae-data.js（默认近 90 天）
python trae_fetch.py

# 3) 积分到期预警：两家一起采，产出 credits-data.js 并打印最急的几笔
python credits_alert.py
```

常用参数：

```bash
python trae_fetch.py --days 30          # 改窗口，默认 90 天
python trae_fetch.py --check            # 只体检：逐个打印凭证候选 + 接口实测结果
python trae_fetch.py --json out.json    # 额外导出一份 JSON

python credits_alert.py --check                # 只体检两边登录态，不抓数据、不写文件
python credits_alert.py --warn-days 14         # 提醒窗口 7 天 → 14 天
python credits_alert.py --only trae            # 只跑一家
python credits_alert.py --alert-exit-code      # 有 3 天内到期时返回退出码 2（给计划任务用）
python credits_alert.py --json credits.json    # 额外导出一份 JSON
```

产出的 `*-data.js` 是 `window.XXX = {...};` 形式的 JS 文件，用 `<script src="...">` 直接引就行——
这样在 `file://` 下打开本地 HTML 也不会撞上 fetch 的跨域限制。

> ⚠️ 这几个 `*-data.js` 里是**你自己的真实用量与余额**，`.gitignore` 已全部排除。别手动 `git add -f`。

## 文件清单

| 文件 | 用途 |
|---|---|
| `workbuddy_fetch.py` | WorkBuddy 离线用量采集 |
| `trae_fetch.py` | Trae 用量采集 + 凭证发现/实测（预警脚本会复用它） |
| `credits_alert.py` | 两家积分余额、逐批到期与预警等级 |
| `trae_crypto_table.py.example` | 自备 Trae 解密常量表的模板 → 复制成 `trae_crypto_table.py` |
| `credits_auth.py.example` | 自备 WorkBuddy 登录态钩子的模板 → 复制成 `credits_auth.py` |
| `.gitignore` | 排除数据产物与所有自备凭证文件 |
| `viewer/index.html` / `style.css` / `app.js` | 可视化软件界面（消耗看板 + 到期预警） |
| `viewer/server.py` | 本地服务：托管页面 + 提供一键刷新接口 |
| `viewer/launcher.py` | 可执行版入口：定数据目录、首次启动自动采集、内部命令 `--run` |
| `start-viewer.bat` | Windows 双击启动源码版（GBK + CRLF + 无 BOM） |
| `build-exe.bat` / `credits_viewer.spec` / `version_info.txt` | 打包单文件 exe 的构建脚本与配置 |
| `LICENSE` | MIT |

两个 `.example` 对应的正式文件都**不会**被提交；缺了它们脚本也不会崩，只是相应来源优雅跳过。

---

## 可视化软件（`viewer/`）

把三份数据放在一个页面里看：**积分消耗看板**（已经花了多少）+ **积分到期预警**（还剩多少、哪笔先过期）。

### 三种打开方式

| 方式 | 怎么做 | 能做什么 |
|---|---|---|
| **最省事：单文件 exe** | 从 [Releases](https://github.com/zy2755/wb-usage-fetchers/releases/latest) 下最新的 `CreditsViewer-v*.exe` 双击（程序显示名是「积分台」） | 不用装 Python，自带运行时；自动开浏览器、一键刷新 |
| **推荐：本地服务** | 双击 `start-viewer.bat`（或 `python viewer/server.py`） | 自动开浏览器、右上角「↻ 刷新数据」一键重跑三个脚本 |
| 直接看数据 | 双击 `viewer/index.html`（`file://`） | 图表、表格全部照常可用；刷新按钮自动隐藏，改完数据按 F5 |

服务只监听 `127.0.0.1`，默认端口 **18790**（被占用会顺延到 18791…）。
端口特意取 15000 以上：本机 Windows 的动态端口范围被改过，落在 1024~15000 的端口
随时可能被系统拿去当出站连接的临时源端口，然后 `bind` 会以 `WinError 10013` 被拒
——明明没人在监听，却误以为「端口被占」。

```bash
python viewer/server.py --port 18791     # 换端口
python viewer/server.py --no-open        # 不自动开浏览器
python viewer/server.py --no-refresh     # 只托管静态页，禁用刷新接口
```

### 打包成单文件 exe

不想在目标机器装 Python，就用 exe 版。自己构建：

```bat
build-exe.bat
:: 或指定一个装了 PyInstaller 的解释器
set PYTHON=D:\Python3.13.15\python.exe
build-exe.bat
```

产物 `dist\积分台.exe`，约 9.5 MB，单文件、自带 Python 运行时。

打包后的位置关系是有意设计的，别随意挪：

| 东西 | 放在哪 | 为什么 |
|---|---|---|
| 页面 html/css/js | 打进 exe，运行时解包到临时目录 | 只读资源，跟着 exe 走 |
| `*-data.js` | **exe 旁边** | 真实用量数据；写进临时目录会随进程退出消失 |
| `credits_auth.py` / `trae_crypto_table.py` | 可以放 **exe 旁边** | 启动器会把该目录加进 `sys.path`，自备模块照样能 import |

启动流程：首次启动若发现三份数据都缺，先跑一遍采集再开页面；之后点「↻ 刷新数据」，
exe 会**把自己再拉起一遍**（内部命令 `--run <模块>`）当独立子进程执行抓取脚本，
保持「某个脚本崩了不拖垮服务」的特性。命令行参数：

```bat
积分台.exe --port 18791        换端口
积分台.exe --no-open           不自动开浏览器
积分台.exe --data-dir D:\data  数据放到别处
积分台.exe --version           看版本
```

⚠️ 两个现实问题：

- 未签名的 PyInstaller 产物可能被杀软（火绒等）或 SmartScreen 拦一次，加信任即可。
  介意的话把 spec 改成 `onedir` 自行打包（目录版更少被误报）。
- 必须在**普通桌面环境**里双击运行。若在受限终端 / 沙箱里启动，读
  `~/.workbuddy/workbuddy.db` 会报 `unable to open database file`，刷新时
  WorkBuddy 那一路会失败（页面和接口都会给出提示）。

📌 发 Releases 时注意：GitHub 会**把非 ASCII 的附件名改成 `default.exe`**
（`gh release upload` 和 REST 接口都一样）。所以本仓库的 Release 附件叫
`CreditsViewer-v1.0.1.exe` 这种 ASCII 名，本地构建产物仍是 `dist\积分台.exe`，
两者只是文件名不同。

### 页面里有什么

**「积分消耗看板」标签**

- 4 张 KPI：窗口内累计消耗、最近一日消耗、当前剩余积分、N 天内将到期（数据来自余额快照）
- 每日消耗堆叠柱：下层 WorkBuddy / 上层 Trae，可切 7 天 / 30 天 / 全部，悬停看当天明细
- 两家对比：累计、占比、轮次、单次均值
- 模型 / 项目 TOP 5（按积分聚合）
- 逐轮明细表：平台筛选 + 会话/模型/项目搜索，默认最近 80 条

**「积分到期预警」标签**

- 预警横幅：直接告诉你几天内有多少分要清零
- 两家余额卡：剩余 / 总量进度条、批次、最早到期、窗口内到期积分
- 未来到期分布柱：按到期日汇总余额，颜色取当天最急等级（3 天内红 / 7 天内橙 / 更晚蓝），可切 14 / 30 / 90 天
- 预警清单：跨平台按到期时间排序，先花最上面那笔
- 逐批明细：平台筛选 + 包名/发放原因搜索

### 导出与打印

页面右上角两个按钮：

| 按钮 | 做什么 | 怎么用 |
|---|---|---|
| **⬇ 导出 PNG** | 生成 1200×1560 的报表图（2 倍像素 = 2400×3120），文件名 `积分台报表_YYYY-MM-DD.png` | 直接下载，适合发消息 / 存档 |
| **🖨 打印 / PDF** | 调浏览器原生打印对话框，A4、两个标签分别起页（明细多时会自然跨页） | 目标打印机选「另存为 PDF」 |

- PNG 报表**不是网页截图**：它把 KPI、每日消耗趋势、两家对比、余额卡、最急 8 笔预警和
  口径说明重新排成一张固定浅色的 SVG 信息图，再用 `Image + canvas` 光栅化成 PNG。
  所以不会带上按钮、筛选框、滚动条；深色模式下导出的仍是白底，方便打印和转发。
- PDF 走 `@media print`：自动展开两个标签、隐藏控件、压低图表高度、强制打印背景色
  （不加这句 Chrome/Edge 会把柱状图印成空白），打印前临时切浅色、结束后恢复你的主题偏好。
- 报表跟随当前的时间窗口：屏幕上切到 7 天 / 30 天 / 全部，导出的 KPI 和趋势也跟着变。
- 两者都零第三方库、不联网；导出文件只含你自己的用量数据，外发前留意一下。

### 实现说明（为什么这么写）

- **零第三方依赖**：图表是手绘 SVG，没有 Chart.js / ECharts / CDN，断网也能开。
  导出同理 —— `viewer/report.js` 用 SVG + canvas 出 PNG，PDF 用浏览器原生打印，
  没有 html2canvas / jsPDF / dom-to-image。
- **不联网**：页面只加载三个本地 `*-data.js`；所有请求都在 `127.0.0.1` 内，不上传任何东西。
  数据文件用 `<script>` 挂到 `window`，所以 `file://` 下也不会撞 fetch 的跨域限制。
- **深浅色跟随系统**（`prefers-color-scheme`），右上角按钮可手动锁定；SVG 直接引用 CSS 变量，
  切主题不用重绘图表。偏好存 `localStorage` 的 `wb-credits-viewer-theme`。
- **缺数据不白屏**：三份数据缺哪份，对应模块就显示空态提示，其余照常渲染。
- **刷新只跑固定脚本**：`/api/refresh` 依次执行 `workbuddy_fetch.py` → `trae_fetch.py` →
  `credits_alert.py`，用 `sys.executable` 调用（不依赖 PATH），一家失败不影响另外两家，
  结果回显在控制台；跑完页面自动 reload。接口不接受任何 URL 参数当命令。

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
   账本里漏记的轮次，脚本会退回会话转录的逐笔积分兜底（见下）。
3. **`usage` 与 `rawUsage` 不是两代格式，是同源镜像**。2026-11-13 在本机核对了
   30,387 条记录（跨 9~11 月）：同一行 JSONL 里 `providerData.usage` 和
   `providerData.rawUsage` 同时存在，token 三元组数值 **100% 一致**。
   ⇒ 本脚本读 `usage` 是对的，别的工具读 `rawUsage` 也对，不需要双读兼容。
4. **`session_usage.used` / `size` 是上下文窗口状态，不是计费 token**。
   只取它的 `credit_json`，别把这两个字段加进 token 汇总。

### 积分兜底：已切换为会话转录（2026-11-15）

`rawUsage.credit` 是**每次模型调用实际扣的积分**，同一轮按
`conversationRequestId` 累加即为该轮积分。2026-11-15 起，脚本默认口径是
**「账本优先 + 转录补账本缺失轮次」**，旧的「扫运行日志」已退休。

| | 轮次 | 积分 |
|---|---|---|
| 转录 `rawUsage.credit` 全量 | 约 1,408 个 rid | 7149.09（01:00 快照） |
| 账本 `session_usage.credit_json` | 905 轮 | 5977.45 |
| 账本漏记、转录补上 | 317 轮（转录独有 503 个 rid / 8732 次调用） | 1336.65 |
| 看板合并后 | 1,393 轮 | 7314.10 |

合并后比转录全量多 164.99 分，正是共有轮里账本偏高的那 48 轮。

共有轮里有 48 轮数值不同，账本净偏高 164.99 分（多在最贵/首轮那笔），
所以**不让转录覆盖账本**，只补账本完全没有的轮次。切换后合并总计 7314.10 分。

> 旧法为什么退休：客户端定期清理历史日志，2026-11-15 实测日志兜底补回 **0 轮 0 分**
> （现存日志只剩 `credit=undefined` 的 response_done 重复行），看板因此净漏 1171.64 分。

---

## 数据源二：Trae（`trae_fetch.py`）

Trae 客户端显示的用量来自官方接口，所以脚本要做的是两件事：**找凭证** + **调接口**。

### 凭证是怎么拿到的

不用你手动去浏览器里复制 token。脚本会扫描本机所有「长得像 Trae 客户端数据目录」的文件夹
（目录名含 `trae` + 有 `logs` / `Partitions` / `Preferences` / `Local State` 之一），
从里面翻出多类候选凭证，然后**逐个实测**——谁能调通账单接口就用谁。

这么做是因为 Trae 客户端改版很频繁（2026-10-11 TraeWork 与 TraeCode 合并为「全新 TRAE」，
目录名和凭证存储位置都变过），**硬编码任何一条路径都会在某个版本后静默失效**。
所以策略是「多路候选 + 实测筛选」而不是「认准一条路」。

已知候选来源（脚本里都有注释）：

- ⭐ **E. `User/globalStorage/storage.json` 里加密的 `iCubeAuthInfo://*`** —— 当前主力
  （AES-128-CBC，14 天长效，客户端登录着就自动续；**需要自备常量表，见下**）
- D. AI agent 进程 `*_stdout.log` 里的 `BootUserInfo.token` —— 10-12 曾短暂打通，
  **现已失效**（日志变 0 字节、旧目录被定期清理），只留作万一
- A. 客户端 `main.log` 里的登录态 token —— 新版只写 `******` 占位，已失效
- B. webview leveldb 里的 `Cloud-IDE-Token` —— 该键已不存在，已失效
- C. `~/.trae-cn/trae-jwt-token` 临时 token —— 实测对账单接口一律 401，靠实测自动淘汰

> 📌 **突然抓不到数了，先跑 `python trae_fetch.py --check`**：
> 它会把每个候选的过期时间、CheckLogin 结果、账单接口实测结果都打出来。
> 最常见的原因是没自备 `trae_crypto_table.py`（E 类被整条跳过）。
> 老经验「先搜 AI agent 的 stdout 日志」已经过时了，别再按那个查。

### ⚠️ 自备 `trae_crypto_table.py`（Trae 用户必看）

主力凭证要解客户端自己加密的 UserStorage，解密用的四张掩码常量表是 Trae
**客户端源码里的内置常量**——出于对商业软件内部实现细节的尊重，**本仓库不分发**它们：

```bash
cp trae_crypto_table.py.example trae_crypto_table.py   # 复制模板，然后自己填值
```

- **没有这个文件脚本照样能跑**，只是跳过 E 类来源、退回其它候选路径（不会报错）
- **填了才启用主力来源**。跑 `python trae_fetch.py --check` 能看到是否生效
- 该文件已被 `.gitignore` 排除，不会误提交

取值方法：从你本机 Trae 客户端安装目录找到 `byteCrypto.js`（或等价的混淆产物），
定位 `ule` / `dle` / `cle` / `lle` 四个各 64 字节的数组，填进模板即可。
客户端大版本升级后格式可能变，届时 `--check` 会提示解不出来，重新校准常量就行。

> `credits_alert.py` 的 Trae 侧直接复用这里的凭证发现逻辑，所以同样需要这份常量表。

### 调的接口（全部是 POST）

```
POST /trae/api/v1/pay/query_user_usage_group_by_session  # 按会话聚合的用量（要翻页）
POST /trae/api/v2/pay/cn_credits_billing_status          # 积分账单；也是「凭证实测」用的探针
POST /trae/api/v2/pay/user_current_entitlement_list      # 当前套餐权益
POST /trae/api/v2/pay/ide_user_ent_usage                 # 新版 v2（兜底）
POST /cloudide/api/v3/trae/CheckLogin                    # 登录态自检（区分「token 无效」与「登录态失效」）
```

几个易踩的坑：

- 账单接口**必须**用 `Authorization: Cloud-IDE-JWT <token>`；
  `x-cloudide-token` 只对 `/cloudide/*` 类接口有效，用在 `/trae/api/*/pay/*` 上一律 401。
- 用量接口**要翻页**：每页固定 20 条（`PAGE_SIZE`），按服务端返回的 `total` 翻到取完，
  否则只拿回第一页、数字会偏小。翻页之间留 `PAGE_DELAY = 0.12s`，连发太密容易被拒。
- 服务端偶发返回 **400 且响应体为空**（并发时更常见）。2026-11-05 起 `api_post()` 会把
  400/408/425/429/5xx 都纳入指数退避重试；某一页重试后仍失败就**跳过该页继续翻**，
  最后如实标 `paging.partial` / `paging.warning`，不再让一页拖垮整个数据源。
  凭证实测也走同一个带重试的函数，避免把「其实可用」的凭证误判成失效。
- 采集变慢时看 `paging.retries`——那是「被拒后重试成功」的次数；每次等待会在 stderr 打一行提示。
- 需要把失败现场落盘排查时，设 `TRAE_FETCH_ERROR_LOG=1` 才会写 `trae_fetch-error.log`；
  默认不产生任何额外文件。

---

## 数据源三：积分到期预警（`credits_alert.py`）

**这是和前两个脚本垂直的另一个维度**：不看「花了多少」，只看「还剩多少 + 什么时候作废」。
一个平台把积分拆成很多批次发放，每批有自己的到期时刻，光看总余额会漏掉
「明天有 300 分要清零」这种事。

### 数据来源

| 平台 | 接口 | 拿到什么 |
|---|---|---|
| WorkBuddy | `POST /billing/meter/get-user-resource-summary` | 各资源包本周期总量 / 剩余 |
| WorkBuddy | `POST /billing/meter/get-user-resource-free-packages` | 免费/赠送包的逐批明细 |
| WorkBuddy | `POST /billing/meter/get-user-resource-paid-packages` | 付费套餐的逐批明细 |
| Trae | `POST /trae/api/v2/pay/user_current_entitlement_list` | 每笔权益的额度与 `expire_time` |
| Trae | `POST /trae/api/v2/pay/ide_user_ent_usage` | 结构一致，主接口失败时兜底 |

全部是**只读**接口，只读你自己的账号，不签到、不领奖、不下单。

### 凭证怎么来（这一节很重要）

**WorkBuddy 侧是「可插拔自备」的**，按优先级三选一：

1. 同目录的 `credits_auth.py`（不入库，见 `credits_auth.py.example`），实现 `get_auth()` 返回 `(token, domain)`
2. 环境变量 `WB_CREDITS_TOKEN` + `WB_CREDITS_DOMAIN`（定时任务友好，不在磁盘留痕）
3. 本机已安装的 WorkBuddy 积分技能：脚本按路径 import，只借它的登录态解析能力，**不复制它的代码**

> 为什么这么绕：WorkBuddy 5.6.2+ 把登录态里的 `accessToken` 换成了 AES-256-GCM 信封，
> 解密要绕本机 DPAPI / 进程内存，属于客户端内部实现细节。公开仓库既不该分发、
> 也不该复制这套东西，所以只留一个接口，让使用者接自己本机的能力。
> 客户端会不定时重写登录态文件且不是原子写，脚本对第三条通道做了 4 次重试，
> 免得把「刚好撞上写文件」误报成「登录过期」。

**Trae 侧不用额外配置**：直接 `import trae_fetch` 并调 `pick_usable_token()`，
解密算法、候选扫描、接口实测全部复用一份代码（所以同样需要自备 `trae_crypto_table.py`）。

### 口径上的三个坑

1. **WorkBuddy 逐批接口同时给两套额度字段**：`Capacity*`（累计）与 `Cycle*`（本周期）。
   「每月刷新」的周期包只有 `Cycle*` 才是当月真剩；先读 `Capacity*` 会显示成
   「整包额度、用量 0」，和客户端弹窗对不上。脚本优先 `Cycle*`。
2. **有效批次（`Status=0`）的 `ExpiredTime` 是空的**，真正的到期时刻在
   `CycleEndTime` / `DeductionEndTime`；已过期批次才有 `ExpiredTime`。脚本按顺序回退。
3. **明细与汇总会有几分的漂移**（两次请求差几秒）。脚本按 `PackageCode` 把差额
   校平到该包最早到期那一笔上；补不进去就记进 `warnings`，不硬凑。

此外只看「还有余额且未过期/未用完」的批次；Trae 侧同到期时刻同名的包会合并，
免得 500+2000 那种同批多包刷屏。商品码枚举是接口参数（不是密钥），
官方改版后可改脚本里的 `WB_FREE_CODES` / `WB_PAID_CODES`，或设
`WB_PACKAGE_CODES_JSON` 覆盖。

### 预警等级（阈值都可调）

| 等级 | 判定 | 含义 |
|---|---|---|
| `urgent` | `daysLeft <= --urgent-days`（默认 3） | 紧急，先花这几笔 |
| `warn` | `daysLeft <= --warn-days`（默认 7） | 提醒，进时间线 |
| `normal` | 更晚 | 正常，不打扰 |
| `unknown` | 接口没给到期时间 | **标未知，不假装安全** |
| `expired` | `daysLeft < 0` 却还有余额 | 异常，写进 `warnings` 待查 |

`daysLeft` 用自然日（÷86400）而不是日历日：晚上 23:00 抓到「明早 8:00 到期」算 0.4 天、
立刻进紧急档，而不是含糊的「还有 1 天」。

`--alert-exit-code` 会在出现 `urgent` / `expired` 时返回退出码 **2**（默认仍返回 0），
这样 Windows 计划任务 / `.bat` 可以据此弹通知，而不会把「有积分快过期」误判成「脚本失败」。

### 某家临时取不到数时

`credits_alert.py` **不会让余额卡凭空消失**：登录态文件正被客户端重写、网络抖动、
或脚本跑在受限环境里导致某家失败时，它会读上一次成功的 `credits-data.js`，
把该平台的数据沿用过来，并标上 `stale: true` / `staleFrom` / `staleReason`；
等级按当前时间重新计算（时间过去了，`daysLeft` 会变）。页面会显示「上次数据」徽标
和横幅提示，`summary` 里也有 `fresh` / `stale` 两个计数。
只有连上一次的数据都没有（比如第一次运行）时，才显示 `ok:false` 的错误卡。

### 输出结构

（下面数字都是示例占位，不含任何真实余额。）

```js
window.CREDITS_ALERT = {
  generatedAt: "2026-11-13 11:40:00",
  thresholds: { warnDays: 7, urgentDays: 3 },
  summary: {
    platforms: 2, ok: 1, level: "warn",
    totalRemain: 800.0,        // 只统计 ok 的那家
    expiringSoon: 200.0,       // 7 天窗口内将到期
    urgentRemain: 0.0,         // 其中 3 天内的
    alertCount: 8
  },
  alerts: [ /* 跨平台按到期时间排序的「需要关注」批次 */ ],
  sources: [
    { ok: true, platform: "WorkBuddy", totalRemain: 800.0, totalSize: 1000.0,
      batchCount: 12, level: "warn", soonest: {...},
      items: [ /* 每笔：expireAt / expireMs / daysLeft / level / remain / size / name ... */ ],
      warnings: [] },
    { ok: false, platform: "Trae", error: "...", items: [], warnings: [] }
  ]
};
```

一家挂了不会拖垮另一家：失败的那家是 `ok:false` + `error`，看板据此降级显示即可。

---

## 三份产物的共同结构

前两份（用量）结构一致，看板可以共用一套渲染逻辑：

```js
window.WB_USAGE = {
  generatedAt,                 // "2026-11-13 11:40:00"
  range: { start, end },       // 数据覆盖的日期范围
  records: [ ... ],            // 逐轮明细：日期 / 积分 / 输入输出 / 缓存 / 会话 / 项目 / 模型
  summary: { ... }             // 汇总 KPI：requests / sessions / credit / tokens / cacheHitRate ...
};
```

`window.TRAE_USAGE` 在此基础上多带：

- `source`、`tokenInfo`（凭证签发/过期/来源/是否实测通过/候选列表）
- `paging`（`total` / `fetched` / `pages` / `pageSize` / **`retries` / `partial` / `warning`**）
- `quota`（套餐额度 `consumed` / `total` / `ratio`）

第三份 `window.CREDITS_ALERT` 是独立的余额快照结构，见上一节。
三份都带 `generatedAt`，方便看板判断数据新鲜度。

---

## 常见问题

**Q：`trae_fetch.py` 报「找不到可用凭证」？**

按顺序排查：
1. 跑 `python trae_fetch.py --check`，它会逐个打印候选凭证的实测结果，看是哪一步挂了。
2. 输出里有没有「本机没有 `trae_crypto_table.py`」？有的话**先解决它**——按上文自备常量表，
   这是最常见的原因（E 类主力来源被整条跳过）。
3. 客户端**现在是登录状态**吗？退出登录过就先重新登录一次。
4. 客户端刚升级过？数据目录可能改名了。脚本是自动发现的，但如果新目录名里没有 `trae` 字样就会漏——
   这时手动把目录加进 `EXTRA_CLIENT_DIRS`。

**Q：`workbuddy_fetch.py` 抓出来是空的？**

确认 `~/.workbuddy/projects/` 下有 `.jsonl`、且 `~/.workbuddy/workbuddy.db` 存在。
没有的话说明 WorkBuddy 装在了别的用户目录，或者还没产生过会话。

**Q：积分预警里 WorkBuddy 报「取不到登录态」？**

三选一：设 `WB_CREDITS_TOKEN` / `WB_CREDITS_DOMAIN`；复制 `credits_auth.py.example`
实现 `get_auth()`；或安装本机积分技能。先跑 `python credits_alert.py --check` 看是哪条通道挂了。
注意客户端正在重写登录态文件时可能短暂失败，脚本已自动重试，稍等十几秒再跑一次通常就好。

**Q：积分预警的数字和客户端弹窗对不上？**

先看 `sources[].warnings`：有没有「明细截断」或「明细与汇总差」。
其次确认读的是 `Cycle*` 而不是 `Capacity*`（周期包的坑，见上文）。
另外逐批接口与汇总接口差几秒，余额有几分漂移是正常的，脚本会自动校平。

**Q：可视化页面打开是空白，或者数字跟想的不一样？**

- 先看右上角「数据 …」三份采集时间：缺哪份就补跑对应脚本，超过 24 小时会标黄。
- 用本地服务时直接点「↻ 刷新数据」；`file://` 打开则手动跑完脚本按 F5。
- 「已经花了多少」和「还剩多少」是两本账，别互相加减；窗口切换（7/30 天）也会改变 KPI。
- 样式看起来不对劲（属性被改、主题不跟随）时，先开**无痕窗口**对比一次——浏览器扩展
  会往页面里注入 inline `<style>`，这是最常见的假故障。
- 端口起不来就 `python viewer/server.py --port 18791`；服务只绑本机，不对外网开放。

**Q：点「↻ 刷新数据」时 WorkBuddy 报 `unable to open database file`，但直接跑脚本是好的？**

这说明**服务进程**读不到 `~/.workbuddy/workbuddy.db`（文件能看到、却打不开），
多半是启动服务的终端被沙箱 / 安全软件限制了读取用户目录，`sqlite3.connect` 才会只抛这一句。
用普通命令行双击 `start-viewer.bat` 启动即可；`/api/status` 会回 `home` 与 `dbExists`
两个字段帮你确认路径。另外两家不受影响，仍会正常刷新，页面也不会因为一家失败而崩。

**Q：能定时跑吗？**

可以。Windows 上建个计划任务，或者写个 `.bat` 调这三个脚本。注意用**绝对路径**指向 Python
解释器（计划任务的环境里经常没有 `PATH`），bat 用 GBK + 无 BOM + CRLF。
想在到期前主动提醒，给 `credits_alert.py` 加 `--alert-exit-code`，用退出码 2 触发通知。

**Q：双击 exe 后页面是空的 / 数据不更新？**

1. 首次启动会先采集一次，控制台里能看到进度；如果它一闪就过去了，手动点页面右上角「↻ 刷新数据」。
2. exe 所在目录**必须可写**（`*-data.js` 就写在它旁边）。放在 `C:\Program Files\` 这类目录会写不出数据，
   换个目录或加 `--data-dir D:\data`。
3. 想看它到底认哪个目录：浏览器开 `http://127.0.0.1:18790/api/status`，`root` 就是数据目录，
   `frozen: true` 表示跑的是 exe。

**Q：exe 被杀软拦了 / SmartScreen 提示未知发布者？**

未签名单文件容易误报，本质是 PyInstaller 的自解压行为。加信任即可；
想避免就自己用 `onedir` 模式打包（改成目录版，误报少很多），或直接跑源码版。

---

## 本次更新（2026-11-13）

- **新增** `credits_alert.py` + `credits_auth.py.example`：WorkBuddy / Trae 两家积分余额、
  逐批到期、跨平台预警时间线与可调阈值。
- **同步** `trae_fetch.py` 的稳定性加固：`api_post` 对 400/429/5xx 指数退避重试、
  凭证实测共用重试函数、单页失败不再整源报废、`paging` 增加 `retries/partial/warning`、
  翻页间隔；错误日志改为 `TRAE_FETCH_ERROR_LOG=1` 才落盘。
- **修正** 过时描述：凭证主力来源已从 AI agent stdout 日志换成加密 UserStorage；
  接口全部是 POST（原文档写 GET）；输出结构示例改成脚本真实产出的字段；
  `workbuddy_fetch.py` 产物注释里的旧文件名。
- **补充** 2026-11-13 的本机核对结论：`usage` / `rawUsage` 同源镜像、
  `session_usage.used/size` 不是计费 token、`rawUsage.credit` 作为更全兜底源的实测数据
  （**默认口径未改**，按和田要求保持「账本优先 + 运行日志兜底」）。
- **新增** `viewer/` 可视化软件：消耗看板 + 到期预警双标签、手绘 SVG 图表、
  深浅主题、筛选搜索；`viewer/server.py` 本地服务提供一键刷新；`start-viewer.bat` 双击启动。
- **新增** `viewer/report.js` + 两个按钮：「⬇ 导出 PNG」生成固定浅色的 SVG 报表图并光栅化下载，
  「🖨 打印 / PDF」用 `@media print` 把两个标签排成 A4 两页（浏览器另存为 PDF）。
- **新增** 单文件 exe 打包：`viewer/launcher.py`（入口）+ `credits_viewer.spec` +
  `version_info.txt` + `build-exe.bat`，产物 `dist\积分台.exe`（约 9.5 MB，自带运行时）。
  三个脚本的产物目录改为可用 `WB_DATA_DIR` 覆盖，打包后数据仍写在 exe 旁边。
- **修复** 头部按钮在临界视口宽度 / 滚动条出现时整块换行：时间戳移出按钮组独立一行、
  标题块可收缩而按钮组不换行、加 `scrollbar-gutter: stable` 预留滚动条槽位。
- **加固** `credits_alert.py`：某家临时取不到数时沿用上一次成功的数据并标 `stale`，
  页面显示「上次数据」徽标与横幅提示，不再让余额卡凭空消失。

---

## 隐私说明

- `workbuddy_fetch.py` **完全离线**；`trae_fetch.py` 和两个平台的积分预警只调官方接口读你自己的用量/余额。
- 产出的 `*-data.js` 含逐日消耗、会话标题、本地项目路径、积分余额与到期时间，
  **属于个人信息，别提交到仓库**（`.gitignore` 已排除）。
- 脚本里不含任何 token 明文——凭证都是运行时从本机读出或由你自备的模块/环境变量提供。
- `credits-data.js` / `usage-data.js` / `trae-data.js` 里都不写凭证字段。
- `viewer/` 页面不联网、不含任何外部资源；`viewer/server.py` 只监听 `127.0.0.1`，
  `/api/refresh` 只运行仓库里固定的三个脚本，不接收任意命令。

## 免责声明

这是**个人自用的逆向工程实践**：两个工具都没有公开的用量查询 API，脚本依赖的是客户端本地数据格式
和未公开的内部接口，这些东西**随时可能因为官方改版而失效**（Trae 就变过好几次）。

- 本项目与 WorkBuddy / Trae 官方**无任何关联**，也不是官方认可的工具。
- 仓库**不含**任何 Trae / WorkBuddy 的源码或二进制片段。解密所需的客户端常量由使用者
  **自行从本机客户端提取**（见上文），本项目只提供模板、**不分发**这些常量；
  WorkBuddy 登录态同理，只留接口、不落凭证。商品码列表是接口参数，不是密钥。
- 请勿用于绕过付费、篡改计费或任何违反上述产品服务条款的用途；仅用于查看你自己的用量与余额。
- 接口失效属正常现象，欢迎提 issue / PR 一起维护，但别指望它永远可用。

## License

MIT — 见 [LICENSE](LICENSE)。
