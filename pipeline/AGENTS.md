# AGENTS.md — 题目答题流水线

本目录是流水线根目录（含 `input/`、`output/`、`scripts/`）。你是执行者：读题图、写草稿、渲染、自检、发布。
这里只放**每次都必须遵守的硬约束**；完整写作规范见 `../docs/answer-pipeline-prompt-codex.md`。

## 0. 边界

- 工作根目录＝本文件所在目录。所有相对路径以它为准。
- 你不常驻：素材由外部程序（`html2note.js` / 唤醒脚本）下载好并入队，你被调用一次就处理完退出。
  **不要挂哨兵、不要定时轮询、不要等新素材**。
- **无人值守**：中途不要向用户提问、不要等确认。看不清就标注「该处无法辨认」，处理完在报告里说明。
- 云端上传由 html2note 负责：`output/` 里出现 HTML 就会被它转 PDF 传上去。不要碰上传。

## 1. 命令

```bash
python scripts/pipeline.py status                       # 看待答
python scripts/pipeline.py render "<md>" --check        # 渲染 + 自检（写 staging）
python scripts/pipeline.py publish "<staging html>" --md "<md>"
python scripts/pipeline.py done "<queue json>" --md "<md>" --html "output/<同名 html>"
python scripts/pipeline.py skip "<queue json>" --reason "…"   # 确认无需作答时
```

- 渲染依赖本机 Chrome 做公式预渲染；报 `ChromeNotFound` 时先看环境，不要自己换渲染器。
- 素材里的 `00_data.mdb` / `00_lock.mdb` / `00_snapshot.bin` 一律忽略，不要解析、不要联网下载。
- **输出乱码**：脚本按 UTF-8 打印，Windows 控制台默认 GBK。先 `chcp 65001`（cmd）或
  `$env:PYTHONIOENCODING="utf-8"`（PowerShell），否则 `status`/`render` 的中文是乱码，
  会把「阻断」和「通过」看反。

## 2. 读图

- **题目原图优先**：`resources.json` 里 `type=0`、带 uuid 的 jpg/png；`status` 显示的「主素材」就是它。
- `00_screenshot.png` 是整页预览（纸面空白多、题目被缩小），**只用来核对有没有漏题**。
- 字小就裁局部放大 3～6 倍再读：

  ```bash
  ffmpeg -y -i "input/<目录>/<原图>" -vf "crop=W:H:X:Y,scale=iw*4:ih*4:flags=lanczos" out.png
  ```

- 数值、小数点、正负号、上下标、坐标刻度、角度、波形折点**必须放大核准**。
- 红笔圈注、手写批注、页边追问往往就是真正的任务，不能漏读。
- 确实看不清就写「该处无法辨认」，不要编。

## 3. 草稿格式

- 首行 `# 学科 · 主题`；每个题块 `## ` 开头，一题一块、一块一页起排；标题里不要出现 `$`。
- 每块三段：`**答案**：` / `**解析**` / 末段 `**高中范围**：`；物理题另加 `**补充理解**`（2～4 段）。
- 公式一律行内 `$...$`，禁止 `$$...$$`、`\[ \]`、`\( \)`；中文引号用全角 “ ”。
- 化学式用 `\mathrm{}` 写正体；方程式整条放 `$...$`，沉淀 `\downarrow`、气体 `\uparrow` 要写。
- 选择题逐项判写成「A 项：…，正确。」，不要用「A．」开头。
- 超页压缩顺序：① 合并相邻短段落 → ② 行内 `\dfrac` 改 `\frac` → ③ 收并列句、删重复辨析。
  **不许**删「补充理解」或「高中范围」腾地方。

## 4. 搜索

可以联网查证，但按规则用：

| 情况 | 做法 |
| --- | --- |
| 纯计算、纯推理、课本内推导 | **不搜** |
| 公式记不准、常数不确定 | 搜，以教材口径为准；论坛数值不用 |
| 题目涉及课外背景 | 搜背景，结论仍按高中范围写 |
| 网上有原题 | 可对照，**不照抄**；条件不符就丢弃 |
| 搜索结果与推导冲突 | 以推导为准，差异写进「补充理解」 |

- 搜索结果**不进成品**：不写 URL、不写「据某某网站」。
- 只在需要核对时搜，报告里说明搜了什么。

## 5. 不允许

- 往 `output/` 直接写文件，或跳过 `--check` 就 publish。
- 编造题干、图形条件、选项。
- 为了让成品通过，去改 `scripts/pipeline.py` 的校验规则。
- 把 `.md` 当交付物，或改 `input/` 里的原始素材。
- 覆盖 `output/` 里已存在的同名成品（`publish` 默认拒绝；除非用户明确要求 `--force`）。

## 6. 目录速查

| 路径 | 内容 |
| --- | --- |
| `input/<时间>_<标题>_<id8>/` | 一条云笔记的素材（图片 + `resources.json`） |
| `.workbuddy/auto/queue/*.json` | 待答队列，`status == "pending_answer"` 就是待办 |
| `.workbuddy/auto/drafts/` | 草稿 Markdown |
| `.workbuddy/auto/staging/` | 渲染缓存（未发布） |
| `output/` | 已发布成品，html2note 会自动转 PDF 上传 |
