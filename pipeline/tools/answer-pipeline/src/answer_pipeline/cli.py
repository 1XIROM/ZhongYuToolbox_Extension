"""命令行入口：answer-pipeline 的十个子命令。

退出码约定：0 成功 / 1 校验未通过 / 2 用法或文件缺失 / 3 渲染环境不可用。
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

from . import queue as q
from .check import check_html, publish
from .latex import ChromeNotFound, KatexAssets, find_chrome, prerender
from .paths import Config, find_root
from .render import build_html, css_for, parse_answers, read_title, render_body

ACTION_HINT = (
    "[ACTION] 请立即处理待答队列，无需等待用户发言："
    "运行 status 查看任务 -> "
    "kind=folder 时读取 materials 里的截图/图片用图像识别读题"
    "（resources.json 只是清单，.mdb/.bin 忽略，不要联网下载），"
    "若图中已含『答案：』『解析』『关键结果』等成品标记，"
    "说明是 output 成品被回灌进 input，禁止重复作答，"
    "改用 skip 命令标记跳过并记录原因；"
    "kind=file 时先读 PDF 文本 -> "
    "确认是原始题目后按高中范围逐题作答写 Markdown"
    "（公式一律写成 $...$ 行内 LaTeX，不要写易错点段落）-> "
    "render 先把成品写进 staging 缓存目录（可加 --title）-> "
    "check 自检（题块数、公式节点与报错、残留源码、外部依赖、易错点）-> "
    "只有 publish 校验通过才移入 output，不通过就修草稿重新 render -> "
    "done 标记完成（若 --html 仍在缓存，done 会自动校验并发布）-> "
    "重新挂一个 wait 哨兵"
)


class Reporter:
    """同时写终端和状态目录下的日志文件。"""

    def __init__(self, cfg: Config, quiet: bool = False, log: bool = True):
        self.cfg = cfg
        self.quiet = quiet
        self.log_path = None
        if log:
            cfg.logs.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%d")
            self.log_path = cfg.logs / f"{stamp}.log"

    def __call__(self, msg: str = "", force: bool = False) -> None:
        if not self.quiet or force:
            print(msg, flush=True)
        if self.log_path:
            try:
                with self.log_path.open("a", encoding="utf-8") as f:
                    f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
            except OSError:
                pass


def _cfg(args) -> Config:
    return Config.build(root=args.root, input_dir=args.input, output_dir=args.output,
                        state_dir=args.state, config_file=args.config)


def cmd_init(cfg: Config, args, out: Reporter) -> int:
    n_folder, n_pdf = q.init_baseline(cfg)
    out(f"[init] 基线已记录：{n_folder} 个文件夹、{n_pdf} 份散装 PDF。"
        f"之后新增素材才会触发答题。")
    return 0


def cmd_status(cfg: Config, args, out: Reporter) -> int:
    data = q.Manifest(cfg).load()
    pending = q.pending_records(cfg)
    n_folder = sum(1 for v in data["processed"].values() if v.get("kind") == "folder")
    out(f"已记录素材：{len(data['processed'])} 个（文件夹 {n_folder} / "
        f"散装 PDF {len(data['processed']) - n_folder}）")
    out(f"待作答任务：{len(pending)} 条")
    for x in pending:
        out(f"  - [{x.get('kind', 'file')}] {q.label_of(x)}")
        src = cfg.abs(x.get("source_dir"))
        if src:
            out(f"    素材目录 {src}")
        main = cfg.abs(x.get("primary_file"))
        out(f"    主素材 {Path(main).name if main else '无'}，"
            f"共 {len(x.get('materials', []))} 个可读文件")
        out(f"    答案写到 {cfg.abs(x.get('answers_md'))}")
        staged = cfg.abs(x.get("staged_html"))
        if staged:
            out(f"    先渲染到缓存 {staged}")
            out(f"    校验通过后发布到 {cfg.abs(x.get('output_html'))}")
        else:
            out(f"    成品写到 {cfg.abs(x.get('output_html'))}")
    return 0


def cmd_scan(cfg: Config, args, out: Reporter) -> int:
    added = q.scan_locked(cfg)
    for r in added:
        kind = "文件夹" if r["kind"] == "folder" else "PDF"
        out(f"[new] {kind} {q.label_of(r)} -> {len(r['materials'])} 个可读素材 -> "
            f"{Path(r['answers_md']).name}")
    out(f"[scan] 新增 {len(added)} 个任务，队列累计 "
        f"{len(list(cfg.queue.glob('*.json')))} 条。")
    return 0


def cmd_watch(cfg: Config, args, out: Reporter) -> int:
    out(f"[watch] 开始监测 {cfg.input_dir} 下的新文件夹/新 PDF（每 {args.interval} 秒）")
    while True:
        try:
            for r in q.scan_locked(cfg):
                out(f"[{q.now()}] 检测到新任务：{q.label_of(r)}，已写入队列")
        except Exception as e:
            out(f"[{q.now()}] 扫描出错：{e}")
        time.sleep(args.interval)


def cmd_wait(cfg: Config, args, out: Reporter) -> int:
    """一次性哨兵：发现新素材，写入队列后立刻退出，用来唤醒上层。"""
    out(f"[wait] 等待 {cfg.input_dir} 下出现新文件夹/新 PDF（每 {args.interval} 秒检查）")
    deadline = None if args.timeout <= 0 else time.monotonic() + args.timeout
    while True:
        added = q.scan_locked(cfg)
        if added:
            time.sleep(2)
            added += q.scan_locked(cfg)
            for r in added:
                out(f"[new] [{r['kind']}] {q.label_of(r)} -> 已写入待答队列")
            out(f"[wait] 检测到 {len(added)} 个新任务，哨兵退出以触发任务完成通知")
            out(ACTION_HINT)
            return 0
        if deadline is not None and time.monotonic() >= deadline:
            out("[wait] 超时未发现新任务，正常退出")
            return 0
        time.sleep(args.interval)


def cmd_render(cfg: Config, args, out: Reporter) -> int:
    src = Path(args.markdown).resolve()
    if not src.exists():
        out(f"[render] 找不到答案文件：{src}")
        return 2
    text = src.read_text(encoding="utf-8")
    title = args.title or read_title(text)
    blocks = parse_answers(text)
    if not blocks:
        out("[render] 未解析到任何题块，请检查 Markdown 的 `## 标题` 格式。")
        return 2

    body = render_body(blocks, title)
    extra_css, note = "", ""
    if not args.no_latex:
        assets = KatexAssets(cache_dir=cfg.latex_cache, katex_dir=cfg.katex_dir)
        try:
            body = prerender(body, css_for(cfg.layout), chrome=cfg.chrome_path,
                             assets=assets, work_dir=cfg.latex_cache)
            extra_css = assets.css()
            st = prerender.last_stats
            note = f"，LaTeX 公式 {st['math']} 个（失败 {st['failed']} 个）"
            if st["failed"]:
                out(f"[render] 警告：{st['failed']} 个公式渲染失败，已原样保留")
        except ChromeNotFound as e:
            if not args.allow_text_fallback:
                out(f"[render] 中止：{e}")
                out("[render] 若确实要在没有浏览器的环境生成（公式会退化为源码），"
                    "加 --allow-text-fallback。")
                return 3
            out(f"[render] 警告：{e}；已按要求退化为纯文本公式")
        except Exception as e:
            if not args.allow_text_fallback:
                out(f"[render] 中止：公式预渲染失败（{e}）")
                return 3
            out(f"[render] 警告：公式预渲染失败，退化为纯文本公式：{e}")

    stem = args.name or src.stem.replace("_答案", "")
    target = cfg.output_dir if args.direct else cfg.staging
    target.mkdir(parents=True, exist_ok=True)
    dest = target / f"{stem}_答案横版.html"
    html = build_html(text, title, cfg.layout, prerendered_body=body, extra_css=extra_css)
    dest.write_text(html, encoding="utf-8")

    if args.direct:
        out(f"[render] {len(blocks)} 道题 -> {dest}{note}（--direct，跳过缓存与校验）")
    else:
        out(f"[render] {len(blocks)} 道题 -> {dest}{note}")
        out(f"[render] 已写入缓存目录（未发布）。"
            f"下一步：answer-pipeline publish \"{dest}\"")
    return 0


def cmd_check(cfg: Config, args, out: Reporter) -> int:
    p = Path(args.html)
    ok, problems, stats = check_html(p, args.md)
    out(f"[check] {p.name}")
    out(f"  题块 {stats.get('pages')} / 标题 {stats.get('h2')} / "
        f"公式节点 {stats.get('katex')}（草稿应有 {stats.get('math_expected')}）/ "
        f"报错 {stats.get('katex_error')} / 内联字体 {stats.get('inline_fonts')} / "
        f"{stats.get('kb')} KB")
    if ok:
        out("  结果：通过，可以 publish")
        return 0
    out("  结果：未通过")
    for x in problems:
        out(f"   - {x}")
    return 1


def cmd_publish(cfg: Config, args, out: Reporter) -> int:
    return 0 if publish(cfg, args.html, args.md) else 1


def cmd_done(cfg: Config, args, out: Reporter) -> int:
    rec = q.mark_done(cfg, args.queue, args.md, args.html, publish_fn=publish)
    if rec is None:
        out(f"[done] 找不到队列记录：{args.queue}")
        return 2
    if "_publish_failed" in rec:
        out("[done] 未标记完成：成品未通过校验，仍留在缓存，请修复草稿后重新 render。")
        return 1
    out(f"[done] 已标记完成：{q.label_of(rec)} -> {cfg.abs(rec.get('output_html'))}")
    return 0


def cmd_skip(cfg: Config, args, out: Reporter) -> int:
    rec = q.mark_skip(cfg, args.queue, args.reason)
    if rec is None:
        out(f"[skip] 找不到队列记录：{args.queue}")
        return 2
    out(f"[skip] 已跳过：{q.label_of(rec)} | 原因：{args.reason}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=None, help="项目根目录（默认自动探测）")
    common.add_argument("--input", default=None, help="素材目录（默认 <root>/input）")
    common.add_argument("--output", default=None, help="成品目录（默认 <root>/output）")
    common.add_argument("--state", default=None, help="状态目录（默认 <root>/.workbuddy/auto）")
    common.add_argument("--config", default=None, help="配置文件路径（默认 <root>/answer-pipeline.json）")
    common.add_argument("-q", "--quiet", action="store_true", help="不打印过程信息")
    common.add_argument("--no-log", action="store_true", help="不写日志文件")

    ap = argparse.ArgumentParser(
        prog="answer-pipeline",
        description="题目素材 -> 待答队列 -> Markdown 答案 -> 横版 HTML / 打印就绪的成品",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", parents=[common], help="把现有素材记为基线，不触发答题")
    sub.add_parser("status", parents=[common], help="查看待答队列")
    sub.add_parser("scan", parents=[common], help="单次扫描新增文件夹或 PDF")

    w = sub.add_parser("watch", parents=[common], help="常驻监测")
    w.add_argument("--interval", type=float, default=2.0, help="轮询间隔（秒）")

    s = sub.add_parser("wait", parents=[common], help="一次性哨兵，发现新素材即退出")
    s.add_argument("--interval", type=float, default=1.0, help="轮询间隔（秒）")
    s.add_argument("--timeout", type=float, default=0, help="超时秒数，0 表示一直等")

    r = sub.add_parser("render", parents=[common], help="Markdown 答案转横版 HTML（默认写入缓存）")
    r.add_argument("markdown")
    r.add_argument("--name", default=None, help="输出文件名前缀（默认取答案文件名）")
    r.add_argument("--title", default="", help="标题前缀，通常是试卷/笔记名称")
    r.add_argument("--no-latex", action="store_true", help="不预渲染 $...$ 公式")
    r.add_argument("--allow-text-fallback", action="store_true",
                   help="没有浏览器时仍生成（公式退化为源码，不推荐）")
    r.add_argument("--direct", action="store_true", help="直接写 output，跳过缓存与校验（不推荐）")

    c = sub.add_parser("check", parents=[common], help="校验缓存成品（只报告，不移动）")
    c.add_argument("html")
    c.add_argument("--md", default=None, help="对应草稿，用于核对公式数量")

    pb = sub.add_parser("publish", parents=[common], help="校验通过后把缓存成品移入 output")
    pb.add_argument("html")
    pb.add_argument("--md", default=None, help="对应草稿，用于核对公式数量")

    d = sub.add_parser("done", parents=[common], help="标记队列任务已完成")
    d.add_argument("queue")
    d.add_argument("--md", default=None)
    d.add_argument("--html", default=None)

    k = sub.add_parser("skip", parents=[common], help="标记无需作答（如成品页回灌）")
    k.add_argument("queue")
    k.add_argument("--reason", default="无需作答")
    return ap


HANDLERS = {
    "init": cmd_init, "status": cmd_status, "scan": cmd_scan, "watch": cmd_watch,
    "wait": cmd_wait, "render": cmd_render, "check": cmd_check,
    "publish": cmd_publish, "done": cmd_done, "skip": cmd_skip,
}


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    cfg = _cfg(args)
    out = Reporter(cfg, quiet=getattr(args, "quiet", False),
                   log=not getattr(args, "no_log", False))
    try:
        return HANDLERS[args.cmd](cfg, args, out)
    except KeyboardInterrupt:
        out("\n[中断] 已停止")
        return 130
    except Exception as e:
        out(f"[错误] {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
