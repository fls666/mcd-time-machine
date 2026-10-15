"""MCP Streamable HTTP 客户端（零第三方依赖，仅用标准库）。

麦当劳中国 MCP Server 接入信息：
    地址    https://mcp.mcd.cn
    协议    Streamable HTTP (JSON-RPC 2.0)
    鉴权    Authorization: Bearer <MCP Token>
    限流    600 次 / 分钟（超限返回 429）

一次完整会话流程：
    1) POST initialize                       -> 响应头带回 mcp-session-id
    2) POST notifications/initialized        -> 通知，通常无响应体
    3) POST tools/list                       -> 发现可用工具与入参 schema
    4) POST tools/call                       -> 调用具体工具

针对静态审查「SSE 响应解析不够健壮」的修复：
    - SSE 流按 `event:` / `data:` 边界解析成多条消息，**严格按 JSON-RPC id 匹配**，
      不再「取最后一条带 id 的消息」；通知类消息（无 id）被正确忽略。
    - 会话失效（404 / session 相关错误码）时自动重建会话并重试一次。
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

SESSION_ERROR_HINTS = ("session", "会话", "expired", "invalid session", "not found")


class McpError(RuntimeError):
    """MCP 调用过程中的可预期错误。"""


class McpClient:
    """极简但健壮的 Streamable HTTP MCP 客户端。"""

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
                "设置环境变量 MCD_MCP_TOKEN（不建议用 --token，命令参数会留在 shell 历史里）。"
            )
        self.timeout = timeout
        self.retries = retries
        self.session_id: Optional[str] = None
        self._seq = 0
        self._initialized = False

    # ------------------------------------------------------------------ 底层

    def _next_id(self) -> int:
        self._seq += 1
        return self._seq

    @staticmethod
    def _parse_messages(raw: bytes, ctype: str) -> List[Dict[str, Any]]:
        """把响应体解析成 JSON-RPC 消息列表；同时支持 JSON 与 SSE。"""
        text = raw.decode("utf-8", "replace").strip()
        if not text:
            return []
        if "text/event-stream" in ctype or text.startswith(("event:", "data:")):
            messages: List[Dict[str, Any]] = []
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
                if isinstance(msg, dict):
                    messages.append(msg)
            return messages
        try:
            msg = json.loads(text)
        except json.JSONDecodeError as exc:
            raise McpError(f"响应不是合法 JSON：{text[:300]}") from exc
        return [msg] if isinstance(msg, dict) else []

    def _post(
        self,
        payload: Dict[str, Any],
        expect_id: Optional[int] = None,
        allow_reinit: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """发送一次请求；expect_id 非空时严格按 id 匹配响应。"""
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
                    messages = self._parse_messages(
                        resp.read(), resp.headers.get("Content-Type", "")
                    )
                    return self._match(messages, expect_id)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:400]
                if exc.code == 401:
                    raise McpError(
                        "401 未授权：MCP Token 无效或已过期，请到控制台重新激活。"
                    ) from exc
                if exc.code == 429 and attempt < self.retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                # 会话失效：清掉 session 重建一次
                if exc.code in (400, 404) and allow_reinit and self.session_id:
                    self.session_id = None
                    self._initialized = False
                    self.initialize()
                    headers["mcp-session-id"] = self.session_id or ""
                    return self._post(payload, expect_id=expect_id, allow_reinit=False)
                raise McpError(f"HTTP {exc.code}: {detail}") from exc
            except urllib.error.URLError as exc:
                last_err = exc
                if attempt < self.retries - 1:
                    time.sleep(2 ** attempt)
                    continue
        raise McpError(f"网络请求失败：{last_err}")

    @staticmethod
    def _match(
        messages: List[Dict[str, Any]], expect_id: Optional[int]
    ) -> Optional[Dict[str, Any]]:
        """按 JSON-RPC id 精确匹配；找不到就返回错误响应或 None。"""
        if not messages:
            return None
        if expect_id is not None:
            for msg in messages:
                if msg.get("id") == expect_id:
                    return msg
            for msg in messages:
                if "error" in msg:
                    return msg
            return None
        for msg in reversed(messages):
            if "id" in msg or "error" in msg:
                return msg
        return None

    @staticmethod
    def _is_session_error(msg: Optional[Dict[str, Any]]) -> bool:
        if not msg or "error" not in msg:
            return False
        err = msg["error"]
        blob = json.dumps(err, ensure_ascii=False).lower()
        return any(hint in blob for hint in SESSION_ERROR_HINTS) or err.get("code") == -32000

    # ------------------------------------------------------------------ 会话

    def initialize(self) -> Dict[str, Any]:
        req_id = self._next_id()
        resp = self._post(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": CLIENT_INFO,
                },
            },
            expect_id=req_id,
        )
        if not resp or "error" in resp:
            raise McpError(f"initialize 失败：{resp}")
        self._post(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}, expect_id=None
        )
        self._initialized = True
        return resp.get("result", {})

    def list_tools(self) -> List[Dict[str, Any]]:
        req_id = self._next_id()
        resp = self._post(
            {"jsonrpc": "2.0", "id": req_id, "method": "tools/list", "params": {}},
            expect_id=req_id,
        )
        if not resp or "error" in resp:
            raise McpError(f"tools/list 失败：{resp}")
        return resp.get("result", {}).get("tools", [])

    # ------------------------------------------------------------------ 调用

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None):
        """调用工具，返回 (结构化数据, 原始文本)。"""
        req_id = self._next_id()
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }
        resp = self._post(payload, expect_id=req_id)

        if resp is None:
            # 没有匹配到响应：可能是会话过期，重建会话后重试一次
            self.session_id = None
            self._initialized = False
            self.initialize()
            resp = self._post(payload, expect_id=req_id, allow_reinit=False)
        if resp is None:
            raise McpError(f"{name} 未收到可匹配的响应")
        if "error" in resp:
            if self._is_session_error(resp):
                self.session_id = None
                self._initialized = False
                self.initialize()
                resp = self._post(payload, expect_id=req_id, allow_reinit=False)
                if resp and "error" not in resp:
                    return self._unwrap(name, resp)
            raise McpError(f"{name} 调用出错：{resp['error']}")
        return self._unwrap(name, resp)

    def _unwrap(self, name: str, resp: Dict[str, Any]):
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

    # ------------------------------------------------------------------ 诊断

    def diagnose(self, tool: str = "order-list", arguments: Optional[Dict[str, Any]] = None):
        """连通性诊断：返回会话、工具列表与目标工具的原始返回摘要。"""
        report: Dict[str, Any] = {"endpoint": self.endpoint, "ok": False}
        info = self.initialize()
        report["server"] = info.get("serverInfo", {})
        report["protocol_version"] = info.get("protocolVersion")
        tools = self.list_tools()
        report["tools_count"] = len(tools)
        report["tools"] = [t.get("name") for t in tools]

        target = next((t for t in tools if t.get("name") == tool), None)
        report["target_tool"] = tool
        report["target_found"] = target is not None
        report["input_schema"] = (target or {}).get("inputSchema", {})

        args = arguments if arguments is not None else self._first_page_args(target)
        try:
            data, raw = self.call_tool(tool, args)
            report["call_ok"] = True
            report["call_args"] = args
            report["response_type"] = type(data).__name__
            report["sample"] = _summarize(data if data is not None else raw)
        except McpError as exc:
            report["call_ok"] = False
            report["call_error"] = str(exc)
        report["ok"] = bool(report.get("call_ok"))
        return report

    @staticmethod
    def _first_page_args(target: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """参照工具 schema 生成一组合法入参；无 schema 时用最保守的空对象。"""
        props = ((target or {}).get("inputSchema", {}) or {}).get("properties", {}) or {}
        args: Dict[str, Any] = {}
        for key in ("pageNo", "pageNum", "page", "pageIndex"):
            if key in props:
                args[key] = 1
                break
        for key in ("pageSize", "size", "limit"):
            if key in props:
                args[key] = 20
                break
        return args


def _summarize(payload: Any, limit: int = 1200) -> str:
    try:
        text = json.dumps(payload, ensure_ascii=False)
    except TypeError:
        text = str(payload)
    return text[:limit]


def _maybe_json(text: str):
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


__all__ = ["McpClient", "McpError", "TOOLS", "DEFAULT_ENDPOINT"]
