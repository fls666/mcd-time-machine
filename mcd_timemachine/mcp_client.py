"""MCP Streamable HTTP 客户端（零第三方依赖，仅用标准库）。

麦当劳中国 MCP Server 接入信息：
    地址    https://mcp.mcd.cn
    协议    Streamable HTTP (JSON-RPC 2.0)
    鉴权    Authorization: Bearer <MCP Token>
    限流    600 次 / 分钟（超限返回 429）

一次完整会话流程：
    1) POST initialize                       -> 响应头带回 mcp-session-id
    2) POST notifications/initialized        -> 通知，无响应体
    3) POST tools/list                       -> 发现可用工具与入参 schema
    4) POST tools/call                       -> 调用具体工具
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_ENDPOINT = "https://mcp.mcd.cn"
PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "mcd-time-machine", "version": "1.0.0"}

# 本工具真实用到的麦当劳 MCP Tools
TOOLS = {
    "orders": "order-list",
    "order_detail": "query-order",
    "mall_orders": "mall-order-list",
    "meals": "query-meals",
    "nutrition": "list-nutrition-foods",
    "coupons": "query-my-coupons",
    "points": "query-my-account",
    "campaign": "campaign-calendar",
    "stores": "query-nearby-stores",
}


class McpError(RuntimeError):
    """MCP 调用过程中的可预期错误。"""


class McpClient:
    """极简 Streamable HTTP MCP 客户端。"""

    def __init__(
        self,
        token: Optional[str] = None,
        endpoint: str = DEFAULT_ENDPOINT,
        timeout: float = 30.0,
        retries: int = 3,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.token = token or os.environ.get("MCD_MCP_TOKEN", "").strip()
        if not self.token:
            raise McpError(
                "未找到 MCP Token。请在 https://open.mcd.cn/mcp 申请后，"
                "设置环境变量 MCD_MCP_TOKEN，或使用 --token 传入。"
            )
        self.timeout = timeout
        self.retries = retries
        self.session_id: Optional[str] = None
        self._seq = 0

    # ------------------------------------------------------------------ 底层

    def _next_id(self) -> int:
        self._seq += 1
        return self._seq

    def _post(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.token}",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self.session_id:
            headers["mcp-session-id"] = self.session_id

        last_err: Optional[Exception] = None
        for attempt in range(self.retries):
            req = urllib.request.Request(
                self.endpoint, data=body, headers=headers, method="POST"
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    sid = resp.headers.get("mcp-session-id")
                    if sid:
                        self.session_id = sid
                    raw = resp.read()
                    ctype = resp.headers.get("Content-Type", "")
                    return self._parse(raw, ctype)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:400]
                if exc.code == 401:
                    raise McpError(
                        "401 未授权：MCP Token 无效或已过期，请到控制台重新激活。"
                    ) from exc
                if exc.code == 429 and attempt < self.retries - 1:
                    time.sleep(2 ** attempt)  # 触发限流，退避重试
                    continue
                raise McpError(f"HTTP {exc.code}: {detail}") from exc
            except urllib.error.URLError as exc:
                last_err = exc
                if attempt < self.retries - 1:
                    time.sleep(2 ** attempt)
                    continue
        raise McpError(f"网络请求失败：{last_err}")

    @staticmethod
    def _parse(raw: bytes, ctype: str) -> Optional[Dict[str, Any]]:
        """同时兼容 JSON 响应与 SSE（text/event-stream）响应。"""
        text = raw.decode("utf-8", "replace").strip()
        if not text:
            return None
        if "text/event-stream" in ctype or text.startswith(("event:", "data:")):
            result: Optional[Dict[str, Any]] = None
            for line in text.splitlines():
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if not chunk:
                    continue
                try:
                    msg = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                if isinstance(msg, dict) and ("id" in msg or "method" in msg):
                    result = msg
            return result
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise McpError(f"响应不是合法 JSON：{text[:300]}") from exc

    # ------------------------------------------------------------------ 会话

    def initialize(self) -> Dict[str, Any]:
        resp = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": CLIENT_INFO,
                },
            }
        )
        if not resp or "error" in resp:
            raise McpError(f"initialize 失败：{resp}")
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return resp.get("result", {})

    def list_tools(self) -> List[Dict[str, Any]]:
        resp = self._post(
            {"jsonrpc": "2.0", "id": self._next_id(), "method": "tools/list", "params": {}}
        )
        if not resp or "error" in resp:
            raise McpError(f"tools/list 失败：{resp}")
        return resp.get("result", {}).get("tools", [])

    # ------------------------------------------------------------------ 调用

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None):
        """调用工具，返回 (结构化数据, 原始文本)。"""
        resp = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            }
        )
        if not resp:
            raise McpError(f"{name} 无响应内容")
        if "error" in resp:
            raise McpError(f"{name} 调用出错：{resp['error']}")

        result = resp.get("result", {})
        text_parts: List[str] = []
        for block in result.get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(block.get("text", ""))
        raw_text = "\n".join(text_parts).strip()
        if result.get("isError"):
            raise McpError(f"{name} 返回业务错误：{raw_text[:300]}")

        structured = result.get("structuredContent")
        data = structured if structured not in (None, {}) else _maybe_json(raw_text)
        return data, raw_text

    def call_tool_any(self, name: str, candidates: List[Dict[str, Any]]):
        """按候选入参依次尝试，适配不同工具的参数命名差异。"""
        errors: List[str] = []
        for args in candidates:
            try:
                data, raw = self.call_tool(name, args)
                return data, raw, args
            except McpError as exc:
                errors.append(f"{args} -> {exc}")
        raise McpError(f"{name} 所有候选入参均失败：\n  " + "\n  ".join(errors))


def _maybe_json(text: str):
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


__all__ = ["McpClient", "McpError", "TOOLS", "DEFAULT_ENDPOINT"]
