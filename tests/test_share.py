"""share 模块的脱敏与字段索引测试。

安全底线：这些用例一旦失败，意味着真实数据可能被写进可公开的 share.json。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mcd_timemachine.share import (
    build_field_index,
    build_share,
    redact,
    scan_pii,
    suggest_mappings,
    write_share,
)


FAKE_ORDER = {
    "orderNo": "MCD20260305123456",
    "payTime": "2026-03-05 12:30:00",
    "totalAmount": 38.5,
    "storeName": "南京西路餐厅",
    "itemCount": 2,
    "items": [
        {"goodsName": "巨无霸", "price": 18.0, "qty": 1},
        {"goodsName": "薯条", "price": 12.5, "qty": 2},
    ],
}


class RedactTest(unittest.TestCase):
    def test_string_content_is_removed(self):
        out = redact(FAKE_ORDER)
        blob = json.dumps(out, ensure_ascii=False)
        for secret in ("MCD20260305123456", "南京西路餐厅", "巨无霸", "薯条"):
            self.assertNotIn(secret, blob)

    def test_no_original_character_survives(self):
        """严格脱敏：不保留标点、分隔符等任何原始字符形态。"""
        out = redact({"v": "2026-03-05 12:30:00", "w": "MCD-1234_ABC",
                      "x": "a@b.com", "y": "13812345678"})
        blob = json.dumps(out, ensure_ascii=False)
        for ch in ("-", "_", "@", "/", "."):
            self.assertNotIn(ch, blob, f"残留了原始字符 {ch!r}")
        for secret in ("2026", "MCD", "1234", "ABC", "b.com", "13812345678"):
            self.assertNotIn(secret, blob)

    def test_field_names_are_kept(self):
        out = redact(FAKE_ORDER)
        for key in ("orderNo", "payTime", "totalAmount", "storeName", "items",
                    "goodsName", "price", "qty"):
            self.assertIn(key, json.dumps(out, ensure_ascii=False))

    def test_shape_preserves_type_only(self):
        self.assertEqual(redact("MCD2026"), "<str:7:alnum>")
        self.assertEqual(redact("南京西路"), "<str:4:cjk>")
        self.assertEqual(redact("2026-03-05 12:30:00"), "<str:19:datetime>")
        self.assertEqual(redact("2026-03-05"), "<str:10:date>")
        self.assertIn("num", redact(38.5))
        self.assertIn("1dp", redact(38.5))
        self.assertIn("int", redact(2))

    def test_sensitive_string_kinds_are_labeled(self):
        self.assertEqual(redact("13812345678"), "<str:11:phone>")
        self.assertEqual(redact("a@b.com"), "<str:7:email>")
        self.assertEqual(redact("https://mcp.mcd.cn/x"), "<str:20:url>")
        self.assertEqual(redact(""), "<str:0:blank>")

    def test_list_is_truncated_with_count(self):
        out = redact({"items": [{"a": 1}] * 10})
        self.assertIn("共10项", json.dumps(out, ensure_ascii=False))

    def test_depth_limit(self):
        deep: dict = {}
        node = deep
        for _ in range(30):
            node["n"] = {}
            node = node["n"]
        out = json.dumps(redact(deep), ensure_ascii=False)
        self.assertIn("depth_limit", out)

    def test_bool_and_none_preserved(self):
        self.assertIs(redact(True), True)
        self.assertIs(redact(None), None)


class FieldIndexTest(unittest.TestCase):
    def test_index_collects_paths_and_types(self):
        index = build_field_index(FAKE_ORDER)
        self.assertIn("orderNo", index)
        self.assertIn("items[].goodsName", index)
        self.assertEqual(index["totalAmount"]["types"], {"float"})
        self.assertFalse(index["items"]["leaf"])

    def test_nested_arrays_do_not_explode(self):
        index = build_field_index({"a": [{"b": [{"c": 1}] * 50}] * 50})
        self.assertLess(len(index), 20)


class SuggestMappingTest(unittest.TestCase):
    def test_guesses_core_fields(self):
        hints = suggest_mappings(build_field_index(FAKE_ORDER))
        self.assertIn("orderNo", hints["order_id"])
        self.assertIn("payTime", hints["order_time"])
        self.assertIn("totalAmount", hints["order_amount"])
        self.assertIn("storeName", hints["store"])
        self.assertIn("items", hints["items_container"])
        self.assertIn("items[].goodsName", hints["item_name"])
        self.assertIn("items[].qty", hints["item_qty"])

    def test_snake_and_camel_case_both_work(self):
        data = {"order_id": "x1", "create_time": "2026-01-01", "pay_amount": 1.0}
        hints = suggest_mappings(build_field_index(data))
        self.assertIn("order_id", hints["order_id"])
        self.assertIn("create_time", hints["order_time"])
        self.assertIn("pay_amount", hints["order_amount"])

    def test_order_id_is_not_mistaken_for_time(self):
        hints = suggest_mappings(build_field_index(FAKE_ORDER))
        self.assertNotIn("orderNo", hints["order_time"])

    def test_store_name_is_not_mistaken_for_item_name(self):
        hints = suggest_mappings(build_field_index(FAKE_ORDER))
        self.assertNotIn("storeName", hints["item_name"])


class PiiScanTest(unittest.TestCase):
    def test_phone_removed(self):
        text, hits = scan_pii("联系 13812345678 处理")
        self.assertNotIn("13812345678", text)
        self.assertTrue(any("phone" in h for h in hits))

    def test_email_and_url_removed(self):
        text, _ = scan_pii('mail a@b.com url https://x.y/z?t=1 "end"')
        self.assertNotIn("a@b.com", text)
        self.assertNotIn("https://x.y/z?t=1", text)

    def test_idcard_and_card_removed(self):
        text, _ = scan_pii("id 310101199003071234 bank 6222021234567890123")
        self.assertNotIn("310101199003071234", text)
        self.assertNotIn("6222021234567890123", text)

    def test_token_like_removed(self):
        text, _ = scan_pii("Bearer abcdefghijklmnopqrstuvwxyz012345")
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz012345", text)

    def test_long_digits_removed(self):
        text, _ = scan_pii("流水 20260305123456")
        self.assertNotIn("20260305123456", text)


class BuildShareTest(unittest.TestCase):
    def test_writes_redacted_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "raw").mkdir()
            (out / "raw" / "orders.json").write_text(
                json.dumps({"list": [FAKE_ORDER]}, ensure_ascii=False),
                encoding="utf-8",
            )
            (out / "doctor.json").write_text(
                json.dumps({
                    "endpoint": "https://mcp.mcd.cn",
                    "tools_count": 29,
                    "target_tool": "order-list",
                    "target_found": True,
                    "call_ok": True,
                    "sample": json.dumps({"list": [FAKE_ORDER]}, ensure_ascii=False),
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            target = write_share(out)
            self.assertTrue(target.exists())
            text = target.read_text(encoding="utf-8")
            for secret in ("MCD20260305123456", "南京西路餐厅", "巨无霸"):
                self.assertNotIn(secret, text)
            payload = json.loads(text)
            self.assertEqual(payload["sections"]["doctor"]["tools_count"], 29)
            self.assertIn("suggested_mappings", payload["sections"]["doctor"])
            self.assertTrue(payload["pii_scan"]["performed"])

    def test_missing_files_is_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = build_share(Path(tmp))
            self.assertEqual(payload["sections"], {})

    def test_token_in_error_is_scrubbed(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "doctor.json").write_text(json.dumps({
                "call_ok": False,
                "call_error": "401 Bearer abcdefghijklmnopqrstuvwxyz012345 手机 13812345678",
            }), encoding="utf-8")
            payload = build_share(out)
            err = payload["sections"]["doctor"]["call_error"]
            self.assertNotIn("abcdefghijklmnopqrstuvwxyz012345", err)
            self.assertNotIn("13812345678", err)

    def test_real_world_leak_scenarios(self):
        """模拟真实返回里夹带的敏感值，确认一个都活不下来。"""
        dirty = {
            "phone": "13812345678",
            "email": "zhangsan@example.com",
            "idCard": "310101199003071234",
            "address": "上海市静安区南京西路1266号",
            "payUrl": "https://pay.mcd.cn/order?no=MCD20260305123456",
            "remark": "不要放香菜",
        }
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "doctor.json").write_text(json.dumps({
                "call_ok": True,
                "sample": json.dumps({"data": dirty}, ensure_ascii=False),
            }), encoding="utf-8")
            text = write_share(out).read_text(encoding="utf-8")
        for secret in ("13812345678", "zhangsan@example.com",
                       "310101199003071234", "南京西路1266号",
                       "MCD20260305123456", "不要放香菜"):
            self.assertNotIn(secret, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
