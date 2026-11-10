"""草稿解析与渲染的回归测试。

这些用例固化的是真实踩过的坑：
- 无编号题块（手写追问）曾被并进上一题并丢掉整段；
- 解析区以 `- ` 开头的行曾被误判成选项；
- 多段答案（译文）曾被压成一行；
- 答案行下方另起一段的公式曾被整行丢弃。
"""
import unittest

from answer_pipeline.render import (Layout, css_for, inline_md, parse_answers,
                                    render_block, render_body)

SAMPLE = """# 高中物理 · 测试

## 第 1 题（多选题）

如图所示，平行板电容器与理想二极管相连。

- A. 场强不变
- B. 电荷量不变

**答案**：A、B
**关键结果**：二极管让电荷量冻结。
**解析**
【第一步：分析电路】二极管只允许充电。
- 这一行在解析区，不能被当成选项。
【第二步：比较电势】$\varphi_P=-E'h<0$。
**高中范围**：平行板电容器与电势差。

## 追问：若改为 B 板接地

**答案**：判定不变。
**解析**
第一步，接地点只规定电势零点。
**高中范围**：电势与电势差。

## 第 2 题（计算题）

**答案**：
第一段译文。
第二段译文。
**解析**
略。
**高中范围**：牛顿定律。
"""


class TestParse(unittest.TestCase):
    def setUp(self):
        self.blocks = parse_answers(SAMPLE)

    def test_block_count(self):
        self.assertEqual(len(self.blocks), 3)

    def test_numbered_block(self):
        b = self.blocks[0]
        self.assertEqual(b["num"], "1")
        self.assertEqual(b["qtype"], "多选题")

    def test_unnumbered_block_is_not_merged(self):
        b = self.blocks[1]
        self.assertIsNone(b["num"])
        self.assertIn("B 板接地", b["heading"])
        self.assertTrue(b["why"], "追问块必须自带解析，不能并进上一题")

    def test_options_are_only_before_analysis(self):
        b = self.blocks[0]
        self.assertEqual(len(b["opts"]), 2)
        self.assertNotIn("这一行在解析区，不能被当成选项。", b["opts"])
        self.assertTrue(any("这一行在解析区" in w for w in b["why"]))

    def test_multi_paragraph_answer(self):
        b = self.blocks[2]
        self.assertEqual(b["answer"], ["第一段译文。", "第二段译文。"])

    def test_key_and_scope(self):
        self.assertIn("电荷量冻结", self.blocks[0]["key"])
        self.assertIn("平行板电容器", self.blocks[0]["scope"])

    def test_easy_mistake_dropped(self):
        md = "## 第 1 题（单选）\n\n**答案**：A\n**易错点**：别忘单位\n**解析**\n略\n"
        b = parse_answers(md)[0]
        self.assertFalse(any("易错点" in x for x in b["why"] + b["stem"]))


class TestRender(unittest.TestCase):
    def test_inline_md_holds_math(self):
        out = inline_md("**粗** 与 $a<b$")
        self.assertIn("<strong>粗</strong>", out)
        self.assertIn("$a&lt;b$", out, "公式内的 < 要转义，但 $ 必须保留给 KaTeX")

    def test_answer_lines(self):
        blocks = parse_answers(SAMPLE)
        html = render_block(blocks[0])
        self.assertEqual(html.count('class="answer-line"'), 1)
        self.assertIn("<strong>答案：</strong>", html)

    def test_multi_answer_paragraph(self):
        blocks = parse_answers(SAMPLE)
        html = render_block(blocks[2])
        self.assertEqual(html.count('class="answer-line"'), 2)
        self.assertEqual(html.count("<strong>答案：</strong>"), 1)

    def test_page_break_rule(self):
        self.assertIn("break-before: page", css_for(Layout()))

    def test_analysis_font_size(self):
        self.assertIn("14pt", css_for(Layout(analysis_pt=14)))

    def test_scope_blockquote(self):
        blocks = parse_answers(SAMPLE)
        html = render_block(blocks[0])
        self.assertIn("<blockquote>高中范围：", html)

    def test_one_section_per_block(self):
        body = render_body(parse_answers(SAMPLE), "标题")
        self.assertEqual(body.count('<section class="page">'), 3)
        self.assertEqual(body.count("<h2>"), 3)


if __name__ == "__main__":
    unittest.main()
