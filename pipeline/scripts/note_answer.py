#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""云笔记 -> input -> DeepSeek -> output 全流程编排。

一条命令做三件事，并把每一步都打到控制台（同时追加到 .workbuddy/note_answer.log）：

  1) 扫云笔记：按原工具的方式确认 8088/9222 在跑，然后 `node html2note.js --cloud`
     下载新笔记到 input/<时间>_<标题>_<id8>/；
  2) 逐条读 input：把目录里的图片、resources.json 元数据、章节图都列出来，
     拼成提示词，交给 DeepSeek（默认走本机 `dsh --profile headless`，也可走
     OpenAI 兼容 HTTP 接口）；
  3) 写 output：`output/<label>_回答.md`，一条笔记一个文件，文件名即来源目录名。

为什么答案不写成 HTML：output/ 里的 HTML 会被 html2note 自动转 PDF 传上云，
人工没复核过的内容不该走那条链路。这里只落 Markdown，确认后再走 publish。

Key 一律从外部读，脚本里没有任何硬编码：
  DEEPSEEK_API_KEY 环境变量  >  <root>\\.workbuddy\\deepseek.key  >  <root>\\config.api.key
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
INPUT_DIR = ROOT / "input"
OUTPUT_DIR = ROOT / "output"
LOG_PATH = ROOT / ".workbuddy" / "note_answer.log"
CFG_API = ROOT / "config.api.json"

sys.path.insert(0, str(ROOT / "tools" / "answer-pipeline" / "src"))

from answer_pipeline import apiclient  # noqa: E402
from answer_pipeline import solver  # noqa: E402
from answer_pipeline.queue import safe_name  # noqa: E402

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
# 这三个是云笔记的私有容器，不参与答题，也不打印内容（AGENTS.md 明确要求忽略）
IGNORED_FILES = {"00_data.mdb", "00_lock.mdb", "00_snapshot.bin"}
ANSWER_SUFFIX = "_回答.md"

DEFAULT_ENGINE = "harness"
DEFAULT_TOOLBOX = Path(r"E:\WorkBuddy\中育Toolbox-独立版")
DEFAULT_API_BASE = "https://api.deepseek.com"
DEFAULT_API_MODEL = "deepseek-chat"
DEFAULT_DSH = Path(r"E:\Program Files\DSH\resources\runtime\cli\bin\dsh.cmd")

EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_FATAL = 2


class Fatal(RuntimeError):
    """整条流程没法开始：缺 python 依赖、缺 node、缺目录等。"""


class NoteError(RuntimeError):
    """单条笔记处理失败，记录后继续下一条。"""


# ---------------------------------------------------------------- 控制台 + 日志

_log_file = None


def _open_log() -> None:
    global _log_file
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _log_file = LOG_PATH.open("a", encoding="utf-8")
        _log_file.write("\n" + "#" * 72 + "\n")
        _log_file.write("# %s  运行开始\n" % _now())
        _log_file.write("#" * 72 + "\n")
    except OSError:
        _log_file = None


def _close_log() -> None:
    if _log_file is not None:
        try:
            _log_file.close()
        except OSError:
            pass


def logout(msg: str = "") -> None:
    """打一行到控制台，并同步写日志。"""
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:  # 控制台编码异常时不要因为一个汉字崩掉
        print(msg.encode("utf-8", "replace").decode("utf-8", "replace"), flush=True)
    if _log_file is not None:
        try:
            _log_file.write(msg + "\n")
            _log_file.flush()
        except OSError:
            pass


def rule(title: str = "") -> None:
    logout("")
    logout("=" * 74)
    if title:
        logout(title)
        logout("=" * 74)


def stage(title: str) -> None:
    logout("")
    logout("-" * 74)
    logout(title)
    logout("-" * 74)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _decode(raw: bytes) -> str:
    """node/Chrome 的输出可能是 UTF-8 也可能是 OEM(GBK)，按顺序试。"""
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _mask(key: str) -> str:
    if not key:
        return "（未提供）"
    return "已提供（%d 字符，尾号 %s）" % (len(key), key[-4:])


# ---------------------------------------------------------------- 云笔记素材

def read_resources(note_dir: Path) -> Dict[str, Any]:
    path = note_dir / "resources.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 元数据坏了不该阻断答题
        logout("  [警告] resources.json 解析失败：%s" % exc)
        return {}


def note_images(note_dir: Path, meta: Dict[str, Any]) -> List[Path]:
    """按「原图优先、整页预览最后」的顺序排出要送模型的图。

    resources.json 里 type=0 是原图，type=2 是整页截图（纸面空白多、字被缩小），
    原图缺失时才退回截图。本地文件名对不上时按目录里的实际图片补齐。
    """
    by_local: Dict[str, int] = {}
    for item in meta.get("resources") or []:
        local = item.get("local")
        if not local:
            continue
        try:
            by_local[str(local)] = int(item.get("type") or 0)
        except (TypeError, ValueError):
            by_local[str(local)] = 0

    images = [p for p in note_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXT]

    def weight(p: Path) -> Tuple[int, str]:
        t = by_local.get(p.name)
        if t == 0:
            return (0, p.name)   # 原图
        if t == 2:
            return (2, p.name)   # 整页预览
        if t == 1:
            return (3, p.name)
        return (1, p.name)       # 元数据里没有的图，排中间

    return sorted(images, key=weight)


def note_texts(note_dir: Path) -> List[Path]:
    """目录里若有纯文本素材（少数笔记会带），也当作原文一并展示。"""
    out = []
    for p in sorted(note_dir.iterdir()):
        if not p.is_file():
            continue
        if p.name in IGNORED_FILES or p.name == "resources.json":
            continue
        if p.suffix.lower() in IMAGE_EXT:
            continue
        if p.suffix.lower() in {".txt", ".md"}:
            out.append(p)
    return out


def print_sources(note_dir: Path, meta: Dict[str, Any], images: List[Path],
                  texts: List[Path]) -> None:
    """把这条笔记的「原文」摊开打印：目录、元数据、文件清单、文本正文。"""
    logout("  来源目录 : %s" % note_dir)
    if meta:
        logout("  笔记信息 : 标题=%s | 创建=%s | 页数=%s | 行数=%s | 文件=%s | 字节=%s"
               % (meta.get("fileName", "?"), meta.get("createTime", "?"),
                  meta.get("pages", "?"), meta.get("rows", "?"),
                  meta.get("files", "?"), meta.get("bytes", "?")))
        logout("  笔记 ID  : %s" % meta.get("fileId", "?"))
    else:
        logout("  笔记信息 : （没有 resources.json，按目录内容处理）")

    logout("  文件清单 :")
    for p in sorted(note_dir.iterdir()):
        if not p.is_file():
            continue
        mark = "  "
        if p.name in IGNORED_FILES:
            mark = "（忽略）"
        logout("    - %-24s %8d 字节 %s" % (p.name, p.stat().st_size, mark))

    logout("  送入模型 : %d 张图片" % len(images))
    for i, p in enumerate(images, 1):
        logout("    %d. %s" % (i, p))

    for p in texts:
        try:
            body = p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logout("  [警告] 读不了 %s：%s" % (p.name, exc))
            continue
        logout("  文本素材 %s（%d 字符）：" % (p.name, len(body)))
        logout("  " + "-" * 70)
        for line in body.splitlines():
            logout("  | " + line)
        logout("  " + "-" * 70)


# ---------------------------------------------------------------- 提示词

def _material_block(note_dir: Path, meta: Dict[str, Any], images: List[Path],
                    texts: List[Path], list_paths: bool = True) -> str:
    """把素材描述成文字。list_paths=False 用于 HTTP 接口：图片已内联，不必列绝对路径。"""
    lines = ["===== 笔记素材 =====", "目录：%s" % note_dir]
    if meta:
        lines.append("标题：%s   创建时间：%s   页数：%s"
                     % (meta.get("fileName", "?"), meta.get("createTime", "?"),
                        meta.get("pages", "?")))
    if list_paths:
        lines.append("图片（共 %d 张，按顺序阅读）：" % len(images))
        for i, p in enumerate(images, 1):
            lines.append("  %d. %s" % (i, p))
    else:
        lines.append("图片（共 %d 张，已按顺序内联在本条消息里）：" % len(images))
    for p in texts:
        lines.append("")
        lines.append("文本素材 %s：" % p.name)
        lines.append(p.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(lines)


def build_harness_prompt(note_dir: Path, meta: Dict[str, Any], images: List[Path],
                         texts: List[Path]) -> str:
    """给 dsh headless 的单条提示词：它必须自己 read_image，所以不能抄那份
    「不要调用任何工具」的系统提示。"""
    spec, _src = solver.load_spec(ROOT)
    return (
        "【云笔记答题】%s\n\n"
        "你是「云笔记自动答题」的执行环节。工作目录是 %s。\n"
        "任务：读取下面这条云笔记的图片，逐题写出答案草稿（Markdown），"
        "然后把草稿正文作为最终回答输出。\n"
        "动手前必须用 read_image 工具把列出的每一张图片都实际看一遍，"
        "不要凭文件名、不要凭元数据猜题目内容。\n"
        "不要修改任何文件，不要提问，不要复述任务，不要在正文前后加说明。\n\n"
        "本次回答由脚本落盘到：%s\n\n"

        "注意：本次的交付物就是正文这段 Markdown 草稿，由脚本负责保存成 output 里的 .md；"
        "规范中「不要直接往 output/ 写文件」「不要把 .md 当交付物」两条是给渲染发布环节写的，"
        "对本次不适用——你只需要把正文输出到 stdout，不要自己写文件。\n\n"
        "===== 写作规范（必须逐条遵守）=====\n%s\n===== 规范结束 =====\n\n"
        "输出要求：\n"
        "1. 第一行写 `# 学科 · 主题`；\n"
        "2. 不要用 ``` 代码块把整篇包起来；\n"
        "3. 看不清的地方写「该处无法辨认」，不要编造题干或数值；\n"
        "4. 每个 `## ` 题块都要有 **答案** / **解析** / **补充理解** / **高中范围**。\n\n"
        "%s\n"
        % (note_dir.name, ROOT, output_path(note_dir), spec,
           _material_block(note_dir, meta, images, texts))
    )


def build_api_messages(note_dir: Path, meta: Dict[str, Any], images: List[Path],
                       texts: List[Path], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """给 OpenAI 兼容接口的消息：图片内联成 data URI，复用管线里的系统提示。"""
    user_text = (
        "请读下面的笔记图片，按规范写出完整答案草稿。\n\n"
        + _material_block(note_dir, meta, images, texts, list_paths=False)
    )
    parts: List[Dict[str, Any]] = [apiclient.text_part(user_text)]
    limit = int(cfg.get("max_images") or 4)
    for p in images[:limit]:
        parts.append(apiclient.image_part(p))
    return [
        {"role": "system", "content": solver.build_system_prompt(ROOT)},
        {"role": "user", "content": parts},
    ]


# ---------------------------------------------------------------- Key

def read_key(cfg: Dict[str, Any]) -> Tuple[str, str]:
    """返回 (key, 来源说明)。顺序：环境变量 > .workbuddy/deepseek.key > config.api.key。"""
    env = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if env:
        return env, "环境变量 DEEPSEEK_API_KEY"
    for rel in (".workbuddy/deepseek.key", "config.api.key"):
        p = ROOT / rel
        if p.exists():
            try:
                text = p.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            if text:
                return text.splitlines()[0].strip(), str(p)
    if cfg.get("api_key"):
        src = ("环境变量 ANSWER_API_KEY"
               if (os.environ.get("ANSWER_API_KEY") or "").strip() else "config.api.json")
        return str(cfg["api_key"]), src
    return "", "（未找到）"


# ---------------------------------------------------------------- 云笔记扫描

def port_open(port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_port(port: int, seconds: float) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if port_open(port):
            return True
        time.sleep(0.5)
    return port_open(port)


def _serve_entry(folder: Path) -> Optional[str]:
    """返回 serve.js 相对目录的路径。

    独立版是扁平结构（serve.js 在根上），本仓库是 web-toolbox/serve.js。
    两种都要认，否则在发布版里双击这个 bat 会找不到工具箱。
    """
    for rel in ("serve.js", os.path.join("web-toolbox", "serve.js")):
        if (folder / rel).exists():
            return rel
    return None


def find_toolbox(explicit: Optional[str]) -> Optional[Path]:
    if explicit:
        p = Path(explicit)
        return p if _serve_entry(p) else None
    if _serve_entry(DEFAULT_TOOLBOX):
        return DEFAULT_TOOLBOX
    # 本仓库自带工具箱（repo-publish/web-toolbox/serve.js），优先复用
    if _serve_entry(ROOT.parent):
        return ROOT.parent
    parent = Path(os.environ.get("ANSWER_WORKBUDDY_ROOT") or r"E:\WorkBuddy")
    if parent.exists():
        for child in parent.iterdir():
            if _serve_entry(child):
                return child
    return None


def ensure_toolbox_server(toolbox: Optional[Path]) -> bool:
    if port_open(8088):
        logout("  8088 已在监听，复用现有 serve.js")
        return True
    if toolbox is None:
        logout("  [提示] 8088 未监听，也没找到 serve.js（可用 --toolbox 指定），跳过拉起")
        return False
    entry = _serve_entry(toolbox) or "serve.js"
    logout("  8088 未监听，按原工具的方式启动 serve.js：%s" % (toolbox / entry))
    try:
        subprocess.Popen(["node", entry, "8088"], cwd=str(toolbox),
                         creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    except OSError as exc:
        logout("  [警告] serve.js 启动失败：%s" % exc)
        return False
    if wait_port(8088, 15):
        logout("  8088 已就绪")
        return True
    logout("  [警告] 8088 等了 15 秒仍未就绪")
    return False


def chrome_path() -> Optional[Path]:
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                 os.environ.get("LOCALAPPDATA")):
        if not base:
            continue
        p = Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
        if p.exists():
            return p
    return None


def ensure_chrome() -> bool:
    if port_open(9222):
        logout("  9222 已在监听，复用现有浏览器会话")
        return True
    exe = chrome_path()
    if not exe:
        logout("  [提示] 9222 未监听且找不到 Chrome，跳过拉起（云笔记需要它才能读）")
        return False
    profile = Path(os.environ.get("TEMP") or ".") / "zhongyu-toolbox-chrome-prof"
    args = [
        str(exe),
        "--remote-debugging-port=9222",
        "--user-data-dir=%s" % profile,
        # 系统代理残留会让工具箱页面的 fetch 全部失败，必须显式关掉代理
        "--no-proxy-server",
        "--no-first-run",
        "--no-default-browser-check",
        "--window-size=1440,960",
        "http://127.0.0.1:8088/",
    ]
    logout("  9222 未监听，启动 Chrome（复用 %s 登录态）" % profile)
    try:
        subprocess.Popen(args, creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    except OSError as exc:
        logout("  [警告] Chrome 启动失败：%s" % exc)
        return False
    if wait_port(9222, 20):
        logout("  9222 已就绪")
        return True
    logout("  [警告] 9222 等了 20 秒仍未就绪（可能 profile 正被别的 Chrome 占用）")
    return False


def cloud_scan(args, toolbox: Optional[Path]) -> Tuple[int, set]:
    """跑一轮云笔记扫描；返回 (返回码, 新增目录名集合)。失败只提示，不中断。"""
    html2note = ROOT / "html2note.js"
    if not html2note.exists():
        logout("  [警告] 找不到 %s，跳过云扫描" % html2note)
        return 1, set()
    node = shutil.which("node")
    if not node:
        logout("  [警告] PATH 里没有 node，跳过云扫描")
        return 1, set()

    before = {p.name for p in INPUT_DIR.iterdir() if p.is_dir()} if INPUT_DIR.exists() else set()

    if args.no_cloud:
        logout("  /no-cloud：跳过扫描，只用 input 里已有的内容")
        return 0, set()

    stage("[1/3] 扫描云笔记（node html2note.js --cloud）")
    if not args.no_start:
        ensure_toolbox_server(toolbox)
        ensure_chrome()
    else:
        logout("  /no-start：不自动拉起 8088/9222，直接尝试扫描")

    if not port_open(9222):
        logout("  [跳过] 9222 没有可用的浏览器调试端口，云笔记读不到。")
        logout("         先跑「启动云笔记工具-笔记唤醒.bat」把工具箱和 Chrome 起来，再回来跑本脚本。")
        return 1, set()

    cmd = [node, str(html2note), "--cloud"]
    if args.cloud_all:
        cmd.append("--cloud-all")
    if args.cloud_limit is not None:
        cmd += ["--cloud-limit", str(args.cloud_limit)]
    logout("  执行：%s" % " ".join('"%s"' % c if " " in str(c) else str(c) for c in cmd))

    rc = 1
    timed_out = {"hit": False}
    try:
        proc = subprocess.Popen(
            [str(c) for c in cmd], cwd=str(ROOT),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0,
        )
    except OSError as exc:
        logout("  [警告] 启动 node 失败：%s" % exc)
        return 1, set()

    assert proc.stdout is not None

    def _kill() -> None:
        # 卡在登录/网络时 html2note 可能一行都不输出，readline 会一直等，
        # 所以用看门狗到点直接杀进程，让 readline 拿到 EOF。
        timed_out["hit"] = True
        try:
            proc.kill()
        except OSError:
            pass

    watchdog = threading.Timer(max(1, args.cloud_timeout), _kill)
    watchdog.start()
    try:
        while True:
            raw = proc.stdout.readline()
            if not raw:
                break
            line = _decode(raw).rstrip("\r\n")
            if line.strip():
                logout("  [cloud] " + line)
        rc = proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        logout("  [警告] 云扫描进程没有按时退出，已强制结束")
        rc = 1
    finally:
        watchdog.cancel()
        try:
            proc.stdout.close()
        except OSError:
            pass

    if timed_out["hit"]:
        logout("  [警告] 云扫描超过 %d 秒，已中止（--cloud-timeout 可调）" % args.cloud_timeout)
        rc = rc or 1

    after = {p.name for p in INPUT_DIR.iterdir() if p.is_dir()} if INPUT_DIR.exists() else set()
    added = after - before
    if rc == 0:
        logout("  扫描结束：本轮新增 %d 个笔记目录（input 现有 %d 个）" % (len(added), len(after)))
    else:
        logout("  [警告] 云扫描返回码 %d：可能是没登录/网络不通/云笔记侧报错。" % rc)
        logout("         上面若出现 login/auth 字样，请在 9222 的 Chrome 窗口里重新登录云笔记；")
        logout("         本次仍会用 input 里已有的内容继续答题。")
        logout("  本轮新增 %d 个笔记目录" % len(added))
    return rc, added


# ---------------------------------------------------------------- 选待答目录

def note_label(note_dir: Path) -> str:
    """output 文件名用的短标签。

    必须和原流水线一致：queue.safe_name 把空格和 \\ / : * ? " < > | 一律换成下划线
    （input 目录名 `..._ds 116_xxx` 对应的成品就是 `..._ds_116_xxx`），
    否则会「已有成品也当成没答过」重复调用模型。
    """
    return safe_name(note_dir.name)


def output_path(note_dir: Path) -> Path:
    return OUTPUT_DIR / (note_label(note_dir) + ANSWER_SUFFIX)


def published_html(note_dir: Path) -> Path:
    """原流水线的成品：已经有它，说明这条笔记早就答过并上传了。"""
    return OUTPUT_DIR / (note_label(note_dir) + "_答案横版.html")


def already_answered(note_dir: Path) -> Optional[str]:
    if output_path(note_dir).exists():
        return str(output_path(note_dir))
    if published_html(note_dir).exists():
        return str(published_html(note_dir))
    return None


def list_notes() -> List[Path]:
    if not INPUT_DIR.exists():
        raise Fatal("找不到 input 目录：%s" % INPUT_DIR)
    return [p for p in INPUT_DIR.iterdir() if p.is_dir()]


def select_notes(args, added: set) -> Tuple[List[Path], str]:
    """按「本轮新增 > 未答过的历史」的顺序挑，默认只取 --limit 条。"""
    notes = list_notes()
    mode = ""
    if args.only:
        wanted = [s.strip().lower() for s in args.only.split(",") if s.strip()]

        def _hit(p: Path) -> bool:
            # 只在「标题_id8」这一段里找，避免时间戳里的数字误命中
            tail = p.name.split("_", 1)[1].lower() if "_" in p.name else p.name.lower()
            return p.name.lower() in wanted or any(w in tail for w in wanted)

        notes = [p for p in notes if _hit(p)]
        mode = "按 --only 指定（子串匹配：%s）" % ", ".join(wanted)

    notes.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    pending = [p for p in notes if args.force or not already_answered(p)]
    limit = 0 if args.all else max(0, args.limit)

    if args.only_new:
        pending = [p for p in pending if p.name in added]
        mode = mode or "只看本轮新下载"

    if not mode and added:
        fresh = [p for p in pending if p.name in added]
        if fresh:
            pending, mode = fresh, "本轮新下载的笔记"
        else:
            mode = "历史回补（本轮新笔记都已有回答）"

    if not mode:
        mode = "历史回补（input 里还没答过的）" if not args.force else "按 --force 重答"
    return (pending[:limit] if limit else pending), mode


# ---------------------------------------------------------------- 引擎

def resolve_dsh(explicit: Optional[str]) -> Optional[str]:
    if explicit:
        return explicit if Path(explicit).exists() else None
    found = shutil.which("dsh")
    if found:
        return found
    if DEFAULT_DSH.exists():
        return str(DEFAULT_DSH)
    return None


def harness_sessions() -> set:
    """DSH 已经落盘的会话 id 集合。

    headless 跑完会把整段往返（提示词 + 回答 + 工具调用）写成
    <DSH_HOME>/sessions/<工作区>/session-*/session.v4.jsonl.zstd，
    在 DSH 里打开这个会话就能看到本条的全部记录。
    """
    home = os.environ.get("DSH_HOME") or str(Path.home() / ".dsh")
    try:
        return {p.parent.name for p in Path(home, "sessions").glob("*/session-*/*.jsonl.zstd")}
    except OSError:
        return set()


def call_harness(prompt: str, args) -> str:
    dsh = resolve_dsh(args.dsh)
    if not dsh:
        raise Fatal(
            "找不到 dsh 命令。请把 DSH 装好并让它进 PATH，或用 --dsh 指定 dsh.cmd 的完整路径。"
        )
    env = dict(os.environ)
    key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if key:
        env["DEEPSEEK_API_KEY"] = key

    cmd = [dsh, "--profile", args.profile, "--approval-policy", "never", "-"]
    logout("  命令：%s" % " ".join(cmd))
    before_sessions = harness_sessions()
    logout("  说明：提示词走 stdin，最终回答取 stdout；诊断信息在 stderr。")
    logout("  正在等待 harness 返回（最长 %d 秒；这期间控制台没有新行是正常的）…"
           % args.timeout)
    try:
        proc = subprocess.run(
            cmd, input=prompt.encode("utf-8"), cwd=str(ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=args.timeout,
        )
    except subprocess.TimeoutExpired:
        raise NoteError(
            "harness 超过 %d 秒没有返回（--timeout 可调）。图片多/题量大时正常，"
            "可以先 /limit 1 单条重试。" % args.timeout
        )
    out = _decode(proc.stdout).strip()
    err = _decode(proc.stderr).strip()

    if err:
        # 诊断信息也打出来，出问题时能直接看到是哪一层挂了
        logout("  [harness stderr]")
        for line in err.splitlines()[-20:]:
            logout("    " + line)

    new_sessions = sorted(harness_sessions() - before_sessions)
    if new_sessions:
        logout("  [harness 会话] %s" % "、".join(new_sessions))
        logout("    这次往返的完整记录已存进 DSH 会话；在 DSH 的会话列表里打开它，"
               "就能看到本条发出的请求和模型的回答。")

    if "MISSING_CREDENTIAL" in err or "no API key" in err:
        raise Fatal(
            "DeepSeek harness 没有可用的 API Key。三种给法任选一种：\n"
            "        1) 先在命令行 set DEEPSEEK_API_KEY=sk-xxx 再运行本脚本；\n"
            "        2) 把 key 单独一行写进 %s；\n"
            "        3) 把 key 写进 %s（或 config.api.json 的 apiKeyFile）。\n"
            "        脚本不会把 key 打印出来，也不会写进日志。"
            % (ROOT / ".workbuddy" / "deepseek.key", ROOT / "config.api.key")
        )
    if proc.returncode != 0 and not out:
        raise NoteError("harness 退出码 %d，且没有输出：\n        %s"
                        % (proc.returncode, (err or "（stderr 为空）")[:800]))
    if not out:
        raise NoteError("harness 没有输出内容（退出码 %d）" % proc.returncode)
    return out


def api_config() -> Dict[str, Any]:
    cfg = solver.load_api_config(ROOT)
    cfg["base_url"] = cfg.get("base_url") or os.environ.get("ANSWER_API_BASE") or DEFAULT_API_BASE
    cfg["model"] = cfg.get("model") or os.environ.get("ANSWER_API_MODEL") or DEFAULT_API_MODEL
    if not cfg.get("api_key"):
        cfg["api_key"] = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    return cfg


def call_api(note_dir: Path, meta: Dict[str, Any], images: List[Path],
             texts: List[Path], args) -> str:
    cfg = api_config()
    key = str(cfg.get("api_key") or "")
    if not key:
        raise Fatal(
            "走 HTTP 接口需要 key：设置 ANSWER_API_KEY 或 DEEPSEEK_API_KEY，"
            "或写进 %s。" % (ROOT / "config.api.key")
        )
    messages = build_api_messages(note_dir, meta, images, texts, cfg)
    url = apiclient.normalize_endpoint(str(cfg["base_url"]))
    logout("  POST %s" % url)
    logout("  model=%s | temperature=%s | system=%d 字符 | user 文本=%d 字符 | 图片=%d 张"
           % (cfg["model"], cfg.get("temperature", 0.3), len(messages[0]["content"]),
              len(messages[1]["content"][0]["text"]), len(messages[1]["content"]) - 1))
    logout("  Key：%s" % _mask(key))
    try:
        return apiclient.chat(
            messages,
            base_url=str(cfg["base_url"]),
            api_key=key,
            model=str(cfg["model"]),
            timeout=float(cfg.get("timeout") or 240),
            temperature=float(cfg.get("temperature") or 0.3),
            max_tokens=int(cfg["max_tokens"]) if cfg.get("max_tokens") else None,
            retries=int(cfg.get("retries") or 1),
        )
    except apiclient.ApiError as exc:
        status = getattr(exc, "status", None)
        hint = ""
        if status in (400, 413, 422):
            hint = ("\n        常见原因：这个模型不支持图片输入（deepseek-chat 是纯文本）。"
                    "改用 --engine harness，或把 base_url 指向支持视觉的 OpenAI 兼容端点。")
        elif status in (401, 403):
            hint = "\n        常见原因：key 无效、过期，或该 key 没有这个模型的权限。"
        elif status is None:
            hint = ("\n        常见原因：网络不通/代理拦截/DNS 失败。"
                    "确认能直接访问 %s。" % cfg["base_url"])
        raise NoteError("接口调用失败：%s%s" % (exc, hint))


# ---------------------------------------------------------------- 单条处理

def process_one(idx: int, total: int, note_dir: Path, engine: str, args) -> str:
    """返回 "ok" / "skip" / "fail"。异常已在这里转成提示。"""
    stage("[%d/%d] %s" % (idx, total, note_dir.name))
    try:
        if not note_dir.exists():
            raise NoteError("目录不存在")
        meta = read_resources(note_dir)
        images = note_images(note_dir, meta)
        texts = note_texts(note_dir)
        print_sources(note_dir, meta, images, texts)

        if not images and not texts:
            logout("  [跳过] 这条笔记没有可读的图片或文本，无法作答。")
            return "skip"

        stage("[%d/%d] 发往模型的请求" % (idx, total))
        if engine == "api":
            cfg = api_config()
            key = str(cfg.get("api_key") or "")
            if not key:
                raise Fatal("走 HTTP 接口需要 key（ANSWER_API_KEY / DEEPSEEK_API_KEY / config.api.key）")
            messages = build_api_messages(note_dir, meta, images, texts, cfg)
            logout("  端点：%s" % apiclient.normalize_endpoint(str(cfg["base_url"])))
            logout("  模型：%s" % cfg["model"])
            logout("  消息：system（%d 字符）+ user（文本 %d 字符 + %d 张图片内联）"
                   % (len(messages[0]["content"]),
                      len(messages[1]["content"][0]["text"]), len(messages[1]["content"]) - 1))
            logout("  ---- user 文本 ----")
            for line in messages[1]["content"][0]["text"].splitlines():
                logout("  | " + line)
            logout("  ---- 文本结束（图片以 base64 内联，不打印） ----")
            if args.dry_run:
                logout("  [dry-run] 未真正发送请求，也未写文件。")
                return "skip"
            answer = call_api(note_dir, meta, images, texts, args)
        else:
            prompt = build_harness_prompt(note_dir, meta, images, texts)
            logout("  完整提示词（%d 字符）：" % len(prompt))
            logout("  " + "-" * 70)
            for line in prompt.splitlines():
                logout("  | " + line)
            logout("  " + "-" * 70)
            if args.dry_run:
                logout("  [dry-run] 未调用 harness，也未写文件。")
                return "skip"
            answer = call_harness(prompt, args)

        logout("")
        logout("  ===== 模型返回的回答（原文） =====")
        for line in answer.splitlines():
            logout("  " + line)
        logout("  ===== 回答结束（%d 字符） =====" % len(answer))

        body = solver.clean_markdown(answer)
        if len(body.strip()) < 20:
            raise NoteError("清洗后的回答过短（%d 字符），当作失败处理，避免落一个空文件"
                            % len(body.strip()))

        dest = output_path(note_dir)
        if dest.exists() and not args.force:
            logout("  [跳过] 已有回答文件：%s（要重答加 /force）" % dest)
            return "skip"
        header = ("<!-- 来源：input/%s/　引擎：%s　生成时间：%s -->\n\n"
                  % (note_dir.name, "DeepSeek " + engine, _now()))
        try:
            dest.write_text(header + body, encoding="utf-8", newline="\n")
        except OSError as exc:
            raise NoteError("写文件失败：%s（%s）" % (dest, exc))

        logout("")
        logout("  [已落盘] %s" % dest)
        logout("            %d 字符 / %d 行；对应来源 input\\%s"
               % (len(header + body), len((header + body).splitlines()), note_dir.name))
        return "ok"
    except Fatal:
        raise
    except NoteError as exc:
        logout("  [失败] %s" % exc)
        return "fail"
    except Exception as exc:  # noqa: BLE001 - 单条出意外不该打断整批
        logout("  [失败] 未预期错误：%s: %s" % (type(exc).__name__, exc))
        return "fail"


# ---------------------------------------------------------------- 入口

def normalize_argv(argv: List[str]) -> List[str]:
    """兼容原工具的 /xxx 风格：/all -> --all，/limit 3 -> --limit 3。"""
    mapping = {
        "/all": "--all", "/dry": "--dry-run", "/force": "--force",
        "/no-cloud": "--no-cloud", "/cloud-all": "--cloud-all",
        "/no-start": "--no-start", "/help": "--help", "/?": "--help",
        "/only-new": "--only-new",
    }
    with_value = {"/limit": "--limit", "/engine": "--engine",
                  "/cloud-limit": "--cloud-limit", "/toolbox": "--toolbox",
                  "/timeout": "--timeout", "/profile": "--profile",
                  "/cloud-timeout": "--cloud-timeout",
                  "/dsh": "--dsh", "/only": "--only"}
    out: List[str] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        low = tok.lower()
        if low in with_value:
            out.append(with_value[low])
            if i + 1 < len(argv):
                out.append(argv[i + 1])
                i += 2
                continue
            i += 1
            continue
        out.append(mapping.get(low, tok))
        i += 1
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="note_answer.py",
        description="云笔记 -> input -> DeepSeek -> output 全流程",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例：
  python scripts\\note_answer.py                     # 扫云笔记，答最新的 3 条
  python scripts\\note_answer.py /all                # 扫完把没答过的全答了
  python scripts\\note_answer.py /no-cloud /limit 1  # 只答 input 里已有的 1 条
  python scripts\\note_answer.py /dry                # 只打印计划，不调用模型
  python scripts\\note_answer.py /engine api         # 走 HTTP 接口而不是 harness
""",
    )
    p.add_argument("--limit", type=int, default=3, help="本轮最多答几条（0=全部，默认 3）")
    p.add_argument("--all", action="store_true", help="等价 --limit 0")
    p.add_argument("--engine", choices=["harness", "api", "auto"], default=DEFAULT_ENGINE,
                   help="harness=本机 dsh（默认）；api=OpenAI 兼容接口；auto=有接口配置就用 api")
    p.add_argument("--force", action="store_true", help="已有回答文件也重答")
    p.add_argument("--only", default="", help="只处理指定 label，逗号分隔")
    p.add_argument("--only-new", action="store_true", help="只处理本轮云扫描新增的目录")
    p.add_argument("--dry-run", action="store_true", help="只打印，不调用模型、不写文件")
    p.add_argument("--no-cloud", action="store_true", help="跳过云扫描")
    p.add_argument("--cloud-all", action="store_true", help="扫描时回补历史（html2note --cloud-all）")
    p.add_argument("--cloud-limit", type=int, default=None, help="html2note --cloud-limit")
    p.add_argument("--cloud-timeout", type=int, default=300, help="云扫描超时秒数")
    p.add_argument("--no-start", action="store_true", help="不自动拉起 8088/9222")
    p.add_argument("--toolbox", default=None, help="工具箱目录（含 serve.js）")
    p.add_argument("--profile", default="headless", help="dsh profile 名")
    p.add_argument("--dsh", default=None, help="dsh 可执行文件路径")
    p.add_argument("--timeout", type=int, default=600, help="单条 harness 超时秒数")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(normalize_argv(sys.argv[1:] if argv is None else argv))
    if args.all:
        args.limit = 0
    _open_log()
    try:
        return _main(args)
    except Fatal as exc:
        logout("")
        logout("[致命] %s" % exc)
        return EXIT_FATAL
    finally:
        logout("")
        logout("# 日志：%s" % LOG_PATH)
        _close_log()


def _main(args) -> int:
    rule("云笔记 -> input -> DeepSeek -> output")
    logout("  时间     : %s" % _now())
    logout("  工作根   : %s" % ROOT)
    logout("  input    : %s（现有 %d 个目录）"
           % (INPUT_DIR, len(list_notes()) if INPUT_DIR.exists() else 0))
    logout("  output   : %s" % OUTPUT_DIR)
    logout("  引擎     : %s%s" % (args.engine, "（dry-run）" if args.dry_run else ""))
    try:
        spec, spec_src = solver.load_spec(ROOT)
        logout("  写作规范 : %s（%d 字符）" % (spec_src, len(spec)))
    except Exception as exc:  # noqa: BLE001 - 规范读不到只是回退，不阻断
        logout("  写作规范 : 读取失败（%s），将用内置精简版" % exc)

    cfg = api_config()
    key, key_src = read_key(cfg)
    logout("  API Key  : %s ← %s" % (_mask(key), key_src))
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise Fatal("创建 output 目录失败：%s（%s）" % (OUTPUT_DIR, exc))
    if not key:
        logout("  [提示] 现在没有任何 key。默认的 harness 引擎仍会尝试使用 DSH 已保存的凭据；")
        logout("         如果它报 MISSING_CREDENTIAL，按下面任一方式给 key 再跑：")
        logout("           set DEEPSEEK_API_KEY=sk-xxx")
        logout("           或写一行到 %s" % (ROOT / ".workbuddy" / "deepseek.key"))
        logout("           或写一行到 %s" % (ROOT / "config.api.key"))

    toolbox = find_toolbox(args.toolbox)
    _rc, added = cloud_scan(args, toolbox)

    engine = args.engine
    if engine == "auto":
        engine = "api" if (cfg.get("base_url") or CFG_API.exists()) else "harness"
        logout("  引擎 auto 解析为：%s" % engine)

    notes, mode = select_notes(args, added)
    stage("[2/3] 逐条答题（选中 %d 条；%s）" % (len(notes), mode))
    if not args.force:
        done = [p for p in list_notes() if already_answered(p)]
        logout("  已有成品 : %d 条跳过（_回答.md 或 _答案横版.html 已存在；/force 可重答）"
               % len(done))
    if not notes:
        logout("  没有待处理的笔记。")
        if not added:
            logout("  input 里没有「还没答过」的目录。若云笔记确实有新内容，先看上面 [cloud] 的输出；")
            logout("  首次运行只会建立基线（下载 0 条），再跑一次才会拿到新笔记。")
        rule("[3/3] 完成")
        logout("  成功 0 / 跳过 0 / 失败 0；没有产生任何文件。")
        return EXIT_OK

    for i, note in enumerate(notes, 1):
        logout("")
        logout("  → 待答：%s" % note.name)
        logout("     回答将写入：%s" % output_path(note))

    counters = {"ok": 0, "skip": 0, "fail": 0}
    results: List[Tuple[str, str]] = []
    fatal: Optional[Fatal] = None
    for i, note in enumerate(notes, 1):
        try:
            state = process_one(i, len(notes), note, engine, args)
        except Fatal as exc:
            fatal = exc
            logout("")
            logout("  [中断] %s" % exc)
            break
        counters[state] += 1
        results.append((note.name, state))

    rule("[3/3] 汇总")
    logout("  云扫描新增 : %d 个笔记目录" % len(added))
    for label, state in results:
        text = {"ok": "[OK]  ", "skip": "[跳过]", "fail": "[失败]"}[state]
        target = str(output_path(INPUT_DIR / label)) if state == "ok" else ""
        logout("  %s %s %s" % (text, label, ("-> " + target) if target else ""))
    logout("  成功 %d / 跳过 %d / 失败 %d（共 %d 条）"
           % (counters["ok"], counters["skip"], counters["fail"], len(results)))

    if fatal is not None:
        logout("  致命错误：%s" % fatal)
        left = len(notes) - len(results)
        if left > 0:
            logout("  已中止，还剩 %d 条没跑；把上面的问题解决后重跑同一条命令即可，"
                   "已成功的不会重复调用（除非 /force）。" % left)
        return EXIT_FATAL
    if counters["fail"]:
        logout("  有 %d 条失败，修好上面的提示后重跑；已成功的不会重复调用（除非 /force）。"
               % counters["fail"])
        return EXIT_PARTIAL
    logout("  全部处理完毕。回答文件在 %s" % OUTPUT_DIR)
    return EXIT_OK


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[中断] 用户取消。", file=sys.stderr)
        sys.exit(130)
