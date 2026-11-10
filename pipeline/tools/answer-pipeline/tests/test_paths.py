"""路径解析：换目录、换机器、嵌入别的项目都不能靠写死的绝对路径。"""
import json
import tempfile
import unittest
from pathlib import Path

from answer_pipeline.paths import Config, Layout, find_root


class TestFindRoot(unittest.TestCase):
    def test_finds_marker_upwards(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "input").mkdir()
            deep = root / "a" / "b" / "c"
            deep.mkdir(parents=True)
            self.assertEqual(find_root(deep), root.resolve())

    def test_finds_workbuddy_marker(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".workbuddy").mkdir()
            self.assertEqual(find_root(root), root.resolve())

    def test_fallback_is_cwd(self):
        with tempfile.TemporaryDirectory() as d:
            empty = Path(d) / "nowhere"
            empty.mkdir()
            # 没有标记时返回传入目录自身（不会神秘地跳到别处）
            self.assertEqual(find_root(empty), empty.resolve())


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_default_layout_of_dirs(self):
        cfg = Config.build(root=str(self.root))
        self.assertEqual(cfg.input_dir, self.root.resolve() / "input")
        self.assertEqual(cfg.output_dir, self.root.resolve() / "output")
        self.assertEqual(cfg.state_dir, self.root.resolve() / ".workbuddy" / "auto")
        self.assertEqual(cfg.staging, cfg.state_dir / "staging")

    def test_override_all(self):
        cfg = Config.build(root=str(self.root), input_dir=str(self.root / "src"),
                           output_dir=str(self.root / "out"), state_dir=str(self.root / ".s"))
        self.assertEqual(cfg.input_dir, (self.root / "src").resolve())
        self.assertEqual(cfg.output_dir, (self.root / "out").resolve())
        self.assertEqual(cfg.drafts, (self.root / ".s" / "drafts").resolve())

    def test_relative_roundtrip(self):
        cfg = Config.build(root=str(self.root))
        p = self.root / "output" / "a.html"
        rel = cfg.rel(p)
        self.assertEqual(rel, "output/a.html")
        self.assertEqual(cfg.abs(rel), self.root.resolve() / "output" / "a.html")

    def test_relative_outside_root_kept_absolute(self):
        cfg = Config.build(root=str(self.root))
        outside = Path(tempfile.gettempdir()) / "x.html"
        self.assertTrue(Path(cfg.rel(outside)).is_absolute())

    def test_abs_passthrough_old_absolute_records(self):
        cfg = Config.build(root=str(self.root))
        old = r"D:\work\题目\input\task"
        self.assertEqual(str(cfg.abs(old)), old, "旧队列里的绝对路径要原样可用")

    def test_config_file_overrides_layout(self):
        cfg_file = self.root / "cfg.json"
        cfg_file.write_text(json.dumps({"layout": {"analysis_pt": 15, "page_size": "A3 landscape"},
                                        "settle_seconds": 0.5}), encoding="utf-8")
        cfg = Config.build(root=str(self.root), config_file=str(cfg_file))
        self.assertEqual(cfg.layout.analysis_pt, 15)
        self.assertEqual(cfg.layout.page_size, "A3 landscape")
        self.assertEqual(cfg.settle_seconds, 0.5)

    def test_unknown_layout_key_raises(self):
        cfg_file = self.root / "cfg.json"
        cfg_file.write_text(json.dumps({"layout": {"typo_pt": 12}}), encoding="utf-8")
        with self.assertRaises(KeyError):
            Config.build(root=str(self.root), config_file=str(cfg_file))

    def test_layout_defaults_match_design(self):
        lay = Layout()
        self.assertEqual(lay.page_size, "A4 landscape")
        self.assertEqual(lay.analysis_pt, 14)
        self.assertIn("break-before", __import__("answer_pipeline.render", fromlist=["x"]).css_for(lay))


class TestEnsureDirs(unittest.TestCase):
    def test_creates_tree(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Config.build(root=d, state_dir=str(Path(d) / ".st"))
            cfg.ensure_dirs()
            for p in (cfg.input_dir, cfg.output_dir, cfg.drafts, cfg.staging, cfg.queue, cfg.logs):
                self.assertTrue(p.is_dir(), p)


if __name__ == "__main__":
    unittest.main()
