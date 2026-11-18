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
这部分由 [`docs/answer-pipeline-prompt.md`](docs/answer-pipeline-prompt.md) 定义：把那份提示词整段复制给任意 AI（本机或远程），它就能接管「读图 → 写稿 → `render --check` → `publish` → `done`」全流程。

## 目录

| 路径 | 内容 |
| --- | --- |
| `web-toolbox/` | 网页工具箱：`index.html`、`index.js` 与 6 个扩展模块、pdf.js 与模板、`fabric.min.js`（内置，不再依赖 CDN）、`example/`、`web/` |
| `web-toolbox/serve.js` | 本地静态服务 + 同源转发：`/proxy/`（图片）、`/linspirer/`（领创 JSON-RPC 与 APK 下载，支持断点续传） |
| `启动云笔记工具.bat` | Windows 一键启动：8088 服务 + 9222 调试 Chrome + 云笔记 watch；`/dry` 只打印动作 |
| `start.sh` | Linux / macOS 一键启动（同上，容器里自动带 `--no-sandbox` 等）；`--watch` / `--status` / `--stop` |
| `package.json` | 只声明 `engines: node >= 21` 与几个 `npm run` 快捷方式，无第三方依赖 |
| `pipeline/html2note.js` | HTML → PDF → 云笔记，支持 `--watch` 常驻与 `--check-cloud` |
| `pipeline/scripts/pipeline.py` | 另一条流水线的调度脚本（扫描 / 队列 / 校验 / 发布） |
| `pipeline/tools/answer-pipeline/` | 「读题作答 → HTML」Python 包（含 KaTeX 资源与单元测试） |
| `docs/answer-pipeline-prompt.md` | **给上层 AI 的交接提示词**：整段复制给任意 AI，它就能接管读题→写稿→渲染→发布全流程 |
| `docs/` | 模块说明、PDF 上传链路的 8 处修复、流水线用法、脱敏说明 |
| `sample/` | 一份脱敏后的输入（笔记页图）与输出（答案 HTML）示例 |

## 快速开始

先确认 **Node >= 21**（`pipeline/html2note.js` 用了全局 `WebSocket`，Node 20 及以下会报 `WebSocket is not defined`）。之后三步：

```bash
git clone https://github.com/1XIROM/ZhongYuToolbox_Extension.git
cd ZhongYuToolbox_Extension

# Windows：双击 启动云笔记工具.bat
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

Windows 下也可以双击**仓库根目录**的 `启动云笔记工具.bat`：依次拉起 8088 服务、9222 调试 Chrome 和云笔记 watch，已在运行的会自动跳过；`启动云笔记工具.bat /dry` 只打印动作不启动。脚本路径全部从自身所在目录（`%~dp0`）推导，所以**必须放在仓库根目录**（放桌面会找不到 `web-toolbox\serve.js` 和 `pipeline\html2note.js`，脚本会明确提示而不是报「系统找不到指定的路径」）。换机器只需改顶部的 `CHROME` / `PROFILE` 等变量。

脚本内容刻意保持**纯 ASCII**：cmd 按系统代码页解析批处理，UTF-8 中文注释会被读成乱码并当成命令执行，`rem` 注释里的尖括号还会被当成输入/输出重定向。

打开页面、用你自己的账号登录后，各模块按页签使用。`pipeline/html2note.js` 也是连这个 9222 + 8088 页面来复用登录态。

### 2. 流水线

```bash
# 只检查登录态（会尝试用 refreshToken 续期，不转换、不上传）
node pipeline/html2note.js --check-auth

# 处理一次 output/ 里的未上传 HTML
node pipeline/html2note.js

# 常驻：监听 output/，并每 30 秒巡检云端根目录里标题含「ds」的笔记并下载
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
