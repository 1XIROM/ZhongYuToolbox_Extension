# 流水线：HTML → PDF → 云笔记（含云端巡检）

`pipeline/html2note.js` 是一条「本地文件 → 云端笔记」的单向流水线，运行在 Node（无第三方依赖）里，通过 Chrome DevTools Protocol 复用网页工具箱页面已经登录的会话。

## 目录约定

```text
<运行目录>/
├── output/                  ← 监听目录：题目 HTML 放这里
├── pdf/                     ← 生成的 PDF
├── input/                   ← --check-cloud 下载回来的笔记目录
├── logs/
│   └── watch-YYYY-MM-DD.log ← 按天切分，UTF-8 BOM 开头
└── .workbuddy/
    ├── uploaded.json        ← 已上传记录（键：文件名@内容SHA-256，值：{sha, fileId, at, note}）
    └── cloud-seen.json      ← 已下载记录（键：fileId，值：{at, title, dir, files, bytes, ver, upd}）
```

`config.json`（模板见 `config.example.json`）可覆盖 `outputDir` / `pdfDir` / `stateFile` / `cdpEndpoint` / `toolboxUrlPart` / `noteTitleFormat` / `pageSize`。

## 命令

```bash
node pipeline/html2note.js                 # 处理一次 output/ 里的待上传 HTML
node pipeline/html2note.js --dry           # 只转换、不上传
node pipeline/html2note.js --force         # 忽略内容哈希，强制重传
node pipeline/html2note.js --only 关键字    # 只处理文件名含关键字的 HTML
node pipeline/html2note.js --check-auth    # 只做一次登录态检查/续期
node pipeline/html2note.js --watch         # 常驻：fs.watch + 周期 rescan
node pipeline/html2note.js --watch --check-cloud            # 常驻 + 云端巡检
node pipeline/html2note.js --watch --check-cloud --cloud-folder 作业   # 只巡检指定文件夹
node pipeline/html2note.js --watch --check-cloud --cloud-title ds      # 只处理标题含 ds 的笔记
```

`--interval <ms>` 调整周期 rescan 间隔（默认 30000）。`--watch` 下 `--force` 被忽略（常驻模式靠内容哈希去重）。

## 一次处理的步骤

1. 连 CDP，找 `toolboxUrlPart` 匹配的页面（找不到就报 `找不到 toolbox 页面`，可用 `PUT /json/new?<url>` 自己开一个）；
2. `ensureAuth`：token 剩余 > 5 分钟直接用；否则用 `refreshToken` 续期；再不行用配置里的账号密码登录（凭据只从 `config.json` 或环境变量读，不落日志）；
3. 在后台标签页打开 HTML，`Emulation.setEmulatedMedia({media:'print'})`，`Page.printToPDF`；
4. 页数自检：优先按 `.page` 元素个数；没有 `.page` 就按 `break-before: page` 的元素个数（允许 +1 容差）；实际页数**多于**期望只告警，少于期望才失败重试（3 次后放弃，宁可不传）；
5. 把 PDF 作为 `File` 塞进页面的 `#pdfFileInput`，等缩略图就绪，填标题（默认 `M月D日HH:mm:ss`），点上传，轮询进度到「上传完成」；
6. 再拉一次笔记列表做差集，确认新笔记的 `fileId`，写入 `uploaded.json`。

## 云端巡检（`--check-cloud`）

每 `cloud.intervalMs`（默认 30000，挂在 rescan tick 上）执行一轮：

- `Notes/GetAll` → 按 `parentId` 过滤（默认根目录，`--cloud-folder` 可指定文件夹名或 `fileId`）→ 再按 `--cloud-title`（默认 `ds`）过滤标题；
- 用 `fileId` + `title` + `version` 判断「新增 / 标题变化 / 版本变化」，三者都没变就跳过；
- 命中则 `Resources/GetByFileId` 拿资源清单，逐条下载到 `input/<创建时间>_<标题>_<fileId 前 8 位>/`，写 `resources.json`。

全过程只发 GET（外加两个鉴权 POST），**不写云端**。

## 日志样例

```text
[2026-11-08 18:56:56] [启动] 监听 E:\...\output
[2026-11-08 18:56:56] [启动] 周期 rescan 间隔 30000 ms；日志: E:\...\logs\watch-2026-11-08.log
[2026-11-08 18:56:57] [登录] token 有效，剩余 118 分钟
[2026-11-08 18:57:01] [转换] 2026年广东卷物理_答案横版.html → pdf\....pdf 216.1 KB 1331ms
[2026-11-08 18:57:01] [上传] ✅ 笔记「11月8日18:57:01」 页=4 PDF=216.1 KB fileId=...
[2026-11-08 18:57:30] [云笔记] 无变化（范围=根目录，标题含「ds」，共 4 条）
```

## 失败处理

- 单文件失败只记录 `[失败-转换]` / `[失败-上传]`，不中断队列；同一 `文件名@哈希` 连续失败 3 次后跳过，等 HTML 改动再试；
- 连不上浏览器时把文件放回队首，等 30 秒重试；
- `Ctrl+C` 会等当前文件处理完再退出（最长 120 秒）。

## 与 `scripts/pipeline.py`（作答链路）的关系

`pipeline/` 同时是「作答链路」的项目根：`html2note.js` 在根目录，`scripts/pipeline.py` 与它是兄弟，数据目录（`input/` `output/` `pdf/` `.workbuddy/`）都在根下，两条链路通过 `output/` 解耦。

```
素材（新笔记目录 / PDF）
        │  scan / watch / wait
        ▼
   待答队列（.workbuddy/auto/queue）
        │  由 AI 写 Markdown 答案
        ▼
   render  →  check（11 项）  →  publish
        ▼
      output/*.html
        │  html2note.js --watch
        ▼
     pdf/*.pdf  →  云笔记
```

`scripts/pipeline.py` **只是一个兼容入口**（22 行）：把 `tools/answer-pipeline/src` 加进 `sys.path`、设 `ANSWER_ROOT`，然后调 `answer_pipeline.cli:main`。真实实现全在包里，保留它是因为定时任务和文档里写的是 `python scripts/pipeline.py …`。

```bash
cd pipeline
python scripts/pipeline.py --help      # 只依赖标准库，能直接跑
```

子命令（`--help` 实测输出）：

| 子命令 | 作用 |
| --- | --- |
| `init` | 把现有素材记为基线，不触发答题 |
| `status` | 查看待答队列 |
| `scan` | 单次扫描新增文件夹或 PDF |
| `watch` | 常驻监测 |
| `wait` | 一次性哨兵，发现新素材即退出 |
| `render` | Markdown 答案转横版 HTML（默认写入缓存） |
| `check` | 校验缓存成品（只报告，不移动） |
| `publish` | 校验通过后把缓存成品移入 `output/` |
| `done` / `skip` | 标记任务已完成 / 无需作答（如成品页回灌） |

包自带 4 个测试（需要 `pytest`）：

```bash
cd pipeline/tools/answer-pipeline
python -m pytest -q     # test_paths / test_queue / test_render / test_check
```
