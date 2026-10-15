"""报告渲染测试：覆盖率可见、真实/示例可区分、HTML 结构完整。"""

from __future__ import annotations

import unittest

from mcd_timemachine.analyzer import analyze
from mcd_timemachine.collector import build_coverage
from mcd_timemachine.demo_data import generate
from mcd_timemachine.reporter import render_html, render_markdown, print_console


def _profile(source: str = "demo", count: int = 40):
    orders = generate(orders=count, seed=3)
    coverage = build_coverage(source, pages=1, raw_records=len(orders), orders=orders)
    return analyze(orders, None, coverage)


class TestMarkdown(unittest.TestCase):
    def test_contains_coverage_section(self):
        md = render_markdown(_profile("mcp"))
        self.assertIn("数据说明", md)
        self.assertIn("真实数据", md)
        self.assertIn("订单覆盖", md)

    def test_demo_is_clearly_marked(self):
        md = render_markdown(_profile("demo"))
        self.assertIn("示例数据", md)
        self.assertIn("不来自任何真实账号", md)

    def test_empty_profile(self):
        md = render_markdown({"empty": True, "summary": {"total_orders": 0},
                              "coverage": build_coverage("demo", 0, 0, [])})
        self.assertIn("暂无可分析的订单数据", md)


class TestHtml(unittest.TestCase):
    def test_structure_balanced(self):
        html = render_html(_profile("mcp"))
        self.assertEqual(html.count("<div"), html.count("</div>"))
        self.assertIn("<!DOCTYPE html>", html)

    def test_source_badges(self):
        real = render_html(_profile("mcp"))
        demo = render_html(_profile("demo"))
        self.assertIn("真实数据", real)
        self.assertIn("示例数据报告", demo)
        self.assertIn("badge demo", demo)

    def test_coverage_numbers_rendered(self):
        html = render_html(_profile("mcp"))
        self.assertIn("有金额", html)
        self.assertIn("翻页情况", html)

    def test_missing_amount_warning(self):
        orders = generate(orders=20, seed=5)
        for order in orders[:5]:
            order["total"] = None
        cov = build_coverage("mcp", 1, len(orders), orders)
        html = render_html(analyze(orders, None, cov))
        self.assertIn("缺少金额信息", html)

    def test_empty_profile_renders(self):
        html = render_html({"empty": True, "summary": {"total_orders": 0},
                            "coverage": build_coverage("demo", 0, 0, []),
                            "generated_at": "2026-01-01 00:00"})
        self.assertIn("暂无可分析的订单", html)

    def test_xss_escaped(self):
        orders = generate(orders=10, seed=2)
        orders[0]["items"][0]["name"] = "<script>alert(1)</script>"
        cov = build_coverage("mcp", 1, len(orders), orders)
        html = render_html(analyze(orders, None, cov))
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;", html)


class TestConsole(unittest.TestCase):
    def test_prints_without_error(self):
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            print_console(_profile("demo"))
        text = buf.getvalue()
        self.assertIn("数据来源", text)
        self.assertIn("年度关键词", text)


if __name__ == "__main__":
    unittest.main()
