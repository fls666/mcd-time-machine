"""数据采集与 Schema 容错归一化。

设计原则（针对静态审查发现的三个 P0 问题重写）：

1. **真分页**：`order-list` 可能因账号订单量大而分页返回。这里先探测服务端接受的
   分页参数命名，再循环翻页直到「本页条数 < 每页条数」或「本页全是重复订单」，
   并统计实际翻了多少页。
2. **可靠去重**：以完整订单号作为唯一键；无订单号时退化为整条记录的完整哈希，
   不再只取 JSON 前 200 字符。
3. **防字段误判**：抽取整单金额 / 时间时只看订单自身与浅层结构，显式跳过商品明细
   列表，避免把某个单品的价格或时间当成整单数据。
4. **覆盖率可验证**：每次采集都产出 provenance，如实记录拉取页数、原始条数、
   去重后条数、有日期 / 金额 / 明细的条数，以及失败的增强工具。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .mcp_client import McpClient, McpError, TOOLS

# ---------------------------------------------------------------- 字段候选表

ID_KEYS = (
    "orderId", "orderNo", "orderCode", "orderSn", "order_id", "order_no",
    "tradeNo", "orderNumber", "id",
)
K_ORDER_TIME = (
    "orderTime", "createTime", "createdAt", "payTime", "paidTime",
    "orderDate", "completeTime", "finishTime", "submitTime", "time", "date",
)
K_STORE = ("storeName", "shopName", "store", "shop", "restaurantName", "storeAddr")
K_TOTAL = (
    "payAmount", "paidAmount", "actualAmount", "realAmount", "orderAmount",
    "totalAmount", "totalPrice", "amount", "payPrice", "shouldPay",
)
K_DISCOUNT = ("discountAmount", "couponAmount", "promotionAmount", "discount", "couponPrice")
K_CHANNEL = ("orderType", "channel", "diningType", "eatType", "businessType", "scene")

K_ITEM_NAME = (
    "productName", "mealName", "goodsName", "itemName", "name", "title",
    "skuName", "dishName",
)
K_ITEM_QTY = ("quantity", "qty", "num", "count", "buyNum", "number")
K_ITEM_PRICE = (
    "price", "unitPrice", "salePrice", "sellPrice", "amount",
    "totalPrice", "subtotal", "payAmount",
)

# 明细列表的容器键：抽取整单字段时必须跳过它们
ITEMS_LIST_KEYS = {
    "items", "itemlist", "products", "productlist", "goods", "goodslist",
    "meals", "meallist", "detaillist", "details", "lines", "skus",
    "orderitems", "orderitemlist", "suborders", "packages",
}

# 分页参数命名的候选组合（按经验概率排序，逐个试探）
PAGE_SHAPES: List[Tuple[str, str]] = [
    ("pageNo", "pageSize"),
    ("pageNum", "pageSize"),
    ("page", "size"),
    ("page", "pageSize"),
    ("page", "limit"),
    ("pageIndex", "pageSize"),
    ("offset", "limit"),
]

# 服务端可能给出的分页终止信号
MORE_KEYS = ("hasMore", "hasNext", "hasNextPage", "isEnd", "isLast", "last")


# ---------------------------------------------------------------- 通用工具

def _walk(node: Any):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def _pick(node: Any, candidates: Iterable[str], depth: int = 1) -> Optional[Any]:
    """按候选键名取值：优先节点自身，其次浅层子结构；**跳过商品明细列表**。

    这是修复「嵌套单品字段污染整单字段」的关键：整单金额 / 时间只看订单本体，
    不会因为某个单品带了 amount / time 就被误当成整单数据。
    """
    if not isinstance(node, dict):
        return None
    lowered = {c.lower() for c in candidates}
    for key, value in node.items():
        if key.lower() in lowered and value not in (None, "", [], {}):
            return value
    if depth <= 0:
        return None
    for key, value in node.items():
        if key.lower() in ITEMS_LIST_KEYS:
            continue
        if isinstance(value, dict):
            found = _pick(value, candidates, depth - 1)
            if found is not None:
                return found
    return None


def _to_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("¥", "").replace(",", "").replace("元", "").strip()
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
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        stamp = float(value)
        if stamp > 1e11:
            stamp /= 1000.0
        if 1e9 < stamp < 4e9:
            try:
                return datetime.fromtimestamp(stamp)
            except (OSError, OverflowError, ValueError):
                return None
        return None
    text = str(value).strip().replace("T", " ")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _guess_category(name: str) -> str:
    rules = [
        ("汉堡", ["汉堡", "堡", "麦香", "巨无霸", "麦辣鸡腿", "双层", "牛堡", "麦满分"]),
        ("小食", ["薯条", "薯饼", "薯格", "鸡块", "鸡翅", "鸡腿", "麦乐鸡",
                 "洋葱圈", "鸡排", "脆汁鸡", "派"]),
        ("甜品", ["甜筒", "新地", "圣代", "麦旋风", "雪冰", "蛋糕", "圆筒", "奶昔"]),
        ("饮料", ["可乐", "雪碧", "芬达", "咖啡", "拿铁", "美式", "茶", "果汁", "牛奶", "酷"]),
        ("早餐", ["早餐", "粥", "油条", "豆浆", "焙果", "松饼", "蛋"]),
        ("沙拉", ["沙拉", "蔬菜", "玉米"]),
    ]
    for category, words in rules:
        if any(word in name for word in words):
            return category
    return "其他"


# ---------------------------------------------------------------- 订单识别

def _looks_like_order(node: Dict[str, Any]) -> bool:
    """判断一个 dict 是否像「订单」而不是「单品」。"""
    keys = {k.lower() for k in node.keys()}
    is_item = bool(keys & {k.lower() for k in K_ITEM_NAME})
    has_id = bool(keys & {k.lower() for k in ID_KEYS})
    has_time = bool(keys & {k.lower() for k in K_ORDER_TIME})
    has_money = bool(keys & {k.lower() for k in K_TOTAL})
    has_items = bool(keys & ITEMS_LIST_KEYS)

    if is_item and not (has_id or has_time):
        return False
    if has_id:
        return True
    if has_time and (has_money or has_items):
        return True
    if has_items and has_money:
        return True
    return False


def _extract_items(order: Dict[str, Any]) -> List[Dict[str, Any]]:
    """从订单的明细列表中抽取商品；只在明确的容器键里找。"""
    items: List[Dict[str, Any]] = []
    for key, value in order.items():
        if key.lower() not in ITEMS_LIST_KEYS or not isinstance(value, list):
            continue
        for entry in value:
            if not isinstance(entry, dict):
                continue
            name = _pick(entry, K_ITEM_NAME, depth=1)
            if not name:
                continue
            items.append(
                {
                    "name": str(name).strip(),
                    "qty": _to_int(_pick(entry, K_ITEM_QTY, depth=1)),
                    "price": _to_float(_pick(entry, K_ITEM_PRICE, depth=1)),
                    "category": _guess_category(str(name)),
                }
            )
        if items:
            return items

    # 兜底：少数结构把明细平铺在同层，此时才允许递归搜索
    for obj in _walk(order):
        if obj is order:
            continue
        name = _pick(obj, K_ITEM_NAME, depth=0)
        if not name:
            continue
        keys = {k.lower() for k in obj.keys()}
        if keys & {k.lower() for k in K_ITEM_QTY} or keys & ITEMS_LIST_KEYS:
            items.append(
                {
                    "name": str(name).strip(),
                    "qty": _to_int(_pick(obj, K_ITEM_QTY, depth=0)),
                    "price": _to_float(_pick(obj, K_ITEM_PRICE, depth=0)),
                    "category": _guess_category(str(name)),
                }
            )
    return items


def _record_key(raw: Dict[str, Any]) -> str:
    """订单唯一键：优先完整订单号，否则用整条记录的完整哈希。"""
    order_id = _pick(raw, ID_KEYS, depth=0)
    if order_id not in (None, "", [], {}):
        return f"id:{order_id}"
    digest = hashlib.sha256(
        json.dumps(raw, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return f"hash:{digest}"


def extract_records(payload: Any) -> List[Tuple[str, Dict[str, Any]]]:
    """从任意返回体中抽取订单记录，返回 [(唯一键, 原始订单 dict)]。"""
    if payload is None:
        return []
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return []
    records: List[Tuple[str, Dict[str, Any]]] = []
    seen = set()
    for obj in _walk(payload):
        if not _looks_like_order(obj):
            continue
        key = _record_key(obj)
        if key in seen:
            continue
        seen.add(key)
        records.append((key, obj))
    return records


def normalize_orders(records: Iterable[Tuple[str, Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """把原始订单记录归一化成分析所需的统一结构。"""
    orders: List[Dict[str, Any]] = []
    for key, raw in records:
        moment = _parse_time(_pick(raw, K_ORDER_TIME, depth=1))
        items = _extract_items(raw)
        total = _to_float(_pick(raw, K_TOTAL, depth=1))
        if total is None and items:
            subtotal = sum((it["price"] or 0) * it["qty"] for it in items)
            total = round(subtotal, 2) if subtotal else None
        orders.append(
            {
                "key": key,
                "order_id": str(_pick(raw, ID_KEYS, depth=0) or ""),
                "time": moment.strftime("%Y-%m-%d %H:%M") if moment else "",
                "date": moment.strftime("%Y-%m-%d") if moment else "",
                "hour": moment.hour if moment else None,
                "weekday": moment.weekday() if moment else None,
                "store": str(_pick(raw, K_STORE, depth=1) or ""),
                "channel": str(_pick(raw, K_CHANNEL, depth=1) or ""),
                "total": total,
                "discount": _to_float(_pick(raw, K_DISCOUNT, depth=1)),
                "items": items,
            }
        )
    orders.sort(key=lambda o: o["time"], reverse=True)
    return orders


def build_coverage(
    source: str,
    pages: int,
    raw_records: int,
    orders: List[Dict[str, Any]],
    warnings: Optional[List[str]] = None,
    tools: Optional[Dict[str, str]] = None,
    truncated: bool = False,
) -> Dict[str, Any]:
    """生成数据覆盖率说明，用于报告中的「数据可信度」区块。"""
    coverage: Dict[str, Any] = {
        "source": source,  # mcp | demo | unknown
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pages": pages,
        "raw_records": raw_records,
        "unique_orders": len(orders),
        "duplicates_removed": max(raw_records - len(orders), 0),
        "with_date": sum(1 for o in orders if o.get("date")),
        "with_amount": sum(1 for o in orders if isinstance(o.get("total"), (int, float))),
        "with_items": sum(1 for o in orders if o.get("items")),
        "with_store": sum(1 for o in orders if o.get("store")),
        "truncated": truncated,
        "warnings": warnings or [],
        "tools": tools or {},
        "range_start": min((o["date"] for o in orders if o.get("date")), default=""),
        "range_end": max((o["date"] for o in orders if o.get("date")), default=""),
    }
    return coverage


# ---------------------------------------------------------------- 分页采集

def _build_page_args(shape: Tuple[str, str], page: int, page_size: int) -> Dict[str, Any]:
    key, size_key = shape
    if key == "offset":
        return {key: (page - 1) * page_size, size_key: page_size}
    return {key: page, size_key: page_size}


def _has_more(payload: Any, got: int, page_size: int) -> bool:
    """根据服务端信号或本页条数判断是否还有下一页。"""
    if isinstance(payload, dict):
        for key in MORE_KEYS:
            value = _pick(payload, (key,), depth=1)
            if isinstance(value, bool):
                return value
    return got >= page_size


def fetch_order_records(
    client: McpClient,
    max_pages: int = 30,
    page_size: int = 100,
) -> Tuple[List[Tuple[str, Dict[str, Any]]], Dict[str, Any]]:
    """循环翻页拉取全部历史订单，返回 (去重后的记录, 分页元信息)。"""
    tool = TOOLS["orders"]
    last_error = ""

    for shape in PAGE_SHAPES:
        records: List[Tuple[str, Dict[str, Any]]] = []
        seen: set = set()
        raw_seen = 0
        pages = 0
        truncated = False
        warnings: List[str] = []
        try:
            page = 1
            while page <= max_pages:
                args = _build_page_args(shape, page, page_size)
                data, text = client.call_tool(tool, args)
                payload = data if data not in (None, {}) else text
                batch = extract_records(payload)
                raw_seen += len(batch)

                new_count = 0
                for key, raw in batch:
                    if key in seen:
                        continue
                    seen.add(key)
                    records.append((key, raw))
                    new_count += 1
                if not batch or new_count == 0:
                    break  # 无数据或全是重复，说明已到末尾（这一页不计入有效页数）
                pages = page
                if not _has_more(payload, len(batch), page_size):
                    break
                page += 1
            else:
                truncated = True
                warnings.append(
                    f"已达到分页上限（第 {max_pages} 页），可能仍有订单未纳入统计，"
                    "请提高 --max-pages 后重新采集"
                )

            return records, {
                "shape": shape,
                "pages": pages,
                "raw_records": raw_seen,
                "truncated": truncated,
                "warnings": warnings,
            }
        except McpError as exc:
            last_error = str(exc)
            continue

    raise McpError(f"{tool} 所有分页参数组合均调用失败，最后一次错误：{last_error}")


# ---------------------------------------------------------------- 采集入口

def collect(
    client: McpClient,
    out_dir: Path,
    with_optional: bool = True,
    max_pages: int = 30,
    page_size: int = 100,
) -> Dict[str, Any]:
    """拉取时光机所需数据，产出订单与 provenance（覆盖率说明）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    tools_status: Dict[str, str] = {}
    warnings: List[str] = []

    records, page_meta = fetch_order_records(
        client, max_pages=max_pages, page_size=page_size
    )
    orders = normalize_orders(records)
    tools_status[TOOLS["orders"]] = "ok"
    warnings.extend(page_meta.get("warnings", []))

    (raw_dir / "orders.json").write_text(
        json.dumps(
            {
                "tool": TOOLS["orders"],
                "page_shape": list(page_meta["shape"]),
                "pages": page_meta["pages"],
                "records": [raw for _, raw in records],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    optional = {
        "meals": (TOOLS["meals"], [{}, {"storeCode": None}]),
        "nutrition": (TOOLS["nutrition"], [{}]),
        "coupons": (TOOLS["coupons"], [{}]),
        "points": (TOOLS["points"], [{}]),
        "campaign": (TOOLS["campaign"], [{}]),
        "mall_orders": (TOOLS["mall_orders"], [{}, {"pageNo": 1, "pageSize": 50}]),
    }
    for key, (tool, cands) in optional.items():
        if not with_optional:
            tools_status[tool] = "not requested"
            continue
        try:
            data, text, args = client.call_tool_any(tool, cands)
            tools_status[tool] = "ok"
            (raw_dir / f"{key}.json").write_text(
                json.dumps({"tool": tool, "args": args, "data": data},
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001 - 增强数据允许失败
            tools_status[tool] = f"skipped: {exc}"
            warnings.append(f"{tool} 未取到数据，报告中相关维度会缺失")

    if not orders:
        warnings.append(
            "未解析出任何订单：可能是返回结构变化，或账号确无历史订单。"
            "请运行 `mcdtm doctor` 查看原始返回结构。"
        )

    coverage = build_coverage(
        source="mcp",
        pages=page_meta["pages"],
        raw_records=page_meta["raw_records"],
        orders=orders,
        warnings=warnings,
        tools=tools_status,
        truncated=page_meta["truncated"],
    )

    (out_dir / "orders.normalized.json").write_text(
        json.dumps(orders, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "provenance.json").write_text(
        json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {"orders": orders, "coverage": coverage}


def load_normalized(out_dir: Path) -> List[Dict[str, Any]]:
    path = out_dir / "orders.normalized.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} 不存在。请先执行 `mcdtm fetch`，或用 `mcdtm demo` 生成示例数据。"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def load_coverage(out_dir: Path) -> Dict[str, Any]:
    path = out_dir / "provenance.json"
    if not path.exists():
        return build_coverage(source="unknown", pages=0, raw_records=0, orders=[])
    return json.loads(path.read_text(encoding="utf-8"))


__all__ = [
    "collect", "normalize_orders", "extract_records", "fetch_order_records",
    "build_coverage", "load_normalized", "load_coverage", "_guess_category",
]
