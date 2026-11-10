"""answer-pipeline：把题目素材变成打印就绪的横版 HTML 答案。

设计上的三条边界：
1. 本包不读题、不作答——审题与写答案由上层（人或 Agent）完成；
2. 渲染只写缓存目录，成品必须过结构自检才允许进 output；
3. 产物是单文件、零脚本、零外部资源的 HTML，断网也能完整打印。
"""
from .paths import Config, Layout, find_root
from .render import parse_answers, render_block, build_html
from .check import check_html, publish
from .queue import scan, init_baseline, pending_records, mark_done, mark_skip

__version__ = "0.1.0"

__all__ = [
    "Config", "Layout", "find_root",
    "parse_answers", "render_block", "build_html",
    "check_html", "publish",
    "scan", "init_baseline", "pending_records", "mark_done", "mark_skip",
    "__version__",
]
