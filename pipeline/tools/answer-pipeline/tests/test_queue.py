"""队列：入队、去重、完成后流转。

重点回归「改名不重复作答」和「完成前必须过校验」两条。
"""
import json
import tempfile
import unittest
from pathlib import Path

from answer_pipeline import queue as q
from answer_pipeline.check import publish
from answer_pipeline.paths import Config

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64

GOOD_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><title>t</title>
<style>@page { size: A4 landscape; }
.page { break-before: page; }</style></head>
<body><!--{pad}-->
<div class="doc"><section class="page"><h2>第 1 题（单选）</h2><p>题干</p>
<p class="answer-line"><strong>答案：</strong>A</p><h3>解析</h3>
<div class="analysis"><p>推导</p></div>
<blockquote>高中范围：受力分析</blockquote></section></div></body></html>
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Config.build(root=str(self.root), state_dir=str(self.root / ".st"))
        self.cfg.settle_seconds = 0
        self.cfg.ensure_dirs()

    def task(self, name="20260101000000_ds_1_abcdef", files=("00_screenshot.png",)):
        d = self.cfg.input_dir / name
        d.mkdir(parents=True, exist_ok=True)
        for i, f in enumerate(files):
            (d / f).write_bytes(PNG + bytes([i]))
        return d

    def queue_files(self):
        return sorted(self.cfg.queue.glob("*.json"))

    def record(self):
        return json.loads(self.queue_files()[0].read_text(encoding="utf-8"))


class TestScan(Base):
    def test_new_folder_queued(self):
        self.task()
        added = q.scan(self.cfg)
        self.assertEqual(len(added), 1)
        self.assertEqual(len(self.queue_files()), 1)
        rec = self.record()
        self.assertEqual(rec["status"], "pending_answer")
        self.assertEqual(rec["kind"], "folder")
        self.assertEqual(rec["schema"], 2)

    def test_paths_are_relative(self):
        self.task()
        q.scan(self.cfg)
        rec = self.record()
        for key in ("source_dir", "answers_md", "staged_html", "output_html"):
            self.assertFalse(Path(rec[key]).is_absolute(), f"{key} 应是相对路径：{rec[key]}")
        self.assertIn("input/", rec["source_dir"])

    def test_second_scan_is_idempotent(self):
        self.task()
        q.scan(self.cfg)
        self.assertEqual(q.scan(self.cfg), [])

    def test_rename_does_not_duplicate(self):
        d = self.task()
        q.scan(self.cfg)
        (d / "00_screenshot.png").rename(d / "renamed.png")
        self.assertEqual(q.scan(self.cfg), [], "同一张图改名后不应重新入队")

    def test_new_material_creates_task(self):
        d = self.task()
        q.scan(self.cfg)
        (d / "01_extra.png").write_bytes(PNG + b"extra")
        self.assertEqual(len(q.scan(self.cfg)), 1, "新增素材属于新任务")

    def test_metadata_change_ignored(self):
        d = self.task()
        q.scan(self.cfg)
        (d / "resources.json").write_text('{"a":1}', encoding="utf-8")
        (d / "00_data.mdb").write_bytes(b"\x00" * 32)
        self.assertEqual(q.scan(self.cfg), [], "元数据与快照文件不参与指纹")

    def test_empty_folder_ignored(self):
        (self.cfg.input_dir / "empty").mkdir()
        self.assertEqual(q.scan(self.cfg), [])

    def test_pdf_as_task(self):
        (self.cfg.input_dir / "paper.pdf").write_bytes(b"%PDF-1.4 test")
        added = q.scan(self.cfg)
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]["kind"], "file")


class TestStatus(Base):
    def test_pending_listing(self):
        self.task()
        q.scan(self.cfg)
        pending = q.pending_records(self.cfg)
        self.assertEqual(len(pending), 1)
        self.assertIn("_queue_file", pending[0])


class TestFinish(Base):
    def test_skip_removes_from_pending(self):
        self.task()
        q.scan(self.cfg)
        rec = q.mark_skip(self.cfg, self.queue_files()[0], "成品回灌")
        self.assertIsNotNone(rec)
        self.assertTrue(rec.get("skipped"))
        self.assertEqual(q.pending_records(self.cfg), [])

    def test_done_publishes_staged_html(self):
        self.task()
        q.scan(self.cfg)
        md = self.cfg.drafts / "a_答案.md"
        md.write_text("# t\n\n## 第 1 题（单选）\n**答案**：A\n**解析**\n推导\n**高中范围**：受力\n",
                      encoding="utf-8")
        staged = self.cfg.staging / "20260101000000_ds_1_abcdef_答案横版.html"
        staged.write_text(GOOD_HTML.replace("{pad}", "p" * 4000), encoding="utf-8")
        rec = q.mark_done(self.cfg, self.queue_files()[0], str(md), str(staged), publish_fn=publish)
        self.assertNotIn("_publish_failed", rec)
        self.assertEqual(rec["status"], "done")
        self.assertFalse(staged.exists(), "通过校验后应离开缓存")
        self.assertTrue((self.cfg.output_dir / staged.name).exists())
        self.assertEqual(q.pending_records(self.cfg), [])

    def test_done_refuses_bad_html(self):
        self.task()
        q.scan(self.cfg)
        staged = self.cfg.staging / "bad_答案横版.html"
        staged.write_text("<!DOCTYPE html><html><body>易错点</body></html>", encoding="utf-8")
        rec = q.mark_done(self.cfg, self.queue_files()[0], None, str(staged), publish_fn=publish)
        self.assertIn("_publish_failed", rec)
        self.assertTrue(staged.exists(), "未过校验的成品不得进入 output")
        self.assertEqual(len(q.pending_records(self.cfg)), 1, "任务不应被标记完成")

    def test_done_missing_queue(self):
        self.assertIsNone(q.mark_done(self.cfg, self.cfg.queue / "nope.json", None, None,
                                      publish_fn=publish))


if __name__ == "__main__":
    unittest.main()
