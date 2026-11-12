# 变更记录

按日期倒序。每个条目都能在 `docs/` 里找到对应的复现步骤或验证数据。

## 2026-11-12 — 领创模块 405：中转域名停用，改走本地同源转发

**修复**：网页版点「登录并获取应用」报 `操作失败: HTTP 405:`。原公网中转域名已复用为「GForm 授权管理」站，nginx 只允许 `OPTIONS, GET`，POST 返回 405 `{"message":"Method Not Allowed"}`；真实上游 `https://cloud.linspirer.com:883/public-interface.php` 仍接受 POST（返回业务 JSON），但不返回 CORS 头，浏览器无法直连。

- 本地静态服务 `.work/serve.js` 新增 `/linspirer/<path>` 同源转发：保留 POST body 与 `Content-Type` 转发到上游，带 CORS、超时、16 MB 上限、最多 3 跳重定向跟随，目标 host 固定以避免变成开放代理；`/linspirer/ping` 用于探活。
- `web-toolbox/linspirer.js` 与本地版 `ZhongYuToolbox-main/linspirer.js`：默认端点改为 `/linspirer/public-interface.php`，保留直连上游作备用；链路级失败自动换端点，业务错误（`code != 0`）不重试；错误信息带上排查指引。`proxyUrl()` 的图标/下载链接随之映射到同源路径。
- 两份 `index.html` 的 `linspirer.js` 加 `?v=2` 破缓存。
- 验证：三份 JS `node --check` 通过；`OPTIONS` 预检 204、`/linspirer/ping` 返回 pong、POST 探针拿到上游业务 JSON；在 vm 里加载真实 `linspirer.js` 构造 AES envelope 走完整链路，返回的是上游业务错误而非「链路不可用」。**未代跑真实设备绑定**（`setdevice` 有服务端副作用）。

**追加修复（同日）— APK 下载到一半自动停止**：上一版转发把 API 响应的 `16 MB` 上限和 `20 s` 超时也套在了 GET 文件下载上，`中育桌面.apk`（32,694,904 B）与 `中育云笔记.apk`（76,095,224 B）传到一半被 `res.destroy()` 掐断，Chrome 报「请检查互联网连接状况」；只有 `文言文150背记.apk`（86,888 B）能下完。

- `/linspirer/` 的 GET/HEAD 改为纯流式 `pipe`：不计数、不主动超时，只有客户端断开才关闭上游；POST JSON-RPC 仍保留限流保护。
- 透传 `Content-Length` / `Content-Range` / `Accept-Ranges` / `Content-Disposition` / `ETag` / `Last-Modified` 等响应头，并把请求的 `Range` / `If-Range` / `If-Modified-Since` / `If-None-Match` 透传给上游，Chrome 的「继续」可真正断点续传。
- 验证：HEAD 返回 `content-length: 32694904` 与 `accept-ranges: bytes`；`Range: bytes=0-1023` 返回 `206` + `content-range: bytes 0-1023/32694904`；完整下载 31 MB APK 得到 32,694,904 字节（3.4 s，不再中断）；72 MB 文件尾部 Range 也返回 `206`。

**同日补充 — 让新 Toolbox 能独立跑起来**

- 新增 `web-toolbox/serve.js`：本地静态服务 + `/proxy/` 图片转发 + `/linspirer/` 领创转发（含 Range 断点续传与流式下载）。此前仓库里只有页面脚本，领创模块依赖的转发服务并不在仓库中，clone 后按旧说明用 `npx serve` 会 404。
- 新增根目录 `启动云笔记工具.bat`：Windows 一键启动 8088 服务、9222 调试 Chrome 与云笔记 watch，`/dry` 只打印动作；路径按脚本所在目录推导，不含本机绝对路径与校内 OSS 主机。
- README 的快速开始改用 `node web-toolbox/serve.js 8088`，目录表补上两个新文件。
- 清理 `index.html` / `pdf-upload.js` 的工作区行尾（仓库 `.gitattributes` 为 `* -text`，不自动转换），避免把数千行纯 CRLF/LF 差异混进提交。

## 2026-11-11 — 上传笔记对齐应用版资源结构

**修复**：PDF 自动上传后的笔记在云笔记 App（1.13.4）里能显示，却无法导出或被别的应用打开。根因是出厂模板 bin 的 SHA-256 摘要链、模板目录层级、资源行 `md5` / `resourceType` 都与「中育Toolbox 应用版」不一致，详见 [docs/pdf-upload-fixes.md](docs/pdf-upload-fixes.md) §9。

- `web-toolbox/pdf-upload.js` 新增逐页模板摘要改写（`file.bin` → `router.bin`，且不污染缓存），恢复 `<uuid>/` 模板目录并保留页目录根的扁平兼容副本，每页截图同时放两处；9 条资源行改用真实 md5 与固定 `resourceType` 语义（1=模板 bin / 2=缩略图 / 0=页面图片）。
- `index.html` 中 `pdf-upload.js?v=14` → `?v=15`、`index.js?v=24` → `?v=25`，避免浏览器沿用旧缓存。
- `index.js` 的「下载笔记」按资源真实扩展名落盘（OSS 上是 webp，旧代码一律写 `.jpg`，按扩展名解码的应用会报文件损坏）。
- 同步修改 `ZhongYuToolbox-main/` 下的同源文件（两份仅脱敏占位符不同）。
- 自检脚本直接加载文件中的真实函数、喂 `example/` 下的 bin 做 12 项断言（摘要替换、长度不变、matrix/command 摘要保留、缓存未污染、9 条资源行等），12/12 通过；两份脚本 `node --check` 通过。真机「导出 PDF / 用其他应用打开」仍需人工确认一次。

## 2026-11-10 — 发布准备

**安全**

- 删除全部 **AES 主密钥常量**：
  - `index.js` / `pdf-upload.js` / `chem.js` 里 codeUtils 的种子串（`generateAesKey` / `aeskey`）；
  - `linspirer.js` 的 `LINSPIRER_KEY` / `LINSPIRER_IV`。
  改为运行时读取 `window.CODE_UTILS_SEED` / `window.LINSPIRER_KEY` / `window.LINSPIRER_IV`；
  新增 `web-toolbox/key-store.js`（只读取与校验）与 `web-toolbox/config.local.example.js`（模板）；
  `config.local.js` 进 `.gitignore`。未配置时抛错而不是静默算出乱码。详见 [docs/keys.md](docs/keys.md)。
- 机械脱敏：校级 API 主机、中转主机、pdf2img 服务、站点域名、两个 OSS 桶、学校代码、账号名、用户数字 id 全部替换为占位符；只覆盖自己写的文件，第三方库原样保留。详见 [docs/sanitization.md](docs/sanitization.md)。
- 未入库：桌面客户端 `app.asar` / exe、APK 样本、抓包目录、Chrome 调试 profile（含登录 token）、`logs/`、`.workbuddy/` 状态、真实笔记与答卷；只保留 `sample/` 下的一份脱敏样例。

**修复**

- `linspirer.js` 副本的中文注释整片乱码（`棰嗗垱` 之类），从原始源码重新生成。
- `pipeline/html2note.js` 里 92 个 `U+FFFD` 残留清理：删掉 34 行损坏注释，恢复 3 条日志文案。
- 去掉首屏的**问卷调查弹窗**（`index.html` 里 `body` 开头的整段 IndexedDB + Modal 逻辑，约 6.6 KB）：它会在每次打开页面时弹「问卷调查邀请」，并把 `localStorage.realName` 拼进外部问卷地址打开（`wj.html?realName=…`）；同时删掉配套的 `surveyModal` 模板与 `survey_completed` 消息监听。原仓库该功能为上游所有。
- 页面内的「使用说明」整节重写（3,783 → 3,221 字符）：按现有 15 个页签改写（云笔记三个子页签、预览/导出 PDF/一键下载、老师分享、在线专栏单篇与批量、化学卷、领创、上游页签、自动化流水线、说明与致谢）；PDF 上传页签里过时的「打包 ZIP / 上传转换」提示同步换成当前行为（本地渲染、逐页旋转、保持前台）。

**工程**

- `.gitattributes` 设 `* -text`：`example/*.bin` 模板必须字节一致，否则资源行的 md5 对不上（校验 `page_router.bin` SHA-256 与源文件一致）。
- 新增 `docs/keys.md`、`docs/sanitization.md`、`docs/pdf-upload-fixes.md`、`docs/pipeline.md`、`docs/web-toolbox.md`、`docs/notes/2026-11-08.md` 与 `sample/`。

## 2026-11-09 … 11-10 — 作答链路并入

- 纳入另一条链路的产物：`pipeline/scripts/pipeline.py`（22 行兼容入口，设置 `ANSWER_ROOT` 后调用包内 `cli:main`）与 `pipeline/tools/answer-pipeline/`（Python 包，含 4 个单元测试）。
- 10 个子命令：`init` / `status` / `scan` / `watch` / `wait` / `render` / `check` / `publish` / `done` / `skip`。
- `python scripts/pipeline.py --help` 只依赖标准库即可运行，已实测。

## 2026-11-08 — PDF 上传链路 8 处修复

1. **卡在 15% 不报错**：标签页在后台被 Chrome 降频，`page.render()` 的 promise 永不 resolve。加 `PDF_RENDER_TIMEOUT_MS = 60000` 超时与明确文案；测试脚本侧用 `Emulation.setFocusEmulationEnabled` 复现/验证（273 ms 完成 vs 132 s 无响应）。
2. **pdf.js worker 走外网**：`workerSrc` 指向 `cdnjs` 的 `pdf.worker.min.js`，跨域 `new Worker` 必被拒（`SecurityError`），退回 fake worker 仍要抓外网脚本。改为本地 `pdf.worker.min.js`。
3. **自动旋转把所有纵向页转 90°**：旧判据只看「横版画布填充率」，A4 纵向页比值 2.0 恒超阈值。改为「文字基线角度优先，纯图页看非白像素的行列分布，判不出就 0」，并加**逐页手动旋转**缩略图条（← → 每次 90°，可全转/复位）。
4. **`Access denied by authorizer's policy.`（403）**：客户端用 **UTC** 日期拼 OSS key，服务端 STS 策略按**北京时间**取 `yyyyMMdd`，每天 00:00–08:00 差一天。改用 `Asia/Shanghai` 推导日期，捕获策略拒绝且两日期不同时用 UTC 重试一次，并把实际日期回传给后续 `fileUrl`/`saveNote` 保持同一天。
5. **手机端「获取文件位置失败」**（四个子问题，App 按 `fileUrl + 页目录 + 文件名` 取文件）：
   - `saveNote` 的 `fileUrl` 主机写死成另一个桶 → 改为按 `window.ossBaseUrl` 生成；
   - 7 个模板与 `snapshot.bin` 原来共用 `ossPageHash/<uuid>/` 目录，与每页 `pageName` 不一致 → 改为每页上传到自己的 `pageHash/` 目录；
   - `loadTemplateFiles()` 返回缓存对象，首次上传时 `delete templates['page_router.bin']` 导致**同一会话第二次上传缺该文件** → 不再删除缓存；
   - 只有第 1 页上传 `screenshot.png`，后续页的截图行指向不存在的对象 → 每页都上传自己的 PNG。
6. **导出的 PDF 每页都是第 1 页**：导出循环取 `pageData.thumbnail`（`resourceType == 2` 的整篇截图）当每页图。改为取该页 `originals` 里的 webp/PNG，并用 `imgFormat()` 按 dataURL 的 MIME 传格式（原来硬编码 `'JPEG'`）。
7. **「下载笔记」按钮永久失效**：`noteDownload2` 把 URL 写死成 `${API_BASE_URL}/special/GetByFileId`（只对中转站点有效），失败后 `this.downloading` 不释放。改用 `getCloudNoteApiPathR()` 并 `try/finally` 复位标志。
8. **模板 `.bin` 被当成图片处理**：下载端按扩展名挑选、放宽 MIME 校验，避免 `application/octet-stream` 被拒。

## 2026-11-08 — HTML → PDF → 云笔记 流水线

- `convertOne`：`file://` 打开 → 等 `readyState` → `Emulation.setEmulatedMedia('print')` → `.page` 个数自检（无 `.page` 时用 `break-before: page` 元素数，容差 ±1）→ `Page.printToPDF`（`preferCSSPageSize` / `printBackground` / 零边距）→ 数 `/Type /Page`。
- `uploadPdf`：注入 `File` 到 `#pdfFileInput` → 等「共 N 页」→ 写标题 → 点上传 → 轮询进度 → `GetAll` 差集认领新笔记的 `fileId`。
- `--watch`：`fs.watch` + 1 s 防抖 + 30 s rescan + 单并发队列 + 内容哈希去重 + `logs/watch-YYYY-MM-DD.log` 按天切分 + `SIGINT` 优雅退出。
- `--check-cloud`：巡检云端目录，按标题关键字过滤，把新笔记（含资源）下载到 `input/`，去重记录在 `.workbuddy/cloud-seen.json`（键 = `fileId`）；**对云端零写操作**。
- `ensureAuth`：`token` 未过期直接用 → `refreshToken` 续期 → 账号密码登录；凭据只从 `config.json` 或环境变量读，不落盘不打日志；`--check-auth` 可单独验证。

## 2026-11-07 … 11-08 — 网页端扩展模块

- 新增 `batch_pick.js`（批量下载）、`share_read.js`（老师分享）、`column_read.js` / `column_batch.js`（专栏）、`pdf-upload.js`（PDF/图片转笔记，含逐页缩略图与手动旋转）、`chem.js` + `chem.html`（化学卷）、`linspirer.js`（领创模块）、`cloudnote-window.html`（独立笔记窗口）。
- 统一用 `?v=` 查询串破缓存，模块间复用页面内的 `uploadFile` / `saveNote` / `aesEncrypt` / `localStorage.token`。

## 基线

- 基于上游 [BennyLoshop/ZhongYuToolbox](https://github.com/BennyLoshop/ZhongYuToolbox) 的网页版工具箱（`index.html` / `index.js` / pdf.js 与模板）。
