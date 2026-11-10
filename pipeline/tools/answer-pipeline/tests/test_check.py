"""结构自检的 11 项规则：每一条都要有反例，否则闸门形同虚设。"""
import tempfile
import unittest
from pathlib import Path

from answer_pipeline.check import RULE_COUNT, check_html, math_expected, publish
from answer_pipeline.paths import Config

SECTION = """<section class="page">
<h2>第 1 题（单选题）</h2>
<p>{stem}</p>
<p class="answer-line"><strong>答案：</strong>A</p>
<h3>解析</h3>
<div class="analysis"><p>{why}</p></div>
<blockquote>高中范围：受力分析</blockquote>
</section>"""


def good_html(stem="题干", why="推导过程", page_css=".page { break-before: page; }",
              body_extra="", head_extra="", sections=1, pad=4000):
    body = "".join(SECTION.format(stem=stem, why=why) for _ in range(sections))
    return (
        '<!DOCTYPE html>\n<html lang="zh-CN"><head><meta charset="UTF-8">'
        f"<title>t</title>\n<style>@page {{ size: A4 landscape; }}\n{page_css}</style>"
        f"{head_extra}\n</head>\n<body>\n<!--{'p' * pad}-->\n<div class=\"doc\">"
        f"{body}{body_extra}</div>\n</body></html>\n"
    )


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def write(self, html, name="a.html"):
        p = self.dir / name
        p.write_text(html, encoding="utf-8")
        return p

    def problems(self, html, md=None):
        p = self.write(html)
        ok, problems, stats = check_html(p, md)
        return ok, problems, stats


class TestRuleCount(unittest.TestCase):
    def test_rule_count_declared(self):
        self.assertEqual(RULE_COUNT, 11)


class TestCheckPass(Base):
    def test_good_file_passes(self):
        ok, problems, stats = self.problems(good_html())
        self.assertTrue(ok, problems)
        self.assertEqual(stats["pages"], 1)
        self.assertEqual(stats["h2"], 1)

    def test_multi_sections_pass(self):
        ok, problems, _ = self.problems(good_html(sections=3))
        self.assertTrue(ok, problems)


class TestCheckFailures(Base):
    def assert_rule(self, html, keyword, md=None):
        ok, problems, _ = self.problems(html, md)
        self.assertFalse(ok, f"应当不通过：{keyword}")
        self.assertTrue(any(keyword in p for p in problems),
                        f"缺少与「{keyword}」相关的问题，实际：{problems}")

    def test_too_small(self):
        self.assert_rule(good_html(pad=10), "文件过小")

    def test_no_section(self):
        html = good_html().replace('<section class="page">', "<div>").replace("</section>", "</div>")
        self.assert_rule(html, "没有题块")

    def test_section_heading_mismatch(self):
        html = good_html(body_extra="<h2>多余标题</h2>")
        self.assert_rule(html, "不匹配")

    def test_missing_page_break(self):
        self.assert_rule(good_html(page_css=".page { margin: 0; }"), "强制分页")

    def test_katex_error(self):
        html = good_html(body_extra='<span class="katex-error">x</span>')
        self.assert_rule(html, "渲染报错")

    def test_leftover_dollar(self):
        self.assert_rule(good_html(why="有残留 $x^2$ 源码"), "未渲染")

    def test_math_count_short(self):
        md = self.dir / "d.md"
        md.write_text("## 第 1 题（单选）\n$a$ 与 $b$\n", encoding="utf-8")
        html = good_html(body_extra='<span class="katex">one</span>')
        self.assert_rule(html, "草稿有", md=str(md))

    def test_fonts_not_inlined(self):
        html = good_html(body_extra='<span class="katex">x</span>')
        self.assert_rule(html, "字体未内联")

    def test_script_tag(self):
        self.assert_rule(good_html(body_extra="<script>1</script>"), "<script>")

    def test_external_link_and_import(self):
        self.assert_rule(good_html(head_extra='<link rel="stylesheet" href="https://x.test/a.css">'),
                         "外部样式或字体引用")
        self.assert_rule(good_html(body_extra='<img src="https://x.test/a.png">'), "外部链接")

    def test_w3c_namespace_allowed(self):
        ok, problems, _ = self.problems(good_html(head_extra='<html xmlns="http://www.w3.org/1999/xhtml">'))
        self.assertTrue(ok, problems)

    def test_easy_mistake_text(self):
        self.assert_rule(good_html(why="易错点：单位"), "易错点")

    def test_missing_labels(self):
        html = good_html().replace("<strong>答案：</strong>", "")
        self.assert_rule(html, "缺少「答案：")


class TestMathExpected(unittest.TestCase):
    def test_counts_inline_and_display(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.md"
            p.write_text("行内 $a$ 与 $$b$$ 和 $c$", encoding="utf-8")
            self.assertEqual(math_expected(str(p)), 3)

    def test_missing_file(self):
        self.assertEqual(math_expected("/nope/none.md"), 0)


class TestPublish(Base):
    def setUp(self):
        super().setUp()
        self.cfg = Config.build(root=str(self.dir), state_dir=str(self.dir / ".st"))
        self.cfg.output_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.staging.mkdir(parents=True, exist_ok=True)

    def test_publish_moves_good_file(self):
        src = self.cfg.staging / "good.html"
        src.write_text(good_html(), encoding="utf-8")
        dest = publish(self.cfg, src, None, quiet=True)
        self.assertIsNotNone(dest)
        self.assertTrue(dest.exists())
        self.assertFalse(src.exists())
        self.assertEqual(dest.parent, self.cfg.output_dir.resolve())

    def test_publish_keeps_bad_file(self):
        src = self.cfg.staging / "bad.html"
        src.write_text(good_html(why="易错点：x"), encoding="utf-8")
        self.assertIsNone(publish(self.cfg, src, None, quiet=True))
        self.assertTrue(src.exists(), "不合格的成品必须留在缓存里")

    def test_publish_idempotent_in_output(self):
        src = self.cfg.output_dir / "already.html"
        src.write_text(good_html(), encoding="utf-8")
        dest = publish(self.cfg, src, None, quiet=True)
        self.assertEqual(dest, src.resolve())
        self.assertTrue(src.exists())


if __name__ == "__main__":
    unittest.main()
