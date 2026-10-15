"""数据采集与 Schema 容错归一化。

麦当劳 MCP 各 Tool 的返回体字段命名不统一，且会随版本演进。
这里不硬编码单一结构，而是用「候选字段名 + 递归搜索」的方式做兼容解析：
    - 先在返回体里递归找出「像订单」的字典列表
    - 再对每个订单递归抽取时间 / 金额 / 门店 / 商品明细
解析不出来时不报错，而是把原始 JSON 落盘，方便 `mcdtm inspect` 人工核对。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .mcp_client import McpClient, TOOLS

# ---------------------------------------------------------------- 字段候选表

K_ORDER_TIME = (
    "orderTime", "createTime", "createdAt", "payTime", "paidTime",
    "orderDate", "completeTime", "finishTime", "submitTime", "time", "date",
)
K_STORE = ("storeName", "shopName", "store", "shop", "restaurantName", "storeAddr")
K_TOTAL = (
    "payAmount", "paidAmount", "actualAmount", "realAmount", "orderAmount",
    "totalAmount", "totalPrice", "amount", "payPrice",
)
K_DISCOUNT = ("discountAmount", "couponAmount", "promotionAmount", "discount", "couponPrice")
K_ITEM_NAME = (
    "productName", "mealName", "goodsName", "itemName", "name", "title",
    "skuName", "dishName",
)
K_ITEM_QTY = ("quantity", "qty", "num", "count", "buyNum", "number")
K_ITEM_PRICE = (
    "price", "unitPrice", "salePrice", "sellPrice", "amount",
    "totalPrice", "subtotal", "payAmount",
)
K_CHANNEL = ("orderType", "channel", "diningType", "eatType", "businessType", "scene")

# order-list 的分页参数命名不确定，逐个尝试
PAGE_CANDIDATES: List[Dict[str, Any]] = [
    {"pageNo": 1, "pageSize": 100},
    {"pageNum": 1, "pageSize": 100},
    {"page": 1, "size": 100},
    {"page": 1, "pageSize": 100, "limit": 100},
    {"limit": 100},
    {"pageSize": 100},
    {},
]


# ---------------------------------------------------------------- 通用工具

def _walk(node: Any):
    """深度遍历 dict / list，产出所有 dict 节点。"""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def _find_key(node: Any, candidates: Iterable[str]) -> Optional[Any]:
    """在节点自身及其子孙中，按候选键名查找第一个非空值。"""
    lowered = {c.lower() for c in candidates}
    for obj in _walk(node):
        for key, value in obj.items():
            if key.lower() in lowered and value not in (None, "", [], {}):
                return value
    return None


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("¥", "").replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def _to_int(value: Any, default: int = 1) -> int:
    number = _to_float(value)
    return int(number) if number and number > 0 else default


_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M", "%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日",
    "%Y%m%d%H%M%S", "%Y%m%d",
)


def _parse_time(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        stamp = float(value)
        if stamp > 1e11:  # 毫秒
            stamp /= 1000.0
        if 1e9 < stamp < 4e9:
            return datetime.fromtimestamp(stamp)
        return None
    text = str(value).strip().replace("T", " ")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------- 订单解析

_ORDER_HINTS = K_ORDER_TIME + K_TOTAL + ("orderId", "orderNo", "orderCode", "orderSn")


def _looks_like_order(node: Dict[str, Any]) -> bool:
    keys = {k.lower() for k in node.keys()}
    return len(keys & {h.lower() for h in _ORDER_HINTS}) >= 2


def _extract_items(order: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for obj in _walk(order):
        for key, value in obj.items():
            if key.lower() not in {"items", "products", "goods", "goodsList",
                                   "productList", "mealList", "detailList", "lines"}:
                continue
            if not isinstance(value, list):
                continue
            for entry in value:
                if not isinstance(entry, dict):
                    continue
                name = _find_key(entry, K_ITEM_NAME)
                if not name:
                    continue
                items.append(
                    {
                        "name": str(name).strip(),
                        "qty": _to_int(_find_key(entry, K_ITEM_QTY)),
                        "price": _to_float(_find_key(entry, K_ITEM_PRICE)),
                        "category": _guess_category(str(name)),
                    }
                )
            if items:
                return items
    return items


def _guess_category(name: str) -> str:
    rules = [
        ("汉堡", ["汉堡", "堡", "麦香", "巨无霸", "麦辣鸡腿", "双层", "牛堡"]),
        ("小食", ["薯条", "薯饼", "薯格", "鸡块", "鸡翅", "鸡腿", "麦乐鸡",
                 "洋葱圈", "鸡排", "脆汁鸡", "派"]),
        ("甜品", ["甜筒", "新地", "圣代", "麦旋风", "雪冰", "蛋糕", "圆筒", "奶昔"]),
        ("饮料", ["可乐", "雪碧", "芬达", "咖啡", "拿铁", "美式", "茶", "果汁", "牛奶", "酷"]),
        ("早餐", ["早餐", "粥", "油条", "豆浆", "焙果", "松饼", "蛋", "麦满分"]),
        ("沙拉", ["沙拉", "蔬菜", "玉米"]),
    ]
    for category, words in rules:
        if any(word in name for word in words):
            return category
    return "其他"


def normalize_orders(payload: Any) -> List[Dict[str, Any]]:
    """从任意 MCP 返回体中尽力抽取订单列表。"""
    if payload is None:
        return []
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return []

    candidates: List[Dict[str, Any]] = []
    seen: set = set()
    for obj in _walk(payload):
        if _looks_like_order(obj):
            marker = json.dumps(obj, sort_keys=True, ensure_ascii=False)[:200]
            if marker in seen:
                continue
            seen.add(marker)
            candidates.append(obj)

    orders: List[Dict[str, Any]] = []
    for raw in candidates:
        moment = _parse_time(_find_key(raw, K_ORDER_TIME))
        items = _extract_items(raw)
        total = _to_float(_find_key(raw, K_TOTAL))
        if total is None and items:
            total = sum((it["price"] or 0) * it["qty"] for it in items) or None
        orders.append(
            {
                "order_id": str(_find_key(raw, ("orderId", "orderNo", "orderCode", "orderSn", "id")) or ""),
                "time": moment.strftime("%Y-%m-%d %H:%M") if moment else "",
                "date": moment.strftime("%Y-%m-%d") if moment else "",
                "hour": moment.hour if moment else None,
                "weekday": moment.weekday() if moment else None,
                "store": str(_find_key(raw, K_STORE) or ""),
                "channel": str(_find_key(raw, K_CHANNEL) or ""),
                "total": total,
                "discount": _to_float(_find_key(raw, K_DISCOUNT)),
                "items": items,
            }
        )

    orders.sort(key=lambda o: o["time"], reverse=True)
    return orders


# ---------------------------------------------------------------- 采集流程

def collect(client: McpClient, out_dir: Path, with_optional: bool = True) -> Dict[str, Any]:
    """拉取时光机所需的全部数据，原始响应全部落盘到 out_dir/raw/。"""
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    bundle: Dict[str, Any] = {}

    def _save(key: str, tool: str, args: Dict[str, Any], data: Any, text: str) -> None:
        bundle[key] = {"tool": tool, "args": args, "data": data, "text": text}
        (raw_dir / f"{key}.json").write_text(
            json.dumps({"tool": tool, "args": args, "data": data}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # 1. 历史订单（核心）
    data, text, args = client.call_tool_any(TOOLS["orders"], PAGE_CANDIDATES)
    _save("orders", TOOLS["orders"], args, data, text)

    # 2. 可选增强数据，失败不影响主流程
    optional = {
        "meals": (TOOLS["meals"], [{}, {"storeCode": None}]),
        "nutrition": (TOOLS["nutrition"], [{}]),
        "coupons": (TOOLS["coupons"], [{}]),
        "points": (TOOLS["points"], [{}]),
        "campaign": (TOOLS["campaign"], [{}]),
        "mall_orders": (TOOLS["mall_orders"], [{}, {"pageNo": 1, "pageSize": 50}]),
    }
    if with_optional:
        for key, (tool, cands) in optional.items():
            try:
                d, t, a = client.call_tool_any(tool, cands)
                _save(key, tool, a, d, t)
            except Exception as exc:  # noqa: BLE001 - 增强数据允许失败
                bundle[key] = {"tool": tool, "args": None, "data": None, "text": f"跳过：{exc}"}

    orders = normalize_orders(bundle["orders"]["data"] or bundle["orders"]["text"])
    (out_dir / "orders.normalized.json").write_text(
        json.dumps(orders, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    bundle["normalized_orders"] = orders
    return bundle


def load_normalized(out_dir: Path) -> List[Dict[str, Any]]:
    path = out_dir / "orders.normalized.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} 不存在。请先执行 `mcdtm fetch`，或用 `mcdtm demo` 生成示例数据。"
        )
    return json.loads(path.read_text(encoding="utf-8"))


__all__ = ["collect", "normalize_orders", "load_normalized", "_guess_category"]
