"""V2.0 故事页测试：六章结构、离线可用、动画降级、隐私默认、卡片导出。"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
        self.assertEqual(payload["privacy"],
                         {"amount": False, "store": False, "date": False, "generated": False})
        for key in ("amount", "store", "date", "generated"):
            self.assertEqual(payload["fields"][key]["hidden_text"], "已隐藏")
        self.assertEqual(PRIVACY_DEFAULTS,
                         {"amount": False, "store": False, "date": False, "generated": False})

    def test_switches_not_checked_by_default(self):
        html = render_story(_profile("demo"))
        for key in ("amount", "store", "date", "generated"):
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


EVIL_NAME = "<img src=x onerror=alert(1)>"
EVIL_STORE = "麦当劳<b>注入</b>&\"'餐厅"
LONG_CJK = "超长" + "芝士双层牛肉汉堡加培根加蛋" * 12
SPECIAL_CHARS = "&<>\"'/\\%$#@!*()[]{}|;:，。、【】￥"


def _hostile_profile(name: str = EVIL_NAME, store: str = EVIL_STORE):
    """把不可信文本塞成最常点的单品，模拟真实 MCP 返回了恶意 / 超长 / 特殊字符字段。

    走完整 analyze 链路，确保测的是真实会进入分享预览的数据，而不是手工伪造的 payload。
    """
    orders = generate(orders=20, seed=7)
    for order in orders:
        order["store"] = store
        for item in order["items"]:
            item["name"] = name
    cov = build_coverage("mcp", 1, len(orders), orders)
    return analyze(orders, None, cov)


class TestSharePreviewInjection(unittest.TestCase):
    """分享预览区不得把订单文本当 HTML 解析（P1：innerHTML 拼接注入）。"""

    def setUp(self):
        self.profile = _hostile_profile()
        self.payload = build_story_payload(self.profile)
        self.html = render_story(self.profile)

    # ---- 源头：脚本一律用 textContent 建节点
    def test_story_js_never_uses_innerhtml(self):
        """只允许出现「赋值」形式的 HTML 注入接口，注释里提到不算。"""
        js = (Path(__file__).resolve().parent.parent
              / "mcd_timemachine" / "assets" / "story.js").read_text(encoding="utf-8")
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
            self.assertIsNone(re.search(r"[.\s(]%s\s*[=(]" % sink, js),
                              f"story.js 仍在向 {sink} 写内容")

    def test_story_js_builds_preview_from_nodes(self):
        js = (Path(__file__).resolve().parent.parent
              / "mcd_timemachine" / "assets" / "story.js").read_text(encoding="utf-8")
        self.assertIn("function el(tag, text)", js)
        self.assertIn("node.textContent = String(text)", js)
        self.assertIn("previewRow(", js)

    # ---- 数据：真实内容要保留，只是以纯文本呈现
    def test_malicious_name_kept_in_payload(self):
        """脱敏/转义不应悄悄丢掉内容，原值要能完整取回。"""
        self.assertIn(EVIL_NAME, self.payload["keywords"])
        self.assertIn(EVIL_STORE, self.payload["fields"]["store"]["value"])

    def test_malicious_name_never_appears_unescaped(self):
        """整份 HTML 里不许有一处未转义的恶意片段（含 JSON 数据块）。"""
        self.assertNotIn(EVIL_NAME, self.html)
        self.assertNotIn("<img src=x", self.html)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", self.html)

    def test_malicious_store_not_executable_in_html(self):
        self.assertNotIn("<b>注入</b>", self.html)
        self.assertIn("&lt;b&gt;注入&lt;/b&gt;", self.html)

    def test_json_payload_never_breaks_script_tag(self):
        match = re.search(
            r'<script id="story-data" type="application/json">(.*?)</script>', self.html, re.S)
        self.assertIsNotNone(match)
        data = json.loads(match.group(1))
        self.assertIn(EVIL_NAME, data["keywords"])
        self.assertNotIn("</script", match.group(1))

    # ---- 特殊字符与中文长文本
    def test_special_chars_escaped_once(self):
        """只转义一次：页面上看到的就是原始字符本身，不是 &amp;lt; 这种双重编码。"""
        html = render_story(_hostile_profile(SPECIAL_CHARS))
        self.assertNotIn("&amp;lt;", html)
        self.assertNotIn("&amp;amp;", html)
        self.assertNotIn(SPECIAL_CHARS.replace("&", "&amp;") + "lt;", html)

    def test_special_chars_still_parse_back(self):
        html = render_story(_hostile_profile(SPECIAL_CHARS))
        match = re.search(
            r'<script id="story-data" type="application/json">(.*?)</script>', html, re.S)
        data = json.loads(match.group(1))
        self.assertTrue(any(SPECIAL_CHARS in kw for kw in data["keywords"]))

    def test_long_cjk_name_preserved(self):
        profile = _hostile_profile(LONG_CJK)
        self.assertTrue(any(LONG_CJK in k["word"] for k in profile["keywords"]))
        html = render_story(profile)
        match = re.search(
            r'<script id="story-data" type="application/json">(.*?)</script>', html, re.S)
        data = json.loads(match.group(1))
        self.assertTrue(any(LONG_CJK in kw for kw in data["keywords"]))

    def test_long_cjk_does_not_break_structure(self):
        html = render_story(_hostile_profile(LONG_CJK))
        self.assertEqual(html.count("<section"), html.count("</section>"))
        self.assertEqual(html.count("<div"), html.count("</div>"))
        self.assertNotIn("__STORY_", html)

    def test_special_chars_roundtrip_in_json(self):
        from mcd_timemachine.story import _json_for_html

        raw = _json_for_html({"n": SPECIAL_CHARS, "cjk": LONG_CJK})
        back = json.loads(raw)
        self.assertEqual(back["n"], SPECIAL_CHARS)
        self.assertEqual(back["cjk"], LONG_CJK)

    # ---- 隐私默认值不因视觉调整而改变
    def test_privacy_defaults_unchanged_after_fix(self):
        self.assertEqual(self.payload["privacy"],
                         {"amount": False, "store": False, "date": False, "generated": False})
        for key in ("amount", "store", "date", "generated"):
            self.assertRegex(self.html, r"id='f-%s'(?! checked)" % key)
            self.assertEqual(self.payload["fields"][key]["hidden_text"], "已隐藏")


class TestPublicMode(unittest.TestCase):
    """公开分享版：敏感值必须在生成期移除，而不是靠 CSS 遮挡。"""

    def setUp(self):
        self.profile = _profile("mcp")
        self.public = render_story(self.profile, "public")
        self.personal = render_story(self.profile, "personal")
        self.payload = build_story_payload(self.profile, "public")

    def test_invalid_mode_rejected(self):
        with self.assertRaises(ValueError):
            render_story(self.profile, "secret")

    def test_no_money_left(self):
        self.assertNotIn("¥", self.public)
        self.assertIn("¥", self.personal)

    def test_no_day_precision_date_left(self):
        self.assertIsNone(re.search(r"\d{4}-\d{2}-\d{2}", self.public))
        self.assertTrue(re.search(r"\d{4}-\d{2}-\d{2}", self.personal))

    def test_month_granularity_kept_for_trend(self):
        """到日日期移除，但保留「年-月」粒度，否则第 5 章趋势图失去意义。"""
        self.assertEqual(len(re.findall(r"\d{4}-\d{2}(?!-)", self.public)),
                         len(re.findall(r"\d{4}-\d{2}(?!-)", self.personal)))
        self.assertGreater(len(re.findall(r"\d{4}-\d{2}(?!-)", self.public)), 0)

    def test_store_names_removed(self):
        """正文只写出「最常光顾」那一家的店名，个人版保留、公开版必须没有。"""
        from mcd_timemachine.story import _top_store, store_names

        top = _top_store(self.profile).split(" · ")[0]
        self.assertTrue(top and top != "未知门店", f"示例数据应带门店名，实际 {top!r}")
        self.assertIn(top, self.personal)
        self.assertNotIn(top, self.public)
        for name in store_names(self.profile):
            self.assertNotIn(name, self.public)

    def test_payload_holds_placeholder_only(self):
        self.assertTrue(self.payload["redacted"])
        self.assertEqual(self.payload["privacy_mode"], "public")
        self.assertEqual(self.payload["generated_at"], "")
        for key in ("amount", "store", "date", "generated"):
            self.assertEqual(self.payload["fields"][key]["value"], "公开版未写入")
            self.assertFalse(self.payload["fields"][key]["available"])

    def test_switches_disabled(self):
        for key in ("amount", "store", "date", "generated"):
            self.assertRegex(self.public, r"id='f-%s'[^>]*disabled" % key)
        self.assertNotRegex(self.personal, r"id='f-amount'[^>]*disabled")

    def test_html_marked_and_banner_switched(self):
        self.assertIn('data-privacy="public"', self.public)
        self.assertIn('data-privacy="personal"', self.personal)
        self.assertIn("class='mode-banner public'", self.public)
        self.assertIn("class='mode-banner personal'", self.personal)
        self.assertIn("公开分享版", self.public)
        self.assertIn("个人完整版", self.personal)

    def test_no_link_to_full_report_in_public(self):
        self.assertNotIn("href='report.html'", self.public)
        self.assertIn("href='report.html'", self.personal)

    def test_json_still_parses_after_redaction(self):
        match = re.search(
            r'<script id="story-data" type="application/json">(.*?)</script>', self.public, re.S)
        self.assertIsNotNone(match)
        data = json.loads(match.group(1))          # 整份 HTML 脱敏后 JSON 仍合法
        self.assertTrue(data["redacted"])
        self.assertNotIn("¥", json.dumps(data, ensure_ascii=False))
        self.assertIsNone(re.search(r"\d{4}-\d{2}-\d{2}", json.dumps(data, ensure_ascii=False)))

    def test_public_page_structure_intact(self):
        for i in range(1, 7):
            self.assertIn(f"id='ch{i}'", self.public)
        self.assertEqual(self.public.count("<section"), self.public.count("</section>"))
        self.assertEqual(self.public.count("<div"), self.public.count("</div>"))
        self.assertNotIn("__STORY_", self.public)

    def test_redact_html_is_idempotent_and_targeted(self):
        from mcd_timemachine.story import redact_html

        src = "<p>2026-06-27 花了 ¥129.00，在麦当劳（中心书城餐厅）</p>"
        once = redact_html(src, ["麦当劳（中心书城餐厅）"])
        self.assertEqual(once, "<p>日期已隐藏 花了 金额已隐藏，在门店已隐藏</p>")
        self.assertEqual(redact_html(once, ["麦当劳（中心书城餐厅）"]), once)

    def test_redact_keeps_month_tooltip(self):
        from mcd_timemachine.story import redact_html

        self.assertEqual(redact_html("title='2026-06：3 单'"), "title='2026-06：3 单'")

    def test_store_names_helper(self):
        from mcd_timemachine.story import store_names

        self.assertEqual(store_names(self.profile),
                         [s["name"] for s in self.profile["stores"] if s.get("name")])


def _walk_scalars(node, key=""):
    """遍历出 (字段名, 标量值)，用于检查「每个数值字段都被显式分类」。"""
    if isinstance(node, dict):
        for k, value in node.items():
            for item in _walk_scalars(value, str(k)):
                yield item
    elif isinstance(node, list):
        for value in node:
            for item in _walk_scalars(value, key):
                yield item
    else:
        yield key, node


class TestStructuredRedaction(unittest.TestCase):
    """第七轮审查 P1：脱敏前移到**渲染之前**，判据是「字段」而不是「文本长相」。

    原来的做法是渲染完再拿正则扫 HTML：规则依赖「金额一定写成 ¥123.00」这类假设，
    将来新增一个不带 ¥ 的金额字段、或一个没进门店名单的地址字段就可能漏出去。
    """

    def setUp(self):
        self.profile = _profile("mcp")

    def _augment(self):
        """造一份「将来可能长这样」的 profile：裸金额 / 地址 / 订单号 / 电话 / 坐标。"""
        import copy

        p = copy.deepcopy(self.profile)
        p["summary"]["yearly_outlay"] = 1288.4          # 不带 ¥ 符号的金额字段
        p["coverage"]["delivery_address"] = "深圳市南山区科技园路 1 号 3 栋 502"
        p["last_order"] = {
            "order_no": "MCD202606270001",
            "phone": "13800138000",
            "lat": 22.540123,
            "lng": 114.057812,
            "note": "备注：送到公司前台，共 1288.40 元",
        }
        p["memories"].append({
            "title": "新的回忆",
            "text": "2026-06-27 那天花了 ¥1,234.50，合计 1234.50 元。",
        })
        return p

    # ---------------------------------------------------------- 第一层：结构化
    def test_every_numeric_field_is_registered_or_redacted(self):
        """数值字段必须被显式分类：登记为安全计数，或按金额抹掉。

        新增数值字段时这条会失败，逼作者做一次判断——这正是「结构化脱敏」
        相对「按文本长相扫」的价值：漏掉的可能性从「没写正则」变成「测试报错」。
        """
        from mcd_timemachine.story import is_safe_number_key, redact_profile

        profile = self._augment()
        redacted = redact_profile(profile)
        offenders = []
        for key in sorted({k for k, _v in _walk_scalars(profile)}):
            values = [v for k, v in _walk_scalars(profile) if k == key]
            if not any(isinstance(v, (int, float)) and not isinstance(v, bool)
                       for v in values):
                continue
            if is_safe_number_key(key):
                continue
            after = [v for k, v in _walk_scalars(redacted) if k == key]
            # 脱敏形态二选一：命名字段的置「已隐藏」，金额字段置「金额已隐藏」
            if not after or not all(v in ("金额已隐藏", "已隐藏") for v in after):
                offenders.append((key, after[:2]))
        self.assertEqual(offenders, [], f"这些数值字段既没登记也没脱敏：{offenders}")

    def test_unregistered_money_field_is_redacted(self):
        """不带 ¥ 符号的金额字段（yearly_outlay）同样要抹掉。"""
        from mcd_timemachine.story import redact_profile

        redacted = redact_profile(self._augment())
        self.assertEqual(redacted["summary"]["yearly_outlay"], "金额已隐藏")
        self.assertNotIn("1288.4", json.dumps(redacted, ensure_ascii=False))

    def test_sensitive_keys_are_dropped_by_name(self):
        """地址 / 订单号 / 电话 / 坐标这类字段名命中即整体移除，不依赖值长什么样。"""
        from mcd_timemachine.story import redact_profile

        redacted = redact_profile(self._augment())
        self.assertEqual(redacted["coverage"]["delivery_address"], "已隐藏")
        for key in ("order_no", "phone", "lat", "lng"):
            self.assertEqual(redacted["last_order"][key], "已隐藏", key)

    def test_text_values_are_scrubbed_before_rendering(self):
        """自由文本里的金额（含「元」后缀）与到日日期在数据层就被抹掉。"""
        from mcd_timemachine.story import redact_profile

        redacted = json.dumps(redact_profile(self._augment()), ensure_ascii=False)
        note = redacted[redacted.index("备注"):redacted.index("备注") + 60]
        self.assertNotIn("1288.40", note)
        self.assertIn("金额已隐藏", note)
        for raw in ("¥1,234.50", "1234.50 元", "2026-06-27", "MCD202606270001"):
            self.assertNotIn(raw, redacted, raw)

    def test_store_count_kept_but_name_gone(self):
        """门店名抹掉，但「去过几次」这种计数要留着，否则第 3、5 章会残缺。"""
        from mcd_timemachine.story import redact_profile, store_names

        redacted = redact_profile(self.profile)
        dump = json.dumps(redacted, ensure_ascii=False)
        for name in store_names(self.profile):
            self.assertNotIn(name, dump)
        self.assertTrue(redacted["stores"], "门店列表不应被整体删掉")
        for store in redacted["stores"]:
            self.assertEqual(store["name"], "门店已隐藏")
            self.assertIsInstance(store["count"], int)

    def test_redact_profile_does_not_mutate_input(self):
        """脱敏返回副本：调用方的 profile 必须原样可用（个人版还要用它渲染）。"""
        from mcd_timemachine.story import redact_profile

        before = json.dumps(self.profile, ensure_ascii=False, sort_keys=True)
        redact_profile(self.profile)
        self.assertEqual(json.dumps(self.profile, ensure_ascii=False, sort_keys=True), before)

    # ---------------------------------------------------------- 第二、三层
    def test_layer_one_and_two_leave_no_residue(self):
        """关掉第三层检查，公开版仍然零残留——证明前两层不是靠兜底才过关。"""
        from mcd_timemachine.story import scan_leaks

        two_layers = render_story(self.profile, "public", leak_check=False)
        self.assertEqual(scan_leaks(two_layers, self.profile), [])
        # 第三层只做检查、不修补，因此真实管线里它的存在不该改动任何一个字符
        self.assertEqual(render_story(self.profile, "public"), two_layers)

    def test_body_rendered_from_redacted_data_is_clean(self):
        """第一层单独就够：用脱敏后的数据渲染正文，正文里没有原值。"""
        from mcd_timemachine.story import _body_of, redact_profile, scan_leaks, store_names

        source = self._augment()
        clean = redact_profile(source)
        body = _body_of(clean, build_story_payload(clean, "public"), "public")
        self.assertNotIn("¥", body)
        self.assertIsNone(re.search(r"\d{4}-\d{2}-\d{2}", body))
        for name in store_names(source):
            self.assertNotIn(name, body)
        self.assertEqual(scan_leaks(body, source), [])

    def test_public_html_has_zero_leak_on_augmented_profile(self):
        from mcd_timemachine.story import scan_leaks

        profile = self._augment()
        html = render_story(profile, "public")
        self.assertEqual(scan_leaks(html, profile), [])
        for raw in ("深圳市南山区科技园路", "13800138000", "MCD202606270001",
                    "22.540123", "114.057812", "1288.4"):
            self.assertNotIn(raw, html, raw)

    def test_scan_leaks_detects_a_planted_leak(self):
        """自检本身要有效：故意把原值拼回 HTML，必须被抓到。"""
        from mcd_timemachine.story import scan_leaks

        spend = self.profile["summary"]["display_spend"]
        planted = f"<p>累计消费 ¥{spend:,.2f}</p>"
        self.assertTrue(scan_leaks(planted, self.profile), "自检漏掉了拼回去的金额")
        self.assertEqual(scan_leaks("<p>没有敏感值</p>", self.profile), [])

    def test_sensitive_tokens_skip_too_short_values(self):
        """过短的金额形态不参与比对，否则会把页面上无关的数字当成泄漏。"""
        from mcd_timemachine.story import sensitive_tokens

        tokens = sensitive_tokens({"summary": {"avg_order": 3.0}})
        self.assertNotIn("3.0", tokens)
        self.assertNotIn("3.00", tokens)
        self.assertIn("¥3.00", tokens)          # 带货币符号的形态要留下

    def test_public_keeps_year_but_not_day(self):
        """年份要留在标题里（range_end 被抹掉后不能再从它推年份）。"""
        html = render_story(self.profile, "public")
        year = (self.profile["coverage"].get("range_end") or "")[:4]
        self.assertTrue(year.isdigit(), year)
        self.assertIn(f"你的 {year} 麦麦年度回忆", html)

    def test_personal_mode_is_untouched_by_the_new_layers(self):
        """个人版一个字符都不该被新逻辑改动：金额、门店、日期必须原样可见。"""
        profile = self._augment()
        html = render_story(profile, "personal")
        self.assertIn("¥", html)
        self.assertEqual(len(re.findall(r"\d{4}-\d{2}-\d{2}", html)),
                         len(re.findall(r"\d{4}-\d{2}-\d{2}", render_story(profile))))

    def test_sensitive_and_money_key_helpers(self):
        from mcd_timemachine.story import is_money_key, is_sensitive_key

        for key in ("address", "delivery_address", "order_no", "orderId", "phone",
                    "mobile", "lat", "lng", "openid", "收货地址", "手机号"):
            self.assertTrue(is_sensitive_key(key), key)
        for key in ("store", "count", "total_orders", "month", "spend_basis",
                    "amount_source", "total_items", "with_date"):
            self.assertFalse(is_sensitive_key(key), key)
            self.assertFalse(is_money_key(key), key)
        self.assertTrue(is_money_key("total_spend"))
        self.assertTrue(is_money_key("spend"))
        self.assertTrue(is_money_key("avg_delivery_fee"))
        self.assertTrue(is_money_key("消费金额"))


class TestExtremeTextGuards(unittest.TestCase):
    """第七轮审查 P2：极端字符（空值 / Emoji / 超长英文 / 混排）不得弄坏版式。"""

    def setUp(self):
        self.js = (Path(__file__).resolve().parent.parent
                   / "mcd_timemachine" / "assets" / "story.js").read_text(encoding="utf-8")

    def test_text_is_walked_by_grapheme_cluster(self):
        """按字素截断：不能把代理对劈成半个字符（会画成方框），
        也不能把 👨‍👩‍👧‍👦 这类 ZWJ 组合拆散。"""
        self.assertIn("function graphemeAt(s, i)", self.js)
        self.assertIn("function codePointAt(s, i)", self.js)
        self.assertIn('var ZWJ_CH = "\\u200d";', self.js)
        # 旧的「按 UTF-16 码元切片」写法必须彻底消失
        self.assertNotIn("var next = out + s[i];", self.js)
        self.assertNotIn("var ch = s[i];", self.js)

    def test_clip_and_wrap_share_the_grapheme_walker(self):
        # 两处都必须按字素步进（clip 单行截断、wrap 换行）
        self.assertEqual(self.js.count("= graphemeAt(s, i)"), 2)

    def test_blank_field_value_falls_back_to_dash(self):
        """值只有空白时不要画出一行空白。"""
        self.assertIn('var shown = value.trim() ? value : "—";', self.js)

    def test_empty_persona_and_keywords_have_fallbacks(self):
        self.assertIn('DATA.persona || "口味待探索"', self.js)
        self.assertIn('["还在探索中"]', self.js)


class TestGeneratedAtHidden(unittest.TestCase):
    """P2：生成时间默认隐藏，只有主动勾选才进分享图片。"""

    def setUp(self):
        self.profile = _profile("demo")
        self.html = render_story(self.profile)
        self.payload = build_story_payload(self.profile)

    def test_generated_field_exists_and_hidden(self):
        self.assertIn("生成时间", self.html)
        self.assertRegex(self.html, r"id='f-generated'(?! checked)")
        self.assertEqual(self.payload["privacy"]["generated"], False)

    def test_generated_value_is_profile_timestamp(self):
        self.assertEqual(self.payload["fields"]["generated"]["value"],
                         self.profile["generated_at"])

    def test_export_config_includes_generated_flag(self):
        js = (Path(__file__).resolve().parent.parent
              / "mcd_timemachine" / "assets" / "story.js").read_text(encoding="utf-8")
        self.assertIn('r.key === "generated"', js)
        self.assertIn("生成于 ", js)

    def test_card_does_not_draw_time_when_unchecked(self):
        """绘制逻辑必须以行的可见状态为准，不能无条件写死时间。"""
        js = (Path(__file__).resolve().parent.parent
              / "mcd_timemachine" / "assets" / "story.js").read_text(encoding="utf-8")
        self.assertIn("genShown", js)
        self.assertNotIn('"生成于 " + (cfg.generatedAt', js)


class TestCardLayoutGuards(unittest.TestCase):
    """P1：长文本不得溢出画布。绘制前必须先约束宽度。"""

    def setUp(self):
        self.js = (Path(__file__).resolve().parent.parent
                   / "mcd_timemachine" / "assets" / "story.js").read_text(encoding="utf-8")

    def test_clip_helper_used_at_every_text_site(self):
        self.assertIn("function clip(ctx, text, maxWidth)", self.js)
        for site in ("chip(ctx, chips[c].text, chips[c].x, chipTop + chips[c].row * CHIP_STEP,",
                     "wrap(ctx, cfg.persona, INNER - 96, 2)",
                     "clip(ctx, row.text, avail)",
                     "clip(ctx, cfg.sourceLabel, INNER)"):
            self.assertIn(site, self.js, f"绘制位置缺少宽度约束：{site}")

    def test_field_value_reserves_label_width(self):
        self.assertIn("var avail = CARD_W - 132 - (132 + labelW) - 32;", self.js)

    def test_keyword_block_is_measured_before_drawing(self):
        """先排版再绘制，才能把关键词块高度算进下方面板的位置。"""
        self.assertIn("var chips = [];", self.js)
        self.assertIn(
            "var budget = FOOT_TOP - BODY_TOP - GAP_TITLE - GAP_CHIP - GAP_ROWS - chipsH - rowsH;",
            self.js)

    def test_persona_panel_height_follows_content(self):
        """人格面板高度必须随描述行数变化——固定 470 高时，人格名占两行的
        情况下描述第 2、3 行会落到面板外面（实测第 3 行 y869~902 vs 面板底 856）。"""
        self.assertIn("var personaLines = wrap(ctx, cfg.persona, INNER - 96, 2);", self.js)
        self.assertIn("var personaH = Math.max(contentNeed, Math.min(PERSONA_MIN_H, budget));",
                      self.js)
        self.assertIn("personaH = Math.min(personaH, budget);", self.js)

    def test_persona_desc_is_trimmed_against_budget(self):
        """放不下就逐行裁描述，而不是让文字越出面板。"""
        self.assertIn("if (contentNeed <= budget || maxDesc === 0) break;", self.js)
        self.assertIn("maxDesc -= 1;", self.js)

    def test_layout_hook_exposes_geometry_only(self):
        """测试钩子只暴露几何，不得把关键词 / 描述原文带出去。"""
        self.assertIn("window.__mcdCardLayout = {", self.js)
        hook = self.js.split("window.__mcdCardLayout = {", 1)[1].split("};", 1)[0]
        for leak in ("cfg.keywords", "cfg.personaDesc", "chips[", "descLines["):
            self.assertNotIn(leak, hook, f"测试钩子泄漏了内容：{leak}")

    def test_long_word_chip_is_clipped_not_overflown(self):
        self.assertIn("clip(ctx, text, Math.max(0, (maxWidth || INNER) - padX * 2))", self.js)

    def test_wrap_marks_truncation(self):
        """超过行数上限要补省略号，而不是静默丢掉后半段。"""
        self.assertIn('clip(ctx, last + "…", maxWidth) || last', self.js)

    def test_within_canvas_constants(self):
        self.assertIn("var INNER = CARD_W - PAD * 2;", self.js)
        self.assertIn("var FOOT_TOP = CARD_H - 260;", self.js)


class TestNoShadowedHelper(unittest.TestCase):
    """静态护栏：变量提升会遮蔽建节点助手，必须挡住这类写法。"""

    def setUp(self):
        self.js = (Path(__file__).resolve().parent.parent
                   / "mcd_timemachine" / "assets" / "story.js").read_text(encoding="utf-8")

    def test_no_local_variable_named_el(self):
        """`var el = ...` 会在整个函数作用域内遮蔽 el() 助手，
        导致 previewRow 抛 "el is not a function"，分享面板整体失效。"""
        self.assertIsNone(re.search(r"\bvar\s+el\b", self.js), "story.js 里出现了遮蔽 el() 的变量")
        self.assertIsNone(re.search(r"function\s*\(\s*el\s*\)", self.js),
                          "story.js 里有名为 el 的参数，会遮蔽 el() 助手")

    def test_helper_is_defined_exactly_once(self):
        self.assertEqual(len(re.findall(r"function el\(tag, text\)", self.js)), 1)


class TestAttributeEscaping(unittest.TestCase):
    """属性值转义：本模块片段用单引号包属性，`_e()` 不管单引号，必须用 `_attr()`。"""

    def test_attr_escapes_both_quote_styles(self):
        from mcd_timemachine.story import _attr

        self.assertEqual(_attr("a'b"), "a&#39;b")
        self.assertEqual(_attr('a"b'), "a&quot;b")
        self.assertEqual(_attr("a<b>&c"), "a&lt;b&gt;&amp;c")

    def test_attribute_sites_use_attr_helper(self):
        py = (Path(__file__).resolve().parent.parent
              / "mcd_timemachine" / "story.py").read_text(encoding="utf-8")
        for snippet in ("data-title='{_attr(title)}'",
                        "data-mask='{_attr(mask)}'",
                        "_attr(m.get('month'))"):
            self.assertIn(snippet, py, "属性位置仍在用只挡双引号的 _e()")

    def test_poisoned_month_cannot_break_out_of_attribute(self):
        """month 取自订单日期前 7 个字符，上游若返回引号不得逃出属性。"""
        profile = _profile()
        profile["monthly"] = [{"month": "2026-06' onmouseover='alert(1)", "orders": 3}]
        html = render_story(profile)
        self.assertNotIn("onmouseover='alert", html)
        self.assertNotIn('onmouseover="alert', html)
        self.assertIn("&#39;", html)

    def test_poisoned_month_still_readable_as_text(self):
        profile = _profile()
        profile["monthly"] = [{"month": "2026-06'x", "orders": 2}]
        html = render_story(profile)
        # 仍要出现在月柱上，不能因为转义被丢掉
        self.assertIn("title='2026-06&#39;x：2 单'", html)


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

    def test_privacy_public_flag_after_subcommand(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                main(["demo", "--count", "30", "--out", tmp, "--privacy", "public"]), 0)
            html = (Path(tmp) / "story.html").read_text(encoding="utf-8")
            self.assertIn('data-privacy="public"', html)
            self.assertNotIn("¥", html)
            # report.html 仍是完整报告，不受 --privacy 影响
            self.assertIn("¥", (Path(tmp) / "report.html").read_text(encoding="utf-8"))

    def test_privacy_public_flag_before_subcommand(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                main(["--privacy", "public", "demo", "--count", "20", "--out", tmp]), 0)
            html = (Path(tmp) / "story.html").read_text(encoding="utf-8")
            self.assertIn('data-privacy="public"', html)

    def test_story_command_can_switch_mode_without_reanalysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(main(["demo", "--count", "30", "--out", tmp]), 0)
            story = Path(tmp) / "story.html"
            self.assertIn('data-privacy="personal"', story.read_text(encoding="utf-8"))
            self.assertEqual(main(["story", "--out", tmp, "--privacy", "public"]), 0)
            public = story.read_text(encoding="utf-8")
            self.assertIn('data-privacy="public"', public)
            self.assertNotIn("¥", public)

    def test_invalid_privacy_value_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                main(["demo", "--out", tmp, "--privacy", "secret"])


# ================================================================ 第八轮审查
#
# P1 脱敏失败要「拒绝输出」而不是悄悄替换；P2 隐私规则边界与「别脱过头」。

def _extreme_profile():
    """把第八轮点名的边界都塞进一份 profile：小数金额 / 中文地址 / 其它日期写法 /
    异常字段名 / 嵌套数据。"""
    import copy

    p = copy.deepcopy(_profile("mcp"))
    p["summary"].update({
        "yearly_outlay": 1288.4,   # 小数金额，且字段名不带 ¥ 语义
        "avg_order": 28.5,
        "spend_pct": 42,           # 名字含金额词、实为比例 → 不该被抹
        "refund_count": 12345,     # 名字含金额词、实为计数 → 不该被抹
    })
    p["coverage"]["delivery_address"] = "深圳市南山区科技园路 1 号 3 栋 502"
    p["coverage"]["中文地址"] = "上海市浦东新区世纪大道 100 号"
    p["last_order"] = {
        "addr2": "北京市朝阳区建国路 88 号 SOHO 现代城 B 座 1203",
        "mobile1": "13800138000",
        "addressLine1": "深圳市南山区科技园路 1 号",
        "phoneNumber": "0755-88886666",
        "receiverName": "张三",
        "收货信息": "广州市天河区天河路 299 号",
        "订单编号": "MCD202606270001",
        "idCardNo": "440301199001011234",
        "bankCard": "6222020200112233445",
        "emailAddress": "someone@example.com",
        "userName": "zhangsan",
        "note": "备注：2026/06/27 送到公司前台，合计 RMB 1288.40（￥1,288.40），"
                "另一次是 June 27, 2026，记 1234.50 元；27/06/2026 那次 88.5 块。",
        "nested": {"deep": {"further": [
            {"order_id": "MCD202606270002", "amount": 66.6,
             "address": "杭州市西湖区文一西路 969 号"},
            {"item": "巨无霸", "price": 22.5},
        ]}},
    }
    p["memories"].append({
        "title": "边界回忆",
        "text": "2026-06-27 那天花了 ¥1,234.50；英文写法 June 27, 2026、"
                "斜杠写法 2026/06/27、短横写法 06-27-2026。",
    })
    return p


EXTREME_MUST_GONE = (
    "深圳市南山区科技园路", "北京市朝阳区建国路", "广州市天河区天河路",
    "杭州市西湖区文一西路", "13800138000", "0755-88886666", "440301199001011234",
    "6222020200112233445", "someone@example.com", "MCD202606270001",
    "MCD202606270002", "1288.4", "1288.40", "1234.50", "88.5", "66.6",
    "June 27, 2026", "2026/06/27", "27/06/2026", "06-27-2026",
)


class TestRedactionBoundaries(unittest.TestCase):
    """第八轮审查 P2：小数金额 / 中文地址 / 英文日期 / 异常字段名 / 嵌套数据。"""

    def setUp(self):
        self.profile = _extreme_profile()
        self.public = render_story(self.profile, "public")

    def test_extreme_values_are_all_gone(self):
        for raw in EXTREME_MUST_GONE:
            self.assertNotIn(raw, self.public, raw)
        self.assertEqual(self.public.count("¥") + self.public.count("￥"), 0)

    def test_zero_leak_and_month_granularity_kept(self):
        from mcd_timemachine.story import scan_leaks

        self.assertEqual(scan_leaks(self.public, self.profile), [])
        self.assertEqual(
            len(re.findall(r"\d{4}-\d{2}(?!-)", self.public)),
            len(re.findall(r"\d{4}-\d{2}(?!-)",
                           render_story(self.profile, "personal"))))

    def test_fullwidth_yen_and_rmb_are_redacted(self):
        from mcd_timemachine.story import redact_html

        self.assertEqual(redact_html("￥1,288.40"), "金额已隐藏")
        self.assertEqual(redact_html("共 RMB 1288.40"), "共 金额已隐藏")

    def test_alt_date_formats_are_redacted(self):
        from mcd_timemachine.story import redact_html

        for raw in ("2026/06/27", "2026.6.27", "27/06/2026", "06-27-2026",
                    "June 27, 2026", "Jun 27 2026"):
            self.assertEqual(redact_html(raw), "日期已隐藏", raw)

    def test_chinese_address_without_label_is_redacted(self):
        from mcd_timemachine.story import redact_html

        out = redact_html("送到上海市浦东新区世纪大道 100 号")
        self.assertNotIn("世纪大道", out)
        self.assertNotIn("100 号", out)
        self.assertIn("地址已隐藏", out)

    def test_abnormal_field_names_detected(self):
        from mcd_timemachine.story import is_sensitive_key

        for key in ("addr2", "mobile1", "addressLine1", "phoneNumber",
                    "receiverName", "收货信息", "订单编号", "姓名", "userName",
                    "idCardNo", "bankCard", "emailAddress", "order_id"):
            self.assertTrue(is_sensitive_key(key), key)
        for key in ("name", "tag", "desc", "count", "month", "store",
                    "total_orders", "spend_pct", "refund_count"):
            self.assertFalse(is_sensitive_key(key), key)

    def test_nested_structures_are_redacted(self):
        from mcd_timemachine.story import redact_profile

        dump = json.dumps(redact_profile(self.profile), ensure_ascii=False)
        for raw in ("MCD202606270002", "文一西路", "66.6", "22.5"):
            self.assertNotIn(raw, dump, raw)
        self.assertIn("已隐藏", dump)

    def test_ratio_and_count_fields_are_not_over_redacted(self):
        """过度脱敏同样是缺陷：比例 / 计数不该被当成金额抹掉。"""
        from mcd_timemachine.story import is_safe_number_key, redact_profile

        red = redact_profile(self.profile)
        self.assertEqual(red["summary"]["spend_pct"], 42)
        self.assertEqual(red["summary"]["refund_count"], 12345)
        self.assertEqual(red["summary"]["yearly_outlay"], "金额已隐藏")
        self.assertTrue(is_safe_number_key("refund_count"))
        self.assertFalse(is_safe_number_key("yearly_outlay"))

    def test_unregistered_number_is_still_redacted(self):
        """允许清单 + 后缀约定之外的新数值字段，必须默认被抹（fail-closed）。"""
        import copy

        from mcd_timemachine.story import redact_profile

        p = copy.deepcopy(self.profile)
        p["summary"]["vip_spending"] = 9999.9
        self.assertEqual(redact_profile(p)["summary"]["vip_spending"], "金额已隐藏")

    def test_safe_numbers_never_enter_the_leak_candidates(self):
        """自检候选里不能混进允许展示的比例 / 计数，否则会误报自己人。"""
        from mcd_timemachine.story import sensitive_tokens

        tokens = sensitive_tokens(self.profile)
        for safe in ("12,345.00", "12345.00", "¥12,345.00", "12,345"):
            self.assertNotIn(safe, tokens, safe)

    def test_personal_masking_covers_alt_formats(self):
        """个人版的「隐私浏览」也要认这些写法：值仍在文件里（个人版本就该如此），
        但必须被包进打码 span，开关一开就遮挡。"""
        from mcd_timemachine.story import _mask_text

        masked = _mask_text("June 27, 2026 花了 ￥1,288.40，另有 RMB 88.5")
        self.assertEqual(masked.count("class='pv'"), 3)
        for raw in ("June 27, 2026", "￥1,288.40", "RMB 88.5"):
            self.assertIn(f">{raw}</span>", masked, raw)


class TestPublicCompleteness(unittest.TestCase):
    """第八轮审查 P2 第二点：公开版字段策略显式化 + 内容完整性（别脱过头）。"""

    def setUp(self):
        self.profile = _profile("mcp")
        self.public = render_story(self.profile, "public")

    def test_complete_page_reports_no_issue(self):
        from mcd_timemachine.story import public_completeness_issues

        self.assertEqual(public_completeness_issues(self.public, self.profile), [])

    def test_policy_defines_the_allow_list(self):
        from mcd_timemachine.story import PUBLIC_FIELD_POLICY, _SAFE_NUMBER_KEYS

        grouped = {k for group in PUBLIC_FIELD_POLICY.values() for k in group}
        self.assertEqual(grouped, set(_SAFE_NUMBER_KEYS))
        self.assertTrue(grouped, "允许清单为空等于让所有数值都被抹掉")

    def test_public_and_personal_keep_the_same_structure(self):
        """公开版只该少金额 / 门店名 / 到日日期，不该少结构。"""
        from mcd_timemachine.story import _section_slice

        personal = render_story(self.profile, "personal")
        for index in range(1, 7):
            a, b = _section_slice(personal, index), _section_slice(self.public, index)
            for needle in ("class='reveal'", "class='bar-head'", "<span class='pill'>",
                           "<div title='", "class='persona-card", "class='shift-head'"):
                self.assertEqual(b.count(needle), a.count(needle), f"第 {index} 章 {needle}")

    def test_missing_chapter_is_detected(self):
        from mcd_timemachine.story import public_completeness_issues

        broken = self.public.replace("id='ch5'", "id='gone'")
        issues = public_completeness_issues(broken, self.profile)
        self.assertTrue(any("缺少第 5 章" in i for i in issues), issues)

    def test_lost_month_bars_are_detected(self):
        from mcd_timemachine.story import public_completeness_issues

        broken = re.sub(r"<div title='\d{4}-\d{2}：\d+ 单'>", "", self.public, count=1)
        self.assertNotEqual(broken, self.public)
        issues = public_completeness_issues(broken, self.profile)
        self.assertTrue(any("月度趋势柱数" in i for i in issues), issues)

    def test_over_redacted_store_count_is_detected(self):
        from mcd_timemachine.story import public_completeness_issues

        stores = [x for x in self.profile["stores"] if x.get("name") not in (None, "未知门店")]
        self.assertTrue(stores)
        broken = self.public.replace(f"常去门店 {len(stores)} 家", "门店已隐藏")
        issues = public_completeness_issues(broken, self.profile)
        self.assertTrue(any("门店家数" in i for i in issues), issues)

    def test_verify_raises_public_content_error(self):
        from mcd_timemachine.story import PublicContentError, verify_public_complete

        with self.assertRaises(PublicContentError) as ctx:
            verify_public_complete("<p>空页面</p>", self.profile)
        self.assertTrue(ctx.exception.report())

    def test_empty_profile_is_not_flagged(self):
        from mcd_timemachine.story import public_completeness_issues

        empty = _empty_profile()
        html = render_story(empty, "public")
        self.assertEqual(public_completeness_issues(html, empty), [])

    def test_various_sample_sizes_pass_both_checks(self):
        from mcd_timemachine.story import public_completeness_issues, scan_leaks

        for count in (1, 2, 3, 5, 6, 7, 12, 30, 160):
            profile = _profile("mcp", count=count)
            html = render_story(profile, "public")
            self.assertEqual(scan_leaks(html, profile), [], f"{count} 单")
            self.assertEqual(public_completeness_issues(html, profile), [], f"{count} 单")


class TestFailClosedPublishing(unittest.TestCase):
    """第八轮审查 P1：检出残留必须**拒绝输出**，而不是悄悄替换后继续写。

    做法是把前两层打成「什么都不做」，看第三层是否真的拦得住。
    这样测的是发布策略本身，而不是某条正则。
    """

    def setUp(self):
        self.profile = _profile("mcp")

    def _bypass_layers(self):
        from mcd_timemachine import story

        return (
            mock.patch.object(story, "redact_profile", lambda profile: profile),
            mock.patch.object(story, "redact_html", lambda html, names=None: html),
        )

    def test_render_public_refuses_when_layers_are_bypassed(self):
        from mcd_timemachine.story import PrivacyLeakError

        first, second = self._bypass_layers()
        with first, second, self.assertRaises(PrivacyLeakError) as ctx:
            render_story(self.profile, "public")
        self.assertTrue(ctx.exception.entries)
        self.assertTrue(ctx.exception.kinds())

    def test_the_check_is_what_catches_it(self):
        """关掉这道检查就能看到泄漏 —— 证明拦住它的确实是检查，而不是别的东西清场了。"""
        from mcd_timemachine.story import scan_leaks

        first, second = self._bypass_layers()
        with first, second:
            loose = render_story(self.profile, "public", leak_check=False)
        self.assertNotEqual(scan_leaks(loose, self.profile), [])

    def test_write_story_leaves_no_file(self):
        from mcd_timemachine.story import PrivacyLeakError

        first, second = self._bypass_layers()
        with tempfile.TemporaryDirectory() as tmp, first, second:
            with self.assertRaises(PrivacyLeakError):
                write_story(self.profile, Path(tmp), "public")
            self.assertEqual(list(Path(tmp).iterdir()), [], "自检不过时不该落下任何文件")

    def test_write_reports_does_not_swallow_the_failure(self):
        """report.md 照写，但隐私失败必须冒出来——不能被「可选资源」的兜底吞掉。"""
        from mcd_timemachine.reporter import write_reports
        from mcd_timemachine.story import PrivacyLeakError

        first, second = self._bypass_layers()
        with tempfile.TemporaryDirectory() as tmp, first, second:
            with self.assertRaises(PrivacyLeakError):
                write_reports(self.profile, Path(tmp), "public")
            self.assertTrue((Path(tmp) / "report.md").is_file())
            self.assertFalse((Path(tmp) / "story.html").exists())

    def test_cli_exit_code_3_and_no_output(self):
        first, second = self._bypass_layers()
        with tempfile.TemporaryDirectory() as tmp, first, second:
            (Path(tmp) / "profile.json").write_text(
                json.dumps(self.profile, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(main(["story", "--out", tmp, "--privacy", "public"]), 3)
            self.assertFalse((Path(tmp) / "story.html").exists())

    def test_error_report_masks_the_values(self):
        """报错信息不能把敏感值再打一遍（否则「报泄漏」本身成了新的泄漏点）。"""
        from mcd_timemachine.story import PrivacyLeakError, sensitive_tokens

        first, second = self._bypass_layers()
        with first, second:
            try:
                render_story(self.profile, "public")
                self.fail("应该抛出 PrivacyLeakError")
            except PrivacyLeakError as exc:
                text = "\n".join([str(exc)] + exc.report())
        for token in sensitive_tokens(self.profile):
            self.assertNotIn(token, text, token)

    def test_personal_mode_is_never_blocked(self):
        """fail-closed 只针对公开版；个人版本来就允许含原始数据。"""
        first, second = self._bypass_layers()
        with first, second:
            html = render_story(self.profile, "personal")
        self.assertIn("¥", html)

    def test_successful_write_is_atomic_and_leaves_no_tmp(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_story(self.profile, Path(tmp), "personal")
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["story.html"])
            self.assertEqual(path.read_text(encoding="utf-8"),
                             render_story(self.profile, "personal"))


class TestBrowserHarness(unittest.TestCase):
    """浏览器验收脚本与 CI 的接线：单引擎等于把 Safari 的差异留在盲区里。"""

    def setUp(self):
        root = Path(__file__).resolve().parent.parent
        self.js = (root / "scripts" / "browser_check.js").read_text(encoding="utf-8")
        self.ci = (root / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")

    def test_engine_is_selectable(self):
        self.assertIn("const ENGINE = process.env.BROWSER_ENGINE || argOf('engine')", self.js)
        self.assertIn("const engine = playwright[ENGINE];", self.js)
        self.assertNotIn("chromium.launch()", self.js)   # 不能写死引擎
        self.assertIn("await engine.launch()", self.js)

    def test_viewport_is_parameterised(self):
        self.assertIn("const VP_LABEL = VIEWPORT.width + 'px';", self.js)
        self.assertIn("webkit", self.js)
        self.assertIn("--viewport=", self.js)

    def test_exported_samples_are_separated_per_engine(self):
        """两种引擎的导出样本不能互相覆盖，否则证据只剩一个引擎的。"""
        self.assertIn("const outDir = keepDir ? path.join(keepDir, ENGINE) : tmpDir;", self.js)

    def test_ci_runs_both_engines_and_installs_both(self):
        self.assertIn("npx playwright install --with-deps chromium webkit", self.ci)
        self.assertIn("--engine=chromium", self.ci)
        self.assertIn("--engine=webkit", self.ci)
        self.assertIn("artifacts/**/*.png", self.ci)


if __name__ == "__main__":
    unittest.main()
