"""采集与归一化层的回归测试。

重点覆盖静态审查提出的三个 P0 问题：
    - 历史订单只取第一页
    - 嵌套单品字段污染整单字段
    - 去重标识不可靠
"""

from __future__ import annotations

import unittest
from typing import Any, Dict, List

from mcd_timemachine.collector import (
    build_coverage,
    extract_records,
    fetch_order_records,
    normalize_orders,
)


def _order(oid: str, time_str: str, amount: float, items: List[Dict[str, Any]]):
    return {
        "orderId": oid,
        "orderTime": time_str,
        "payAmount": amount,
        "storeName": "测试餐厅",
        "items": items,
    }


class FakeClient:
    """按 pageNo 返回分页数据的假客户端。"""

    def __init__(self, pages: List[List[Dict[str, Any]]], shape_key: str = "pageNo"):
        self.pages = pages
        self.shape_key = shape_key
        self.calls: List[Dict[str, Any]] = []

    def call_tool(self, name: str, arguments: Dict[str, Any]):
        self.calls.append(dict(arguments))
        page = arguments.get(self.shape_key, arguments.get("page", 1))
        index = int(page) - 1
        if index < 0 or index >= len(self.pages):
            return {"data": {"list": []}}, ""
        return {"data": {"list": self.pages[index]}}, ""


class TestFieldExtraction(unittest.TestCase):
    def test_order_total_not_polluted_by_item_price(self):
        """整单金额必须取 payAmount，而不是明细里第一个单品的价格。"""
        payload = {
            "data": {
                "list": [
                    _order("A1", "2026-03-01 12:10", 38.5,
                           [{"productName": "巨无霸", "quantity": 1, "price": 24.0}])
                ]
            }
        }
        orders = normalize_orders(extract_records(payload))
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["total"], 38.5)
        self.assertEqual(orders[0]["items"][0]["price"], 24.0)

    def test_nested_item_time_not_used_as_order_time(self):
        payload = {
            "data": [
                {
                    "orderId": "A2",
                    "orderTime": "2026-04-02 08:30",
                    "payAmount": 20.0,
                    "items": [{"productName": "薯饼", "quantity": 2, "price": 6.5,
                               "createTime": "2020-01-01 00:00"}],
                }
            ]
        }
        orders = normalize_orders(extract_records(payload))
        self.assertEqual(orders[0]["date"], "2026-04-02")
        self.assertEqual(orders[0]["hour"], 8)

    def test_item_node_is_not_treated_as_order(self):
        """只含商品信息的节点不应被识别成订单。"""
        payload = {
            "data": {
                "list": [
                    {"productName": "麦辣鸡翅", "quantity": 2, "price": 13.0},
                ]
            }
        }
        self.assertEqual(extract_records(payload), [])

    def test_total_falls_back_to_items_sum(self):
        payload = {
            "data": [
                _order("A3", "2026-05-05 19:00", None,
                       [{"productName": "薯条", "quantity": 2, "price": 12.0}])
            ]
        }
        orders = normalize_orders(extract_records(payload))
        self.assertIsInstance(orders[0]["total"], float)
        self.assertAlmostEqual(orders[0]["total"], 24.0, places=2)

    def test_timestamp_and_string_time(self):
        payload = {"data": [_order("A4", 1772000000, 30.0, [])]}
        orders = normalize_orders(extract_records(payload))
        self.assertTrue(orders[0]["date"].startswith("2026"))


class TestDeduplication(unittest.TestCase):
    def test_same_order_id_across_pages_deduped(self):
        page1 = [_order("SAME", "2026-01-01 12:00", 10.0, []),
                 _order("B1", "2026-01-02 12:00", 20.0, [])]
        page2 = [_order("SAME", "2026-01-01 12:00", 10.0, []),
                 _order("B2", "2026-01-03 12:00", 30.0, [])]
        client = FakeClient([page1, page2])
        records, meta = fetch_order_records(client, max_pages=5, page_size=2)
        orders = normalize_orders(records)
        self.assertEqual(len(orders), 3)
        self.assertEqual(meta["raw_records"], 4)
        self.assertEqual(sorted(o["order_id"] for o in orders), ["B1", "B2", "SAME"])

    def test_orders_without_id_use_full_hash(self):
        """没有订单号时，用完整记录哈希去重，不同订单不会被误判为同一条。"""
        a = {"orderTime": "2026-01-01 12:00", "payAmount": 10.0}
        b = {"orderTime": "2026-01-01 12:00", "payAmount": 11.0}
        records = extract_records({"list": [a, b, dict(a)]})
        self.assertEqual(len(records), 2)


class TestPagination(unittest.TestCase):
    def test_fetches_all_pages(self):
        pages = [
            [_order(f"P{i}-{j}", f"2026-0{i}-01 12:00", 10.0, []) for j in range(3)]
            for i in range(1, 4)
        ]
        client = FakeClient(pages)
        records, meta = fetch_order_records(client, max_pages=10, page_size=3)
        self.assertEqual(len(records), 9)
        self.assertEqual(meta["pages"], 3)
        self.assertFalse(meta["truncated"])

    def test_stops_when_page_not_full(self):
        pages = [[_order(f"X{j}", "2026-02-01 12:00", 10.0, []) for j in range(3)],
                 [_order("X9", "2026-02-05 12:00", 10.0, [])]]
        client = FakeClient(pages)
        records, meta = fetch_order_records(client, max_pages=10, page_size=3)
        self.assertEqual(len(records), 4)
        self.assertEqual(meta["pages"], 2)

    def test_max_pages_limit_marks_truncated(self):
        pages = [[_order(f"Y{idx}-{j}", "2026-02-01 12:00", 10.0, []) for j in range(3)]
                 for idx in range(10)]
        client = FakeClient(pages)
        records, meta = fetch_order_records(client, max_pages=3, page_size=3)
        self.assertEqual(len(records), 9)
        self.assertTrue(meta["truncated"])
        self.assertTrue(any("分页" in w for w in meta["warnings"]))

    def test_empty_response_is_safe(self):
        client = FakeClient([])
        records, meta = fetch_order_records(client, max_pages=3, page_size=2)
        self.assertEqual(records, [])
        self.assertEqual(meta["pages"], 0)  # 没有任何一页产出数据


class TestCoverage(unittest.TestCase):
    def test_coverage_counts(self):
        orders = [
            {"date": "2026-01-01", "total": 10.0, "items": [{"name": "a"}], "store": "s"},
            {"date": "", "total": None, "items": [], "store": ""},
        ]
        cov = build_coverage("mcp", pages=2, raw_records=5, orders=orders)
        self.assertEqual(cov["unique_orders"], 2)
        self.assertEqual(cov["duplicates_removed"], 3)
        self.assertEqual(cov["with_date"], 1)
        self.assertEqual(cov["with_amount"], 1)
        self.assertEqual(cov["with_items"], 1)
        self.assertEqual(cov["range_start"], "2026-01-01")
        self.assertEqual(cov["range_end"], "2026-01-01")

    def test_coverage_on_empty(self):
        cov = build_coverage("demo", 0, 0, [])
        self.assertEqual(cov["unique_orders"], 0)
        self.assertEqual(cov["range_start"], "")


class AmountSourceTest(unittest.TestCase):
    """实付金额与商品估算必须区分，避免年度消费失真。"""

    def test_actual_amount_preferred(self):
        payload = {"data": {"list": [
            _order("A1", "2026-03-01 12:00", 38.5,
                   [{"productName": "巨无霸", "quantity": 1, "price": 24.0}])
        ]}}
        orders = normalize_orders(extract_records(payload))
        self.assertEqual(orders[0]["amount_source"], "actual")
        self.assertEqual(orders[0]["total"], 38.5)

    def test_missing_amount_marked_as_estimated(self):
        payload = {"data": {"list": [{
            "orderId": "E1", "orderTime": "2026-03-02 12:00",
            "items": [{"productName": "薯条", "quantity": 2, "price": 12.5}],
        }]}}
        orders = normalize_orders(extract_records(payload))
        self.assertEqual(orders[0]["amount_source"], "estimated")
        self.assertEqual(orders[0]["total"], 25.0)

    def test_no_amount_and_no_items_is_unknown(self):
        payload = {"data": {"list": [
            {"orderId": "U1", "orderTime": "2026-03-03 12:00"}
        ]}}
        orders = normalize_orders(extract_records(payload))
        self.assertEqual(orders[0]["amount_source"], "unknown")
        self.assertIsNone(orders[0]["total"])

    def test_coverage_reports_estimation(self):
        orders = [
            {"total": 10.0, "amount_source": "actual", "date": "2026-01-01", "items": []},
            {"total": 20.0, "amount_source": "estimated", "date": "2026-01-02", "items": []},
        ]
        cov = build_coverage("mcp", 1, 2, orders)
        self.assertEqual(cov["actual_amount_orders"], 1)
        self.assertEqual(cov["estimated_amount_orders"], 1)
        self.assertTrue(any("估算" in w for w in cov["warnings"]))


class SchemaPagingTest(unittest.TestCase):
    """按服务端 inputSchema 精确匹配分页参数，而不是瞎猜。"""

    def test_shapes_derived_from_schema(self):
        from mcd_timemachine.collector import _paging_shapes_from_schema

        schema = {"properties": {"pageNo": {}, "pageSize": {}}}
        self.assertEqual(_paging_shapes_from_schema(schema), [("pageNo", "pageSize")])

    def test_schema_with_cursor_is_preferred(self):
        from mcd_timemachine.collector import _paging_shapes_from_schema

        schema = {"properties": {"cursor": {}, "pageSize": {}, "pageNo": {}}}
        shapes = _paging_shapes_from_schema(schema)
        self.assertEqual(shapes[0], ("cursor", "pageSize"))

    def test_empty_schema_falls_back(self):
        from mcd_timemachine.collector import _paging_shapes_from_schema

        self.assertEqual(_paging_shapes_from_schema({}), [])
        self.assertEqual(_paging_shapes_from_schema(None), [])


class FakeSchemaClient:
    """带 list_tools 的假客户端，可指定返回体中的分页信号。"""

    def __init__(self, pages, schema, signals=None):
        self.pages = pages
        self.schema = schema
        self.signals = signals or {}
        self.calls = []

    def list_tools(self):
        return [{"name": "order-list", "inputSchema": self.schema}]

    def call_tool(self, name, arguments):
        self.calls.append(dict(arguments))
        page = int(arguments.get("pageNo", arguments.get("page", 1))) - 1
        if page < 0 or page >= len(self.pages):
            return {"data": {"list": []}}, ""
        body = {"data": {"list": self.pages[page]}}
        body.update(self.signals.get(page, {}))
        return body, ""


class CursorPagingTest(unittest.TestCase):
    def test_cursor_drives_next_page(self):
        """服务端给 nextCursor 时，必须带着游标翻下一页。"""
        calls = []

        class C:
            def list_tools(self):
                return [{"name": "order-list",
                         "inputSchema": {"properties": {"cursor": {}, "pageSize": {}}}}]

            def call_tool(self, name, arguments):
                calls.append(dict(arguments))
                cursor = arguments.get("cursor")
                if cursor is None:
                    return {"data": {"list": [_order("C1", "2026-01-01 12:00", 10.0, [])]},
                            "nextCursor": "TOKEN2"}, ""
                if cursor == "TOKEN2":
                    return {"data": {"list": [_order("C2", "2026-01-02 12:00", 10.0, [])]}}, ""

        records, meta = fetch_order_records(C(), max_pages=5, page_size=10)
        self.assertEqual(len(records), 2)
        self.assertEqual(meta["paging_mode"], "cursor")
        self.assertEqual(calls[1].get("cursor"), "TOKEN2")

    def test_total_hint_used_to_stop(self):
        schema = {"properties": {"pageNo": {}, "pageSize": {}}}
        pages = [[_order(f"T{j}", "2026-01-01 12:00", 10.0, [])] for j in range(4)]
        client = FakeSchemaClient(pages, schema, signals={0: {"total": 2}})
        records, meta = fetch_order_records(client, max_pages=10, page_size=1)
        self.assertEqual(len(records), 2)
        self.assertTrue(meta["coverage_confirmed"])
        self.assertEqual(meta["total_hint"], 2)

    def test_total_pages_respected(self):
        schema = {"properties": {"pageNo": {}, "pageSize": {}}}
        pages = [[_order(f"P{j}", "2026-01-01 12:00", 10.0, [])] for j in range(5)]
        client = FakeSchemaClient(pages, schema, signals={0: {"totalPages": 2}})
        records, meta = fetch_order_records(client, max_pages=10, page_size=1)
        self.assertEqual(len(records), 2)
        self.assertEqual(meta["stop_reason"], "exhausted")

    def test_has_more_false_stops_and_confirms(self):
        schema = {"properties": {"pageNo": {}, "pageSize": {}}}
        pages = [[_order("H1", "2026-01-01 12:00", 10.0, [])]]
        client = FakeSchemaClient(pages, schema, signals={0: {"hasMore": False}})
        records, meta = fetch_order_records(client, max_pages=10, page_size=10)
        self.assertEqual(len(records), 1)
        self.assertTrue(meta["coverage_confirmed"])
        self.assertEqual(meta["stop_reason"], "exhausted")


class UnconfirmedCoverageTest(unittest.TestCase):
    """整页重复时必须承认「未确认」，不能假装已经取完。"""

    def test_duplicate_page_marks_unconfirmed(self):
        class C:
            def list_tools(self):
                return [{"name": "order-list",
                         "inputSchema": {"properties": {"pageNo": {}, "pageSize": {}}}}]

            def call_tool(self, name, arguments):
                # 服务端忽略分页参数，永远返回同一页
                return {"data": {"list": [_order("D1", "2026-01-01 12:00", 10.0, [])]}}, ""

        records, meta = fetch_order_records(C(), max_pages=5, page_size=10)
        self.assertEqual(len(records), 1)
        self.assertFalse(meta["coverage_confirmed"])
        self.assertEqual(meta["stop_reason"], "duplicate")
        self.assertTrue(any("未确认" in w for w in meta["warnings"]))

    def test_max_pages_marks_unconfirmed(self):
        schema = {"properties": {"pageNo": {}, "pageSize": {}}}
        pages = [[_order(f"M{idx}-{j}", "2026-02-01 12:00", 10.0, []) for j in range(3)]
                 for idx in range(10)]
        client = FakeSchemaClient(pages, schema)
        records, meta = fetch_order_records(client, max_pages=2, page_size=3)
        self.assertTrue(meta["truncated"])
        self.assertFalse(meta["coverage_confirmed"])
        self.assertEqual(meta["stop_reason"], "max_pages")

    def test_total_hint_mismatch_warns(self):
        schema = {"properties": {"pageNo": {}, "pageSize": {}}}
        pages = [[_order("W1", "2026-01-01 12:00", 10.0, [])]]
        client = FakeSchemaClient(pages, schema, signals={0: {"total": 99, "hasMore": False}})
        records, meta = fetch_order_records(client, max_pages=10, page_size=10)
        self.assertTrue(any("未覆盖" in w for w in meta["warnings"]))


if __name__ == "__main__":
    unittest.main()
