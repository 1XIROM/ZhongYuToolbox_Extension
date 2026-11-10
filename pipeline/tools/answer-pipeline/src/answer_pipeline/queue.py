"""素材监测、去重、队列维护。

input 下支持两种来源：
  A. 一级子文件夹（笔记导出包）：文件夹 = 一个任务，里面的图片/PDF 是素材，
     resources.json 只是资源清单，.mdb/.bin 是二进制快照，忽略。
  B. 根目录下直接放置的 PDF：一个 PDF = 一个任务（兼容旧方式）。

去重不看文件夹名，而看素材内容的指纹：同一张题图改名、重新导出、
笔记标题变化都不该重复作答。队列里存的是相对 root 的路径，换机器仍可用。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .lock import FileLock
from .paths import Config

CST = timezone(timedelta(hours=8))
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
SCHEMA = 2


def now() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


def safe_name(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_")
    return (cleaned or "task")[:80]


def label_of(record: dict) -> str:
    if record.get("label"):
        return record["label"]
    raw = record.get("source_pdf") or record.get("source_dir") or ""
    return Path(raw).name


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_DIGEST_CACHE: Dict[tuple, str] = {}


def _file_md5(path: Path) -> str:
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size)
    if key not in _DIGEST_CACHE:
        _DIGEST_CACHE[key] = hashlib.md5(path.read_bytes()).hexdigest()
    return _DIGEST_CACHE[key]


def folder_digest(folder: Path, files: List[Path], with_paths: bool = False) -> str:
    """按素材内容指纹去重。

    默认只认内容、不认文件名：同一张题图被改名、重新导出、笔记标题变化
    都视为同一任务，不会重复作答。with_paths=True 是旧算法（路径参与哈希），
    仅用于兼容历史 manifest 里已存在的键。
    """
    h = hashlib.sha256()
    materials = sorted(
        (p for p in files if p.suffix.lower() in (IMAGE_EXT | {".pdf"})),
        key=lambda x: x.relative_to(folder).as_posix(),
    )
    if materials:
        for p in materials:
            if with_paths:
                h.update(p.relative_to(folder).as_posix().encode("utf-8"))
            h.update(_file_md5(p).encode("ascii"))
    else:
        for p in files:
            h.update(p.relative_to(folder).as_posix().encode("utf-8"))
            h.update(str(p.stat().st_size).encode("ascii"))
    return h.hexdigest()


def folder_already_seen(processed: Dict[str, dict], folder: Path, files: List[Path]) -> Optional[str]:
    """返回命中的旧键（若有）。

    历史上加入过带路径的算法，产生的键仍然有效；否则升级后所有老素材
    都会被当成新任务重新入队。
    """
    for with_paths in (False, True):
        key = "dir:" + folder_digest(folder, files, with_paths=with_paths)
        if key in processed:
            return key
    return None


class Manifest:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def load(self) -> dict:
        p = self.cfg.manifest
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                data.setdefault("processed", {})
                return data
            except Exception:
                return {"processed": {}}
        return {"processed": {}}

    def save(self, data: dict) -> None:
        self.cfg.state_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.manifest.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def input_folders(cfg: Config) -> List[Path]:
    if not cfg.input_dir.exists():
        return []
    return sorted((p for p in cfg.input_dir.iterdir() if p.is_dir()), key=lambda x: x.name)


def folder_files(folder: Path) -> List[Path]:
    return sorted(p for p in folder.rglob("*") if p.is_file())


def collect_folder(folder: Path) -> dict:
    files = folder_files(folder)
    images = [p for p in files if p.suffix.lower() in IMAGE_EXT]
    pdfs = [p for p in files if p.suffix.lower() == ".pdf"]
    materials = images + pdfs
    primary = next((p for p in images if "screenshot" in p.name.lower()), None)
    if primary is None and materials:
        primary = materials[0]
    meta = folder / "resources.json"
    return {"files": files, "materials": materials, "primary": primary,
            "meta": meta if meta.exists() else None, "pdfs": pdfs}


def is_settled(files: List[Path], settle_seconds: float) -> bool:
    """文件夹内最新文件的修改时间距今超过阈值，视为写入完成。"""
    if not files:
        return False
    newest = max(p.stat().st_mtime for p in files)
    return time.time() - newest >= settle_seconds


def _record_paths(cfg: Config, label: str) -> dict:
    return {
        "answers_md": cfg.rel(cfg.drafts / f"{label}_答案.md"),
        "staged_html": cfg.rel(cfg.staging / f"{label}_答案横版.html"),
        "output_html": cfg.rel(cfg.output_dir / f"{label}_答案横版.html"),
    }


def scan(cfg: Config) -> List[dict]:
    """扫描新增任务；返回本次新增的队列记录（已写入 queue/*.json）。"""
    mf = Manifest(cfg)
    data = mf.load()
    cfg.queue.mkdir(parents=True, exist_ok=True)
    added: List[dict] = []

    for d in input_folders(cfg):
        info = collect_folder(d)
        files = info["files"]
        if not files or not is_settled(files, cfg.settle_seconds):
            continue
        if folder_already_seen(data["processed"], d, files):
            continue
        digest = folder_digest(d, files)
        key = "dir:" + digest
        label = safe_name(d.name)
        qf = cfg.queue / f"{label}_{digest[:8]}.json"
        primary = info["primary"]
        record = {
            "schema": SCHEMA,
            "kind": "folder",
            "source_dir": cfg.rel(d),
            "source_name": d.name,
            "label": label,
            "sha256": key,
            "detected_at": now(),
            "status": "pending_answer",
            "files": [{"path": cfg.rel(p), "name": p.name, "size": p.stat().st_size}
                      for p in files],
            "materials": [cfg.rel(p) for p in info["materials"]],
            "primary_file": cfg.rel(primary) if primary else None,
            "resources_json": cfg.rel(info["meta"]) if info["meta"] else None,
            "pdf_files": [cfg.rel(p) for p in info["pdfs"]],
            **_record_paths(cfg, label),
        }
        qf.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        data["processed"][key] = {
            "file": cfg.rel(d), "kind": "folder", "sha256": key,
            "first_seen": record["detected_at"], "status": "queued",
            "queue": cfg.rel(qf),
        }
        added.append(record)

    pdfs = sorted(cfg.input_dir.glob("*.pdf")) if cfg.input_dir.exists() else []
    for p in pdfs:
        key = sha256_file(p)
        if key in data["processed"]:
            continue
        label = safe_name(p.stem)
        qf = cfg.queue / f"{label}_{key[:8]}.json"
        record = {
            "schema": SCHEMA,
            "kind": "file", "source_pdf": cfg.rel(p), "label": label, "sha256": key,
            "size": p.stat().st_size, "detected_at": now(),
            "status": "pending_answer",
            "materials": [cfg.rel(p)], "primary_file": cfg.rel(p),
            "pdf_files": [cfg.rel(p)],
            **_record_paths(cfg, label),
        }
        qf.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        data["processed"][key] = {
            "file": cfg.rel(p), "kind": "file", "sha256": key,
            "first_seen": record["detected_at"], "status": "queued",
            "queue": cfg.rel(qf),
        }
        added.append(record)

    if added:
        mf.save(data)
    return added


def scan_locked(cfg: Config) -> List[dict]:
    with FileLock(cfg.lock_path, cfg.lock_timeout):
        return scan(cfg)


def init_baseline(cfg: Config) -> tuple:
    """把现有素材记为基线，之后只处理新增的。"""
    with FileLock(cfg.lock_path, cfg.lock_timeout):
        mf = Manifest(cfg)
        data = mf.load()
        for d in input_folders(cfg):
            files = folder_files(d)
            if not files:
                continue
            if folder_already_seen(data["processed"], d, files):
                continue
            key = "dir:" + folder_digest(d, files)
            data["processed"].setdefault(key, {
                "file": cfg.rel(d), "kind": "folder", "sha256": key,
                "first_seen": now(), "status": "baseline",
                "note": "初始化时已存在，不触发答题",
            })
        if cfg.input_dir.exists():
            for p in sorted(cfg.input_dir.glob("*.pdf")):
                key = sha256_file(p)
                data["processed"].setdefault(key, {
                    "file": cfg.rel(p), "kind": "file", "sha256": key,
                    "first_seen": now(), "status": "baseline",
                    "note": "初始化时已存在，不触发答题",
                })
        mf.save(data)
        n_folder = sum(1 for v in data["processed"].values() if v.get("kind") == "folder")
        return n_folder, len(data["processed"]) - n_folder


def pending_records(cfg: Config) -> List[dict]:
    if not cfg.queue.exists():
        return []
    out = []
    for p in sorted(cfg.queue.glob("*.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if rec.get("status") == "pending_answer":
            rec["_queue_file"] = str(p)
            out.append(rec)
    return out


def load_record(cfg: Config, queue_path) -> Optional[dict]:
    qf = Path(queue_path)
    if not qf.is_absolute():
        qf = (cfg.root / qf)
    if not qf.exists():
        return None
    rec = json.loads(qf.read_text(encoding="utf-8"))
    rec["_queue_file"] = str(qf)
    return rec


def save_record(cfg: Config, rec: dict) -> None:
    qf = Path(rec.pop("_queue_file"))
    qf.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    rec["_queue_file"] = str(qf)


def mark_done(cfg: Config, queue_path, md: Optional[str], html: Optional[str],
              publish_fn) -> Optional[dict]:
    """标记完成；若成品仍在缓存里，先校验并发布，失败则不标记。"""
    from .check import publish

    with FileLock(cfg.lock_path, cfg.lock_timeout):
        rec = load_record(cfg, queue_path)
        if rec is None:
            return None
        rec["status"] = "done"
        rec["finished_at"] = now()
        if md:
            rec["answers_md"] = cfg.rel(Path(md).resolve())
        if html:
            hp = Path(html).resolve()
            if hp.parent == cfg.staging.resolve():
                published = publish_fn(cfg, hp, md)
                if not published:
                    return {"_publish_failed": str(hp)}
                hp = published
            rec["output_html"] = cfg.rel(hp)
        save_record(cfg, rec)
        mf = Manifest(cfg)
        data = mf.load()
        key = rec.get("sha256")
        if key in data["processed"]:
            data["processed"][key]["status"] = "done"
            data["processed"][key]["finished_at"] = rec["finished_at"]
        mf.save(data)
        return rec


def mark_skip(cfg: Config, queue_path, reason: str) -> Optional[dict]:
    with FileLock(cfg.lock_path, cfg.lock_timeout):
        rec = load_record(cfg, queue_path)
        if rec is None:
            return None
        rec["status"] = "done"
        rec["skipped"] = True
        rec["skip_reason"] = reason
        rec["finished_at"] = now()
        save_record(cfg, rec)
        mf = Manifest(cfg)
        data = mf.load()
        key = rec.get("sha256")
        if key in data["processed"]:
            data["processed"][key]["status"] = "skipped"
            data["processed"][key]["note"] = reason
            data["processed"][key]["finished_at"] = rec["finished_at"]
        mf.save(data)
        return rec
