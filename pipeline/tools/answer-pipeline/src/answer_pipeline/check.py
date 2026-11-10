"""成品自检与发布。

这套校验是整条管线的闸门：放进 output 的每一份都必须过这一关。
它不依赖浏览器，只看结构，因此可以在任何环境下快速跑完。
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import List, Optional, Tuple

from .paths import Config

# 必须出现的语义标签：缺任何一个都说明草稿结构跑偏了
REQUIRED_LABELS = ("答案：", "解析", "高中范围")

RULE_COUNT = 11


def math_expected(md_path: Optional[str]) -> int:
    """统计草稿里的公式个数，用于核对渲染后节点数是否够。"""
    if not md_path:
        return 0
    try:
        text = Path(md_path).read_text(encoding="utf-8")
    except Exception:
        return 0
    return len(re.findall(r"\$\$[\s\S]+?\$\$|\$[^$\n]+?\$", text))


def check_html(html_path, md_path: Optional[str] = None) -> Tuple[bool, List[str], dict]:
    p = Path(html_path)
    problems: List[str] = []
    stats: dict = {}
    if not p.exists():
        return False, [f"文件不存在：{p}"], stats

    raw = p.read_text(encoding="utf-8")
    stats["kb"] = round(len(raw.encode("utf-8")) / 1024, 1)
    body = re.sub(r"<style[\s\S]*?</style>", "", raw)
    text = re.sub(r"<[^>]+>", "", body)
    stats["pages"] = len(re.findall(r'<section class="page">', body))
    stats["h2"] = body.count("<h2>")
    stats["katex"] = body.count('class="katex"')
    stats["katex_error"] = body.count("katex-error")
    stats["inline_fonts"] = raw.count("data:font/woff2")

    if stats["kb"] < 2:
        problems.append(f"文件过小（{stats['kb']} KB），疑似渲染失败")
    if stats["pages"] < 1:
        problems.append('没有题块（<section class="page"> 数为 0）')
    if stats["h2"] != stats["pages"]:
        problems.append(f"题块 {stats['pages']} 个但题目标题 {stats['h2']} 个，不匹配")
    if "break-before: page" not in raw:
        problems.append("缺少强制分页规则（break-before: page），打印会挤在一起")
    if stats["katex_error"]:
        problems.append(f"有 {stats['katex_error']} 个公式渲染报错")
    left = re.findall(r"\$[^$\n]{1,120}\$", text)
    if left:
        problems.append(f"有 {len(left)} 处未渲染的 $...$ 源码，例：{left[0][:40]}")
    expect = math_expected(md_path)
    stats["math_expected"] = expect
    if expect and stats["katex"] < expect:
        problems.append(f"草稿有 {expect} 个公式，成品只有 {stats['katex']} 个渲染节点")
    if stats["katex"] and not stats["inline_fonts"]:
        problems.append("公式字体未内联，换机器打开可能变形")
    if re.search(r"<script\b", body):
        problems.append("包含 <script>，打印时可能因脚本未执行丢内容")
    if re.search(r"<link\b|@import|url\(fonts/", raw):
        problems.append("存在外部样式或字体引用（<link>/@import/url(fonts/)")
    ext = [m.group(0) for m in re.finditer(r"https?://[^\s\"'()]+", raw)
           if "www.w3.org" not in m.group(0)]
    if ext:
        problems.append(f"存在外部链接，例：{ext[0]}")
    if "易错点" in text:
        problems.append("出现「易错点」字样，与最新要求不符")
    for k in REQUIRED_LABELS:
        if k not in text:
            problems.append(f"缺少「{k}」内容")

    stats["rules"] = RULE_COUNT
    return not problems, problems, stats


def publish(cfg: Config, html_path, md_path: Optional[str] = None,
            quiet: bool = False) -> Optional[Path]:
    """校验通过才把缓存成品移入 output；返回最终路径，失败返回 None。"""
    src = Path(html_path).resolve()
    ok, problems, stats = check_html(src, md_path)
    if not ok:
        if not quiet:
            print(f"[check] 未通过，{src.name} 仍留在缓存：")
            for x in problems:
                print(f"  - {x}")
        return None
    if src.parent == cfg.output_dir.resolve():
        if not quiet:
            print(f"[publish] 已在 output 中，跳过移动：{src}")
        return src
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    dest = cfg.output_dir / src.name
    if dest.exists():
        dest.unlink()
    shutil.move(str(src), str(dest))
    if not quiet:
        print(f"[publish] 校验通过 -> {dest}"
              f"（{stats['pages']} 题块 / {stats['katex']} 公式 / {stats['kb']} KB）")
    return dest
