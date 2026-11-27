"""答案 Markdown -> 横版 HTML。

结构约定（与草稿格式一一对应）：
  ## 标题            一个题块，成品里是一个 <section class="page">，强制另起一页
  **答案**：...       可跨多段
  **关键结果**：...
  **解析**           之后到本块结束的段落按【第一步】式推导排版
  **高中范围**：...

只输出标准 Markdown 语义标签，不靠颜色/背景/脚本传达信息，保证打印不丢内容。
"""
from __future__ import annotations

import base64
import os
import re
from pathlib import Path
from typing import Iterable, List, Optional

from .paths import Layout

PAGE_MARK = "\n"
DOC_TAIL = "\n</body>\n</html>\n"

_IMG_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".webp": "image/webp", ".gif": "image/gif"}
_IMG_MAX_BYTES = 4 * 1024 * 1024


def img_tag(raw: str, alt: str = "") -> Optional[str]:
    """把 `![alt](本地路径)` 变成内嵌 base64 的 <img>，找不到文件返回 None。

    Markdown 的图片语法在成品里原本会被降级成一段替代文字（`<img>` 数为 0），
    所以图片只能手写 HTML 内嵌。这里把这一步做进渲染器：草稿照普通写法，
    渲染时读盘、转 data URI，单文件带走图片，打印与转 PDF 都不依赖外部链接。
    """
    p = Path(raw.strip())
    if not p.is_absolute():
        root = os.environ.get("ANSWER_ROOT") or os.getcwd()
        p = Path(root) / p
    try:
        if not p.is_file() or p.suffix.lower() not in _IMG_MIME:
            return None
        if p.stat().st_size > _IMG_MAX_BYTES:
            return None
        uri = "data:" + _IMG_MIME[p.suffix.lower()] + ";base64," + \
              base64.b64encode(p.read_bytes()).decode()
    except Exception:
        return None
    cls = ' class="inline-figure"' if alt.strip() else ""
    return f'<img{cls} src="{uri}" alt="{alt}">'


def _pt(v: float) -> str:
    return f"{v:g}pt"


def css_for(layout: Layout) -> str:
    return f"""
  @page {{ size: {layout.page_size}; margin: {layout.page_margin}; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; padding: 24px; background: {layout.page_bg};
    font-family: "Microsoft YaHei", "微软雅黑", "SimSun", sans-serif;
    color: #1b1f27; font-size: {_pt(layout.body_pt)}; line-height: {layout.line_height:g}; }}
  .doc {{ width: {layout.doc_width}; min-height: {layout.doc_min_height}; margin: 0 auto;
    padding: {layout.page_margin}; background: {layout.paper_bg};
    box-shadow: 0 12px 40px rgba(0,0,0,.45); }}
  .page {{ break-before: page; page-break-before: always; }}
  .page + .page {{ border-top: 1px dashed #c9c9c9; margin-top: 18px; padding-top: 12px; }}
  h2 {{ font-size: {_pt(layout.heading_pt)}; font-weight: 700; margin: 0 0 10px;
    padding-bottom: 5px; border-bottom: 1px solid #999; break-after: avoid; }}
  h3 {{ font-size: {_pt(layout.subheading_pt)}; font-weight: 700; margin: 14px 0 8px; break-after: avoid; }}
  p {{ margin: 0 0 9px; orphans: 2; widows: 2; }}
  ul, ol {{ margin: 0 0 10px; padding-left: 1.7em; }}
  li {{ margin: 2px 0; }}
  /* 跨页时不要把一段劈成两半：整块（答案行、解析段、列表项）要么整段留在
     上一页，要么整段移到下一页。以前没有这条，撑过一页的题会把一句话切成
     上下两页，读起来像断句。 */
  p, li, blockquote, .answer-line {{ break-inside: avoid; page-break-inside: avoid; }}
  .analysis p {{ break-inside: avoid; page-break-inside: avoid; }}
  strong {{ font-weight: 700; }}
  em {{ font-style: italic; }}
  blockquote {{ margin: 12px 0 0; padding: 5px 0 0 10px;
    border-left: 2px solid #888; color: #444; font-size: {_pt(layout.quote_pt)}; }}
  code {{ font-family: Consolas, "Courier New", monospace; font-size: .92em;
    border: 1px solid #ccc; padding: 0 2px; }}
  pre {{ border: 1px solid #ccc; padding: 8px; white-space: pre-wrap; }}
  table {{ border-collapse: collapse; width: 100%; margin: 6px 0 12px; font-size: .92em; }}
  th, td {{ border: 1px solid #8d8d8d; padding: 4px 7px; text-align: left; vertical-align: top; }}
  th {{ font-weight: 700; }}
  /* 表格跨页时：整行不切开，表头在下一页重复一次，否则续页的表会没有列名 */
  tr {{ break-inside: avoid; page-break-inside: avoid; }}
  thead {{ display: table-header-group; }}
  img {{ max-width: 100%; height: auto; }}
  img.inline-figure {{ display: block; margin: 10px auto; max-height: 230px;
    border: 1px solid #ddd; }}
  hr {{ border: 0; border-top: 1px solid #ccc; margin: 14px 0; }}
  .answer-line {{ font-size: {_pt(layout.answer_pt)}; }}
  .analysis {{ font-size: {_pt(layout.analysis_pt)}; line-height: 1.6; }}
  .analysis p {{ margin: 0 0 10px; }}
  sub, sup {{ font-size: .8em; }}
  .katex {{ font-size: 1.06em; white-space: nowrap; }}
  .katex-display {{ margin: 8px 0; }}
  .katex .mord, .katex .mbin, .katex .mrel {{ white-space: nowrap; }}
  @media print {{
    body {{ background: #fff; padding: 0; }}
    .doc {{ width: auto; min-height: 0; margin: 0; padding: 0;
      background: transparent; box-shadow: none; }}
    .page + .page {{ border-top: 0; margin-top: 0; padding-top: 0; }}
  }}
"""


def doc_head(title: str, layout: Layout, extra_css: str = "") -> str:
    return (
        '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="UTF-8">\n'
        f'<title>{title}</title>\n<style>\n' + css_for(layout) + "\n" + extra_css + "\n</style>\n"
        '</head>\n<body>\n'
    )


def inline_md(text: str) -> str:
    """极简行内 Markdown；$...$ 公式与图片先摘出来，避免被加粗/代码规则改写。"""
    spans: List[str] = []
    imgs: List[str] = []

    def hold(m):
        spans.append(m.group(0))
        return f"\x00{len(spans) - 1}\x00"

    def hold_img(m):
        tag = img_tag(m.group(2), m.group(1))
        imgs.append(tag if tag else m.group(0))
        return f"\x01{len(imgs) - 1}\x01"

    text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", hold_img, text)
    text = re.sub(r"\$\$[\s\S]+?\$\$|\$[^$\n]+?\$", hold, text)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    for i, s in enumerate(spans):
        s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        # 行内公式里的 `![…](…)` 不该被当图片，也不会成为占位符，无需处理
        text = text.replace(f"\x00{i}\x00", s)
    for i, tag in enumerate(imgs):
        text = text.replace(f"\x01{i}\x01", tag)
    return text


def is_table_sep(line: str) -> bool:
    return bool(re.match(r"^\|?[\s:-]*-{3,}[\s:|-]*\|?$", line)) and "-" in line


def split_row(line: str) -> List[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def normalize_md(md_text: str) -> tuple:
    """把常见的写法偏差就地修正，返回 (修正后的文本, 修正说明列表)。

    这些偏差以前要么被 lint 判死、要么静默丢内容，每踩一次就要重写草稿再
    重渲一轮。能无歧义改对的就不该让作者重来：

      - `$$...$$`、`\\[...\\]`、`\\(...\\)` 一律收敛为行内 `$...$`
      - `**解析：**` 这类把冒号包进星号的标签改成 `**解析**：`
      - 标题里的 `$` 去掉（标题按纯文字输出，留着只会变成源码）
      - 以 `A．` 开头、没写 `- ` 的选项补上列表前缀

    只改内存里的文本，不写回草稿文件；函数是幂等的，重复调用不会再改。
    """
    fixes: List[str] = []
    out = md_text

    n = len(re.findall(r"\$\$[\s\S]+?\$\$", out))
    if n:
        out = re.sub(r"\$\$([\s\S]+?)\$\$",
                     lambda m: "$" + re.sub(r"\s*\n\s*", " ", m.group(1)).strip() + "$", out)
        fixes.append(f"{n} 处 `$$…$$` 改成行内 `$…$`")

    for pat, label in ((r"\\\[([\s\S]+?)\\\]", "\\[ \\]"), (r"\\\(([\s\S]+?)\\\)", "\\( \\)")):
        got = re.findall(pat, out)
        if got:
            out = re.sub(pat, lambda m: "$" + m.group(1).strip() + "$", out)
            fixes.append(f"{len(got)} 处 `{label}` 改成 `$…$`")

    new, k = re.subn(r"\*\*(答案|解析|高中范围|关键结果)\s*[：:]\s*\*\*\s*[：:]?\s*",
                     lambda m: f"**{m.group(1)}**：", out)
    if k:
        out = new
        fixes.append(f"{k} 处标签写法归一（冒号移出星号）")

    lines = out.splitlines()
    touched = 0
    for i, ln in enumerate(lines):
        if ln.startswith("## ") and "$" in ln:
            lines[i] = ln.replace("$", "")
            touched += 1
    if touched:
        out = "\n".join(lines)
        fixes.append(f"{touched} 个标题里的 `$` 已去掉（标题按纯文字输出）")

    lines = out.splitlines()
    fixed = 0
    for i, ln in enumerate(lines):
        s = ln.strip()
        if len(s) <= 60 and re.match(r"^[A-DＡ-Ｄ][．.、]\s*\S", s) and not s.startswith("- "):
            lines[i] = "- " + s
            fixed += 1
    if fixed:
        out = "\n".join(lines)
        fixes.append(f"{fixed} 行选项补上 `- ` 前缀")

    return out, fixes


def _strip_answer_label(line: str) -> str:
    """剥掉答案标签，兼容 `**答案**：x`、`**答案：**x`、`**答案**` 三种写法。

    旧正则只剥 `**答案`，遇到 `**答案**：` 会留下一个冒号，
    成品里就成了「答案：：」。
    """
    v = re.sub(r"^\*\*答案\s*[：:]\s*\*\*", "", line)
    v = re.sub(r"^\*\*答案\*\*", "", v)
    v = re.sub(r"^[：:]\s*", "", v)
    return v.replace("**", "").strip()


def parse_answers(md_text: str) -> List[dict]:
    """按 `## 标题` 切题块。

    `## 第 N 题（题型）` 是常规形态；`## 追问：…` 这类无编号的补充块
    同样单独成块（例如题目旁边手写的第二问），不会被并进上一题。
    """
    parts = re.split(r"(?=^## )", md_text, flags=re.M)
    blocks = []
    for part in parts:
        heading = re.match(r"^## (.+?)\s*\n", part)
        if not heading:
            continue
        title_text = heading.group(1).strip()
        head = re.match(r"第\s*(\d+)\s*题[（(]([^）)]+)[）)]", title_text)
        block = {"num": head.group(1) if head else None,
                 "qtype": head.group(2) if head else None,
                 "heading": title_text,
                 "stem": [], "opts": [], "answer": [], "key": "", "why": [],
                 "quotes": [], "scope": ""}
        mode = "stem"
        raw_lines = part[heading.end():].splitlines()
        i = 0
        while i < len(raw_lines):
            raw = raw_lines[i]
            i += 1
            line = raw.strip()
            if not line or line == "---":
                continue
            # 旧草稿可能残留「易错点」段落，按最新要求一律不输出
            if re.match(r"^(\*\*)?(【)?易错点", line):
                continue
            if line.startswith("> "):
                block["quotes"].append(line[2:].strip())
                continue
            if line.startswith("- ") and mode in ("stem", "opts"):
                block["opts"].append(line[2:])
                mode = "opts"
                continue
            if line.startswith("**答案"):
                val = _strip_answer_label(line)
                if val:
                    block["answer"].append(val)
                mode = "ans"
                continue
            if line.startswith("**关键结果**"):
                block["key"] = re.sub(r"^\*\*关键结果\*\*[：:]\s*", "", line).strip()
                mode = "key"
                continue
            if line.startswith("**解析**"):
                # `**解析**：正文` 这种写在标签同一行的草稿很常见，
                # 旧逻辑只切模式、把冒号后的正文整段丢掉，成品里解析凭空缺一段。
                rest = re.sub(r"^\*\*解析\*\*[：:]\s*", "", line).strip()
                if rest:
                    block["why"].append(rest)
                mode = "why"
                continue
            if line.startswith("**高中范围**"):
                block["scope"] = re.sub(r"^\*\*高中范围\*\*[：:]\s*", "", line).strip()
                mode = "scope"
                continue
            # 表格：当前行以 | 开头、下一行是分隔行（|---|）
            if line.startswith("|") and i < len(raw_lines) and is_table_sep(raw_lines[i].strip()):
                rows = [split_row(line)]
                i += 1
                while i < len(raw_lines) and raw_lines[i].strip().startswith("|"):
                    rows.append(split_row(raw_lines[i].strip()))
                    i += 1
                block["why"].append({"kind": "table", "rows": rows})
                continue

            # 无序列表（非选项区的 `- `）
            if line.startswith("- ") and mode not in ("stem", "opts"):
                items = [line[2:].strip()]
                while i < len(raw_lines) and raw_lines[i].strip().startswith("- "):
                    items.append(raw_lines[i].strip()[2:].strip())
                    i += 1
                block["why"].append({"kind": "ul", "items": items})
                continue

            # 有序列表 `1. ` / `1、`
            m = re.match(r"^\d+[.、]\s*(.+)$", line)
            if m and mode not in ("stem", "opts"):
                items = [m.group(1).strip()]
                while i < len(raw_lines):
                    m2 = re.match(r"^\d+[.、]\s*(.+)$", raw_lines[i].strip())
                    if not m2:
                        break
                    items.append(m2.group(1).strip())
                    i += 1
                block["why"].append({"kind": "ol", "items": items})
                continue

            if mode == "stem":
                block["stem"].append(line)
            elif mode == "ans":
                block["answer"].append(line)
            elif mode == "why":
                block["why"].append(line)
        blocks.append(block)
    return blocks


def render_block(b: dict, title: str = "") -> str:
    head = f'第 {b["num"]} 题（{b["qtype"]}）' if b.get("num") else inline_md(b["heading"])
    if title:
        head = f'{inline_md(title)} · {head}'
    out = ['<section class="page">', f'  <h2>{head}</h2>']
    if b["stem"]:
        out.append(f'  <p>{inline_md(" ".join(b["stem"]))}</p>')
    if b["opts"]:
        out.append('  <ul>' + ''.join(f'<li>{inline_md(o)}</li>' for o in b["opts"]) + '</ul>')
    ans = [a for a in b["answer"] if a] or [""]
    out.append(f'  <p class="answer-line"><strong>答案：</strong>{inline_md(ans[0])}</p>')
    for extra in ans[1:]:
        out.append(f'  <p class="answer-line">{inline_md(extra)}</p>')
    if b["key"]:
        out.append(f'  <p><strong>关键结果：</strong>{inline_md(b["key"])}</p>')
    out.append('  <h3>解析</h3>')
    out.append('  <div class="analysis">')
    for w in b["why"]:
        if isinstance(w, dict):
            kind = w.get("kind")
            if kind == "table":
                head, *body = w["rows"]
                out.append('    <table><thead><tr>' +
                           ''.join(f'<th>{inline_md(c)}</th>' for c in head) +
                           '</tr></thead><tbody>')
                for r in body:
                    out.append('      <tr>' + ''.join(f'<td>{inline_md(c)}</td>' for c in r) + '</tr>')
                out.append('    </tbody></table>')
            elif kind == "ul":
                out.append('    <ul>' + ''.join(f'<li>{inline_md(x)}</li>' for x in w["items"]) + '</ul>')
            elif kind == "ol":
                out.append('    <ol>' + ''.join(f'<li>{inline_md(x)}</li>' for x in w["items"]) + '</ol>')
            continue
        out.append(f'    <p>{inline_md(w)}</p>')
    out.append('  </div>')
    for q in b.get("quotes", []):
        out.append(f'  <blockquote>{inline_md(q)}</blockquote>')
    if b["scope"]:
        out.append(f'  <blockquote>高中范围：{inline_md(b["scope"])}</blockquote>')
    out.append('</section>')
    return '\n'.join(out) + '\n'


def render_body(blocks: Iterable[dict], title: str = "") -> str:
    body = PAGE_MARK.join(render_block(b, title) for b in blocks)
    return '<div class="doc">\n' + body + '\n</div>'


def read_title(md_text: str) -> str:
    lines = md_text.splitlines()
    if lines and lines[0].startswith("# "):
        return lines[0][2:].strip()
    return ""


def lint_md(md_text: str) -> tuple:
    """渲染前体检草稿：把会被静默吞掉/漏渲染的写法一次性报出来。

    这些坑以前都是渲染完、跑 check、再截图才发现的，每踩一次就要多一轮
    render（3～4 秒）+ 一次人工核对。放到渲染前，改一稿就能全改对。

    返回 (fatal, warn)：fatal 会直接让成品结构坏掉，渲染也是白渲；
    warn 只是提醒，成品仍可交付。
    """
    fatal: List[str] = []
    warn: List[str] = []
    lines = md_text.splitlines()

    heads = [(i + 1, ln[3:].strip()) for i, ln in enumerate(lines) if ln.startswith("## ")]
    if not heads:
        fatal.append("没有 `## 标题` 行，一个题块都切不出来")
    for no, h in heads:
        if "$" in h:
            fatal.append(f"第 {no} 行标题里有 `$`：标题按纯文字输出，公式不会渲染，成品会残留 $ 源码")

    for i, ln in enumerate(lines, 1):
        s = ln.strip()
        # `**答案：**` 是历史写法，_strip_answer_label 专门兼容过，能正常解析，
        # 所以只提醒统一；解析/高中范围/关键结果 的冒号包在 ** 里就真的读不到，
        # 内容会被当成题干或直接丢掉，必须判死。
        if re.match(r"^\*\*答案\s*[：:]\s*\*\*", s):
            warn.append(f"第 {i} 行 `**答案：**` 能解析，但建议统一写成 `**答案**：`")
        for lab in ("解析", "高中范围", "关键结果"):
            if re.match(r"^\*\*" + lab + r"\s*[：:]\s*\*\*", s):
                fatal.append(f"第 {i} 行 `**{lab}：**` 把冒号包进了 `**`，该标签不会被识别（"
                             f"内容会被当成题干），要写成 `**{lab}**：`")
        if re.match(r"^(\*\*)?(【)?易错点", s):
            warn.append(f"第 {i} 行有「易错点」段落，按最新要求会被丢弃")
        if re.match(r"^[A-DＡ-Ｄ][．.、]", s):
            warn.append(f"第 {i} 行像选项但没写 `- ` 前缀（`- A．…`），会被并进题干")

    # 单个 $ 落单：多半是公式没闭合，KaTeX 会渲染失败或吞掉后半段
    for i, ln in enumerate(lines, 1):
        bare = re.sub(r"\$\$[\s\S]+?\$\$", "", ln)
        if bare.count("$") % 2:
            warn.append(f"第 {i} 行 `$` 个数是奇数，可能有公式没闭合")

    if not any(re.match(r"^\s*\*\*答案", ln) for ln in lines):
        fatal.append("整篇没有 `**答案**` 行")
    if not any(re.match(r"^\s*\*\*解析", ln) for ln in lines):
        fatal.append("整篇没有 `**解析**` 行")
    if not any(re.match(r"^\s*\*\*高中范围", ln) for ln in lines):
        warn.append("整篇没有 `**高中范围**` 行，check 会判缺内容")
    return fatal, warn


def usable_height_px(layout: Layout) -> float:
    """一页能放多高的内容（像素，96dpi）。

    A4 横版高 210mm，减掉上下页边距。屏幕上 section 宽度已被 .doc 的
    左右 padding 压到与打印一致，所以高度可以直接拿打印值比。
    """
    def mm(v: str) -> float:
        m = re.match(r"([\d.]+)\s*(mm|cm|in|pt|px)?", v.strip())
        if not m:
            return 0.0
        n, unit = float(m.group(1)), (m.group(2) or "mm")
        return {"mm": n, "cm": n * 10, "in": n * 25.4,
                "pt": n * 25.4 / 72, "px": n * 25.4 / 96}[unit]

    page_h = 210.0 if "landscape" in layout.page_size.lower() else 297.0
    vals = [mm(x) for x in layout.page_margin.split()] or [0.0]
    if len(vals) == 1:
        top = bottom = vals[0]
    elif len(vals) == 2:
        top, bottom = vals
    else:
        top, bottom = vals[0], vals[2]
    return max(0.0, (page_h - top - bottom) / 25.4 * 96)


def build_html(md_text: str, title: str, layout: Layout,
               prerendered_body: Optional[str] = None,
               extra_css: str = "") -> str:
    # 兜底再修一次：render 走 _render_one 时已经修过并打印了明细，
    # 这里照顾直接调 build_html 的入口（幂等，不会二次改坏）。
    md_text, _ = normalize_md(md_text)
    blocks = parse_answers(md_text)
    body = prerendered_body if prerendered_body is not None else render_body(blocks, title)
    return doc_head(title, layout, extra_css) + body + DOC_TAIL
