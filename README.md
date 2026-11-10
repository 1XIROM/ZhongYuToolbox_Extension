# 云笔记工具箱（网页端扩展 + 自动化流水线）

> 个人学习与自动化用途的逆向研究项目：给某校「云笔记 / 云笔记工具箱」网页版补齐它没有的能力，并把「题目 HTML → PDF → 云笔记」做成可常驻的一条流水线。
>
> **本仓库已脱敏**：不含账号、令牌、真实笔记内容、校内地址与 **AES 主密钥**。代码里的 `<api-host>`、`<oss-bucket>`、`<relay-host>` 等占位符需要替换成你自己的部署值才能运行，详见 [docs/sanitization.md](docs/sanitization.md)。

## 与上游的关系

本仓库基于 [BennyLoshop/ZhongYuToolbox](https://github.com/BennyLoshop/ZhongYuToolbox)。上游是「中育云笔记」网页端的逆向工具箱（`index.html` + `index.js` + pdf 相关脚本）；本仓库保留它的页面骨架与接口封装，在其之上做了下面这些改动。

### 相对上游改了什么

| 类别 | 内容 |
| --- | --- |
| 新增模块 | `batch_pick.js`（批量下载）、`share_read.js`（老师分享）、`column_read.js` / `column_batch.js`（专栏）、`pdf-upload.js`（PDF/图片转笔记）、`chem.js` + `chem.html`（化学卷）、`linspirer.js`（领创模块）、`cloudnote-window.html` |
| PDF 上传链路 | 8 处修复，每处都有复现与验证数据 → [docs/pdf-upload-fixes.md](docs/pdf-upload-fixes.md) |
| 新流水线 | `pipeline/html2note.js`：HTML → PDF → 云笔记，含 `--watch` 常驻、`--check-cloud` 巡检、自动续期登录 |
| 新流水线 | `pipeline/scripts/pipeline.py` + `pipeline/tools/answer-pipeline/`：读题作答 → 渲染 → 校验 → 发布 |
| 安全 | **删除全部 AES 主密钥常量**（见下节），域名 / 桶名 / 账号脱敏 |
| 文档 | `docs/`、`sample/` 与这份 README 均为新增 |

逐条变更见 [CHANGELOG.md](CHANGELOG.md)。

### 关于 AES 主密钥

上游把 AES 主密钥直接写在脚本里（`index.js` / `pdf-upload.js` / `chem.js` 的 codeUtils 种子串，以及 `linspirer.js` 的 KEY/IV）。公开仓库里保留这些常量，等于把「解开流量」的能力一起发布出去，所以：

- 常量已全部删除，改为运行时读取 `window.CODE_UTILS_SEED` / `window.LINSPIRER_KEY` / `window.LINSPIRER_IV`；
- 取值放在 `web-toolbox/config.local.js`（**已在 .gitignore 中**），由 `key-store.js` 读取；未配置时相关功能会抛出明确错误，而不是静默算出乱码；
- 模板见 `web-toolbox/config.local.example.js`，细节见 [docs/keys.md](docs/keys.md)；
- 如果你的副本里曾经提交过这些常量，请让服务端轮换密钥。

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

## 目录

| 路径 | 内容 |
| --- | --- |
| `web-toolbox/` | 网页工具箱：`index.html`、`index.js` 与 6 个扩展模块、pdf.js 与模板、`example/`、`web/` |
| `pipeline/html2note.js` | HTML → PDF → 云笔记，支持 `--watch` 常驻与 `--check-cloud` |
| `pipeline/scripts/pipeline.py` | 另一条流水线的调度脚本（扫描 / 队列 / 校验 / 发布） |
| `pipeline/tools/answer-pipeline/` | 「读题作答 → HTML」Python 包（含 KaTeX 资源与单元测试） |
| `docs/` | 模块说明、PDF 上传链路的 8 处修复、流水线用法、脱敏说明 |
| `sample/` | 一份脱敏后的输入（笔记页图）与输出（答案 HTML）示例 |

## 快速开始

### 1. 网页工具箱

```bash
# 起静态服务（换成你的目录）
npx serve web-toolbox -l 8088
# 另开一个带调试端口的 Chrome
chrome --remote-debugging-port=9222 \
       --user-data-dir="<你的临时 profile>" \
       http://127.0.0.1:8088/
```

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
- 没有 UI 自动化回归测试，验证靠脚本探测（见 `docs/pdf-upload-fixes.md` 的验证方法）。

## 免责声明

本项目仅用于个人学习、自动化与接口兼容性研究，与任何学校/公司无关。请只在你有权访问的账号与数据上使用；不要用它批量抓取或传播他人内容。

## 其他

本项目由Deepseek辅助制作
