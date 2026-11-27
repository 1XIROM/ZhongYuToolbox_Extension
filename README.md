# 云笔记工具箱（网页端扩展 + 自动化流水线）

> 个人学习与自动化用途的逆向研究项目：给某校「云笔记 / 云笔记工具箱」网页版补齐它没有的能力，并把「题目 HTML → PDF → 云笔记」做成可常驻的一条流水线。
>
> **本仓库不脱敏、克隆即可跑**：`web-toolbox/` 里写的是真实接口地址（学校 API 主机、OSS 桶、中转主机等），中育 `codeUtils` 的 AES 种子也已内置，**不需要替换任何占位符**。唯一需要自备的是**领创（Linspirer）的 KEY / IV**，写到 `web-toolbox/config.local.js` 即可（见下方「关于 AES 密钥」）。账号、令牌与真实笔记内容不入库。

## 与上游的关系

本仓库基于 [BennyLoshop/ZhongYuToolbox](https://github.com/BennyLoshop/ZhongYuToolbox)。上游是「中育云笔记」网页端的逆向工具箱（`index.html` + `index.js` + pdf 相关脚本）；本仓库保留它的页面骨架与接口封装，在其之上做了下面这些改动。

### 相对上游改了什么

| 类别 | 内容 |
| --- | --- |
| 新增模块 | `batch_pick.js`（批量下载）、`share_read.js`（老师分享）、`column_read.js` / `column_batch.js`（专栏）、`pdf-upload.js`（PDF/图片转笔记）、`chem.js` + `chem.html`（化学卷）、`linspirer.js`（领创模块）、`cloudnote-window.html` |
| PDF 上传链路 | 8 处修复，每处都有复现与验证数据 → [docs/pdf-upload-fixes.md](docs/pdf-upload-fixes.md) |
| 新流水线 | `pipeline/html2note.js`：HTML → PDF → 云笔记，含 `--watch` 常驻、`--check-cloud` 巡检、自动续期登录 |
| 新流水线 | `pipeline/scripts/pipeline.py` + `pipeline/tools/answer-pipeline/`：读题作答 → 渲染 → 校验 → 发布 |
| 安全 | **领创 KEY/IV 不出现在仓库里**（中育种子与上游一致，已内置），账号凭据不入库 |
| 文档 | `docs/`、`sample/` 与这份 README 均为新增 |

逐条变更见 [CHANGELOG.md](CHANGELOG.md)。

### 关于 AES 密钥

上游把密钥直接写在脚本里。本仓库分两类处理：

- **中育 codeUtils 种子**（`index.js` / `chem.js` / `pdf-upload.js` 加密请求体用的那把）：与上游公开仓库完全一致，已经**内置**在 `web-toolbox/key-store.js`，克隆下来即可登录，不需要额外配置。
- **领创（Linspirer）KEY / IV**：仓库**不提供**，需要你自己从所连接的领创环境中取得，写在 `web-toolbox/config.local.js`（**已在 .gitignore 中**）。没配置时领创模块会抛出明确提示，其它功能不受影响。

```bash
cp web-toolbox/config.local.example.js web-toolbox/config.local.js
# 只填 LINSPIRER_KEY / LINSPIRER_IV 即可
```

细节见 [docs/keys.md](docs/keys.md)。

### 关于域名与学校信息

本仓库**不脱敏**：`index.js` 等文件里写的是真实接口地址（学校 API 主机、OSS 桶、中转服务器等），目的是「克隆下来就能跑」。

这意味着：

- 里面出现的学校代码、接口域名属于学校自有信息，请在**自用或小范围分享**的前提下使用；
- 如果你打算公开分发，建议自己再脱敏一遍（发布版曾提供 `build-repo.js` 的替换规则，见 [docs/sanitization.md](docs/sanitization.md)）；
- 领创的 KEY/IV 与任何账号凭据都**不在**仓库里，这一点没有妥协。

## 两条链

```
┌──────────────────────────── web-toolbox/ ────────────────────────────┐
│  index.html + 6 个扩展模块（批量下载 / 老师分享 / 专栏 / PDF 上传 …）  │
│  运行方式：静态服务托管 + Chrome 开调试端口，页面里直接操作            │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ 复用页面内的 uploadFile / saveNote /
                                │ aesEncrypt / localStorage.token
┌───────────────────────────────┴──────────────────────────────────────┐
│  pipeline/html2note.js                                                │
│  output/*.html → 无头 Chrome printToPDF → 塞进页面 → 上传成云笔记     │
│  --watch 常驻 + fs.watch + 30s rescan + 按天日志 + 自动续期登录        │
│  --check-cloud 顺带巡检云端文件夹，把新笔记下载到 input/               │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ input/ 里出现新笔记目录
┌───────────────────────────────┴──────────────────────────────────────┐
│  pipeline/tools/answer-pipeline（另一条 AI 链路的产物，Python 包）     │
│  读题 → 作答 → 渲染 HTML（KaTeX + 打印样式）→ 11 项校验 → 发布到       │
│  output/，随后被上面的 html2note.js 接走                              │
└──────────────────────────────────────────────────────────────────────┘
```

最后那环里「**谁来读题作答**」不在代码里——`tools/answer-pipeline/` 只做作答之后的渲染、校验与发布。
这部分由两份提示词之一来定义，按「答题者自己能不能跑命令」二选一：

| 答题者 | 用哪份 | 它负责到哪一步 |
| --- | --- | --- |
| 本机 Agent（有文件系统与命令权限） | [`docs/answer-pipeline-prompt.md`](docs/answer-pipeline-prompt.md) | 读图 → 写稿 → `render --check` → `publish` → `done` 全流程 |
| 通过 API 密钥调用的模型（单次请求、无文件系统） | [`docs/answer-pipeline-prompt-api.md`](docs/answer-pipeline-prompt-api.md) | 只读图 → 产 Markdown 草稿；渲染与发布由调用方程序做 |

API 版不需要哨兵、不需要定时任务——请求是调用方主动发的，没有"等人来叫"这一环；
篇幅也改由字数预算自估（模型看不到渲染结果），超页时由调用方把 `render` 的
「需压掉 N 行」回传，做第二次纯文本压缩。

## 目录

| 路径 | 内容 |
| --- | --- |
| `web-toolbox/` | 网页工具箱：`index.html`、`index.js` 与 6 个扩展模块、pdf.js 与模板、`fabric.min.js`（内置，不再依赖 CDN）、`example/`、`web/` |
| `web-toolbox/serve.js` | 本地静态服务 + 同源转发：`/proxy/`（图片）、`/linspirer/`（领创 JSON-RPC 与 APK 下载，支持断点续传） |
| `打开Toolbox.bat` | Windows 只开网页：确保 8088 服务在跑，然后用浏览器打开 `http://127.0.0.1:8088/`；**不启动**调试 Chrome 与云笔记 watch；`/dry` 只打印动作 |
| `启动云笔记工具.bat` | Windows 启动上传下载链路：8088 服务 + 9222 调试 Chrome + 云笔记 watch；`/dry` 只打印动作 |
| `启动云笔记工具-自动答题.bat` | 上面那条 + 自动答题：调 `launch-auto.ps1`，再拉起 `codex-wake.ps1`（队列里有新任务就叫 Codex 写稿；失败时按 `templates/failure-template.html` 出一页说明）；`/dry` 只打印动作 |
| `启动云笔记工具-图片唤醒.bat` | 上面那条 + 图片唤醒：跑 `wake_on_image.py`，按图片 md5 精确匹配 `.workbuddy/wake_refs.json`，命中后由 `wake_workbuddy.py` 唤醒本机助手；`/dry`、`/probe`（只做输入框定位自检） |
| `启动云笔记工具-笔记唤醒.bat` | 上面那条 + 笔记唤醒：跑 `wake_on_note.py`，**每条新笔记都唤醒**；执行器同上；`/dry`、`/probe` |
| `启动云笔记工具-笔记答题.bat` | 不依赖唤醒：命令行把「扫云笔记 → 读图 → 调 DeepSeek → 写 `output/*.md`」一次做完；选项见下 |
| `pipeline/scripts/note_answer.py` | 上一条的编排器：拉服务、扫云笔记、拼提示词、调 harness 或 HTTP 接口、逐条落 Markdown |
| `launch-auto.ps1` / `codex-wake.ps1` / `templates/failure-template.html` | 自动答题的 PowerShell 实现：前者补充启动、后者是队列驱动的答题器（锁、退避、并行、超时都在这），模板是失败时的占位成品 |
| `pipeline/scripts/wake_on_image.py` / `wake_on_note.py` / `wake_workbuddy.py` | 唤醒链路三件套：两个检测器 + 一个执行器（定位目标客户端输入框并投递） |
| `start.sh` | Linux / macOS 一键启动（同上，容器里自动带 `--no-sandbox` 等）；`--watch` / `--status` / `--stop` |
| `package.json` | 只声明 `engines: node >= 21` 与几个 `npm run` 快捷方式，无第三方依赖 |
| `pipeline/AGENTS.md` | 工作区约定：`codex-wake.ps1` 投递的提示词会引用它，Codex 每次会话也会自动加载 |
| `docs/answer-pipeline-prompt-codex.md` | Codex 版交接提示词（含搜索规则）；另有 `docs/answer-pipeline-prompt.md`（常驻版）与 `-api.md`（纯 API 版） |
| `pipeline/html2note.js` | HTML → PDF → 云笔记，支持 `--watch` 常驻与 `--check-cloud` |
| `pipeline/scripts/pipeline.py` | 另一条流水线的调度脚本（扫描 / 队列 / 校验 / 发布） |
| `pipeline/tools/answer-pipeline/` | 「读题作答 → HTML」Python 包（含 KaTeX 资源与单元测试） |
| `docs/answer-pipeline-prompt.md` | **给上层 AI 的交接提示词（Agent 版）**：答题者本机能跑命令，整段复制给它即可接管读题→写稿→渲染→发布全流程 |
| `docs/answer-pipeline-prompt-api.md` | **同一链路的 API 版提示词**：答题者是通过密钥调用的模型，只产草稿；不需要哨兵与定时任务 |
| `docs/` | 模块说明、PDF 上传链路的 8 处修复、流水线用法、脱敏说明 |
| `sample/` | 一份脱敏后的输入（笔记页图）与输出（答案 HTML）示例 |

## 快速开始

先确认 **Node >= 21**（`pipeline/html2note.js` 用了全局 `WebSocket`，Node 20 及以下会报 `WebSocket is not defined`）。之后三步：

```bash
git clone https://github.com/1XIROM/ZhongYuToolbox_Extension.git
cd ZhongYuToolbox_Extension

# Windows：双击 打开Toolbox.bat（只看网页）
#          双击 启动云笔记工具.bat（另加上传下载链路）
# Linux / macOS：
chmod +x start.sh && ./start.sh          # 加 --watch 则连常驻流水线一起起

# 浏览器会打开 http://127.0.0.1:8088/ —— 填学校代码 + 账号密码登录
```

`config.local.js` **不是必需的**：中育链路用的种子已内置在 `web-toolbox/key-store.js`，缺这个文件只会在控制台留一条 404，并且「领创」模块会提示缺少 KEY/IV。要跑领创再按 [docs/keys.md](docs/keys.md) 建一份。

### 1. 网页工具箱

```bash
# 起本地服务：静态文件 + /proxy/ 图片转发 + /linspirer/ 领创转发
# 领创模块必须用它，普通静态服务器没有 POST 转发，会直接 404/405
node web-toolbox/serve.js 8088
# 另开一个带调试端口的 Chrome
# --no-proxy-server 必须加：系统里若有残留代理（如 Clash 的 127.0.0.1:7890），
# 代理一旦不在运行，页面内所有外网请求都会 Failed to fetch；而 127.* 在代理
# 绕过名单里，本地页面看起来完全正常，很容易误判成"只是浏览器坏了"。
chrome --remote-debugging-port=9222 \
       --no-proxy-server \
       --user-data-dir="<你的临时 profile>" \
       http://127.0.0.1:8088/
```

Windows 下共五类启动方式，按需要选一个（都在**仓库根目录**）：

| 想做的事 | 双击 | 实际会启动 |
| --- | --- | --- |
| 只用网页（看题、下载、上传手选 PDF） | `打开Toolbox.bat` | 8088 服务（已在跑就复用）+ 一个普通浏览器窗口 |
| 让 `output/` 自动转 PDF 并上传、定时同步云笔记 | `启动云笔记工具.bat` | 8088 服务 + 9222 调试 Chrome + `html2note.js --watch --check-cloud` |
| 在上面基础上让 **Codex** 自动答题 | `启动云笔记工具-自动答题.bat` | 上面三件 + `codex-wake.ps1`（队列驱动：有新任务才叫 Codex，失败按模板出一页说明） |
| 在上面基础上**唤醒本机助手**来答题 | `启动云笔记工具-图片唤醒.bat`<br>`启动云笔记工具-笔记唤醒.bat` | 8088 + 9222 + watch + 检测器（图片 md5 匹配 / 每条新笔记）+ `wake_workbuddy.py` 执行唤醒 |
| 用 **DeepSeek harness** 一次性答题（不常驻） | `启动云笔记工具-笔记答题.bat` | 需要时拉 8088 + 9222，扫一轮云笔记，逐条读图作答，写 `output/<label>_回答.md` 后退出 |

> **这两个自动答题脚本都还是初版，可行性未验证**
>
> 指的是 `启动云笔记工具-自动答题.bat`（Codex 作答）与 `启动云笔记工具-笔记答题.bat`（DeepSeek harness 作答）。
> 两者都还没在完整链路上做过验收：多图题、超页压缩、失败重试、断点续答这些边界，目前只有零散验证，没有回归测试。
> 建议先用 `/dry` 看计划、再用 `/limit 1` 跑一条，确认真读到了题、答案落到了预期路径，再放大批量。

**图片唤醒与笔记唤醒的区别**

- **图片唤醒**：只有收到的图与 `.workbuddy/wake_refs.json` 里登记的 md5 **完全一致**时才触发。适合题型固定、内容重复的场景，误触发最少，但新题型要先登记参考图。
- **笔记唤醒**：每来一条新笔记就唤醒。适合内容每次都不同的场景，代价是唤醒频率高，一旦助手没及时跟上就会积压。
- 两者都会先生成/复用草稿，再由被唤醒的助手把答案写进 `output/`；`/probe` 参数只做一次输入框定位自检，用于排查"唤醒了但没投递进去"。

> **只在 WorkBuddy 桌面版 + Windows + 深色主题下可用**
>
> 两个「唤醒」脚本靠 `wake_workbuddy.py` 做界面自动化：它按亮度 24～52 的「面板色」去认输入框——深色主题下输入框与侧边栏同色、比纯黑页面背景亮，这个亮度区间就是识别依据。一旦换成浅色主题、换其他客户端（Codex CLI 等），或换到别的系统，前提就不成立，结果是定位失败、或把内容投到错的位置。
>
> 这条限制不牵连 `启动云笔记工具-自动答题.bat`：它走 `codex-wake.ps1` 以子进程方式调用 Codex，全程不碰界面。

### 2. 流水线

```bash
# 只检查登录态（会尝试用 refreshToken 续期，不转换、不上传）
node pipeline/html2note.js --check-auth

# 处理一次 output/ 里的未上传 HTML
node pipeline/html2note.js

# 常驻：监听 output/，并每 10 秒巡检云端根目录里标题含「ds」的笔记并下载
node pipeline/html2note.js --watch --check-cloud
```

状态与产物默认落在运行目录下（见 [docs/pipeline.md](docs/pipeline.md)）：

```
output/     题目 HTML（监听目录）
pdf/        生成的 PDF
logs/       watch-YYYY-MM-DD.log（按天）
.workbuddy/ uploaded.json（已上传记录）、cloud-seen.json（已下载记录）
input/      从云端下载回来的笔记目录
```

### 用 DeepSeek harness 一次性答题

`启动云笔记工具-笔记答题.bat` 是唯一完全不依赖界面自动化的答题入口：跑完一轮就退出，适合定时触发或在改完题后手动补一批。

```bat
启动云笔记工具-笔记答题.bat                :: 默认：最多答 3 条还没有答案的笔记
启动云笔记工具-笔记答题.bat /all           :: 还没答案的全部答完
启动云笔记工具-笔记答题.bat /limit 5       :: 本次最多 5 条
启动云笔记工具-笔记答题.bat /engine api    :: 不跑本地 harness，改调 OpenAI 兼容 HTTP 接口
启动云笔记工具-笔记答题.bat /no-cloud      :: 不扫云笔记，只处理 input\ 里已有的
启动云笔记工具-笔记答题.bat /cloud-all     :: 首次全量回填（把历史笔记都拉下来）
启动云笔记工具-笔记答题.bat /force         :: 已有答案的也重答
启动云笔记工具-笔记答题.bat /no-start      :: 不自动拉起 8088 / 9222
启动云笔记工具-笔记答题.bat /dry           :: 只打印计划：不调模型、不写 output（但仍会扫一轮云笔记）
启动云笔记工具-笔记答题.bat /help
```

Key 的读取顺序：`DEEPSEEK_API_KEY` → `<root>\.workbuddy\deepseek.key` → `<root>\config.api.key`。都没有时会明确提示；默认的 harness 引擎仍会尝试用 DSH 已保存的凭据。

**它只写 Markdown，不写 HTML。** 因为 `output/` 里的 HTML 会被 html2note 立刻转 PDF 传上云笔记，没人工复核的内容不该走那条链路。复核之后再 `render --check` → `publish`。

它和上面两个「唤醒」脚本是两套东西：这条链路全程命令行 + API，不碰界面，因此**不受深色主题和客户端限制**。

### 这些脚本会匹配哪些笔记、怎么自动作答

四条自动化链路共用同一套「从云笔记往下捞」的规则，区别只在**怎么触发**和**谁来答**。

**① 云笔记侧：哪些笔记会被下载**

由 `html2note.js --cloud` 决定，判据在 `pipeline/config.json` 的 `cloud` 段：

| 条件 | 默认 | 改法 |
| --- | --- | --- |
| 扫描范围 | 根目录 | `cloud.folder`，或命令行 `--cloud-folder <文件夹名或 parentId>` |
| 标题关键词 | `ds` | `cloud.title`；传 `--cloud-title ""` 关掉这层过滤 |
| 落地目录 | `input/` | `cloud.dir` |
| 已见记录 | `.workbuddy/cloud-seen.json` | `cloud.stateFile` |

- **只下载标题含 `ds` 的笔记**（这是本机的命名约定：题目类笔记都叫 `ds N`）。其余笔记只被"看见"，不落地。
- 首次运行只建基线、不回溯；要补历史用 `--cloud-all`。
- 每个笔记落成一个目录：`input/<创建时间>_<标题>_<fileId前8>/`。里面是 `resources.json` + 原始图片（`resources.json` 里 `type=0` 那张，字大清楚）+ `00_screenshot.png` 整页预览。
- `00_data.mdb` / `00_lock.mdb` / `00_snapshot.bin` 是云笔记的私有容器，**一律忽略**：不解析、不参与提示词、不联网下载。
- 全程只发 GET，不写云端。

**② 本地：哪些笔记会被作答**

| 链路 | 判据 | 谁答 |
| --- | --- | --- |
| 图片唤醒 | 收到的图与 `.workbuddy/wake_refs.json` 登记的 md5 **完全一致** | 被唤醒的本机助手 |
| 笔记唤醒 | `input/` 下出现新目录（对比 `wake_note_seen.json`），每条都触发 | 同上 |
| Codex 自动答题 | `answer-pipeline` 扫到 `input/` 新文件夹并入队（`status == pending_answer`） | `codex-wake.ps1` 调 Codex |
| 笔记答题（DeepSeek） | `input/` 里没有对应 `output/<label>_回答.md` 的笔记，默认一次最多 3 条 | `note_answer.py` 调 DeepSeek |

**③ 答案落到哪、谁来上传**

| 链路 | 产物 | 会不会自动上云 |
| --- | --- | --- |
| Codex 自动答题 | `output/<label>_答案横版.html`（已过 `render --check`） | 会：html2note 立刻转 PDF 上传 |
| 两个唤醒脚本 | 同上，由被唤醒的助手按 `AGENTS.md` 走完 publish | 会 |
| 笔记答题（DeepSeek） | `output/<label>_回答.md` | **不会**：只落 Markdown，复核后再 `render --check` → `publish` |

最后这条是刻意的：`output/` 里的 HTML 会被自动转 PDF 传上云笔记，没人工复核的答案不该直接进那条链路。所以笔记答题只写 `.md`，把"发布"这一步留给人。

自动答题还需要工作区里有一份 `AGENTS.md`（`codex-wake.ps1` 的提示词要求 Codex「按工作区 AGENTS.md 的流程」执行，Codex 每次会话也会自动加载它）。发布版自带的是 `pipeline/AGENTS.md`；如果你的流水线目录在别处，把它复制过去并改掉里面的命令前缀。

四类的 8088 服务是同一个：先开网页再启动链路（或反过来）都不会重复起进程，脚本会打印 `[skip ] 8088 already listening`。只想看网页却开了链路，会多留一个 watch 进程在后台轮询；反之只看网页不开链路，自动上传和云笔记同步就不会发生。各脚本都支持 `/dry`（例如 `打开Toolbox.bat /dry`），只打印动作不启动。

换机器时除了顶部 `CHROME` / `PROFILE`，自动答题还要看两个 PowerShell 脚本里的默认值：`launch-auto.ps1` 的 `$PipelineRoot`（默认 `E:\WorkBuddy`，脚本靠它找含 `html2note.js` 的目录）、`codex-wake.ps1` 的 `-Root` / `-CodexCmd` / `-Profile`（默认 `E:\WorkBuddy\题目`、`E:\Program Files\Codex\codex.cmd`、`wb`）。

脚本路径全部从自身所在目录（`%~dp0`）推导，所以**必须放在仓库根目录**（放桌面会找不到 `web-toolbox\serve.js` 和 `pipeline\html2note.js`，脚本会明确提示而不是报「系统找不到指定的路径」）。

脚本内容刻意保持**纯 ASCII**：cmd 按系统代码页解析批处理，UTF-8 中文注释会被读成乱码并当成命令执行，`rem` 注释里的尖括号还会被当成输入/输出重定向。

打开页面、用你自己的账号登录后，各模块按页签使用。`pipeline/html2note.js` 也是连这个 9222 + 8088 页面来复用登录态。

## 这套东西解决过什么

`docs/pdf-upload-fixes.md` 记录了 PDF 上传链路上 8 个真实故障，每个都有复现与验证数据，例如：

- 卡在 15%：页面在后台被 Chrome 降频，`page.render()` 永不返回 → 60 秒超时 + 明确提示；
- 上传 403 `Access denied by authorizer's policy.`：客户端用 **UTC 日期**拼 OSS key，服务端策略按**北京时间**，每天 00:00–08:00 差一天；
- 手机打开提示「获取文件位置失败」：`fileUrl` 主机写死、模板文件没放进页目录、缓存对象被 `delete` 导致第二次上传缺 `page_router.bin`；
- 下载回来的 PDF 18 页全是第 1 页：导出取了 `resourceType == 2` 的整篇截图当每页图。

## 已知限制

- 网页工具箱依赖校内平台的接口与网页结构，平台改版即可能失效；
- PDF 上传**默认本地渲染**（pdf.js 2880×1800），服务端 `<pdf2img-host>` 仅作兜底；
- 自动朝向判定只覆盖有文字层的页面，纯图片页需要人工逐页旋转；
- 调试 Chrome **必须带 `--no-proxy-server` 启动**：系统里残留的代理配置（如 Clash 的 `127.0.0.1:7890`）一旦不在运行，页面内所有外网请求都会 `Failed to fetch`，而 `127.*` 在代理绕过名单里，本地页面看起来完全正常，极易误判成"只有浏览器坏了"。`启动云笔记工具.bat` 已内置该参数，手动启动请照抄上面的命令；
- 没有 UI 自动化回归测试，验证靠脚本探测（见 `docs/pdf-upload-fixes.md` 的验证方法）。

## 免责声明

本项目仅用于个人学习、自动化与接口兼容性研究，与任何学校/公司无关。请只在你有权访问的账号与数据上使用；不要用它批量抓取或传播他人内容。

## 其他

本项目在DeepSeek的辅助下制作
