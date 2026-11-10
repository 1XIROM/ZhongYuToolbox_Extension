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

import re
from pathlib import Path
from typing import Iterable, List, Optional

from .paths import Layout

PAGE_MARK = "\n"
DOC_TAIL = "\n</body>\n</html>\n"


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
  strong {{ font-weight: 700; }}
  em {{ font-style: italic; }}
  blockquote {{ margin: 12px 0 0; padding: 5px 0 0 10px;
    border-left: 2px solid #888; color: #444; font-size: {_pt(layout.quote_pt)}; }}
  code {{ font-family: Consolas, "Courier New", monospace; font-size: .92em;
    border: 1px solid #ccc; padding: 0 2px; }}
  pre {{ border: 1px solid #ccc; padding: 8px; white-space: pre-wrap; }}
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
    """极简行内 Markdown；$...$ 公式先摘出来，避免被加粗/代码规则改写。"""
    spans: List[str] = []

    def hold(m):
        spans.append(m.group(0))
        return f"\x00{len(spans) - 1}\x00"

    text = re.sub(r"\$\$[\s\S]+?\$\$|\$[^$\n]+?\$", hold, text)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    for i, s in enumerate(spans):
        s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(f"\x00{i}\x00", s)
    return text


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
                 "stem": [], "opts": [], "answer": [], "key": "", "why": [], "scope": ""}
        mode = "stem"
        for raw in part[heading.end():].splitlines():
            line = raw.strip()
            if not line or line == "---":
                continue
            # 旧草稿可能残留「易错点」段落，按最新要求一律不输出
            if re.match(r"^(\*\*)?(【)?易错点", line):
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
                mode = "why"
                continue
            if line.startswith("**高中范围**"):
                block["scope"] = re.sub(r"^\*\*高中范围\*\*[：:]\s*", "", line).strip()
                mode = "scope"
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
        out.append(f'    <p>{inline_md(w)}</p>')
    out.append('  </div>')
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


def build_html(md_text: str, title: str, layout: Layout,
               prerendered_body: Optional[str] = None,
               extra_css: str = "") -> str:
    blocks = parse_answers(md_text)
    body = prerendered_body if prerendered_body is not None else render_body(blocks, title)
    return doc_head(title, layout, extra_css) + body + DOC_TAIL
