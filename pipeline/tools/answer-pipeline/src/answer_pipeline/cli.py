"""命令行入口：answer-pipeline 的子命令。

退出码约定：0 成功 / 1 校验未通过 / 2 用法或文件缺失 / 3 渲染环境不可用。
"""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from . import queue as q
from .check import check_html, publish
from .latex import ChromeNotFound, KatexAssets, find_chrome, prerender
from .paths import Config, find_root
from .render import (build_html, css_for, lint_md, normalize_md, parse_answers,
                     read_title, render_body, usable_height_px)

ACTION_HINT = (
    "[ACTION] 请立即处理待答队列，无需等待用户发言："
    "运行 status 查看任务 -> "
    "待答 ≥2 条时并行派子 agent（每个负责一条：读图 + 写草稿，允许自己跑 render --check，"
    "禁止 publish/done/改队列），再一条命令批量复核；只有 1 条就直接做 -> "
    "kind=folder 时读「主素材」那张图（带 uuid 的 jpg/png 是题目原图，字大清楚；"
    "00_screenshot.png 只是整页预览、题目被缩小，容易读错小字和图形刻度，没有原图时才用它），"
    "用图像识别读题，图形条件（波形折点、角度、坐标刻度）要裁出局部放大核准"
    "（resources.json 只是清单，.mdb/.bin 忽略，不要联网下载），"
    "若图中已含『答案：』『解析』『关键结果』等成品标记，"
    "说明是 output 成品被回灌进 input，禁止重复作答，"
    "改用 skip 命令标记跳过并记录原因；"
    "kind=file 时先读 PDF 文本 -> "
    "确认是原始题目后按高中范围逐题作答写 Markdown"
    "（公式一律写成 $...$ 行内 LaTeX，不要写易错点段落）-> "
    "render --check 一条命令拿到全部结论：草稿体检、渲染、结构校验、每块高度；"
    "有「[lint] 阻断」就按提示改写（不会白渲染），"
    "块高只用来看「单页 / 跨第 2 页 / 超两页」，超两页才需要拆块或压缩，"
    "高度是实测值不要再截图量 -> "
    "只有 publish 校验通过才移入 output -> "
    "done 标记完成（若 --html 仍在缓存，done 会自动校验并发布）-> "
    "重新挂哨兵：watch --interval 1 --exit-on-new（有新任务即退出，用退出唤醒你；"
    "不要只挂 wait——wait 一次性，它退出后 watch 仍常驻，会出现「watch 打印了检测到新任务"
    "却没人处理」的空档）"
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
        prev = cfg.abs(x.get("preview_file"))
        names = Path(main).name if main else "无"
        if prev and main and Path(prev) != Path(main):
            names += f"（题目原图；整页预览 {Path(prev).name}）"
        out(f"    主素材 {names}，共 {len(x.get('materials', []))} 个可读文件")
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
    """监测 input 目录，把新素材写进队列。

    默认常驻不退出。加 --exit-on-new 时，一旦有活可干就退出——因为上层只能通过
    「后台任务结束」这个信号被唤醒，常驻进程永远不会给这个信号。于是会出现
    「watch 明明打印了『检测到新任务』，却没人来处理」的空档（2026-11-23 ds_76/ds_77
    就在这个空档里压了 40 分钟）。加了这个开关后，watch 既是哨兵也是叫醒器。
    """
    exit_on_new = getattr(args, "exit_on_new", False)
    out(f"[watch] 开始监测 {cfg.input_dir} 下的新文件夹/新 PDF（每 {args.interval} 秒）")

    if exit_on_new:
        try:
            backlog = len(q.pending_records(cfg))
        except Exception:
            backlog = 0
        if backlog:
            out(f"[watch] 启动时队列已有 {backlog} 条待答任务，直接退出以唤醒上层")
            out(ACTION_HINT)
            return 0

    errors = 0
    empty_hinted: set = set()
    dup_hinted: set = set()
    while True:
        try:
            found = list(q.scan_locked(cfg))
            errors = 0
        except Exception as e:  # 长跑哨兵不能因为一轮读写异常就整个退出
            errors += 1
            out(f"[{q.now()}] 扫描出错（第 {errors} 次）：{type(e).__name__}: {e}")
            if errors >= 5:
                out("[watch] 连续 5 轮失败，哨兵退出，请检查磁盘/权限或输入目录")
                return 1
            time.sleep(min(30, args.interval * 5))
            continue

        for r in found:
            out(f"[{q.now()}] 检测到新任务：{q.label_of(r)}，已写入队列")

        # 空目录会被 scan 跳过（避免文件还没拷完就被当任务），但要显式说一声，
        # 否则用户会以为哨兵没工作（2026-11-23 testfolder1 就是这么被误会的）。
        # 内容重复（同一张图复制到新文件夹）也在判定阶段被静默跳过，同样要说明。
        try:
            manifest_proc = q.Manifest(cfg).load().get("processed", {})
            own_paths = {v.get("file") for v in manifest_proc.values()}
            for d in q.input_folders(cfg):
                # 只对最近动过的目录做提示，否则会把上百个历史目录全念一遍
                try:
                    if time.time() - d.stat().st_mtime > 900:
                        continue
                except OSError:
                    continue
                files = q.folder_files(d)
                if not files:
                    if d.name in empty_hinted:
                        continue
                    empty_hinted.add(d.name)
                    out(f"[{q.now()}] 忽略空目录「{d.name}」："
                        f"里面没有图片或 PDF，放一张截图进去就会入队")
                    continue
                empty_hinted.discard(d.name)  # 补了文件就允许再次提示
                # 目录本身在清单里（已入队/已完成）属于正常，不提示
                if cfg.rel(d) in own_paths or d.name in dup_hinted:
                    continue
                hit = q.folder_already_seen(manifest_proc, d, files)
                if hit:
                    status = (manifest_proc.get(hit) or {}).get("status")
                    if status in ("done", "skipped", "baseline"):
                        dup_hinted.add(d.name)
                        out(f"[{q.now()}] 跳过「{d.name}」：内容与已处理素材相同"
                            f"（同一张图不会重复处理，换一张没处理过的才会入队）")
        except Exception:
            pass

        if found and exit_on_new:
            out(f"[watch] 新增 {len(found)} 条任务，退出以触发任务完成通知")
            out(ACTION_HINT)
            return 0

        time.sleep(args.interval)


def cmd_wait(cfg: Config, args, out: Reporter) -> int:
    """一次性哨兵：队列里出现新的待答任务就退出，用来唤醒上层。

    判据不能只看「这一轮自己扫到了新素材」：常驻的 watch 与它并行时，watch 可能先
    把素材记进 manifest，wait 再扫就只能看到 0 个新增，于是永远不退出、也永远叫不醒
    （2026-11-15 ds_36 就是这么卡住的）。所以改成盯「队列里的待答条数有没有涨」——
    不管是谁扫到的，只要队列涨了就退出。
    """
    out(f"[wait] 等待 {cfg.input_dir} 下出现新任务（每 {args.interval} 秒检查）")
    deadline = None if args.timeout <= 0 else time.monotonic() + args.timeout
    errors = 0
    try:
        last = len(q.pending_records(cfg))
    except Exception:
        last = 0
    if last:
        out(f"[wait] 启动时队列已有 {last} 条待答任务，无需等待，直接唤醒")
        out(ACTION_HINT)
        return 0
    while True:
        try:
            q.scan_locked(cfg)
            now_pending = len(q.pending_records(cfg))
            errors = 0
        except Exception as e:  # 长跑哨兵不能因为一轮读写异常就整个退出
            errors += 1
            out(f"[wait] 本轮扫描失败（第 {errors} 次）：{type(e).__name__}: {e}")
            if errors >= 5:
                out("[wait] 连续 5 轮失败，哨兵退出，请检查磁盘/权限或输入目录")
                return 1
            time.sleep(min(30, args.interval * 5))
            continue
        if now_pending > last:
            time.sleep(2)
            q.scan_locked(cfg)
            recs = q.pending_records(cfg)
            for r in recs:
                out(f"[new] [{r['kind']}] {q.label_of(r)} -> 已在待答队列")
            out(f"[wait] 队列新增 {now_pending - last} 条待答任务，"
                f"哨兵退出以触发任务完成通知")
            out(ACTION_HINT)
            return 0
        last = now_pending
        if deadline is not None and time.monotonic() >= deadline:
            out("[wait] 超时未发现新任务，正常退出")
            return 0
        time.sleep(args.interval)


def _report_layout(out, heights, limit: float, blocks) -> None:
    """把每块的实际高度和「还剩多少余量」报出来。

    以前这一步要靠无头浏览器截图再肉眼量，一轮两三分钟；
    现在顺手量完直接给结论：超了还差几行。

    规则是「每页至多一题，允许题内跨页」，所以超一页只是会跨到第 2 页，
    不是错误；一块撑过两页（> 2×limit）才是真的该拆该压。
    """
    if not heights:
        return
    # 711px 是版面算出来的上限，但打印实测 695px 就会溢出到第 2 页、
    # 678px 还是单页。所以另设一条 680px 的安全线：逼到安全线以内才算稳，
    # 落在安全线与上限之间属于「贴边」，压几行就能收回。
    safe = limit - 31
    out(f"[render] 排版 {len(heights)} 块 / 单页上限 {limit:.0f}px（安全线 {safe:.0f}px）")
    for i, h in enumerate(heights):
        pct = h / limit * 100 if limit else 0
        if h <= safe:
            out(f"  块{i + 1} {h}px（{pct:.0f}%）✓ 单页，余 {safe - h:.0f}px")
        elif h <= limit:
            need = max(1, round((h - safe) / 30))
            out(f"  块{i + 1} {h}px（{pct:.0f}%）⚠ 贴边：压实测会溢出到第 2 页，"
                f"压掉约 {need} 行可稳在单页")
        elif h <= limit + 60:
            need = max(1, round((h - limit) / 30))
            out(f"  块{i + 1} {h}px（{pct:.0f}%）→ 跨到第 2 页（只多 {h - limit:.0f}px），"
                f"压 {need} 行可收回单页")
        else:
            import math as _m
            pages = max(2, _m.ceil(h / limit))
            tail = h - limit * (pages - 1)
            out(f"  块{i + 1} {h}px（{pct:.0f}%）→ 占 {pages} 页"
                f"（末页用掉 {tail:.0f}px），跨页不限，不必强压")
    too_long = [i + 1 for i, h in enumerate(heights) if h > limit * 2]
    if too_long:
        names = "、".join(f"第 {i} 块" for i in too_long)
        out(f"[render] 警告：{names}超过两页，建议拆块或压缩")


def _render_one(cfg: Config, src: Path, title_override: str, args):
    """渲染一份草稿，返回 (退出码, 日志行)。

    结果用返回值而不是直接打印，是为了批量渲染时能把几份的输出按顺序拼回来
    —— 并发跑的时候谁先完成是不确定的，直接打印会错位。
    """
    log = []
    say = log.append

    if not src.exists():
        say(f"[render] 找不到答案文件：{src}")
        return 2, log
    text = src.read_text(encoding="utf-8")
    text, fixes = normalize_md(text)
    for f in fixes:
        say(f"[lint] 已自动修正：{f}")
    title = title_override or read_title(text)

    fatal, warn = lint_md(text)
    for w in warn:
        say(f"[lint] 提醒：{w}")
    if fatal:
        for f in fatal:
            say(f"[lint] 阻断：{f}")
        say("[render] 草稿有会让结构出错的问题，先修再渲染（省一次白渲染）。")
        return 2, log

    blocks = parse_answers(text)
    if not blocks:
        say("[render] 未解析到任何题块，请检查 Markdown 的 `## 标题` 格式。")
        return 2, log

    body = render_body(blocks, title)
    extra_css, note, heights = "", "", []
    if not args.no_latex or not args.no_measure:
        assets = KatexAssets(cache_dir=cfg.latex_cache, katex_dir=cfg.katex_dir)
        try:
            body, st = prerender(body, css_for(cfg.layout), chrome=cfg.chrome_path,
                                 assets=assets, work_dir=cfg.latex_cache,
                                 with_math=not args.no_latex,
                                 measure=not args.no_measure)
            heights = list(st.get("heights") or [])
            if not args.no_latex:
                extra_css = assets.css()
                note = f"，LaTeX 公式 {st['math']} 个（失败 {st['failed']} 个）"
                if st["failed"]:
                    say(f"[render] 警告：{st['failed']} 个公式渲染失败，已原样保留")
        except ChromeNotFound as e:
            if not args.allow_text_fallback:
                say(f"[render] 中止：{e}")
                say("[render] 若确实要在没有浏览器的环境生成（公式会退化为源码），"
                    "加 --allow-text-fallback。")
                return 3, log
            say(f"[render] 警告：{e}；已按要求退化为纯文本公式")
        except Exception as e:
            if not args.allow_text_fallback:
                say(f"[render] 中止：公式预渲染失败（{e}）")
                return 3, log
            say(f"[render] 警告：公式预渲染失败，退化为纯文本公式：{e}")

    stem = args.name or src.stem.replace("_答案", "")
    target = cfg.output_dir if args.direct else cfg.staging
    target.mkdir(parents=True, exist_ok=True)
    dest = target / f"{stem}_答案横版.html"
    html = build_html(text, title, cfg.layout, prerendered_body=body, extra_css=extra_css)
    dest.write_text(html, encoding="utf-8")

    if args.direct:
        say(f"[render] {len(blocks)} 道题 -> {dest}{note}（--direct，跳过缓存与校验）")
    else:
        say(f"[render] {len(blocks)} 道题 -> {dest}{note}")
    _report_layout(say, heights, usable_height_px(cfg.layout), blocks)

    if args.check:
        ok, problems, stats = check_html(dest, str(src))
        limit = usable_height_px(cfg.layout)
        # 允许题内跨页，所以只有撑过两页才算排版硬伤。
        # 跨页不限：块高不再作为不通过的理由，只在上面按页数提示。
        # 仍然保留「每页至多一题」的语义（.page 元素强制另起一页）。
        say(f"[check] 题块 {stats.get('pages')} / 标题 {stats.get('h2')} / "
            f"公式节点 {stats.get('katex')}（草稿应有 {stats.get('math_expected')}）/ "
            f"报错 {stats.get('katex_error')} / 内联字体 {stats.get('inline_fonts')} / "
            f"{stats.get('kb')} KB")
        if ok and not problems:
            say("  结果：通过，可以 publish")
            return 0, log
        say("  结果：未通过")
        for x in problems:
            say(f"   - {x}")
        return 1, log

    if not args.direct:
        say(f"[render] 已写入缓存目录（未发布）。"
            f"下一步：answer-pipeline publish \"{dest}\"")
    return 0, log


def cmd_render(cfg: Config, args, out: Reporter) -> int:
    srcs = [Path(p).resolve() for p in args.markdown]
    if len(srcs) > 1 and args.name:
        out("[render] --name 只能配合单份草稿使用（批量时文件名取自草稿名）。")
        return 2
    if len(srcs) > 1 and args.title:
        out("[render] 提醒：批量渲染不套 --title，各份取自己首行的 `# 标题`。")

    if len(srcs) == 1:
        code, lines = _render_one(cfg, srcs[0], args.title, args)
        for line in lines:
            out(line)
        return code

    results = [None] * len(srcs)
    workers = max(1, min(args.jobs, len(srcs)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_render_one, cfg, src, "", args): i
                for i, src in enumerate(srcs)}
        for fut in as_completed(futs):
            results[futs[fut]] = fut.result()

    out(f"[batch] 并发 {workers} 路渲染 {len(srcs)} 份草稿")
    codes = []
    for i, item in enumerate(results):
        code, lines = item if item else (1, ["[render] 未返回结果"])
        codes.append(code)
        out(f"[batch] --- {srcs[i].name} ---")
        for line in lines:
            out(line)
    ok_n = sum(1 for c in codes if c == 0)
    out(f"[batch] 通过 {ok_n}/{len(codes)} 份")
    if 3 in codes:
        return 3
    if 2 in codes:
        return 2
    return 0 if ok_n == len(codes) else 1



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
    ok = publish(cfg, args.html, args.md, force=getattr(args, "force", False)) is not None
    return 0 if ok else 1


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


def cmd_solve(cfg: Config, args, out: Reporter) -> int:
    """用 API 给一条待答任务写草稿，默认随后自动 render --check。"""
    from . import apiclient, solver

    try:
        record = solver.pick_record(cfg, getattr(args, "task", None))
    except Exception as e:  # noqa: BLE001 - 找不到任务属于用法问题
        out(f"[solve] {e}")
        return 2

    api = solver.load_api_config(cfg.root)
    if getattr(args, "base_url", None):
        api["base_url"] = args.base_url.strip()
    if getattr(args, "model", None):
        api["model"] = args.model.strip()

    label = record.get("label") or "task"
    out(f"[solve] 任务 {label} | 素材 {record.get('primary_file') or record.get('source_dir')}")

    if args.dry_run:
        try:
            info = solver.solve_one(cfg, record, api, do_check=False, dry_run=True, verbose=False)
        except Exception as e:  # noqa: BLE001
            out(f"[solve] {e}")
            return 2
        out(f"[solve] 端点   {info['endpoint']}")
        out(f"[solve] 模型   {info['model'] or '(未配置)'}")
        out(f"[solve] 规范   {info['spec_source']}（system {info['system_chars']} 字）")
        out(f"[solve] 图片   {len(info['images'])} 张")
        for p in info["images"]:
            out(f"         {p}")
        out(f"[solve] 请求   messages 合计约 {info['messages_chars']} 字符")
        out(f"[solve] 草稿将写入 {info['md']}")
        out("[solve] --dry-run：没有发起请求，也没有写文件")
        return 0

    missing = [k for k in ("base_url", "api_key", "model") if not api.get(k)]
    if missing:
        out(f"[solve] 缺少配置：{', '.join(missing)}")
        out("        复制 config.api.example.json 为 config.api.json 并填写；")
        out("        或设置环境变量 ANSWER_API_BASE / ANSWER_API_KEY / ANSWER_API_MODEL")
        return 2

    try:
        res = solver.solve_one(cfg, record, api, do_check=not args.no_check)
    except apiclient.ApiError as e:
        out(f"[solve] 调用失败：{e}")
        if e.status:
            out(f"        HTTP {e.status}")
        return 1
    except KeyboardInterrupt:
        raise
    except Exception as e:  # noqa: BLE001
        out(f"[solve] {type(e).__name__}: {e}")
        return 1

    out("")
    if res.get("passed") is True:
        out(f"[solve] 通过（{res['attempts']} 次成稿）：{res['md']}")
        if getattr(args, "publish", False):
            ok, msg = solver.finish_task(cfg, record, Path(res["md"]))
            if not ok:
                out(f"[solve] 未发布：{msg}")
                return 1
            return 0
        staged = record.get("staged_html") or ""
        if staged:
            out(f"        下一步：python scripts/pipeline.py publish \"{staged}\" --md \"{res['md']}\"")
        return 0
    if res.get("checked") and res.get("passed") is False:
        out(f"[solve] 自检未通过（已重试到第 {res['attempts']} 版）：{res['md']}")
        out("        草稿保留，按上面的报错手改后重跑 render --check")
        return 1
    out(f"[solve] 草稿已生成（未自检）：{res['md']}")
    return 0


def _answer_round(cfg: Config, api, args, out: Reporter) -> int:
    """处理一轮待答任务，返回本轮完成（落进 output/）的条数。"""
    from . import apiclient, solver

    tasks = solver.list_pending(cfg)
    if not tasks:
        if not getattr(args, "quiet", False):
            out("[answer] 队列为空")
        return 0
    if getattr(args, "limit", 0):
        tasks = tasks[:args.limit]

    failures = solver.load_failures(cfg)
    ok_n = fail_n = 0
    for rec in tasks:
        label = rec.get("label") or "task"
        cnt = int((failures.get(label) or {}).get("count") or 0)
        if cnt >= args.max_failures:
            out(f"[answer] 跳过 {label}：已失败 {cnt} 次"
                f"（删掉 {solver.FAIL_FILE} 里该条可重试）")
            fail_n += 1
            continue

        out(f"[answer] 处理 {label} …")
        try:
            res = solver.solve_one(cfg, rec, api, do_check=not args.no_check)
        except apiclient.ApiError as e:
            solver.note_failure(cfg, label, str(e))
            out(f"[answer] {label} 调用失败：{e}")
            fail_n += 1
            if args.stop_on_error:
                break
            continue
        except Exception as e:  # noqa: BLE001 - 单条失败不该拖垮整轮
            solver.note_failure(cfg, label, f"{type(e).__name__}: {e}")
            out(f"[answer] {label} 出错：{type(e).__name__}: {e}")
            fail_n += 1
            if args.stop_on_error:
                break
            continue

        if res.get("checked") and res.get("passed") is False:
            solver.note_failure(cfg, label, "自检未通过")
            out(f"[answer] {label} 自检未通过，草稿保留：{res['md']}")
            fail_n += 1
            continue

        if args.no_publish:
            out(f"[answer] {label} 草稿已出（未发布）：{res['md']}")
            ok_n += 1
            continue

        ok, msg = solver.finish_task(cfg, rec, Path(res["md"]))
        if ok:
            solver.clear_failure(cfg, label)
            ok_n += 1
        else:
            solver.note_failure(cfg, label, msg)
            out(f"[answer] {label} 未发布：{msg}")
            fail_n += 1

    out(f"[answer] 本轮完成 {ok_n} 条，失败 {fail_n} 条")
    return ok_n


def cmd_answer(cfg: Config, args, out: Reporter) -> int:
    """消费队列：API 写稿 -> 自检 -> 发布进 output/ -> 标记完成。

    与手工链路共用同一套 render/publish/done，所以 output/ 里的成品没有差别，
    后面的 html2note 照常把它转 PDF 上传，不需要任何改动。
    """
    from . import solver

    api = solver.load_api_config(cfg.root)
    if getattr(args, "base_url", None):
        api["base_url"] = args.base_url.strip()
    if getattr(args, "model", None):
        api["model"] = args.model.strip()

    missing = [k for k in ("base_url", "api_key", "model") if not api.get(k)]
    if missing:
        out(f"[answer] 缺少配置：{', '.join(missing)}")
        out("        复制 config.api.example.json 为 config.api.json 并填写；")
        out("        或设置环境变量 ANSWER_API_BASE / ANSWER_API_KEY / ANSWER_API_MODEL")
        return 2

    if not args.watch:
        _answer_round(cfg, api, args, out)
        return 0

    out(f"[answer] 常驻启动：每 {args.interval:g}s 检查一次队列，Ctrl+C 退出")
    out(f"[answer] 模型 {api['model']} @ {api['base_url']}")
    try:
        while True:
            n = _answer_round(cfg, api, args, out)
            if n == 0:
                time.sleep(max(1.0, float(args.interval)))
    except KeyboardInterrupt:
        out("[answer] 已停止")
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
    w.add_argument("--exit-on-new", action="store_true",
                   help="检测到新任务（或启动时已有积压）就退出，用退出触发上层完成通知")

    s = sub.add_parser("wait", parents=[common], help="一次性哨兵，发现新素材即退出")
    s.add_argument("--interval", type=float, default=1.0, help="轮询间隔（秒）")
    s.add_argument("--timeout", type=float, default=0, help="超时秒数，0 表示一直等")

    r = sub.add_parser("render", parents=[common],
                       help="Markdown 答案转横版 HTML（默认写入缓存，可一次给多份并发渲染）")
    r.add_argument("markdown", nargs="+", help="一份或多份答案草稿")
    r.add_argument("--name", default=None, help="输出文件名前缀（仅单份时可用，默认取答案文件名）")
    r.add_argument("--title", default="", help="标题前缀，通常是试卷/笔记名称（仅单份时套用）")
    r.add_argument("--jobs", type=int, default=4, help="批量渲染的并发路数（默认 4）")
    r.add_argument("--no-latex", action="store_true", help="不预渲染 $...$ 公式")
    r.add_argument("--no-measure", action="store_true",
                   help="不测量每块高度（省一次浏览器启动，但看不到超页告警）")
    r.add_argument("--check", action="store_true",
                   help="渲染完立刻自检并输出结果，省掉单独跑一次 check")
    r.add_argument("--allow-text-fallback", action="store_true",
                   help="没有浏览器时仍生成（公式退化为源码，不推荐）")
    r.add_argument("--direct", action="store_true", help="直接写 output，跳过缓存与校验（不推荐）")

    c = sub.add_parser("check", parents=[common], help="校验缓存成品（只报告，不移动）")
    c.add_argument("html")
    c.add_argument("--md", default=None, help="对应草稿，用于核对公式数量")

    pb = sub.add_parser("publish", parents=[common], help="校验通过后把缓存成品移入 output")
    pb.add_argument("html")
    pb.add_argument("--md", default=None, help="对应草稿，用于核对公式数量")
    pb.add_argument("--force", action="store_true",
                    help="允许覆盖 output 里同名的已发布成品（默认拒绝：发布即终版）")

    d = sub.add_parser("done", parents=[common], help="标记队列任务已完成")
    d.add_argument("queue")
    d.add_argument("--md", default=None)
    d.add_argument("--html", default=None)

    k = sub.add_parser("skip", parents=[common], help="标记无需作答（如成品页回灌）")
    k.add_argument("queue")
    k.add_argument("--reason", default="无需作答")

    sv = sub.add_parser("solve", parents=[common],
                        help="用 API 读题写草稿（默认随后自动 render --check）")
    sv.add_argument("task", nargs="?",
                    help="队列 JSON 路径或 label；省略则取最早的一条待答")
    sv.add_argument("--dry-run", action="store_true",
                    help="只打印请求摘要（端点/模型/图片/规范来源），不调用、不写文件")
    sv.add_argument("--no-check", action="store_true", help="写完草稿就停，不跑 render --check")
    sv.add_argument("--base-url", default=None, help="覆盖 config.api.json 的 base_url")
    sv.add_argument("--model", default=None, help="覆盖 config.api.json 的 model")
    sv.add_argument("--publish", action="store_true",
                    help="校验通过后直接发布进 output/ 并标记完成（等价于 answer 的单条行为）")

    an = sub.add_parser("answer", parents=[common],
                        help="消费队列：API 写稿 + 自检 + 发布 + 标记完成（可选常驻）")
    an.add_argument("--watch", action="store_true",
                    help="常驻：一直等新任务（默认处理完一轮就退出）")
    an.add_argument("--interval", type=float, default=20.0,
                    help="常驻轮询间隔（秒，默认 20）")
    an.add_argument("--limit", type=int, default=0, help="单轮最多处理几条（0 表示不限）")
    an.add_argument("--no-check", action="store_true", help="跳过 render --check（不建议）")
    an.add_argument("--no-publish", action="store_true", help="只出草稿，不发布进 output/")
    an.add_argument("--stop-on-error", action="store_true", help="任一任务出错就停止本轮")
    an.add_argument("--max-failures", type=int, default=2,
                    help="同一任务失败达到该次数后跳过（默认 2，记录在 solve-failures.json）")
    an.add_argument("--base-url", default=None, help="覆盖 config.api.json 的 base_url")
    an.add_argument("--model", default=None, help="覆盖 config.api.json 的 model")
    return ap


HANDLERS = {
    "init": cmd_init, "status": cmd_status, "scan": cmd_scan, "watch": cmd_watch,
    "wait": cmd_wait, "render": cmd_render, "check": cmd_check,
    "publish": cmd_publish, "done": cmd_done, "skip": cmd_skip, "solve": cmd_solve,
    "answer": cmd_answer,
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
