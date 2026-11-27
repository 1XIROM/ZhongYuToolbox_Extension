"""solve：把队列里的一条任务交给模型写草稿，并自动跑 render --check。

它只替代「外部 AI 读图写稿」这一段，不碰 render/publish/done：
  - 输入：队列记录（素材目录、primary_file、preview_file）
  - 输出：.workbuddy/auto/drafts/<label>_答案.md
  - 自检：调 scripts/pipeline.py render <md> --check，不通过就把报错回喂模型重写一次

为什么不在这里直接 publish：
  发布进 output/ 即终版，且 html2note 会立刻把它转 PDF 传上云笔记。
  写稿环节出错时最该做的是停下来看，而不是替用户按下发布。

写作规范不重复维护：直接从上位提示词文件里抽取【草稿格式】…【禁止】那几节，
保证 API 链路和手工链路用的是同一套格式约束。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import apiclient
from .paths import Config

SPEC_BEGIN = "【草稿格式】"
SPEC_END = "【何时必须产出 HTML】"
SPEC_FILE = "提示词-外部AI接管答题链路.md"
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}

DEFAULT_SPEC = """【草稿格式】
- 首行写 `# 学科 · 主题`。
- 每个题块以 `## ` 开头，一题一块；带编号的写「## 第 3 题（多选题）」。
- `**答案**：` 给出结论；`**解析**` 用【第一步：…】【第二步：…】分段。
- 末段写 `**高中范围**：…`。
- 公式一律写成行内 `$...$`，禁止 `$$...$$`、`\\[ \\]`、`\\( \\)`。
- 中文引号用全角 “ ”。

【版式硬约束】
- 横向 A4，每页至多一题。
- 只用标准 Markdown 语义标签。
"""


# ---------------------------------------------------------------- 配置

def load_api_config(root: Path) -> Dict[str, Any]:
    """读 config.api.json，再让环境变量覆盖。

    优先级：环境变量 > config.api.json > 默认值。
    这样 CI/临时换 key 不必改文件，而日常使用把 key 写进 apiKeyFile 也不会进仓库。
    """
    cfg: Dict[str, Any] = {
        "base_url": "",
        "api_key": "",
        "model": "",
        "temperature": 0.3,
        "max_tokens": 0,
        "timeout": apiclient.DEFAULT_TIMEOUT,
        "retries": 1,
        "max_images": 4,
        "max_width": 1600,
    }
    path = root / "config.api.json"
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - 配置坏了要原样告诉用户
            raise apiclient.ApiError("config.api.json 解析失败：%s" % exc) from exc
        for key in ("base_url", "api_key", "model"):
            if raw.get(key):
                cfg[key] = str(raw[key]).strip()
        for key in ("temperature", "max_tokens", "timeout", "retries", "max_images", "max_width"):
            if raw.get(key) is not None:
                cfg[key] = raw[key]
        key_file = raw.get("apiKeyFile")
        if key_file and not cfg["api_key"]:
            kp = Path(key_file)
            if not kp.is_absolute():
                kp = root / kp
            if kp.exists():
                cfg["api_key"] = kp.read_text(encoding="utf-8").strip().splitlines()[0].strip()

    env_map = {
        "ANSWER_API_BASE": "base_url",
        "ANSWER_API_KEY": "api_key",
        "ANSWER_API_MODEL": "model",
    }
    for env, key in env_map.items():
        val = os.environ.get(env)
        if val:
            cfg[key] = val.strip()

    # api_key 允许带 "Bearer " 前缀（有人会照抄文档），这里去掉避免重复
    if cfg["api_key"].lower().startswith("bearer "):
        cfg["api_key"] = cfg["api_key"][7:].strip()
    return cfg


# ---------------------------------------------------------------- 素材

def _is_image(p: Optional[Path]) -> bool:
    return bool(p) and p.suffix.lower() in IMAGE_EXT


def pick_images(cfg: Config, record: Dict[str, Any], limit: int) -> List[Path]:
    """挑要送给模型的图。

    顺序有讲究：原图（primary，resources.json 里 type=0）优先，整页预览
    （preview，00_screenshot.png）只作兜底——它含大片纸面空白、题目被缩小，
    小字号和坐标刻标最容易在这里读错。
    """
    out: List[Path] = []
    seen = set()

    def add(p: Optional[Path]) -> None:
        if p and _is_image(p) and p.exists():
            key = str(p.resolve()).lower()
            if key not in seen:
                seen.add(key)
                out.append(p)

    add(cfg.abs(record.get("primary_file")))
    for item in record.get("materials") or []:
        add(cfg.abs(item))
    add(cfg.abs(record.get("preview_file")))
    return out[:limit]


def maybe_shrink(path: Path, max_width: int, out_dir: Optional[Path] = None) -> Path:
    """大于 max_width 的图用 ffmpeg 等比缩小，减少请求体与 token。

    ffmpeg 不在或失败时原样返回——缩放只是省钱，不该成为失败点。
    缩出来的副本放在调用方给的临时目录里，由调用方负责清理。
    """
    if not max_width or max_width <= 0:
        return path
    exe = shutil.which("ffmpeg")
    if not exe:
        return path
    try:
        probe = subprocess.run(
            [shutil.which("ffprobe") or "ffprobe", "-v", "error",
             "-select_streams", "v:0", "-show_entries", "stream=width",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
        )
        width = int((probe.stdout or "0").strip() or 0)
    except Exception:  # noqa: BLE001 - 探测失败就不缩
        return path
    if width <= max_width:
        return path

    out_dir = out_dir or Path(tempfile.mkdtemp(prefix="answer-img-"))
    tmp = out_dir / (path.stem + ".png")
    try:
        subprocess.run(
            [exe, "-y", "-v", "error", "-i", str(path),
             "-vf", "scale=%d:-2:flags=lanczos" % max_width, str(tmp)],
            capture_output=True, timeout=120, check=True,
        )
        return tmp if tmp.exists() else path
    except Exception:  # noqa: BLE001 - 缩放失败退回原图
        return path


# ---------------------------------------------------------------- 提示词

def load_spec(root: Path) -> Tuple[str, str]:
    """从提示词文件里抽写作规范；抽不到就用内置精简版。

    返回 (正文, 来源说明)。来源说明会打印出来，方便确认用的是哪一版规范。
    """
    path = root / SPEC_FILE
    if not path.exists():
        return DEFAULT_SPEC, "内置精简版"
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001 - 读不到就用兜底
        return DEFAULT_SPEC, "内置精简版"
    start_m = re.search(r"^" + re.escape(SPEC_BEGIN), text, re.M)
    if not start_m:
        return DEFAULT_SPEC, "内置精简版"
    start = start_m.start()
    end_m = re.search(r"^" + re.escape(SPEC_END), text[start:], re.M)
    end = start + end_m.start() if end_m else len(text)
    block = text[start:end if end > start else len(text)].strip()
    if len(block) < 200:
        return DEFAULT_SPEC, "内置精简版"
    return block, os.path.basename(str(path))


def build_system_prompt(root: Path) -> str:
    spec, _ = load_spec(root)
    return (
        "你是「题目自动答题管线」的写稿环节。你只做一件事：读题目图片，"
        "写出完全符合下面规范的答案草稿（Markdown）。\n"
        "不要调用任何工具，不要提问，不要复述任务，不要在正文前后加说明。\n\n"
        "===== 写作规范（必须逐条遵守）=====\n"
        + spec + "\n===== 规范结束 =====\n\n"
        "额外输出要求：\n"
        "1. 直接从 `# 学科 · 主题` 这一行开始；\n"
        "2. 不要用 ``` 代码块包住整篇；\n"
        "3. 看不清或残缺的题整题略过，不要猜；\n"
        "4. 每个题块（##）必须同时有 答案 / 解析 / 高中范围 三段。\n"
    )


def clean_markdown(text: str) -> str:
    """把模型输出洗成纯 Markdown 正文。"""
    s = text.replace("\r\n", "\n").replace("\u3000", " ").strip()
    s = re.sub(r"^\ufeff", "", s)

    # 整篇被 ```markdown ... ``` 包住的情况最常见
    fence = re.match(r"^```[a-zA-Z]*\n(.*)\n```$", s, re.S)
    if fence:
        s = fence.group(1).strip()

    # 前面可能有一段客套话：从第一个一级标题起截
    lines = s.split("\n")
    for i, line in enumerate(lines):
        if line.strip().startswith("# "):
            s = "\n".join(lines[i:])
            break

    # 末尾可能还有一句"以上是……"，只在它不是正文格式时才切
    s = re.sub(r"\n+```\s*$", "", s).strip()
    return s + "\n"


# ---------------------------------------------------------------- 队列

def _load_record(rec_path: Path) -> Dict[str, Any]:
    """读队列记录，并把来源路径挂在 _queue_path 上（不写回文件）。"""
    rec = json.loads(Path(rec_path).read_text(encoding="utf-8"))
    rec["_queue_path"] = str(Path(rec_path).resolve())
    return rec


def pick_record(cfg: Config, task: Optional[str]) -> Dict[str, Any]:
    """按 路径 / label / 第一条待答 的顺序找任务。"""
    qdir = cfg.queue
    if task:
        p = Path(task)
        if p.exists():
            return _load_record(p)
        cand = sorted(qdir.glob("%s_*.json" % task)) if qdir.exists() else []
        if not cand:
            raise RuntimeError("找不到任务：%s（既不是文件，也不是队列里的 label）" % task)
        return _load_record(cand[0])

    pending = list_pending(cfg)
    if not pending:
        raise RuntimeError("队列里没有待答任务；先跑 `status` 看看，或用 scan 扫一次")
    return pending[0]


def list_pending(cfg: Config) -> List[Dict[str, Any]]:
    """按 detected_at 升序返回全部待答任务。"""
    records: List[Dict[str, Any]] = []
    qdir = cfg.queue
    if qdir.exists():
        for f in qdir.glob("*.json"):
            try:
                records.append(_load_record(f))
            except Exception:  # noqa: BLE001 - 坏记录跳过，不阻塞其他任务
                continue
    pending = [r for r in records if r.get("status") == "pending_answer"]
    pending.sort(key=lambda r: str(r.get("detected_at") or ""))
    return pending


# ---------------------------------------------------------------- 自检

def render_check(cfg: Config, md: Path, extra_args: List[str]) -> Tuple[bool, str]:
    """跑 render --check，返回 (是否通过, 完整输出)。"""
    script = cfg.root / "scripts" / "pipeline.py"
    if not script.exists():
        return True, "[solve] 找不到 scripts/pipeline.py，跳过自检"
    # 必须把 --state 一起传下去：调用方可能用 --state 指向临时目录，
    # 子进程若只拿到 --root，会写回真实的 .workbuddy/auto/staging。
    cmd = [sys.executable, str(script), "render", str(md), "--check",
           "--root", str(cfg.root), "--state", str(cfg.state_dir)] + extra_args
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=900, env=env, cwd=str(cfg.root),
        )
    except subprocess.TimeoutExpired:
        return False, "[solve] render --check 超时（900s）"
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    ok = proc.returncode == 0 and "阻断" not in out and "未通过" not in out
    return ok, out


def _feedback_message(check_out: str) -> str:
    clipped = check_out[-3000:]
    return (
        "上一版草稿没有通过渲染自检，输出如下：\n\n"
        "```text\n" + clipped + "\n```\n\n"
        "请按报错修改，只输出修正后的完整 Markdown 正文，"
        "不要解释改了什么，也不要用代码块包住整篇。"
    )


# ---------------------------------------------------------------- 主流程

def solve_one(cfg: Config, record: Dict[str, Any], api: Dict[str, Any],
              *, do_check: bool = True, dry_run: bool = False,
              verbose: bool = True) -> Dict[str, Any]:
    """对一个任务跑完整流程；返回结果摘要（供 CLI 打印）。"""
    label = record.get("label") or "task"
    # 草稿路径以 cfg.drafts 为准（尊重 --state 覆盖）：记录里的 answers_md 是相对
    # 项目根的路径，直接用它会在 --state 指向临时目录时仍写回真实的 .workbuddy/auto。
    md_path = cfg.drafts / ("%s_答案.md" % label)
    images = pick_images(cfg, record, int(api.get("max_images") or 4))
    if not images:
        raise RuntimeError("任务 %s 没有可读的图片素材（primary_file/materials 都是空的）" % label)

    prepared: List[Path] = []
    tmpdir: Optional[Path] = None
    try:
        max_w = int(api.get("max_width") or 0)
        for p in images:
            shrunk = maybe_shrink(p, max_w, tmpdir)
            if shrunk != p and tmpdir is None:
                tmpdir = shrunk.parent
            prepared.append(shrunk)

        order = []
        for i, p in enumerate(prepared, 1):
            if "screenshot" in p.name.lower():
                order.append("- 第 %d 张：整页预览，只用来核对有没有漏题，小字不要照它读" % i)
            else:
                order.append("- 第 %d 张：题目原图，以它为准" % i)
        user_text = (
            "任务：%s\n素材目录：%s\n共 %d 张图片，按顺序给出：\n%s\n\n请读题后写出答案草稿。"
            % (label, record.get("source_dir") or record.get("source_pdf") or "",
               len(prepared), "\n".join(order))
        )
        content: List[Dict[str, Any]] = [apiclient.text_part(user_text)]
        for p in prepared:
            content.append(apiclient.image_part(p))
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": build_system_prompt(cfg.root)},
            {"role": "user", "content": content},
        ]

        if dry_run:
            spec_src = load_spec(cfg.root)[1]
            endpoint = api.get("base_url") or ""
            if endpoint:
                try:
                    endpoint = apiclient.normalize_endpoint(endpoint)
                except apiclient.ApiError:
                    pass
            else:
                endpoint = "(未配置 base_url)"
            return {
                "label": label, "dry_run": True, "images": [str(p) for p in prepared],
                "spec_source": spec_src, "md": str(md_path),
                "messages_chars": len(json.dumps(messages, ensure_ascii=False)),
                "system_chars": len(messages[0]["content"]),
                "endpoint": endpoint,
                "model": api.get("model") or "",
            }

        text = apiclient.chat(
            messages,
            base_url=api["base_url"],
            api_key=api["api_key"],
            model=api["model"],
            timeout=float(api.get("timeout") or apiclient.DEFAULT_TIMEOUT),
            temperature=float(api.get("temperature", 0.3)),
            max_tokens=int(api.get("max_tokens") or 0) or None,
            retries=int(api.get("retries") or 0),
        )
        md = clean_markdown(text)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(md, encoding="utf-8")
        if verbose:
            print("[solve] 草稿已写入 %s（%d 字）" % (md_path, len(md)))

        result: Dict[str, Any] = {"label": label, "md": str(md_path), "chars": len(md),
                                  "images": len(prepared), "checked": False, "passed": None,
                                  "attempts": 1}
        if not do_check:
            return result

        ok, out = render_check(cfg, md_path, [])
        result["checked"] = True
        result["passed"] = ok
        if verbose:
            print(out)
        if ok:
            return result

        # 只回喂一次：再错说明是格式理解问题，继续烧 token 不划算
        if verbose:
            print("[solve] 自检未通过，回喂报错重写一次…")
        messages.append({"role": "assistant", "content": md})
        messages.append({"role": "user", "content": _feedback_message(out)})
        text2 = apiclient.chat(
            messages,
            base_url=api["base_url"], api_key=api["api_key"], model=api["model"],
            timeout=float(api.get("timeout") or apiclient.DEFAULT_TIMEOUT),
            temperature=float(api.get("temperature", 0.3)),
            max_tokens=int(api.get("max_tokens") or 0) or None,
            retries=int(api.get("retries") or 0),
        )
        md2 = clean_markdown(text2)
        md_path.write_text(md2, encoding="utf-8")
        ok2, out2 = render_check(cfg, md_path, [])
        result.update({"chars": len(md2), "passed": ok2, "attempts": 2})
        if verbose:
            print(out2)
        return result
    finally:
        # 缩图用的临时目录，无论成功失败都清掉
        if tmpdir is not None:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------- 收尾与失败记录

FAIL_FILE = "solve-failures.json"


def _fail_path(cfg: Config) -> Path:
    return cfg.state_dir / FAIL_FILE


def load_failures(cfg: Config) -> Dict[str, Any]:
    p = _fail_path(cfg)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - 记录坏了就当没有
        return {}


def note_failure(cfg: Config, label: str, reason: str) -> int:
    """累加失败次数，返回累计值。

    常驻模式必须记这个：同一个任务反复失败，不记次数就会一直重试、一直烧 token。
    """
    data = load_failures(cfg)
    row = data.get(label) or {}
    row["count"] = int(row.get("count") or 0) + 1
    row["reason"] = " ".join(str(reason).split())[:300]
    row["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    data[label] = row
    try:
        _fail_path(cfg).parent.mkdir(parents=True, exist_ok=True)
        _fail_path(cfg).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001 - 写不进去不该影响主流程
        pass
    return row["count"]


def clear_failure(cfg: Config, label: str) -> None:
    data = load_failures(cfg)
    if label in data:
        data.pop(label, None)
        try:
            _fail_path(cfg).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass


def finish_task(cfg: Config, record: Dict[str, Any], md_path: Path,
                verbose: bool = True) -> Tuple[bool, str]:
    """校验并发布到 output/，再把队列标记 done。

    走的是和手工链路完全相同的 mark_done（内部先 publish 再改队列），
    所以 API 出的成品与人工 publish 的成品在 output/ 里没有任何差别，
    后面的 html2note 也无从区分。
    """
    from . import queue as q
    from .check import publish

    qp = record.get("_queue_path")
    if not qp:
        return False, "记录里没有队列路径（不是从队列加载的任务）"
    label = record.get("label") or "task"
    staged = cfg.staging / ("%s_答案横版.html" % label)
    if not staged.exists():
        return False, "找不到渲染产物：%s" % staged

    rec = q.mark_done(cfg, Path(qp), str(md_path), str(staged), publish_fn=publish)
    if rec is None:
        return False, "找不到队列记录：%s" % qp
    if "_publish_failed" in rec:
        return False, "成品未通过校验，仍留在缓存"
    out_path = cfg.abs(rec.get("output_html")) or staged
    if verbose:
        print("[solve] 已发布并标记完成：%s" % out_path)
    return True, str(out_path)
