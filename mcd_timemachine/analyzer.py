"""口味画像、消费趋势、年度回忆与下次尝试推荐。

所有计算都是纯本地的：不上传任何订单数据，只做统计与文本生成。
报告同时输出「数据覆盖率」，如实说明统计范围与缺失情况，避免看起来漂亮却不可信。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from .collector import _guess_category, build_coverage

SLOTS = [
    ("清晨", 5, 9),
    ("早餐", 9, 11),
    ("午餐", 11, 14),
    ("下午茶", 14, 17),
    ("晚餐", 17, 21),
    ("夜宵", 21, 24),
    ("深夜", 0, 5),
]

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# 麦当劳经典单品库（当菜单 Tool 未返回数据时的兜底推荐池）
FALLBACK_MENU = [
    ("巨无霸", "汉堡"), ("麦辣鸡腿堡", "汉堡"), ("板烧鸡腿堡", "汉堡"),
    ("双层吉士汉堡", "汉堡"), ("麦香鱼", "汉堡"), ("安格斯MAX厚牛堡", "汉堡"),
    ("双层牛柳汉堡", "汉堡"), ("吉士汉堡", "汉堡"), ("猪柳蛋麦满分", "早餐"),
    ("麦麦脆汁鸡", "小食"), ("麦乐鸡", "小食"), ("薯条", "小食"),
    ("那么大鸡排", "小食"), ("麦辣鸡翅", "小食"), ("洋葱圈", "小食"),
    ("扭扭薯条", "小食"), ("脆香鸡", "小食"), ("薯饼", "早餐"),
    ("麦旋风", "甜品"), ("新地", "甜品"), ("甜筒", "甜品"),
    ("圆筒", "甜品"), ("奶昔", "甜品"), ("苹果派", "甜品"),
    ("咖啡拿铁", "饮料"), ("美式咖啡", "饮料"), ("可乐", "饮料"),
    ("雪碧", "饮料"), ("热朱古力", "饮料"), ("鲜煮咖啡", "饮料"),
    ("玉米杯", "沙拉"), ("蔬菜沙拉", "沙拉"),
    ("皮蛋瘦肉粥", "早餐"), ("热香饼", "早餐"),
]


def _slot_of(hour: Optional[int]) -> str:
    if hour is None:
        return "未知"
    for name, start, end in SLOTS:
        if start <= hour < end:
            return name
    return "未知"


def _money(value: Optional[float]) -> str:
    return f"¥{value:,.2f}" if value is not None else "—"


def _iter_dicts(node: Any):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _iter_dicts(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_dicts(value)


def _menu_pool(extras: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
    """优先用麦当劳 MCP 返回的真实菜单，缺失时用经典单品兜底。"""
    pool: List[Dict[str, str]] = []
    if extras:
        payload = extras.get("meals", {}).get("data")
        for node in _iter_dicts(payload):
            name = None
            for key in ("productName", "mealName", "goodsName", "name", "title", "skuName"):
                value = node.get(key)
                if isinstance(value, str) and value.strip():
                    name = value.strip()
                    break
            if not name:
                continue
            # 必须同时带价格 / 编码 / 分类字段，才认为是菜品节点而不是分类容器
            if not any(k in node for k in ("price", "productCode", "code",
                                           "categoryName", "mealCode", "salePrice")):
                continue
            pool.append({"name": name, "category": _guess_category(name)})
    if len(pool) < 5:
        pool = [{"name": n, "category": c} for n, c in FALLBACK_MENU]
    seen, unique = set(), []
    for item in pool:
        if item["name"] in seen:
            continue
        seen.add(item["name"])
        unique.append(item)
    return unique


def _nutrition_map(extras: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    if not extras:
        return result
    payload = extras.get("nutrition", {}).get("data")
    for node in _iter_dicts(payload):
        name = None
        for key in ("productName", "mealName", "foodName", "name", "title"):
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                name = value.strip()
                break
        if not name:
            continue
        result[name] = {k: v for k, v in node.items() if isinstance(v, (int, float, str))}
    return result


# ---------------------------------------------------------------- 主分析

def analyze(
    orders: List[Dict[str, Any]],
    extras: Optional[Dict[str, Any]] = None,
    coverage: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not orders:
        return {
            "empty": True,
            "summary": {"total_orders": 0},
            "coverage": coverage or build_coverage("unknown", 0, 0, []),
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }

    # 不依赖调用方传入的顺序，内部统一按时间倒序，保证 first/last 判定正确
    orders = sorted(orders, key=lambda o: o.get("time") or "", reverse=True)
    dated = [o for o in orders if o.get("date")]
    totals = [o["total"] for o in orders if isinstance(o.get("total"), (int, float))]
    total_spend = sum(totals)
    avg_order = total_spend / len(totals) if totals else 0.0

    item_count: Counter = Counter()
    item_spend: defaultdict = defaultdict(float)
    category_count: Counter = Counter()
    category_spend: defaultdict = defaultdict(float)
    for order in orders:
        for item in order.get("items", []):
            name = item.get("name")
            if not name:
                continue
            qty = item.get("qty") or 1
            price = item.get("price") or 0
            item_count[name] += qty
            item_spend[name] += price * qty
            category_count[item.get("category", "其他")] += qty
            category_spend[item.get("category", "其他")] += price * qty

    slot_count: Counter = Counter()
    weekday_count: Counter = Counter()
    monthly_data: Dict[str, Dict[str, float]] = defaultdict(lambda: {"orders": 0, "spend": 0.0})
    for order in dated:
        slot_count[_slot_of(order.get("hour"))] += 1
        if order.get("weekday") is not None:
            weekday_count[WEEKDAYS[order["weekday"]]] += 1
        month = order["date"][:7]
        monthly_data[month]["orders"] += 1
        monthly_data[month]["spend"] += order.get("total") or 0

    store_count: Counter = Counter(o.get("store") or "未知门店" for o in orders)
    channel_count: Counter = Counter(o.get("channel") or "未标注" for o in orders)

    pair_count: Counter = Counter()
    for order in orders:
        names = sorted({i["name"] for i in order.get("items", []) if i.get("name")})
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                pair_count[(names[i], names[j])] += 1

    days = sorted({o["date"] for o in dated})
    parsed_days = [datetime.strptime(d, "%Y-%m-%d") for d in days]
    if parsed_days:
        best_streak = current = 1
        for i in range(1, len(parsed_days)):
            gap = (parsed_days[i] - parsed_days[i - 1]).days
            if gap == 1:
                current += 1
                best_streak = max(best_streak, current)
            elif gap > 1:
                current = 1
        span_days = (parsed_days[-1] - parsed_days[0]).days + 1
    else:
        best_streak = 0
        span_days = 0

    # ---------------- 年度回忆
    priced = [o for o in orders if isinstance(o.get("total"), (int, float))]
    priciest = max(priced, key=lambda o: o["total"], default=None)

    def _qty(order: Dict[str, Any]) -> int:
        return sum(i.get("qty", 1) for i in order.get("items", []))

    biggest = max((o for o in orders if o.get("items")), key=_qty, default=None)

    memories: List[Dict[str, str]] = []
    if dated:
        first = dated[-1]
        first_item = first["items"][0]["name"] if first.get("items") else ""
        memories.append({
            "title": "故事的起点",
            "text": f"{first['date']}，你下了第一单"
                    f"{('，' + first_item) if first_item else ''}。",
        })
    if priciest:
        names = "、".join(i["name"] for i in priciest.get("items", [])[:4]) or "若干好吃的"
        memories.append({
            "title": "最难忘的一单",
            "text": f"{priciest.get('date') or '某天'} 花了 {_money(priciest['total'])}，"
                    f"点了 {names}。",
        })
    if biggest and biggest is not priciest and _qty(biggest) > 1:
        memories.append({
            "title": "最豪横的一次",
            "text": f"{biggest.get('date') or '某天'} 一次点了 {_qty(biggest)} 件。",
        })
    if monthly_data:
        month, value = max(monthly_data.items(), key=lambda kv: kv[1]["orders"])
        memories.append({
            "title": "最沉迷的月份",
            "text": f"{month} 你点了 {int(value['orders'])} 单，累计 {_money(value['spend'])}。",
        })
    if store_count:
        store, cnt = store_count.most_common(1)[0]
        if store != "未知门店":
            memories.append({"title": "你的主场", "text": f"{store}，一共去过 {cnt} 次。"})
    night = slot_count.get("夜宵", 0) + slot_count.get("深夜", 0)
    if night:
        memories.append({"title": "深夜食堂", "text": f"有 {night} 单发生在 21 点之后。"})
    if best_streak:
        memories.append({"title": "最长连续打卡", "text": f"连续 {best_streak} 天。"})

    # ---------------- 口味画像
    total_items_qty = sum(item_count.values()) or 1

    def _ratio(predicate) -> float:
        return round(
            100 * sum(qty for name, qty in item_count.items() if predicate(name))
            / total_items_qty, 1
        )

    spicy = _ratio(lambda n: "辣" in n)
    sweet = round(100 * category_count.get("甜品", 0) / total_items_qty, 1)
    burger = round(100 * category_count.get("汉堡", 0) / total_items_qty, 1)
    fries = _ratio(lambda n: "薯条" in n or "薯格" in n or "薯饼" in n)
    coffee = _ratio(lambda n: "咖啡" in n or "拿铁" in n or "美式" in n)
    breakfast = round(100 * slot_count.get("早餐", 0) / max(len(dated), 1), 1)
    late_night = round(100 * night / max(len(dated), 1), 1)
    budget = avg_order > 0 and avg_order < 30

    candidates: List[Dict[str, Any]] = [
        {"tag": "嗜辣星人", "threshold": 10, "score": spicy, "desc": f"{spicy}% 的单品带「辣」字"},
        {"tag": "甜食主义", "threshold": 8, "score": sweet, "desc": f"甜品占了 {sweet}%"},
        {"tag": "汉堡原教旨", "threshold": 18, "score": burger, "desc": f"汉堡占比 {burger}%"},
        {"tag": "薯条依赖症", "threshold": 10, "score": fries, "desc": f"薯条类出现率 {fries}%"},
        {"tag": "咖啡续命", "threshold": 7, "score": coffee, "desc": f"咖啡类占比 {coffee}%"},
        {"tag": "早起干饭人", "threshold": 12, "score": breakfast, "desc": f"{breakfast}% 的订单在早餐时段"},
        {"tag": "夜宵常客", "threshold": 12, "score": late_night, "desc": f"{late_night}% 的订单在 21 点后"},
    ]
    if budget:
        candidates.append(
            {"tag": "性价比猎手", "threshold": 0, "score": round(avg_order, 1),
             "desc": f"平均客单价仅 {_money(avg_order)}"}
        )

    tags = [c for c in candidates if float(c["score"]) >= c["threshold"]]
    tags.sort(key=lambda t: float(t["score"]), reverse=True)
    if len(tags) < 3:
        rest = sorted((c for c in candidates if c not in tags),
                      key=lambda t: float(t["score"]), reverse=True)
        tags.extend(rest[: 3 - len(tags)])
    if not tags:
        tags.append({"tag": "均衡派", "score": 100, "desc": "什么都来一点，不偏科"})
    for t in tags:
        t.pop("threshold", None)

    taste_shift = _compute_shift(dated, category_count)
    keywords = _build_keywords(
        item_count, category_count, slot_count, store_count, pair_count, tags
    )
    recommendations = _recommend(
        item_count, category_count, _menu_pool(extras), _nutrition_map(extras)
    )

    return {
        "empty": False,
        "summary": {
            "total_orders": len(orders),
            "total_spend": round(total_spend, 2),
            "avg_order": round(avg_order, 2),
            "total_items": int(total_items_qty),
            "first_date": dated[-1]["date"] if dated else "",
            "last_date": dated[0]["date"] if dated else "",
            "span_days": span_days,
            "best_streak": best_streak,
            "avg_per_month": round(total_spend / max(len(monthly_data), 1), 2),
            "orders_with_amount": len(totals),
            "orders_with_date": len(dated),
            "orders_with_items": sum(1 for o in orders if o.get("items")),
        },
        "top_items": [
            {"name": n, "qty": q, "spend": round(item_spend[n], 2)}
            for n, q in item_count.most_common(10)
        ],
        "categories": [
            {"name": c, "qty": q, "spend": round(category_spend[c], 2),
             "pct": round(100 * q / total_items_qty, 1)}
            for c, q in category_count.most_common()
        ],
        "slots": [{"name": s[0], "count": slot_count.get(s[0], 0)} for s in SLOTS],
        "weekdays": [{"name": w, "count": weekday_count.get(w, 0)} for w in WEEKDAYS],
        "monthly": [
            {"month": m, "orders": int(v["orders"]), "spend": round(v["spend"], 2)}
            for m, v in sorted(monthly_data.items())
        ],
        "stores": [{"name": s, "count": c} for s, c in store_count.most_common(5)],
        "channels": [{"name": c, "count": n} for c, n in channel_count.most_common()],
        "combos": [{"pair": f"{a} + {b}", "count": c} for (a, b), c in pair_count.most_common(5)],
        "memories": memories,
        "tags": tags,
        "keywords": keywords,
        "taste_shift": taste_shift,
        "recommendations": recommendations,
        "coverage": coverage or build_coverage("unknown", 0, len(orders), orders),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


# ---------------------------------------------------------------- 故事化维度

def _compute_shift(dated: List[Dict[str, Any]], category_count: Counter) -> List[Dict[str, Any]]:
    """对比前后半程的品类占比变化，给出「这一年的口味变化」。"""
    if len(dated) < 6:
        return []
    dates = sorted(o["date"] for o in dated)
    mid = dates[len(dates) // 2]

    def _half(second: bool) -> Counter:
        counter: Counter = Counter()
        for order in dated:
            if (order["date"] >= mid) != second:
                continue
            for item in order.get("items", []):
                counter[item.get("category", "其他")] += item.get("qty") or 1
        return counter

    early, late = _half(False), _half(True)
    total_early, total_late = sum(early.values()) or 1, sum(late.values()) or 1

    deltas = []
    for category in set(list(early) + list(late)):
        pct_early = 100 * early.get(category, 0) / total_early
        pct_late = 100 * late.get(category, 0) / total_late
        deltas.append(
            {
                "category": category,
                "early": round(pct_early, 1),
                "late": round(pct_late, 1),
                "delta": round(pct_late - pct_early, 1),
            }
        )
    deltas.sort(key=lambda d: abs(d["delta"]), reverse=True)
    return deltas[:4]


def _build_keywords(
    item_count: Counter,
    category_count: Counter,
    slot_count: Counter,
    store_count: Counter,
    pair_count: Counter,
    tags: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    keywords: List[Dict[str, str]] = []

    if item_count:
        name, qty = item_count.most_common(1)[0]
        keywords.append({"word": name, "reason": f"全年点了 {qty} 次，年度单品"})
    if category_count:
        cat, qty = category_count.most_common(1)[0]
        keywords.append({"word": f"{cat}控", "reason": f"{cat}类一共 {qty} 件"})
    slots = [(s, c) for s, c in slot_count.most_common() if s != "未知" and c > 0]
    if slots:
        slot, cnt = slots[0]
        keywords.append({"word": f"{slot}档", "reason": f"{cnt} 单发生在这个时段"})
    if tags:
        keywords.append({"word": tags[0]["tag"], "reason": tags[0]["desc"]})
    if pair_count:
        (a, b), cnt = pair_count.most_common(1)[0]
        keywords.append({"word": f"{a}+{b}", "reason": f"这对组合同单出现 {cnt} 次"})
    stores = [(s, c) for s, c in store_count.most_common() if s != "未知门店"]
    if stores:
        store, cnt = stores[0]
        keywords.append({"word": store, "reason": f"去了 {cnt} 次，你的主场"})
    return keywords[:6]


# ---------------------------------------------------------------- 推荐

def _recommend(
    item_count: Counter,
    category_count: Counter,
    menu: List[Dict[str, str]],
    nutrition: Dict[str, Dict[str, Any]],
) -> List[Dict[str, str]]:
    def _norm(text: str) -> str:
        for ch in "()（）[]【】":
            text = text.replace(ch, " ")
        return "".join(text.split())

    eaten = {_norm(n) for n in item_count.keys()}
    pref = {c: q for c, q in category_count.most_common(3)}
    max_pref = max(pref.values()) if pref else 1
    top_category = list(pref)[0] if pref else ""

    scored = []
    for item in menu:
        name = item["name"]
        if _norm(name) in eaten:
            continue
        score = pref.get(item["category"], 0) / max_pref * 60
        if item["category"] == top_category:
            score += 25
        if "辣" in name and any("辣" in e for e in eaten):
            score += 10
        if item["category"] not in category_count:
            score += 8
        reason = f"你偏爱「{item['category']}」，这一款还没试过"
        if item["category"] not in category_count:
            reason = f"你从没点过「{item['category']}」，从这一款开始破圈"
        energy = _lookup_energy(name, nutrition)
        if energy:
            reason += f"；约 {energy}"
        scored.append({"name": name, "reason": reason, "score": round(score, 1)})

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:6]


def _lookup_energy(name: str, nutrition: Dict[str, Dict[str, Any]]) -> str:
    for key, entry in nutrition.items():
        if key in name or name in key:
            for field in ("energy", "Energy", "热量", "kcal", "calorie"):
                value = entry.get(field)
                if isinstance(value, (int, float)):
                    return f"{value} kcal"
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return ""


__all__ = ["analyze", "SLOTS", "WEEKDAYS"]
