"""分析层的回归测试：统计口径、边界数据与故事化维度。"""

from __future__ import annotations

import unittest
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


if __name__ == "__main__":
    unittest.main()
