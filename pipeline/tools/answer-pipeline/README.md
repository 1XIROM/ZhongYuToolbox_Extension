# answer-pipeline

把题目素材变成**打印就绪**的横版 HTML 答案：监测素材 → 去重入队 → 渲染 → 结构自检 → 发布。

它不是「AI 自动答题器」。审题与作答由上层（人或 Agent）完成，本包负责作答之后的所有机械环节，以及一道不可绕过的质量闸门。

## 为什么是 HTML 而不是 PDF

- 平台侧生成/编辑 PDF 往往受限制，而 HTML 用浏览器「打印为 PDF」可得到等价结果；
- KaTeX 公式预渲染成静态节点并内联字体，产物**单文件、零脚本、零 CDN**——断网、换机器、隔一年再打开，排版都不会塌；
- 校验只做结构不依赖浏览器，因此能在几毫秒内判断「这份成品能不能交付」。

## 流程

```
input/ 新素材
   │  scan（内容指纹去重）
   ▼
queue/*.json  pending_answer
   │  上层读图作答 → drafts/*_答案.md
   ▼
render  →  staging/*_答案横版.html      ← 只写缓存
   │  check（11 项结构校验）
   ▼
publish →  output/                      ← 校验通过才移入
   │  done（回写队列与清单，成品若仍在缓存会自动校验发布）
   ▼
done
```

不可绕过的两条：**渲染不直接写 `output`**，**未过校验不得标记完成**。

## 安装

```bash
pip install -e .          # 装成命令：answer
# 或者不安装，直接用模块形式：
PYTHONPATH=src python -m answer_pipeline.cli status
```

需要本机有 Chrome / Edge / Chromium 之一（用于 KaTeX 预渲染）。找不到时可用环境变量指定：

```bash
export ANSWER_CHROME="/path/to/chrome"     # Windows: set ANSWER_CHROME=C:\...\chrome.exe
```

KaTeX 资源已随包内置（`assets/katex/`，约 600 KB），首次运行无需联网。

## 快速开始

```bash
# 1. 把「已经存在的素材」记为基线，之后只处理新增的
answer init

# 2. 看一眼队列
answer status

# 3. 监测新素材（二选一）
answer watch --interval 2                 # 常驻
answer wait --interval 1 --timeout 0      # 一次性哨兵：发现新素材即退出，用来唤醒上层

# 4. 拿到草稿后：渲染 → 校验 → 发布 → 标记完成
answer render drafts/xxx_答案.md --title "高中物理 · 电容器"
answer check  .workbuddy/auto/staging/xxx_答案横版.html --md drafts/xxx_答案.md
answer publish .workbuddy/auto/staging/xxx_答案横版.html --md drafts/xxx_答案.md
answer done   .workbuddy/auto/queue/xxx.json --md drafts/xxx_答案.md \
              --html output/xxx_答案横版.html
```

## 目录约定

```
<root>/
├─ input/                       # 素材：一级子文件夹 = 一个任务；根目录 PDF = 一个任务
├─ output/                      # 只放通过校验的成品
└─ .workbuddy/auto/             # 运行状态（建议 gitignore）
   ├─ drafts/                   # 答案草稿（Markdown）
   ├─ staging/                  # 渲染缓存，未校验
   ├─ queue/                    # 任务队列 JSON（相对路径）
   ├─ logs/                     # 每日日志
   ├─ manifest.json             # 已处理素材清单
   └─ .lock                     # 跨进程锁
```

队列里存的是**相对 root 的路径**，项目整体搬目录或换机器后仍可用。

## 草稿格式

格式即结构，直接决定成品版式：

```markdown
# 学科 · 主题

## 第 1 题（多选题）

题干原文。

- A. 选项一
- B. 选项二

**答案**：A、B
**关键结果**：一句话结论。
**解析**
【第一步：…】…
【第二步：…】…
**高中范围**：本题考查的知识边界。
```

规则：

| 语法 | 作用 |
|---|---|
| `## 任意标题` | 一个题块 = 成品一页起排；编号可省（如「追问：…」） |
| `**答案**：` | 可跨多段，首段带「答案：」前缀，其余同字号续排 |
| `**关键结果**：` | 结论摘要，可省略 |
| `**解析**` | 之后的段落进 `.analysis`（14pt） |
| `**高中范围**：` | 渲染成引用块，也是校验必检项 |
| `- ` 开头 | 只在题干区当选项，解析区不会被误判 |

公式一律写行内 `$...$`。不要写 `$$`、`\[ \]`、「易错点」段落——前者会让节点数对不上，后者是明确的排除项。

## 配置

`<root>/answer-pipeline.json`（全部可选）：

```json
{
  "layout": {
    "page_size": "A4 landscape",
    "page_margin": "12mm 16mm 10mm",
    "analysis_pt": 14,
    "body_pt": 11
  },
  "settle_seconds": 2.0,
  "lock_timeout": 30,
  "chrome_path": "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "katex_dir": "/opt/katex"
}
```

环境变量：`ANSWER_ROOT`、`ANSWER_INPUT`、`ANSWER_OUTPUT`、`ANSWER_STATE`、`ANSWER_CHROME`。
命令行参数优先级最高：`--root`、`--input`、`--output`、`--state`、`--config`。

## 校验的 11 项

`check` 不通过即返回 1，`publish` 会拒绝移动文件。

1. 文件不是空壳（> 2 KB）
2. 至少一个题块
3. 题块数 == `<h2>` 数
4. 存在强制分页 `break-before: page`
5. `katex-error` 为 0
6. 正文无残留 `$...$` 源码
7. 公式节点数 ≥ 草稿公式数
8. 有公式则字体必须已内联
9. 无 `<script>`
10. 无 `<link>` / `@import` / `url(fonts/)` / 外链（`www.w3.org` 命名空间除外）
11. 含「答案：」「解析」「高中范围」，且不含「易错点」

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 校验未通过（成品留在缓存） |
| 2 | 用法错误或文件缺失 |
| 3 | 渲染环境不可用（找不到浏览器等） |

## 嵌到别的项目里

本包不假设目录名，全部路径可覆盖，因此可以整包拷进任何仓库：

```bash
git subtree add --prefix=tools/answer-pipeline <repo> main --squash
# 或
git submodule add <repo> tools/answer-pipeline
```

在宿主项目里只需保证三件事：素材目录、草稿目录、成品目录，然后用 `--root` 指过去：

```bash
answer status --root /path/to/host-project
```

## 设计取舍

- **为什么先在浏览器里渲染，再抽静态 DOM？** 直接引 KaTeX JS 的页面一旦在 PDF 转换器里不执行脚本，公式就退化成 `$x^2$`。预渲染把「执行一次」提前到生成阶段。
- **为什么校验不查视觉？** 视觉审查需要人（或模型）看截图，属于上层职责；结构校验是廉价、确定、可自动化的那部分，把它做扎实才能保证 `output` 里没有坏件。
- **为什么答案支持多段？** 译文、多小问、逐项辨析都需要分段，早期实现只取一行，导致整段内容被静默丢弃。
- **为什么指纹只认内容？** 同一张题图被改名、重新导出很常见，按名字去重会重复作答。旧算法把路径也哈希了进去，升级时保留旧键兼容，避免历史素材被重新入队。

## 已知限制

- 依赖本机浏览器做公式预渲染；纯无头服务器需装 Chromium。
- 只面向中文排版（中文字体族写死为雅黑/宋体回退），其他语言需要改 `css_for`。
- 不做 OCR，也不理解题目内容——`input` 里的图片需要上层读成文字。

## 许可

代码 MIT。内置的 KaTeX 0.16.9（`assets/katex/`）为 MIT 许可，版权归 KaTeX 作者所有。
