"""V2.0 故事页测试：六章结构、离线可用、动画降级、隐私默认、卡片导出。"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from mcd_timemachine.analyzer import analyze
from mcd_timemachine.cli import main
from mcd_timemachine.collector import build_coverage
from mcd_timemachine.demo_data import generate
from mcd_timemachine.story import (
    CARD_HEIGHT,
    CARD_WIDTH,
    PRIVACY_DEFAULTS,
    build_story_payload,
    render_story,
    write_story,
)


def _profile(source: str = "demo", count: int = 60):
    orders = generate(orders=count, seed=11)
    coverage = build_coverage(source, pages=1, raw_records=len(orders), orders=orders)
    return analyze(orders, None, coverage)


def _empty_profile():
    return {"empty": True, "summary": {"total_orders": 0},
            "coverage": build_coverage("demo", 0, 0, []),
            "generated_at": "2026-01-01 00:00"}


class TestStoryStructure(unittest.TestCase):
    def setUp(self):
        self.profile = _profile("demo")
        self.html = render_story(self.profile)

    def test_six_chapters_present(self):
        for i in range(1, 7):
            self.assertIn(f"id='ch{i}'", self.html)
        for title in ("时光机启动", "故事的起点", "我的麦麦日常",
                      "口味人格测试结果", "这一年的变化", "年度终章"):
            self.assertIn(title, self.html)

    def test_sections_balanced(self):
        self.assertEqual(self.html.count("<section"), self.html.count("</section>"))
        self.assertEqual(self.html.count("<div"), self.html.count("</div>"))

    def test_no_unreplaced_placeholder(self):
        self.assertNotIn("__STORY_", self.html)

    def test_single_file_offline(self):
        """单文件离线可打开：不得引用任何外部资源。"""
        self.assertEqual(re.findall(r'(?:src|href)\s*=\s*["\']https?://', self.html), [])
        self.assertNotIn("@import", self.html)
        self.assertNotIn("//fonts.googleapis", self.html)

    def test_assets_are_packaged(self):
        base = Path(__file__).resolve().parent.parent / "mcd_timemachine" / "assets"
        for name in ("story.html", "story.css", "story.js"):
            self.assertTrue((base / name).is_file(), f"缺少资源 {name}")


class TestPayload(unittest.TestCase):
    def test_privacy_defaults_all_hidden(self):
        payload = build_story_payload(_profile("demo"))
        self.assertEqual(payload["privacy"], {"amount": False, "store": False, "date": False})
        for key in ("amount", "store", "date"):
            self.assertEqual(payload["fields"][key]["hidden_text"], "已隐藏")
        self.assertEqual(PRIVACY_DEFAULTS, {"amount": False, "store": False, "date": False})

    def test_switches_not_checked_by_default(self):
        html = render_story(_profile("demo"))
        for key in ("amount", "store", "date"):
            self.assertRegex(html, r"id='f-%s'(?! checked)" % key)

    def test_persona_comes_from_top_tag(self):
        profile = _profile("demo")
        payload = build_story_payload(profile)
        self.assertEqual(payload["persona"], profile["tags"][0]["tag"])

    def test_payload_embeds_as_valid_json(self):
        html = render_story(_profile("mcp"))
        match = re.search(
            r'<script id="story-data" type="application/json">(.*?)</script>', html, re.S)
        self.assertIsNotNone(match)
        data = json.loads(match.group(1))
        self.assertIn("fields", data)
        self.assertEqual(data["keywords"][:1],
                         [k["word"] for k in _profile("mcp")["keywords"]][:1])

    def test_script_breakout_escaped(self):
        orders = generate(orders=12, seed=4)
        orders[0]["items"][0]["name"] = "</script><script>alert(1)</script>"
        cov = build_coverage("mcp", 1, len(orders), orders)
        html = render_story(analyze(orders, None, cov))
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;", html)

    def test_json_payload_escapes_breakout(self):
        from mcd_timemachine.story import _json_for_html

        raw = _json_for_html({"name": "</script><script>alert(1)</script>"})
        self.assertNotIn("</script>", raw)
        self.assertIn("\\u003c/script\\u003e", raw)
        # 转义后仍可被 JSON 解析回原值
        self.assertEqual(json.loads(raw)["name"], "</script><script>alert(1)</script>")


class TestDegradation(unittest.TestCase):
    def setUp(self):
        self.html = render_story(_profile("demo"))

    def test_reveal_visible_without_js(self):
        """.reveal 默认可见，只有 html.js 接管后才隐藏做动画。"""
        self.assertIn(".reveal { opacity: 1;", self.html)
        self.assertIn("html.js .reveal {", self.html)

    def test_reduced_motion_supported(self):
        self.assertIn("@media (prefers-reduced-motion: reduce)", self.html)
        self.assertIn("html.story-privacy", self.html)

    def test_observer_with_fallback(self):
        self.assertIn("IntersectionObserver", self.html)
        self.assertIn('"IntersectionObserver" in window', self.html)

    def test_noscript_hint(self):
        self.assertIn("<noscript>", self.html)
        self.assertIn("仍可完整阅读", self.html)

    def test_mobile_viewport(self):
        self.assertIn("width=device-width,initial-scale=1,viewport-fit=cover", self.html)
        self.assertIn("100svh", self.html)


class TestShareCard(unittest.TestCase):
    def setUp(self):
        self.html = render_story(_profile("demo"))

    def test_canvas_is_vertical_1080x1920(self):
        self.assertEqual((CARD_WIDTH, CARD_HEIGHT), (1080, 1920))
        self.assertRegex(self.html, r"width=['\"]1080['\"]")
        self.assertRegex(self.html, r"height=['\"]1920['\"]")

    def test_export_buttons(self):
        self.assertRegex(self.html, r"id=['\"]downloadCard['\"]")
        self.assertRegex(self.html, r"id=['\"]copyConfig['\"]")
        self.assertIn("share-card.png", self.html)

    def test_hidden_field_never_drawn(self):
        """未勾选的字段只画「已隐藏」，真实值不进入卡片绘制。"""
        self.assertIn("row.hidden", self.html)
        self.assertIn("未勾选的字段不会画进图片", self.html)

    def test_warns_html_itself_is_sensitive(self):
        self.assertIn("不要直接转发这个 HTML 文件", self.html)

    def test_privacy_mask_spans_on_page(self):
        self.assertIn("class='pv'", self.html)
        self.assertIn("data-mask=", self.html)

    def test_memory_amounts_and_dates_are_maskable(self):
        """年度回忆正文里的金额与日期也要能被统一打码，不能只在统计卡上做。"""
        self.assertIn("<span class='pv' data-mask='已隐藏'>¥", self.html)
        # 「你的主场」这类会写出门店名的回忆同样被标记
        self.assertRegex(self.html, r"<span class='pv' data-mask='已隐藏'>麦当劳")

    def test_no_raw_date_left_in_hero_range(self):
        self.assertNotRegex(
            self.html, r"class='hero-range'>统计区间 \d{4}-\d{2}-\d{2}")


class TestEmptyAndWrite(unittest.TestCase):
    def test_empty_profile_renders(self):
        html = render_story(_empty_profile())
        self.assertIn("还没有可以回放的订单", html)
        self.assertIn("id='ch1'", html)
        self.assertNotIn("id='ch4'", html)

    def test_write_story(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_story(_profile("demo"), Path(tmp))
            self.assertTrue(path.is_file())
            self.assertTrue(path.read_text(encoding="utf-8").startswith("<!DOCTYPE html>"))

    def test_write_reports_includes_story(self):
        from mcd_timemachine.reporter import write_reports

        with tempfile.TemporaryDirectory() as tmp:
            paths = write_reports(_profile("demo"), Path(tmp))
            self.assertIn("story", paths)
            self.assertTrue(paths["story"].is_file())
            self.assertTrue(paths["html"].is_file())
            self.assertTrue(paths["md"].is_file())


class TestCliStory(unittest.TestCase):
    def test_demo_then_story(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(main(["demo", "--count", "30", "--out", tmp]), 0)
            story = Path(tmp) / "story.html"
            self.assertTrue(story.is_file())
            story.unlink()
            self.assertEqual(main(["story", "--out", tmp]), 0)
            self.assertTrue(story.is_file())

    def test_story_without_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(main(["story", "--out", tmp]), 1)


if __name__ == "__main__":
    unittest.main()
