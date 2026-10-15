"""口味画像、消费趋势、年度回忆与下次尝试推荐。

所有计算都是纯本地的：不上传任何订单数据，只做统计与文本生成。
报告同时输出「数据覆盖率」，如实说明统计范围与缺失情况，避免看起来漂亮却不可信。
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

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

# -------- 口味画像的样本量门槛
# 低于这个量不做任何偏好判定，避免「1 单全是辣」就得出「嗜辣星人」
MIN_TAG_ORDERS = 5
MIN_TAG_ITEMS = 8
# 达到这个订单数才认为样本充分，可信度不打折
SAMPLE_FULL_ORDERS = 10

# -------- 商品名称标准化：规格 / 包装 / 别名
_SPEC_RE = re.compile(r"[（(【\[][^)）】\]]*[)）】\]]")
_SPEC_WORDS_RE = re.compile(
    r"(大份|中份|小份|加大|超大|双份|两份|三份|套餐|单人餐|双人餐|"
    r"分享装|超值装|加量装|儿童餐|快乐儿童餐)"
)
_SIZE_SUFFIX_RE = re.compile(r"[xX×]\s*\d+$|\d+\s*(块|只|串|支)$")
_ALIAS = {
    "可口可乐": "可乐",
    "零度可乐": "可乐",
    "健怡可乐": "可乐",
    "无糖可乐": "可乐",
    "冰美式": "美式咖啡",
    "拿铁": "咖啡拿铁",
    "香辣鸡翅": "麦辣鸡翅",
}

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

    # ---- 金额口径：实际支付 / 商品估算 / 未标注 必须分开统计，不能混算
    def _bucket(order: Dict[str, Any]) -> str:
        src = order.get("amount_source")
        return src if src in ("actual", "estimated") else "unlabeled"

    def _sum_of(bucket: str) -> Tuple[float, int]:
        values = [
            o["total"] for o in orders
            if isinstance(o.get("total"), (int, float)) and _bucket(o) == bucket
        ]
        return round(sum(values), 2), len(values)

    actual_spend, actual_orders = _sum_of("actual")
    estimated_spend, estimated_orders = _sum_of("estimated")
    unlabeled_spend, unlabeled_orders = _sum_of("unlabeled")

    totals = [o["total"] for o in orders if isinstance(o.get("total"), (int, float))]
    total_spend = round(sum(totals), 2)
    unknown_amount_orders = len(orders) - len(totals)

    if actual_orders:
        # 只要有实际支付金额，主指标就只认实际支付，估算值不参与
        display_spend = actual_spend
        avg_order = actual_spend / actual_orders
        spend_basis = "actual" if not (estimated_orders or unlabeled_orders) else "mixed"
    else:
        display_spend = round(estimated_spend + unlabeled_spend, 2)
        priced_n = estimated_orders + unlabeled_orders
        avg_order = display_spend / priced_n if priced_n else 0.0
        spend_basis = "estimated" if estimated_orders else (
            "unlabeled" if unlabeled_orders else "none"
        )

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
            "title": "记录中的第一单",
            "text": f"{first['date']}，这是这段记录里最早的一单"
                    f"{('，' + first_item) if first_item else ''}。",
        })
    if priciest:
        names = "、".join(i["name"] for i in priciest.get("items", [])[:4]) or "若干好吃的"
        amount_note = "" if priciest.get("amount_source") == "actual" else "（估算）"
        memories.append({
            "title": "最豪华的一单",
            "text": f"{priciest.get('date') or '某天'} 花了 {_money(priciest['total'])}"
                    f"{amount_note}，点了 {names}。",
        })
    if biggest and biggest is not priciest and _qty(biggest) > 1:
        memories.append({
            "title": "最豪横的一次",
            "text": f"{biggest.get('date') or '某天'} 一次点了 {_qty(biggest)} 件。",
        })
    if monthly_data:
        month, value = max(monthly_data.items(), key=lambda kv: kv[1]["orders"])
        memories.append({
            "title": "最常光顾的月份",
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

    spicy_items = sum(q for n, q in item_count.items() if "辣" in n)
    fries_items = sum(q for n, q in item_count.items()
                      if "薯条" in n or "薯格" in n or "薯饼" in n)
    coffee_items = sum(q for n, q in item_count.items()
                       if "咖啡" in n or "拿铁" in n or "美式" in n)
    breakfast_orders = slot_count.get("早餐", 0)
    items_total = sum(item_count.values())

    # 每个标签统一给出 0-100 的置信度，避免「百分比」与「金额」直接比较
    def _confidence(score: float, threshold: float) -> int:
        if threshold <= 0:
            return 55
        return max(0, min(100, int(round(50 + 50 * (score - threshold) / threshold))))

    def _label(conf: int) -> str:
        if conf >= 75:
            return "高"
        if conf >= 55:
            return "中"
        return "低"

    # ---- 样本量检查必须放在标签计算「之前」：
    # 样本不足时不做任何偏好判定，避免「1 单全是辣」就得出「嗜辣星人」
    data_sufficient = len(orders) >= MIN_TAG_ORDERS and items_total >= MIN_TAG_ITEMS
    # 样本越薄，可信度打折越狠（最低 6 折），避免小样本给出高可信度结论
    sample_factor = max(0.0, min(1.0, len(orders) / SAMPLE_FULL_ORDERS))
    confidence_scale = 0.6 + 0.4 * sample_factor
    sample = {"orders": len(orders), "items": items_total}

    tags: List[Dict[str, Any]]
    if not data_sufficient:
        tags = [{
            "tag": "口味探索中", "score": 0, "confidence": 0, "comparable": True,
            "confidence_label": "样本不足",
            "desc": f"只有 {len(orders)} 单 / {items_total} 件单品，样本太少，先不下偏好结论",
            "evidence": f"需要至少 {MIN_TAG_ORDERS} 单且 {MIN_TAG_ITEMS} 件单品才开始画像",
            "sample": sample,
        }]
    else:
        # support = 支撑该标签的绝对数量；min_support 是最小支撑量，防止个别订单就命中
        candidates: List[Dict[str, Any]] = [
            {"tag": "嗜辣星人", "threshold": 10, "score": spicy,
             "support": spicy_items, "min_support": 3,
             "desc": f"{spicy}% 的单品带「辣」字",
             "evidence": f"共 {items_total} 件单品，其中 {spicy_items} 件带「辣」字"},
            {"tag": "甜食主义", "threshold": 8, "score": sweet,
             "support": category_count.get("甜品", 0), "min_support": 3,
             "desc": f"甜品占了 {sweet}%",
             "evidence": f"甜品累计 {category_count.get('甜品', 0)} 件，"
                         f"占全部单品 {sweet}%"},
            {"tag": "汉堡原教旨", "threshold": 18, "score": burger,
             "support": category_count.get("汉堡", 0), "min_support": 3,
             "desc": f"汉堡占比 {burger}%",
             "evidence": f"汉堡累计 {category_count.get('汉堡', 0)} 件，"
                         f"占全部单品 {burger}%"},
            {"tag": "薯条依赖症", "threshold": 10, "score": fries,
             "support": fries_items, "min_support": 3,
             "desc": f"薯条类出现率 {fries}%",
             "evidence": f"薯条类累计 {fries_items} 件，占全部单品 {fries}%"},
            {"tag": "咖啡续命", "threshold": 7, "score": coffee,
             "support": coffee_items, "min_support": 2,
             "desc": f"咖啡类占比 {coffee}%",
             "evidence": f"咖啡类累计 {coffee_items} 件，占全部单品 {coffee}%"},
            {"tag": "早起干饭人", "threshold": 12, "score": breakfast,
             "support": breakfast_orders, "min_support": 2,
             "desc": f"{breakfast}% 的订单在早餐时段",
             "evidence": f"{len(dated)} 单中有 {breakfast_orders} 单发生在早餐时段"},
            {"tag": "夜宵常客", "threshold": 12, "score": late_night,
             "support": night, "min_support": 2,
             "desc": f"{late_night}% 的订单在 21 点后",
             "evidence": f"{len(dated)} 单中有 {night} 单发生在 21 点之后"},
        ]
        if budget:
            # 分数是金额，与百分比不可比，单独给置信度并标记 comparable=False
            candidates.append(
                {"tag": "性价比猎手", "threshold": 0, "score": round(avg_order, 1),
                 "support": actual_orders or len(totals), "min_support": MIN_TAG_ORDERS,
                 "desc": f"平均客单价仅 {_money(avg_order)}",
                 "evidence": f"{actual_orders or len(totals)} 单有金额记录，"
                             f"平均 {_money(avg_order)}",
                 "comparable": False,
                 "confidence": max(0, min(100, int(round(100 * (30 - avg_order) / 30))))}
            )

        tags = []
        for cand in candidates:
            score = float(cand["score"])
            if score < cand["threshold"]:
                continue
            if int(cand.get("support", 0)) < int(cand.get("min_support", 0)):
                continue  # 支撑样本太少，不贴这个标签
            item = dict(cand)
            item.setdefault("comparable", True)
            raw_conf = item.get("confidence") or _confidence(score, cand["threshold"])
            conf = max(0, min(100, int(round(raw_conf * confidence_scale))))
            item["confidence"] = conf
            item["confidence_label"] = _label(conf)
            item["sample"] = sample
            tags.append(item)
        # 先按置信度、再按是否可跨标签比较排序，最多保留 5 个
        tags.sort(key=lambda t: (t["confidence"], t["comparable"]), reverse=True)
        tags = tags[:5]
        for t in tags:
            t.pop("threshold", None)
            t.pop("support", None)
            t.pop("min_support", None)

        if not tags:
            tags = [{
                "tag": "均衡派", "score": 100, "confidence": 50, "comparable": True,
                "confidence_label": "中",
                "desc": "什么都来一点，没有明显偏科",
                "evidence": f"覆盖了 {len(category_count)} 个品类，分布较均匀",
                "sample": sample,
            }]

    taste_shift, shift_basis = _compute_shift(dated, category_count)
    keywords = _build_keywords(
        item_count, category_count, slot_count, store_count, pair_count, tags
    )
    recommendations = _recommend(
        item_count, category_count, _menu_pool(extras), _nutrition_map(extras),
        orders=orders, shift=taste_shift,
    )

    return {
        "empty": False,
        "summary": {
            "total_orders": len(orders),
            "total_spend": round(total_spend, 2),
            "display_spend": round(display_spend, 2),
            "spend_basis": spend_basis,
            "actual_spend": actual_spend,
            "actual_amount_orders": actual_orders,
            "estimated_spend": estimated_spend,
            "estimated_amount_orders": estimated_orders,
            "unknown_amount_orders": unknown_amount_orders,
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
        "data_sufficient": data_sufficient,
        "keywords": keywords,
        "taste_shift": taste_shift,
        "taste_shift_basis": shift_basis,
        "recommendations": recommendations,
        "coverage": coverage or build_coverage("unknown", 0, len(orders), orders),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


# ---------------------------------------------------------------- 故事化维度

def _compute_shift(
    dated: List[Dict[str, Any]], category_count: Counter
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """对比前后半程的品类占比变化，给出「这一年的口味变化」。

    分组方式：先按完整时间（日期 + 时刻）升序排序，再按**订单序号**对半切。
    不能按「中间日期」切 —— 大量订单挤在同一天时，前后两组会严重失衡甚至空组。
    """
    basis: Dict[str, Any] = {"method": "按订单序号对半", "available": False}
    if len(dated) < 6:
        basis["reason"] = f"需要至少 6 单，当前 {len(dated)} 单"
        return [], basis

    ordered = sorted(dated, key=lambda o: (o.get("time") or o.get("date") or ""))
    mid = len(ordered) // 2
    if mid == 0 or mid >= len(ordered):
        basis["reason"] = "订单数不足，无法分成两组"
        return [], basis

    early_orders, late_orders = ordered[:mid], ordered[mid:]

    def _counter(group: List[Dict[str, Any]]) -> Counter:
        counter: Counter = Counter()
        for order in group:
            for item in order.get("items", []):
                counter[item.get("category", "其他")] += item.get("qty") or 1
        return counter

    early, late = _counter(early_orders), _counter(late_orders)
    total_early, total_late = sum(early.values()), sum(late.values())
    if not total_early or not total_late:
        basis["reason"] = "前后半程中有一半没有任何单品，无法比较"
        return [], basis

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

    basis.update(
        {
            "available": True,
            "early_orders": len(early_orders),
            "late_orders": len(late_orders),
            "early_range": f"{early_orders[0]['date']} ~ {early_orders[-1]['date']}",
            "late_range": f"{late_orders[0]['date']} ~ {late_orders[-1]['date']}",
        }
    )
    return deltas[:4], basis


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
    # 「口味探索中」是样本不足的提示，不是用户的口味关键词
    real_tags = [t for t in tags if t.get("tag") not in ("口味探索中",)]
    if real_tags:
        keywords.append({"word": real_tags[0]["tag"], "reason": real_tags[0]["desc"]})
    if pair_count:
        (a, b), cnt = pair_count.most_common(1)[0]
        keywords.append({"word": f"{a}+{b}", "reason": f"这对组合同单出现 {cnt} 次"})
    stores = [(s, c) for s, c in store_count.most_common() if s != "未知门店"]
    if stores:
        store, cnt = stores[0]
        keywords.append({"word": store, "reason": f"去了 {cnt} 次，你的主场"})
    return keywords[:6]


# ---------------------------------------------------------------- 推荐

def _base_name(text: str) -> str:
    """商品名标准化：去掉括号内容、规格词与数量后缀，归并别名。

    这样「麦乐鸡(5块)」「麦乐鸡 大份」都会被认成已经吃过的「麦乐鸡」，
    不会被当成新品重复推荐。
    """
    name = str(text or "")
    name = _SPEC_RE.sub("", name)
    name = _SIZE_SUFFIX_RE.sub("", name)
    name = _SPEC_WORDS_RE.sub("", name)
    # 归一：去掉所有空白与常见分隔符
    name = "".join(name.split())
    for ch in "()（）[]【】·・-—_":
        name = name.replace(ch, "")
    return _ALIAS.get(name, name)


def _recommend(
    item_count: Counter,
    category_count: Counter,
    menu: List[Dict[str, str]],
    nutrition: Dict[str, Dict[str, Any]],
    orders: Optional[List[Dict[str, Any]]] = None,
    shift: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, str]]:
    """推荐「还没点过」的单品。

    评分信号（可解释、不重复堆同一句话）：
    - 品类偏好强度（占你订单的比例）
    - 复购频率：该品类出现在多少单里
    - 近期偏好：最近 1/3 订单里该品类的热度
    - 口味变化：占比正在上升的品类
    - 破圈：从没点过的品类
    并做多样性控制：同一品类最多 2 个。
    """
    orders = orders or []
    shift = shift or []

    eaten_base = {_base_name(n) for n in item_count.keys()}
    eaten_raw = {str(n) for n in item_count.keys()}

    pref = {c: q for c, q in category_count.most_common(3)}
    max_pref = max(pref.values()) if pref else 1
    top_category = list(pref)[0] if pref else ""

    # 复购频率：品类出现在多少单里
    category_orders: Counter = Counter()
    for order in orders:
        cats = {i.get("category", "其他") for i in order.get("items", []) if i.get("name")}
        for c in cats:
            category_orders[c] += 1

    # 近期偏好：最近 1/3 订单（按时间倒序后的前段）
    recent_cats: Counter = Counter()
    if orders:
        recent_n = max(1, len(orders) // 3)
        for order in sorted(orders, key=lambda o: o.get("time") or "", reverse=True)[:recent_n]:
            for item in order.get("items", []):
                recent_cats[item.get("category", "其他")] += item.get("qty") or 1

    rising = {r["category"]: r["delta"] for r in shift if r.get("delta", 0) > 0}
    # 只在「显著」时才引用这些信号，否则每条推荐都会说同一句废话
    top_freq = {c for c, _ in category_orders.most_common(2)}
    top_recent = {c for c, _ in recent_cats.most_common(2)}

    def _stable_jitter(text: str) -> int:
        # 稳定哈希，避免依赖 PYTHONHASHSEED 造成测试不稳定
        return sum(ord(c) for c in text) % 7

    scored: List[Dict[str, Any]] = []
    for item in menu:
        name = str(item.get("name") or "")
        if not name:
            continue
        base = _base_name(name)
        if base in eaten_base or name in eaten_raw:
            continue
        # 吃过高度相似的同名商品（子串命中）也不再推荐
        if any(base and (base in e or e in base) for e in eaten_base):
            continue

        category = item.get("category", "其他")
        score = pref.get(category, 0) / max_pref * 55
        # (优先级, 文案)：数字越小越优先，最终只取最有力的两条
        signals: List[Tuple[int, str]] = []

        if category == top_category and top_category:
            score += 20
            signals.append((2, f"「{category}」是你点得最多的品类，这一款还没试过"))
        elif category in pref:
            score += 8
            signals.append((2, f"你偏爱「{category}」，这一款还没试过"))
        else:
            signals.append((6, f"这一款属于「{category or '其他'}」，你还没试过"))

        freq = category_orders.get(category, 0)
        if freq >= 3:
            score += min(12, freq)
        if category in top_freq and freq >= 3:
            signals.append((3, f"这个品类你一共在 {freq} 单里点过"))

        if category in top_recent:
            score += 10
            signals.append((4, "它出现在你最近的订单里"))

        if category in rising:
            score += 12
            signals.append((1, f"「{category}」后半程占比上升了 {rising[category]} 个百分点"))

        if category not in category_count:
            score += 14
            signals.append((0, f"你从没点过「{category}」，从这一款开始破圈"))

        if "辣" in name and any("辣" in e for e in eaten_raw):
            score += 8
            signals.append((5, "和你常点的辣味对得上"))

        score += _stable_jitter(name) * 0.5

        signals.sort(key=lambda s: s[0])
        reason = "；".join(text for _, text in signals[:2]) or "这一款你还没试过"
        energy = _lookup_energy(name, nutrition)
        if energy:
            reason += f"；约 {energy}"
        scored.append({"name": name, "reason": reason,
                       "category": category, "score": round(score, 1)})

    scored.sort(key=lambda x: x["score"], reverse=True)

    # 多样性：同一品类最多 2 个，且保证至少 1 个来自没吃过的品类
    per_category: Counter = Counter()
    picked: List[Dict[str, Any]] = []
    for cand in scored:
        if per_category[cand["category"]] >= 2:
            continue
        per_category[cand["category"]] += 1
        picked.append(cand)
        if len(picked) >= 6:
            break

    for cand in picked:
        cand.pop("category", None)
    return picked


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
