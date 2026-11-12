# PDF 上传链路：8 处真实故障与修法

按「症状 → 根因 → 修法 → 怎么验证」记录。所有数字来自当时的实测输出。

## 1. 卡在 15%，进度条不动

**症状**：点「开始上传」后停在 `15% 正在转换PDF…`，180 秒无变化，网络面板里没有任何 OSS 请求。

**根因**：页面在后台被 Chrome 降频（`document.visibilityState === 'hidden'`），`page.render()` 返回的 Promise 永不 resolve；同一个页面在前台渲染只需 273 ms。

**修法**：给 `getDocument` / `page.render` 包 60 秒超时，失败时提示「窗口可能被浏览器降频（最小化）、worker 未加载或该页过大，请切到前台重试」。

## 2. 转换偶发失败：worker 被跨域拦

**症状**：控制台出现 `SecurityError: Failed to construct 'Worker': Script at 'https://cdnjs…/pdf.worker.min.js' cannot be accessed from origin …`，随后 `Warning: Setting up fake worker.`。

**根因**：`pdfjs.GlobalWorkerOptions.workerSrc` 被改成 CDN 地址，跨域 `new Worker()` 必被拒；退回的 fake worker 仍要动态抓 CDN 脚本，外网抖动时无超时。

**修法**：改成同源的 `pdf.worker.min.js`。

## 3. 竖版页被一律转横

**症状**：所有 A4 纵向页在笔记里都侧躺 90°。

**根因**：旧判据是比较「内容放进 2880×1800 横版画布」的填充率——纵向页 `c90/c0 ≈ 2.0`，永远大于阈值 1.25，于是**所有纵向页必转**。

**修法**：自动判定只作默认值；新增逐页缩略图 UI（全部左转 / 右转 / 恢复自动），最终旋转量以用户点击为准。有文字层的页面按文字基线角度投票（要求实质文字量，单行水印不算）；纯图片页宁可判 0，交给人工。

## 4. 上传 403 `Access denied by authorizer's policy.`

**症状**：渲染完成后第一步 PUT 就失败，弹窗「失败 / Access denied by authorizer's policy.」。

**根因**：`uploadFile()` 用 `new Date().toISOString()` 取日期拼 OSS key（**UTC**），而 STS 策略按**北京时间**取 `yyyyMMdd`。每天 00:00–08:00 两者差一天，key 前缀不在策略 Resource 内，直接被拒。实测同一时刻 `beijing=20261108` / `utc=20261107`。

**修法**：用 `toLocaleDateString('en-CA', {timeZone: 'Asia/Shanghai'})` 构造 `yyyyMMdd`；若仍失败且两个日期不同，用 UTC 日期重试一次；成功后把实际日期写到 `window.ossDateStr`，供上层拼 `fileUrl` / 资源 URL 时复用。

## 5. 手机打开提示「获取文件位置失败」

这其实是**四个**独立缺陷叠加，任何一个都会让手机端拼出的地址 404：

| 子问题 | 现象 | 修法 |
| --- | --- | --- |
| `fileUrl` 主机写死 | 笔记 `fileUrl` 指向 A 桶，对象都在 B 桶 | 用 `window.ossBaseUrl`（真实桶根）拼，兜底也改到 B 桶 |
| 模板文件放共享目录 | 7 个模板 + snapshot 只有一份，`pageName` 是每页独立目录 | 每页都把自己的模板上传到 `<pageHash>/` 下 |
| 缓存对象被 `delete` | 同一页面会话第 2 次起上传全缺 `page_router.bin` | `loadTemplateFiles()` 返回的是**引用**，去掉 `delete templates[...]`，改为只读副本 |
| 只有第 1 页有截图 | 第 2 页起 `screenshot.png` 指向不存在的对象 | 去掉 `if (!shotUploaded)` 守卫，每页都上传自己的 PNG 并回写该页 md5 |

**验证方法**：按手机端约定重建 `fileUrl + <页目录> + <文件名>`，逐条探测。修复后 4 页笔记 36/36 全部 200（每页 9 个文件 × 4 页）。

## 6. 下载回来的 PDF，18 页全是第 1 页

**症状**：上传 18 页，下载导出的 PDF 也是 18 页，但 17 页内容完全相同。

**根因**：导出代码用 `pageData.thumbnail.url` 取图——`thumbnail` 是 `resourceType == 2` 的那条资源，也就是整篇笔记共用的一张 `screenshot.png`；另外 `pdf.addImage(img, 'JPEG', …)` 把格式写死，而数据实际是 webp/PNG。

**修法**：优先从 `pageData.originals` 里挑图片扩展名（`webp/png/jpg/gif`）的那条；`addImage` 的格式参数按 dataURL 的 MIME 动态给。

**验证方法**：逐页渲染后比 SHA-256。修复前 `pages 18 distinct 2`，修复后 `pages 18 distinct 18`。

## 7. 「下载笔记」按钮点一次后永久失效

**症状**：下载一次后，再点任何笔记的下载都没反应，只能刷新页面。

**根因**：`noteDownload2()` 把 URL 写死成 `…/special/GetByFileId`（该中转只对某所学校有效），请求失败后 `data.data` 为空 → 解密抛错 → `this.downloading` 永远停在 1，后续调用直接 `return`。

**修法**：改用与预览一致的 `getCloudNoteApiPathR('GetByFileId', …)`；整体 `try/catch/finally`，在 `finally` 里释放标志。

## 8. Linux/无扩展名资源被当成图片拒绝

**根因**：下载逻辑要求 `content-type` 必须是 `image/*`，模板 `.bin` 是 `application/octet-stream`。

**修法**：只对图片类资源做 MIME 校验，`.bin` 直接按扩展名放行（打包进 zip）。

## 9. 上传的笔记能显示，却无法导出 / 被别的应用打开

**症状**：答题流水线自动上传的 PDF 笔记，在云笔记 App 1.13.4 里能翻页，但「导出 PDF / 用其他应用打开」失败；同一份 PDF 用「中育Toolbox 应用版」上传则正常。

**根因**：对比应用版 `NoteView`（`rt()` / `Ge()`）与早期 `core.py` 的上传结构，模板 bin 不是可以原样复用的静态文件：

| 位置 | 出厂内容 |
| --- | --- |
| `a2b4fb47-…_file.bin` | 示例页图片的 SHA-256（`b7b3e515…`），全文件恰好 1 个 64-hex |
| `router.bin` | `file.bin` / `matrix.bin` / `command.bin` 三个模板各自的 SHA-256 |
| `page_router.bin` | 模板目录名 `a888b5fb-e65d-4611-a3af-1f80a0fb6ced` |

应用版每页上传前先把 `file.bin` 里的摘要改成**当页真实 webp** 的 SHA-256，再算出改写后 `file.bin` 的 SHA-256 回填到 `router.bin`，并按 `page_router.bin` 里的 uuid 目录存放模板。旧链路直接上传出厂模板，于是：App 按摘要找不到页面资源、模板被扁平化后路径也对不上；再加上资源行 `md5` 全写死、`resourceType` 误用 `pageIndex`（第 2 页起被当成 bin / 缩略图），导出与分享时的整条校验链就断了。

**修法**（`pdf-upload.js?v=15`）：

- 新增 `sha256Hex()` / `replaceTemplateHash()` / `patchTemplatesForPage()`，逐页改写摘要链；改写的是副本，不污染模板缓存；
- 模板按 `pageHash/<uuid>/<file>` 上传（`page_router.bin` 放页目录根），页目录根另留一份扁平副本兼容旧取法；截图同时放 `<uuid>/screenshot.png` 与页根；
- 9 条资源行的 `md5` 全部按真实字节算，`resourceType` 改为 1=模板 bin / 2=缩略图 / 0=页面图片；`id` 用 App 本地路径、`ossImageUrl` 用 OSS 路径；
- 北京时间日期、真实桶根 `fileUrl`、每页一份模板等既有修复保持不变。

**验证方法**：自检脚本直接加载文件里的真实函数，喂 `example/` 下的 bin 做 12 项断言（摘要替换、文件长度不变、matrix/command 摘要保留、缓存未污染、9 条资源行、type 语义等），12/12 通过；两份脚本 `node --check` 通过。真机侧仍需点一次「导出 PDF / 用其他应用打开」确认。

---

## 这些修法的「验收标准」

1. 网页端：上传 4 页 PDF，`Resources/GetByFileId` 返回 `rows=36`，每页 9 条；
2. 手机端：点开笔记能正常显示（这一步只能真机验证）；
3. 导出：下载回来再逐页渲染，`distinct == pages`；
4. 结构自检：按手机约定重建 URL，全部 200；
5. 摘要链：每页 `file.bin` 的图片摘要 == 该页 webp 的 SHA-256，`router.bin` 里的 file.bin 摘要 == 改写后 `file.bin` 的 SHA-256（脚本自检）；
6. 兼容：上传后的笔记在云笔记 App 里能导出 PDF / 用其他应用打开（真机验证）。
