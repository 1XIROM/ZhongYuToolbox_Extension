#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检测 input 里出现"特定图片"时，往 WorkBuddy 对话窗口发一条唤醒消息。

判定方式：**图片 md5 精确匹配**。
实测同一张源图重新上传，云笔记重新生成笔记后，下载到的 00_screenshot.png 逐字节相同，
所以 md5 比对既准又不误伤，不需要感知哈希。

用法：
  python scripts/wake_on_image.py --add "<图片路径>" --note "课时达标自测封面"
  python scripts/wake_on_image.py --list
  python scripts/wake_on_image.py --once            # 扫一遍
  python scripts/wake_on_image.py --loop 20         # 每 20 秒扫一次，常驻
  python scripts/wake_on_image.py --once --dry      # 命中只粘贴不发送（测位）

配置与状态：
  .workbuddy/wake_refs.json    参考图清单（md5 + 说明）
  .workbuddy/wake_fired.json   已触发过的素材目录，避免重复唤醒
"""

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INPUT = ROOT / "input"
STATE_DIR = ROOT / ".workbuddy"
REFS = STATE_DIR / "wake_refs.json"
FIRED = STATE_DIR / "wake_fired.json"
FAILS = STATE_DIR / "wake_fail.json"
CLOSED = STATE_DIR / "wake_closed.json"      # 人工标为「已完结、无需出页」的目录
WAKE = ROOT / "scripts" / "wake_workbuddy.py"

# 连续失败到这个次数就先放一放：唤醒失败多半是窗口没开或不在前台，
# 每 20 秒重试一次只会反复抢焦点、反复往别处粘贴，比不唤醒更烦。
FAIL_LIMIT = 3
FAIL_COOLDOWN = 600      # 秒

IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def log(msg):
    """命中与结果写进 logs/wake-YYYY-MM-DD.log（与执行模块同一个文件）。"""
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        d = ROOT / "logs"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / f"wake-{time.strftime('%Y-%m-%d')}.log", "a",
                  encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
    except Exception:
        pass


def pending_dirs(limit=4, hours=24):
    """列出最近 hours 小时内还没出页的素材目录（时间倒序）。返回 (前几条, 总数)。

    判据：目录名把空格换成下划线，再加「_答案横版.html」后，output 里有没有这个文件。
    只看最近一天，是因为早期素材有一批本来就 skip 过、命名也不统一，全量统计会
    每次都报「未出页 35 条」，把真正要处理的淹掉；限定一天后命名规则一致，可以精确比。
    """
    out_dir = ROOT / "output"
    closed = load_json(CLOSED, {"closed": {}})["closed"]
    cutoff = time.time() - hours * 3600
    # 必须先按 mtime 排序再 break。按目录名倒序排是错的：testfolder3 这种
    # 以字母开头的目录会排在最前面，它的时间很旧，一 break 就把整批都跳过了。
    dirs = []
    for d in INPUT.iterdir():
        if not d.is_dir():
            continue
        try:
            dirs.append((d.stat().st_mtime, d))
        except OSError:
            continue
    dirs.sort(reverse=True)
    names = []
    for mt, d in dirs:
        if mt < cutoff:
            break
        if d.name in closed:            # 人工标过「已完结」，不再算待处理
            continue
        if (out_dir / (d.name.replace(" ", "_") + "_答案横版.html")).exists():
            continue
        names.append(d.name)
    return names[:limit], len(names)


def ensure_sentinel():
    """哨兵没在跑就起一个（watch --interval 1 --exit-on-new）。

    队列有存量时它启动即退出——等于再喊一声；没有存量就常驻等新素材。
    这样不会出现「检测在跑、但没人处理」的空档。
    """
    probe = ('if (Get-CimInstance Win32_Process | Where-Object { '
             '$_.CommandLine -like "*pipeline.py watch*" }) { exit 0 } else { exit 1 }')
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", probe],
                           capture_output=True, timeout=25)
        if r.returncode == 0:
            return "哨兵已在运行"
    except Exception:
        pass
    try:
        subprocess.Popen(
            [sys.executable, str(ROOT / "scripts" / "pipeline.py"),
             "watch", "--interval", "1", "--exit-on-new"],
            cwd=str(ROOT), creationflags=0x00000008,      # DETACHED_PROCESS，不弹窗
        )
        return "哨兵已开启"
    except Exception as e:
        return f"哨兵开启失败：{e}"


def md5_of(p: Path) -> str:
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(p: Path, default):
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default


def save_json(p: Path, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def cmd_add(path_str: str, note: str) -> int:
    p = Path(path_str)
    if not p.exists():
        print(f"文件不存在：{p}")
        return 1
    m = md5_of(p)
    data = load_json(REFS, {"refs": []})
    if any(r["md5"] == m for r in data["refs"]):
        print(f"这张图已在清单里（md5={m[:12]}…）")
        return 0
    data["refs"].append({
        "md5": m,
        "note": note or p.stem,
        "sample": str(p),
        "added": time.strftime("%Y-%m-%d %H:%M:%S"),
        "size": p.stat().st_size,
    })
    save_json(REFS, data)
    print(f"已加入参考图清单：{note or p.stem}  md5={m}")
    return 0


def cmd_list() -> int:
    data = load_json(REFS, {"refs": []})
    if not data["refs"]:
        print("参考图清单为空。先运行 --add 添加一张。")
        return 0
    print(f"共 {len(data['refs'])} 张参考图：")
    for r in data["refs"]:
        print(f"  {r['md5']}  {r['note']}  （{r.get('size', '?')} 字节，{r.get('added', '')}）")
    return 0


def scan_once(dry: bool, force: bool = False, quiet: bool = False) -> int:
    refs = load_json(REFS, {"refs": []})["refs"]
    if not refs:
        print("参考图清单为空，先 --add 一张。")
        return 2

    ref_map = {r["md5"]: r for r in refs}
    fired = load_json(FIRED, {"fired": []})["fired"]
    fired_set = set() if force else set(fired)
    fails = load_json(FAILS, {"fails": {}})["fails"]
    now = time.time()

    dirs = [d for d in sorted(INPUT.iterdir()) if d.is_dir()]
    hits = []
    skipped = 0
    cooling = 0
    for d in dirs:
        if d.name in fired_set:
            skipped += 1
            continue
        fb = fails.get(d.name)
        if fb and fb.get("n", 0) >= FAIL_LIMIT and now - fb.get("last", 0) < FAIL_COOLDOWN:
            cooling += 1
            continue
        # 优先看整页预览，也看原图——两类都可能与参考一致
        for f in sorted(d.iterdir()):
            if f.suffix.lower() not in IMG_EXT:
                continue
            try:
                m = md5_of(f)
            except Exception:
                continue
            if m in ref_map:
                hits.append((d, f, ref_map[m]))
                break

    if not hits:
        if not quiet:
            extra = f"，跳过已触发的 {skipped} 个" if skipped else ""
            if cooling:
                extra += f"，冷却中 {cooling} 个"
            print(f"扫描 {len(dirs)} 个素材目录，无新命中{extra}。")
            if skipped:
                print("（想重新触发用 --reset 清空记录，或加 --force 忽略记录）")
        return 0

    if hits:
        log("哨兵: " + ensure_sentinel())

    for d, f, ref in hits:
        head, total = pending_dirs()
        if total > 1:
            # 只报数量、不逐个列目录名：一条消息 200 多字会把输入框撑成好几行。
            # 清单本身在 logs/wake-*.log 里，收到消息后按需再查。
            msg = (f"检测到「{ref['note']}」：{d.name}。未出页素材共 {total} 条，"
                   f"请一并处理，处理完确认哨兵在跑。")
        else:
            msg = f"检测到「{ref['note']}」：{d.name}。请处理这条素材，处理完确认哨兵在跑。"
        log(f"[命中] {d.name}  ← {ref['note']}  ({f.name})  未出页 {total} 条")
        cmd = [sys.executable, str(WAKE), msg]
        if dry:
            cmd.append("--dry")
        try:
            # 不捕获输出：唤醒脚本在等输入框出现时会每 10 秒打印一行进度
            #（对话生成中要等，最长 180 秒），捕获了这期间窗口里就像卡死。
            r = subprocess.run(cmd, cwd=str(ROOT), timeout=300)
            ok = r.returncode == 0
            log("唤醒: " + ("OK" if ok else "FAIL（见上一行原因）"))
        except Exception as e:
            log(f"唤醒失败: {e}")
            ok = False

        # dry 只是测位，不算真的触发过，不写记录
        if ok and not dry:
            fired.append(d.name)
            save_json(FIRED, {"fired": fired})
            if d.name in fails:
                del fails[d.name]
                save_json(FAILS, {"fails": fails})
        elif not ok and not dry:
            fb = fails.get(d.name, {"n": 0})
            fb["n"] = fb.get("n", 0) + 1
            fb["last"] = time.time()
            fails[d.name] = fb
            save_json(FAILS, {"fails": fails})
            if fb["n"] >= FAIL_LIMIT:
                log(f"连续失败 {fb['n']} 次，冷却 {FAIL_COOLDOWN // 60} 分钟内不再重试这条"
                      f"（检查 WorkBuddy 窗口是否打开、对话是否长时间在生成、是否被别的窗口挡住）")
    return 0


def cmd_reset() -> int:
    save_json(FIRED, {"fired": []})
    save_json(FAILS, {"fails": {}})
    print("已清空触发记录。")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--add", metavar="图片路径", help="把一张图加入参考清单")
    ap.add_argument("--note", default="", help="配合 --add 的说明，会写进唤醒消息")
    ap.add_argument("--list", action="store_true", help="列出参考图")
    ap.add_argument("--once", action="store_true", help="扫一遍就退出")
    ap.add_argument("--loop", type=int, metavar="秒", help="常驻，每隔 N 秒扫一次")
    ap.add_argument("--dry", action="store_true", help="命中时只粘贴不发送")
    ap.add_argument("--force", action="store_true", help="忽略已触发记录，强制再触发一次")
    ap.add_argument("--reset", action="store_true", help="清空已触发记录")
    args = ap.parse_args()

    if args.add:
        sys.exit(cmd_add(args.add, args.note))
    if args.list:
        sys.exit(cmd_list())
    if args.reset:
        sys.exit(cmd_reset())
    if args.once:
        sys.exit(scan_once(args.dry, force=args.force))
    if args.loop:
        print(f"[wake_on_image] 每 {args.loop} 秒扫一次 {INPUT}；Ctrl+C 结束")
        n = 0
        try:
            while True:
                n += 1
                # 每 12 轮（默认约 1 分钟）打一行心跳，否则窗口里一直没动静，
                # 分不清「检测在跑但没命中」和「检测已经挂了」。
                scan_once(args.dry, force=args.force, quiet=(n % 12 != 0))
                time.sleep(args.loop)
        except KeyboardInterrupt:
            print("\n已停止")
            sys.exit(0)
    ap.print_help()


if __name__ == "__main__":
    main()
