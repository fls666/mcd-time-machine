"""分析层的回归测试：统计口径、边界数据与故事化维度。"""

from __future__ import annotations

import unittest
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List

from mcd_timemachine.analyzer import analyze
from mcd_timemachine.collector import build_coverage
from mcd_timemachine.demo_data import generate


def _mk(date: str, hour: int, items: List[Dict[str, Any]], total: float = None,
        store: str = "测试餐厅", amount_source: str = "actual"):
    import datetime

    weekday = datetime.datetime.strptime(date, "%Y-%m-%d").weekday()
    amount = total
    if amount is None:
        amount = sum((i.get("price") or 0) * (i.get("qty") or 1) for i in items)
    return {
        "amount_source": amount_source,
        "order_id": f"{date}-{hour}",
        "time": f"{date} {hour:02d}:00",
        "date": date,
        "hour": hour,
        "weekday": weekday,
        "store": store,
        "channel": "堂食",
        "total": amount,
        "discount": 0,
        "items": items,
    }


class TestAnalyzeBasics(unittest.TestCase):
    def setUp(self):
        self.orders = [
            _mk("2026-01-01", 12, [{"name": "巨无霸", "qty": 1, "price": 24.0,
                                    "category": "汉堡"}]),
            _mk("2026-01-02", 19, [{"name": "巨无霸", "qty": 2, "price": 24.0,
                                    "category": "汉堡"},
                                   {"name": "薯条", "qty": 1, "price": 12.0,
                                    "category": "小食"}]),
            _mk("2026-02-01", 22, [{"name": "麦旋风", "qty": 1, "price": 9.5,
                                    "category": "甜品"}]),
        ]

    def test_summary_numbers(self):
        profile = analyze(self.orders)
        s = profile["summary"]
        self.assertEqual(s["total_orders"], 3)
        self.assertAlmostEqual(s["total_spend"], 24.0 + 60.0 + 9.5, places=2)
        self.assertAlmostEqual(s["avg_order"], (93.5) / 3, places=2)
        self.assertEqual(s["total_items"], 5)  # 1 + 3 + 1
        self.assertEqual(s["first_date"], "2026-01-01")
        self.assertEqual(s["last_date"], "2026-02-01")

    def test_top_items_sorted(self):
        profile = analyze(self.orders)
        self.assertEqual(profile["top_items"][0]["name"], "巨无霸")
        self.assertEqual(profile["top_items"][0]["qty"], 3)

    def test_category_ratio(self):
        profile = analyze(self.orders)
        cats = {c["name"]: c["qty"] for c in profile["categories"]}
        self.assertEqual(cats["汉堡"], 3)
        self.assertEqual(cats["小食"], 1)
        self.assertEqual(cats["甜品"], 1)

    def test_slot_and_weekday(self):
        profile = analyze(self.orders)
        slots = {s["name"]: s["count"] for s in profile["slots"]}
        self.assertEqual(slots["午餐"], 1)
        self.assertEqual(slots["晚餐"], 1)
        self.assertEqual(slots["夜宵"], 1)

    def test_combos(self):
        profile = analyze(self.orders)
        self.assertIn("巨无霸 + 薯条", [c["pair"] for c in profile["combos"]])

    def test_recommendations_exclude_eaten(self):
        profile = analyze(self.orders)
        names = {r["name"] for r in profile["recommendations"]}
        self.assertNotIn("巨无霸", names)
        self.assertNotIn("麦旋风", names)
        self.assertTrue(names)

    def test_keywords_present(self):
        profile = analyze(self.orders)
        words = [k["word"] for k in profile["keywords"]]
        self.assertIn("巨无霸", words)
        self.assertTrue(any("控" in w for w in words))


class TestEdgeCases(unittest.TestCase):
    def test_empty_orders(self):
        profile = analyze([], None, build_coverage("demo", 0, 0, []))
        self.assertTrue(profile["empty"])
        self.assertEqual(profile["summary"]["total_orders"], 0)

    def test_orders_without_amount(self):
        orders = [_mk("2026-01-01", 12, [{"name": "可乐", "qty": 1, "price": 9.0,
                                          "category": "饮料"}])]
        orders[0]["total"] = None
        profile = analyze(orders)
        self.assertEqual(profile["summary"]["orders_with_amount"], 0)
        self.assertEqual(profile["summary"]["total_spend"], 0)

    def test_single_day_streak(self):
        orders = [_mk("2026-03-01", 12, [{"name": "薯条", "qty": 1, "price": 12.0,
                                          "category": "小食"}])]
        profile = analyze(orders)
        self.assertEqual(profile["summary"]["best_streak"], 1)
        self.assertEqual(profile["summary"]["span_days"], 1)

    def test_consecutive_days_streak(self):
        orders = [
            _mk(f"2026-03-0{d}", 12, [{"name": "薯条", "qty": 1, "price": 12.0,
                                       "category": "小食"}])
            for d in (1, 2, 3)
        ] + [_mk("2026-03-09", 12, [{"name": "薯条", "qty": 1, "price": 12.0,
                                     "category": "小食"}])]
        profile = analyze(orders)
        self.assertEqual(profile["summary"]["best_streak"], 3)

    def test_no_crash_on_items_without_price(self):
        orders = [_mk("2026-01-01", 12, [{"name": "赠品", "qty": 1, "price": None,
                                          "category": "其他"}])]
        profile = analyze(orders)
        self.assertFalse(profile["empty"])

    def test_demo_data_is_deterministic(self):
        a = generate(orders=30, seed=7)
        b = generate(orders=30, seed=7)
        self.assertEqual(a, b)
        self.assertEqual(len(a), 30)

    def test_demo_data_as_of_is_fully_reproducible(self):
        """显式基准日期后，同一 seed 在任何时候都产出完全一致的数据。

        不传 end 时用 `datetime.now()`，两次调用会差几微秒——日期相同但时刻可能跨分钟，
        所以「完全一致」这件事只有固定 end 才成立。CI 的版面断言依赖这一点。
        """
        fixed = datetime(2026, 6, 30)
        a = generate(orders=40, seed=7, end=fixed)
        b = generate(orders=40, seed=7, end=fixed)
        self.assertEqual(a, b)
        self.assertEqual(a[-1]["time"][:10] <= "2026-06-30", True)

        # 换个基准日期，数据应当随之变化（证明确实生效，而不是被忽略）
        other = generate(orders=40, seed=7, end=datetime(2025, 1, 15))
        self.assertNotEqual([o["order_id"] for o in a], [o["order_id"] for o in other])

    def test_demo_data_as_of_accepts_aware_datetime(self):
        """带时区的 datetime 不应引发 naive/aware 相减报错。"""
        aware = datetime(2026, 6, 30, tzinfo=timezone.utc)
        orders = generate(orders=10, seed=1, end=aware)
        self.assertEqual(len(orders), 10)

    def test_taste_shift_returns_delta(self):
        orders = []
        for month in range(1, 7):
            orders.append(_mk(f"2026-0{month}-05", 12,
                              [{"name": "巨无霸", "qty": 1, "price": 24.0,
                                "category": "汉堡"}]))
        for month in range(7, 13):
            orders.append(_mk(f"2026-{month:02d}-05", 12,
                              [{"name": "麦旋风", "qty": 1, "price": 9.5,
                                "category": "甜品"}]))
        profile = analyze(orders)
        shift = {s["category"]: s["delta"] for s in profile["taste_shift"]}
        self.assertGreater(shift["甜品"], 0)
        self.assertLess(shift["汉堡"], 0)


class TestAmountBasis(unittest.TestCase):
    """实付金额与估算金额必须分开统计，不能混算。"""

    def _o(self, date, total, source, items=None):
        return {
            "order_id": date, "time": f"{date} 12:00", "date": date,
            "hour": 12, "weekday": 0, "store": "测试餐厅", "channel": "堂食",
            "total": total, "amount_source": source, "discount": 0,
            "items": items or [{"name": "巨无霸", "qty": 1, "price": 24.0,
                                "category": "汉堡"}],
        }

    def test_actual_and_estimated_not_mixed(self):
        orders = [
            self._o("2026-01-01", 100.0, "actual"),
            self._o("2026-01-02", 900.0, "estimated"),
        ]
        s = analyze(orders)["summary"]
        self.assertEqual(s["actual_spend"], 100.0)
        self.assertEqual(s["estimated_spend"], 900.0)
        # 主指标只认实际支付
        self.assertEqual(s["display_spend"], 100.0)
        self.assertEqual(s["spend_basis"], "mixed")
        self.assertEqual(s["avg_order"], 100.0)

    def test_total_spend_still_full_figure(self):
        orders = [
            self._o("2026-01-01", 100.0, "actual"),
            self._o("2026-01-02", 900.0, "estimated"),
        ]
        s = analyze(orders)["summary"]
        self.assertEqual(s["total_spend"], 1000.0)

    def test_no_actual_falls_back_to_estimated(self):
        orders = [self._o("2026-01-02", 30.0, "estimated")]
        s = analyze(orders)["summary"]
        self.assertEqual(s["display_spend"], 30.0)
        self.assertEqual(s["spend_basis"], "estimated")

    def test_orders_without_amount_counted(self):
        orders = [
            self._o("2026-01-01", 100.0, "actual"),
            self._o("2026-01-02", None, "unknown"),
        ]
        s = analyze(orders)["summary"]
        self.assertEqual(s["unknown_amount_orders"], 1)
        self.assertEqual(s["actual_spend"], 100.0)

    def test_unlabeled_amount_source_not_treated_as_actual(self):
        order = self._o("2026-01-01", 50.0, "actual")
        order.pop("amount_source")
        s = analyze([order])["summary"]
        self.assertEqual(s["actual_spend"], 0.0)
        self.assertEqual(s["spend_basis"], "unlabeled")


class TestTagConfidence(unittest.TestCase):
    """标签必须用统一可信度排序，且不得为了凑数硬贴标签。"""

    def _o(self, date, items, total=40.0):
        return {
            "order_id": date, "time": f"{date} 12:00", "date": date,
            "hour": 12, "weekday": 0, "store": "测试餐厅", "channel": "堂食",
            "total": total, "amount_source": "actual", "discount": 0,
            "items": items,
        }

    def test_confidence_within_range(self):
        orders = [self._o(f"2026-01-{d:02d}", [
            {"name": "麦辣鸡腿堡", "qty": 2, "price": 22.0, "category": "汉堡"},
            {"name": "薯条", "qty": 1, "price": 12.0, "category": "小食"},
        ]) for d in range(1, 9)]
        for tag in analyze(orders)["tags"]:
            self.assertIn("confidence", tag)
            self.assertTrue(0 <= tag["confidence"] <= 100)
            self.assertIn("evidence", tag)

    def test_no_padding_when_only_one_tag_qualifies(self):
        # 只有咖啡类明显，其他品类都不达阈值
        orders = [self._o(f"2026-01-{d:02d}", [
            {"name": "拿铁", "qty": 1, "price": 18.0, "category": "饮料"},
            {"name": "玉米杯", "qty": 1, "price": 8.0, "category": "沙拉"},
        ]) for d in range(1, 9)]
        tags = analyze(orders)["tags"]
        self.assertEqual([t["tag"] for t in tags], ["咖啡续命"])

    def test_tiny_data_shows_exploring(self):
        orders = [self._o("2026-01-01", [
            {"name": "玉米杯", "qty": 1, "price": 8.0, "category": "沙拉"},
        ])]
        profile = analyze(orders)
        self.assertFalse(profile["data_sufficient"])
        self.assertEqual([t["tag"] for t in profile["tags"]], ["口味探索中"])

    def test_sorted_by_confidence_not_raw_score(self):
        orders = [self._o(f"2026-01-{d:02d}", [
            {"name": "麦辣鸡腿堡", "qty": 1, "price": 22.0, "category": "汉堡"},
            {"name": "拿铁", "qty": 3, "price": 18.0, "category": "饮料"},
        ]) for d in range(1, 9)]
        tags = analyze(orders)["tags"]
        confs = [t["confidence"] for t in tags]
        self.assertEqual(confs, sorted(confs, reverse=True))


class TestMemoryWording(unittest.TestCase):
    """回忆文案不能使用未经证实的说法。"""

    def test_titles_are_accurate(self):
        orders = [
            _mk("2026-01-01", 12, [{"name": "巨无霸", "qty": 1, "price": 24.0,
                                    "category": "汉堡"}], total=24.0),
            _mk("2026-06-01", 19, [{"name": "安格斯MAX厚牛堡", "qty": 2,
                                    "price": 40.0, "category": "汉堡"}], total=80.0),
        ]
        titles = [m["title"] for m in analyze(orders)["memories"]]
        self.assertIn("记录中的第一单", titles)
        self.assertIn("最豪华的一单", titles)
        self.assertNotIn("故事的起点", titles)
        self.assertNotIn("最难忘的一单", titles)
        self.assertNotIn("最沉迷的月份", titles)


class TestSmallSampleGuard(unittest.TestCase):
    """样本量检查必须先于标签计算：1 单全是辣也不能得出「嗜辣星人」。"""

    def _o(self, date, items, hour=12):
        import datetime
        weekday = datetime.datetime.strptime(date, "%Y-%m-%d").weekday()
        return {
            "order_id": date, "time": f"{date} {hour:02d}:00", "date": date,
            "hour": hour, "weekday": weekday, "store": "测试餐厅", "channel": "堂食",
            "total": 40.0, "amount_source": "actual", "discount": 0, "items": items,
        }

    def test_single_spicy_order_not_tagged(self):
        orders = [self._o("2026-01-01", [
            {"name": "麦辣鸡腿堡", "qty": 2, "price": 22.0, "category": "汉堡"},
            {"name": "麦辣鸡翅", "qty": 2, "price": 12.0, "category": "小食"},
        ])]
        profile = analyze(orders)
        self.assertFalse(profile["data_sufficient"])
        self.assertEqual([t["tag"] for t in profile["tags"]], ["口味探索中"])
        self.assertEqual(profile["tags"][0]["confidence"], 0)

    def test_below_item_threshold_not_tagged(self):
        # 5 单但只有 6 件单品，仍低于 8 件门槛
        orders = [self._o(f"2026-01-0{d}", [
            {"name": "麦辣鸡腿堡", "qty": 1, "price": 22.0, "category": "汉堡"},
        ]) for d in range(1, 6)]
        profile = analyze(orders)
        self.assertFalse(profile["data_sufficient"])
        self.assertEqual([t["tag"] for t in profile["tags"]], ["口味探索中"])

    def test_min_support_blocks_weak_tag(self):
        # 只有 1 件咖啡，占比刚好过阈值，但支撑量不足，不应贴「咖啡续命」
        with_coffee = [
            {"name": "拿铁", "qty": 1, "price": 18.0, "category": "饮料"},
            {"name": "巨无霸", "qty": 1, "price": 24.0, "category": "汉堡"},
        ]
        without_coffee = [
            {"name": "麦香鱼", "qty": 1, "price": 18.0, "category": "汉堡"},
            {"name": "麦乐鸡", "qty": 1, "price": 11.0, "category": "小食"},
        ]
        orders = [self._o("2026-01-01", with_coffee)]
        orders += [self._o(f"2026-01-{d:02d}", without_coffee) for d in range(2, 9)]
        names = [t["tag"] for t in analyze(orders)["tags"]]
        self.assertNotIn("咖啡续命", names)

    def test_sample_and_label_present(self):
        orders = [self._o(f"2026-01-{d:02d}", [
            {"name": "巨无霸", "qty": 1, "price": 24.0, "category": "汉堡"},
            {"name": "薯条", "qty": 1, "price": 12.0, "category": "小食"},
        ]) for d in range(1, 9)]
        for tag in analyze(orders)["tags"]:
            self.assertIn("sample", tag)
            self.assertIn(tag["confidence_label"], ("高", "中", "低", "样本不足"))

    def test_small_sample_lowers_confidence(self):
        items = [
            {"name": "巨无霸", "qty": 1, "price": 24.0, "category": "汉堡"},
            {"name": "麦香鱼", "qty": 1, "price": 18.0, "category": "汉堡"},
        ]
        small = analyze([self._o(f"2026-01-{d:02d}", items) for d in range(5, 10)])
        large = analyze([self._o(f"2026-{m:02d}-{d:02d}", items)
                         for m in range(1, 5) for d in range(1, 6)])
        get_burger = lambda p: next(t["confidence"] for t in p["tags"]
                                    if t["tag"] == "汉堡原教旨")
        self.assertLess(get_burger(small), get_burger(large))


class TestShiftGrouping(unittest.TestCase):
    """同一天大量订单时，前后半程不能失衡或空组。"""

    def test_same_day_orders_split_evenly(self):
        from mcd_timemachine.analyzer import _compute_shift

        # 12 单里 10 单挤在同一天
        dated = [{"date": "2026-03-01", "time": f"2026-03-01 {10 + i:02d}:00",
                  "items": [{"name": "巨无霸", "qty": 1, "category": "汉堡"}]}
                 for i in range(10)]
        dated += [{"date": "2026-01-01", "time": "2026-01-01 12:00",
                   "items": [{"name": "麦旋风", "qty": 1, "category": "甜品"}]},
                  {"date": "2026-05-01", "time": "2026-05-01 12:00",
                   "items": [{"name": "麦旋风", "qty": 1, "category": "甜品"}]}]
        deltas, basis = _compute_shift(dated, Counter())
        self.assertTrue(basis["available"])
        self.assertEqual(basis["early_orders"], 6)
        self.assertEqual(basis["late_orders"], 6)

    def test_too_few_orders_returns_reason(self):
        from mcd_timemachine.analyzer import _compute_shift

        dated = [{"date": "2026-01-01", "time": "2026-01-01 12:00",
                  "items": [{"name": "巨无霸", "qty": 1, "category": "汉堡"}]}]
        deltas, basis = _compute_shift(dated, Counter())
        self.assertEqual(deltas, [])
        self.assertFalse(basis["available"])
        self.assertIn("reason", basis)


class TestRecommendation(unittest.TestCase):
    """名称标准化与推荐多样性。"""

    def test_base_name_normalizes_spec(self):
        from mcd_timemachine.analyzer import _base_name

        self.assertEqual(_base_name("麦乐鸡（5块）"), "麦乐鸡")
        self.assertEqual(_base_name("麦乐鸡(5块)"), "麦乐鸡")
        self.assertEqual(_base_name("巨无霸 大份"), "巨无霸")
        self.assertEqual(_base_name("薯条 加大"), "薯条")
        self.assertEqual(_base_name("可口可乐"), "可乐")

    def test_spec_variant_not_recommended_as_new(self):
        from mcd_timemachine.analyzer import _recommend

        item_count = Counter({"麦乐鸡（5块）": 3})
        menu = [{"name": "麦乐鸡", "category": "小食"},
                {"name": "巨无霸", "category": "汉堡"}]
        recs = _recommend(item_count, Counter({"小食": 3}), menu, {})
        self.assertNotIn("麦乐鸡", [r["name"] for r in recs])

    def test_at_most_two_per_category(self):
        from mcd_timemachine.analyzer import _recommend

        menu = ([{"name": f"汉堡{i}", "category": "汉堡"} for i in range(6)]
                + [{"name": f"甜品{i}", "category": "甜品"} for i in range(3)])
        recs = _recommend(Counter({"薯条": 5}), Counter({"小食": 5}), menu, {})
        counts = Counter(r["name"][:2] for r in recs)
        self.assertLessEqual(counts["汉堡"], 2)
        self.assertLessEqual(counts["甜品"], 2)

    def test_reasons_are_concise(self):
        from mcd_timemachine.analyzer import _recommend

        menu = [{"name": "麦旋风", "category": "甜品"},
                {"name": "安格斯MAX厚牛堡", "category": "汉堡"}]
        recs = _recommend(Counter({"薯条": 20}), Counter({"小食": 20}), menu, {})
        for r in recs:
            self.assertLessEqual(r["reason"].count("；"), 1)


if __name__ == "__main__":
    unittest.main()
