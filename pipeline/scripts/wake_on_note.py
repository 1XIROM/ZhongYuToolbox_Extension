#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""监听 input 里新出现的素材目录（= 云笔记下载了一篇新笔记），发消息叫醒。

与 wake_on_image 的分工：
  wake_on_image  只认**特定图片**（比对 md5），用来叫醒某一类固定封面；
  wake_on_note   认**任何新素材目录**，等价于「云笔记里有标题含 ds 的新笔记」。
                 它替代了 pipeline 的 input 哨兵（watch --exit-on-new）：
                 既然每次新素材都会直接叫醒人，就不必再挂一个「发现即退出」的进程。

用法：
  python scripts/wake_on_note.py --once        # 跑一轮（首次会建基线，不通知）
  python scripts/wake_on_note.py --loop 1      # 常驻，每 1 秒看一次
  python scripts/wake_on_note.py --loop 1 --dry   # 命中只粘贴不发送（测位）

状态：.workbuddy/wake_note_seen.json（已通知过的目录名）
日志：logs/wake-YYYY-MM-DD.log（与 wake_workbuddy 同一个文件）
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INPUT = ROOT / "input"
STATE = ROOT / ".workbuddy" / "wake_note_seen.json"
WAKE = ROOT / "scripts" / "wake_workbuddy.py"

RETRY_COOLDOWN = 60          # 发送失败后，同一目录隔多久再试（秒）


def log(msg):
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


def load_state():
    """返回 (已通知集合, 失败冷却表)。没有基线时集合为 None。"""
    try:
        d = json.loads(STATE.read_text(encoding="utf-8"))
        return set(d.get("seen", [])), dict(d.get("fails", {}))
    except Exception:
        return None, {}


def save_state(seen, fails):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(
        {"seen": sorted(seen), "fails": fails,
         "at": time.strftime("%Y-%m-%d %H:%M:%S")},
        ensure_ascii=False, indent=2), encoding="utf-8")


def dirs_now():
    try:
        return {d.name for d in INPUT.iterdir() if d.is_dir()}
    except Exception:
        return set()


def scan_once(seen, fails, dry=False, quiet=False):
    now = dirs_now()
    if seen is None:
        # 首次运行只建基线：否则会把 input 里上百个历史目录全部当新素材轰炸一遍
        save_state(now, {})
        log(f"[基线] 记录 {len(now)} 个已有目录，不通知")
        return now, fails

    fresh = sorted(now - seen)
    if not fresh:
        return seen, fails

    for name in fresh:
        fb = fails.get(name)
        if fb and time.time() - fb.get("last", 0) < RETRY_COOLDOWN:
            continue
        msg = f"检测到新笔记素材：{name}。请处理这条素材。"
        log(f"[新素材] {name}")
        cmd = [sys.executable, str(WAKE), msg]
        if dry:
            cmd.append("--dry")
        try:
            r = subprocess.run(cmd, cwd=str(ROOT), timeout=300)
            ok = r.returncode == 0
        except Exception as e:
            log(f"   发送异常: {e}")
            ok = False
        if ok and not dry:
            seen.add(name)
            fails.pop(name, None)
            save_state(seen, fails)
        elif not dry:
            fails[name] = {"n": fb.get("n", 0) + 1 if fb else 1, "last": time.time()}
            save_state(seen, fails)
            log(f"   发送失败（第 {fails[name]['n']} 次），{RETRY_COOLDOWN}s 后重试")
    return seen, fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, metavar="秒", help="常驻，每隔 N 秒看一次（建议 1）")
    ap.add_argument("--once", action="store_true", help="跑一轮就退出")
    ap.add_argument("--dry", action="store_true", help="命中时只粘贴不发送")
    ap.add_argument("--reset", action="store_true", help="清空状态，下次运行重建基线")
    args = ap.parse_args()

    if args.reset:
        STATE.unlink(missing_ok=True)
        print("已清空监听状态，下次运行会重建基线。")
        return

    seen, fails = load_state()

    if args.once:
        scan_once(seen, fails, dry=args.dry)
        return

    if args.loop:
        sec = max(1, args.loop)
        print(f"[wake_on_note] 每 {sec} 秒看一次 {INPUT}；Ctrl+C 结束", flush=True)
        n = 0
        try:
            while True:
                n += 1
                # 每 60 轮打一行心跳，便于区分「在跑但没新素材」和「已经挂了」
                seen, fails = scan_once(seen, fails, dry=args.dry, quiet=(n % 60 != 0))
                if n % 60 == 0:
                    log(f"[心跳] 共 {len(dirs_now())} 个素材目录，无新素材")
                time.sleep(sec)
        except KeyboardInterrupt:
            print("\n已停止")
        return

    ap.print_help()


if __name__ == "__main__":
    main()
