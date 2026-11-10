# 网页工具箱模块

在原版 `index.html` / `index.js`（单页工具）之上，以**独立文件 + 包装既有函数**的方式加了 6 个扩展模块和 1 个独立窗口，不改原模块内部逻辑。

| 文件 | 大小 | 功能 | 接入点 |
| --- | --- | --- | --- |
| `batch_pick.js` | 47 KB | 图库 / 回收站 / 云笔记 / 专栏章节的批量勾选与一键下载，每篇笔记一个文件夹 | 包装列表渲染，注入复选框与工具栏 |
| `share_read.js` | 46 KB | 「老师分享」页签：老师 → 文件夹树 → 文件内容（只读链路） | 新页签 + 独立接口调用 |
| `column_read.js` | 15 KB | 老专栏站登录（换 `navToken`）+ 接口诊断 | 独立页签 |
| `column_batch.js` | 14 KB | 专栏一键下载：复刻接口 → 递归复制文件 URL → 并发 → zip | 独立页签 |
| `pdf-upload.js` | 50 KB | PDF → 云笔记：本地 pdf.js 渲染、自动纠偏、逐页缩略图、逐页手动旋转 | 包装 `uploadFile` / `saveNote` / `aesEncrypt` |
| `pdf-templates.js` + `pdf.min.js` + `pdf.worker.min.js` | 1.5 MB | 模板 base64 内嵌 + pdf.js 3.11.174 本地化 | 普通 `<script>` 加载 |
| `cloudnote-window.html` | 6.6 KB | 独立云笔记窗口：在新标签页内部预热会话，绕开「登录态按标签页隔离」 | 由 `index.js` 打开 |

`index.html` 里用 `?v=` 查询串做缓存破除（`index.js?v=24`、`pdf-upload.js?v=14`、`share_read.js?v=8`、`column_read.js?v=7`、`batch_pick.js?v=7`、`column_batch.js?v=4`）。

## PDF 上传：分步流程

1. **读模板**：`loadTemplateFiles()` 从 `example/` 取 7 个 `.bin`（`page_router.bin`、`a888b5fb-…/` 下 6 个），失败回落到 `window.PDF_TEMPLATES`；
2. **渲染**：`renderPdfLocally()` 用 pdf.js 逐页画到 2880×1800 画布，webp 质量 0.92，另存一张 PNG 做缩略图；`autoRotate` 只决定**默认**旋转，最终以 `pdfRotState.angles[i]` 为准；
3. **逐页上传**：每页 9 个文件（7 个模板 + `screenshot.png` + 整页 webp）都上传到该页自己的目录 `<oss 根>/note_v2/res/<userId>/<yyyyMMdd>/<fileId>/<pageHash>/`；
4. **写资源行**：`Resources/AddOrUpdate`（AES 加密的数组），每条 `pageName` 指向页目录、`ossImageUrl` 指向具体文件；
5. **写笔记**：`Notes/AddOrUpdate`（AES 加密的对象），`fileUrl` 用真实桶根拼接（**不再写死**）。

## 为什么这些细节重要

手机端是按「`fileUrl` + 页目录 + 文件名」拼远端地址取文件的。只要有一处对不上（主机写死、模板放在共享目录、少传第 2 页起的 `screenshot.png`、缓存对象被 `delete`），手机就会报「获取文件位置失败」，而网页端因为走的是自己那份 `ossImageUrl`，看起来一切正常。

详见 [pdf-upload-fixes.md](pdf-upload-fixes.md)。

## 已知依赖

- 页面仍从 `cdnjs.cloudflare.com` 加载 `fabric.js 5.3.1`（原版就有，未替换）；
- `crypto-js.js`、`aliyun-oss-sdk.min.js`、`jszip.min.js`、`jspdf.umd.min.js`、`pdf.min.js` 都是本地文件；
- 生成的题目 HTML 本身是**零脚本、零 CDN** 的（KaTeX 已内联/本地化），可以离线打开。
