"""MCP 客户端的响应解析与匹配测试（不联网）。"""

from __future__ import annotations

import unittest

from mcd_timemachine.mcp_client import McpClient, McpError


class TestParseMessages(unittest.TestCase):
    def test_plain_json(self):
        raw = b'{"jsonrpc":"2.0","id":1,"result":{}}'
        msgs = McpClient._parse_messages(raw, "application/json")
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["id"], 1)

    def test_sse_multiple_events(self):
        body = (
            'event: message\n'
            'data: {"jsonrpc":"2.0","method":"notifications/progress","params":{}}\n\n'
            'event: message\n'
            'data: {"jsonrpc":"2.0","id":7,"result":{"ok":true}}\n\n'
        ).encode("utf-8")
        msgs = McpClient._parse_messages(body, "text/event-stream")
        self.assertEqual(len(msgs), 2)

    def test_empty_body(self):
        self.assertEqual(McpClient._parse_messages(b"", "application/json"), [])

    def test_invalid_json_raises(self):
        with self.assertRaises(McpError):
            McpClient._parse_messages(b"<html>502</html>", "text/html")


class TestMatchById(unittest.TestCase):
    messages = [
        {"jsonrpc": "2.0", "method": "notifications/progress"},  # 通知，无 id
        {"jsonrpc": "2.0", "id": 7, "result": {"ok": True}},
        {"jsonrpc": "2.0", "id": 8, "result": {"ok": False}},
    ]

    def test_matches_exact_id(self):
        hit = McpClient._match(self.messages, 8)
        self.assertIsNotNone(hit)
        self.assertFalse(hit["result"]["ok"])

    def test_ignores_notifications(self):
        hit = McpClient._match(self.messages, 7)
        self.assertTrue(hit["result"]["ok"])

    def test_missing_id_returns_none(self):
        self.assertIsNone(McpClient._match(self.messages, 99))

    def test_error_response_returned(self):
        msgs = [{"jsonrpc": "2.0", "error": {"code": -32000, "message": "session expired"}}]
        hit = McpClient._match(msgs, 3)
        self.assertIn("error", hit)

    def test_session_error_detected(self):
        self.assertTrue(
            McpClient._is_session_error(
                {"error": {"code": -32000, "message": "Session not found"}}
            )
        )
        self.assertFalse(McpClient._is_session_error({"result": {}}))


class TestTokenHandling(unittest.TestCase):
    def test_missing_token_raises(self):
        import os

        old = os.environ.pop("MCD_MCP_TOKEN", None)
        try:
            with self.assertRaises(McpError):
                McpClient(token=None)
        finally:
            if old:
                os.environ["MCD_MCP_TOKEN"] = old

    def test_env_token_accepted(self):
        import os

        old = os.environ.get("MCD_MCP_TOKEN")
        os.environ["MCD_MCP_TOKEN"] = "env-token"
        try:
            client = McpClient()
            self.assertEqual(client.token, "env-token")
        finally:
            if old is None:
                os.environ.pop("MCD_MCP_TOKEN", None)
            else:
                os.environ["MCD_MCP_TOKEN"] = old


class TestFirstPageArgs(unittest.TestCase):
    def test_builds_from_schema(self):
        target = {"inputSchema": {"properties": {"pageNo": {}, "pageSize": {}}}}
        args = McpClient._first_page_args(target)
        self.assertEqual(args, {"pageNo": 1, "pageSize": 20})

    def test_empty_schema(self):
        self.assertEqual(McpClient._first_page_args(None), {})


if __name__ == "__main__":
    unittest.main()
