"""脱敏导出：把真实 MCP 返回结构转成不含原始值的形状描述。

注意：脱敏能去掉绝大多数敏感内容，但**无法保证覆盖所有未知字段**，
因此产物定位是「大幅降低风险的排障材料」，而不是「绝对安全的公开文件」。
公开前仍应人工通读一遍。

背景
----
联调时需要把「真实返回长什么样」交给维护者，才能补齐 collector 的字段候选表。
但真实返回里有订单号、金额、门店、餐品等个人信息，不能直接贴到 Issue。

本模块把任意 JSON 结构压成「骨架」：

    {
      "orderNo": "MCD20260305",
      "payTime": "2026-03-05 12:30:00",
      "totalAmount": 38.5,
      "storeName": "南京西路餐厅",
      "items": [{"goodsName": "巨无霸", "price": 18.0, "qty": 1}]
    }

脱敏成：

    {
      "orderNo": "aaa99999999",
      "payTime": "9999-99-99 99:99:99",
      "totalAmount": "<num:1dp>",
      "storeName": "汉汉汉汉汉",
      "items": [{"goodsName": "汉汉汉", "price": "<num:1dp>", "qty": "<num:int>"}]
    }

字段名全部保留（这是真正需要的信息），值只保留"形状"：
    - 汉字  -> 汉      数字 -> 9      字母 -> a      标点原样
    - 数字  -> <num:int> / <num:1dp>，附带位数（数量级）
    - 布尔 / null 原样

同时产出字段索引与候选映射建议，供维护者直接填进 collector 的表。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

MAX_DEPTH = 8
MAX_LIST_ITEMS = 2

# 值形状归类用的规则
_DIGIT = re.compile(r"\d")
_ALPHA = re.compile(r"[A-Za-z]")
_CJK = re.compile(r"[㐀-鿿]")

# 字段语义关键词
_SEMANTIC: List[Tuple[str, Tuple[str, ...]]] = [
    ("order_id", ("orderid", "orderno", "ordercode", "ordernum", "orderno_",
                  "order_id", "ordercode", "billno", "tradeno", "transactionid")),
    ("order_time", ("ordertime", "createtime", "paytime", "paymenttime",
                    "createts", "orderdate", "orderat", "submitTime",
                    "order_time", "trade_time", "pay_time", "gmtcreate",
                    "createdat", "created_at", "buytime", "consumetime")),
    ("order_amount", ("totalamount", "payamount", "actualamount", "orderamount",
                      "amount", "totalfee", "payfee", "realamount", "price",
                      "total", "money", "actualpay", "paidamount", "paymoney")),
    ("store", ("storename", "shopname", "restaurantname", "store", "shop",
               "restaurant", "merchant", "storeid", "shopid", "storecode",
               "address", "storeaddress", "shopaddress")),
    ("item_name", ("goodsname", "itemname", "productname", "mealname",
                   "menuname", "name", "title", "skuname", "foodname",
                   "goods_name", "product_name", "dishname")),
    ("item_qty", ("qty", "quantity", "count", "num", "amount_count", "buynum",
                  "goodsnum", "itemcount")),
    ("item_price", ("price", "saleprice", "unitprice", "goodsprice",
                    "itemprice", "amount", "subtotal", "linetotal")),
]


# 字符串分类：只判定"是什么类型"，不保留任何一个原始字符
_URL_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://|://", re.I)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_DATE_RE = re.compile(r"^\D*\d{4}[-/年.]\d{1,2}[-/月.]\d{1,2}")
_TIME_RE = re.compile(r"\d{1,2}:\d{2}")
_PHONE_RE = re.compile(r"^1[3-9]\d{9}$")
_IDCARD_RE = re.compile(r"^\d{17}[\dXx]$")
_DIGITS_RE = re.compile(r"^\d+$")
_ALPHA_RE = re.compile(r"^[A-Za-z]+$")
_ALNUM_RE = re.compile(r"^[A-Za-z0-9]+$")
_CJK_RE = re.compile(r"^[㐀-鿿]+$")
_CJK_ANY_RE = re.compile(r"[㐀-鿿]")


def _classify_str(text: str) -> str:
    """判定字符串类别。只输出类别标签，绝不回显内容。"""
    stripped = text.strip()
    if not stripped:
        return "blank"
    if _URL_RE.search(stripped):
        return "url"
    if _EMAIL_RE.match(stripped):
        return "email"
    if _PHONE_RE.match(stripped):
        return "phone"
    if _IDCARD_RE.match(stripped):
        return "idcard"
    if _DATE_RE.match(stripped):
        return "datetime" if _TIME_RE.search(stripped) else "date"
    if _TIME_RE.match(stripped):
        return "time"
    if _DIGITS_RE.match(stripped):
        # 10 位秒级 / 13 位毫秒级时间戳
        return "timestamp" if len(stripped) in (10, 13) else "digits"
    if _ALPHA_RE.match(stripped):
        return "alpha"
    if _ALNUM_RE.match(stripped):
        return "alnum"
    if _CJK_RE.match(stripped):
        return "cjk"
    if _CJK_ANY_RE.search(stripped):
        return "cjk_mixed"
    return "text"


def _shape_str(text: str) -> str:
    """字符串 -> `<str:长度:类别>`。不保留任何原始字符（含标点）。"""
    return f"<str:{len(text)}:{_classify_str(text)}>"


def _shape_num(value: Any) -> str:
    if isinstance(value, bool):
        return "<bool>"
    if isinstance(value, int):
        return f"<num:int:{len(str(abs(value)))}位>"
    if isinstance(value, float):
        text = repr(value)
        dp = len(text.split(".")[1]) if "." in text else 0
        return f"<num:{dp}dp:{len(str(int(abs(value))))}位>"
    return f"<num:{type(value).__name__}>"


def redact(obj: Any, depth: int = 0, max_depth: int = MAX_DEPTH) -> Any:
    """递归压成骨架。"""
    if obj is None or isinstance(obj, bool):
        return obj
    if isinstance(obj, (int, float)):
        return _shape_num(obj)
    if isinstance(obj, str):
        return _shape_str(obj)
    if depth >= max_depth:
        return f"<{type(obj).__name__}:depth_limit>"
    if isinstance(obj, dict):
        return {str(k): redact(v, depth + 1, max_depth) for k, v in obj.items()}
    if isinstance(obj, list):
        head = [redact(v, depth + 1, max_depth) for v in obj[:MAX_LIST_ITEMS]]
        if len(obj) > MAX_LIST_ITEMS:
            head.append(f"<...共{len(obj)}项>")
        return head
    return f"<{type(obj).__name__}>"


# ------------------------------------------------------------------ 字段索引

def _norm_key(key: str) -> str:
    """把 userName / user_name / UserName 统一成 username 便于匹配。"""
    return re.sub(r"[_\-\s]", "", key).lower()


def build_field_index(obj: Any, prefix: str = "",
                      index: Optional[Dict[str, Dict[str, Any]]] = None,
                      depth: int = 0) -> Dict[str, Dict[str, Any]]:
    """收集所有字段路径及其类型分布。"""
    if index is None:
        index = {}
    if depth >= MAX_DEPTH:
        return index
    if isinstance(obj, dict):
        for key, value in obj.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            slot = index.setdefault(
                path, {"types": set(), "count": 0, "leaf": True, "samples": []}
            )
            slot["count"] += 1
            if isinstance(value, (dict, list)):
                slot["leaf"] = False
                slot["types"].add(type(value).__name__)
                build_field_index(value, path, index, depth + 1)
            else:
                slot["types"].add(type(value).__name__)
                if len(slot["samples"]) < 3:
                    slot["samples"].append(redact(value))
    elif isinstance(obj, list):
        for item in obj[:MAX_LIST_ITEMS]:
            build_field_index(item, f"{prefix}[]", index, depth + 1)
    return index


def _freeze_index(index: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for path, slot in sorted(index.items()):
        rows.append({
            "path": path,
            "types": sorted(slot["types"]),
            "count": slot["count"],
            "leaf": slot["leaf"],
            "samples": slot["samples"],
        })
    return rows


# ------------------------------------------------------------------ 映射建议

_CONTAINER_HINTS = ("item", "goods", "product", "detail", "meal", "dish", "sku", "line")


def suggest_mappings(index: Dict[str, Dict[str, Any]]) -> Dict[str, List[str]]:
    """根据字段名与值形状，猜出 collector 该用哪些字段。

    判定顺序：
        1. 先找出明细容器（items / goods / details 之类的数组）
        2. 容器内的字段只归类为 item_name / item_qty / item_price
        3. 容器外的字段才归类为 order_id / order_time / order_amount / store
        4. 一个字段只归入第一个命中的类别，避免订单号同时被当成时间
    """
    hints: Dict[str, List[str]] = {
        "order_id": [], "order_time": [], "order_amount": [],
        "store": [], "items_container": [], "item_name": [],
        "item_qty": [], "item_price": [],
    }

    containers: List[str] = []
    for path, slot in index.items():
        if slot["leaf"]:
            continue
        if any(h in _norm_key(path.split(".")[-1].replace("[]", ""))
               for h in _CONTAINER_HINTS):
            containers.append(path)
    hints["items_container"] = sorted(containers)

    def in_container(path: str) -> bool:
        return any(path.startswith(c) for c in containers)

    def push(target: str, path: str) -> None:
        if path not in hints[target]:
            hints[target].append(path)

    for path, slot in index.items():
        if not slot["leaf"]:
            continue
        key = path.split(".")[-1].replace("[]", "")
        norm = _norm_key(key)
        types = set(slot["types"])
        shape = slot["samples"][0] if slot["samples"] else ""

        # 明细内部的字段
        if in_container(path):
            for target in ("item_name", "item_qty", "item_price"):
                keywords = dict(
                    (t, k) for t, k in _SEMANTIC if t == target
                ).get(target, ())
                if not keywords or not any(kw in norm for kw in keywords):
                    continue
                if target == "item_name" and "str" not in types:
                    continue
                if target in ("item_qty", "item_price") and not ({"int", "float"} & types):
                    continue
                push(target, path)
                break
            continue

        # 整单字段
        for target in ("order_id", "order_time", "order_amount", "store"):
            keywords = dict((t, k) for t, k in _SEMANTIC if t == target)[target]
            if not any(kw in norm for kw in keywords):
                continue
            if target == "order_amount" and not ({"int", "float"} & types):
                continue
            if target in ("order_id", "order_time", "store") and "str" not in types:
                continue
            push(target, path)
            break
        else:
            # 兜底：字段名无关键词，但类别明显是日期
            if isinstance(shape, str) and (":datetime>" in shape or ":date>" in shape):
                push("order_time", path)

    return hints


# ------------------------------------------------------------------ 导出

def build_share(out_dir: Path) -> Dict[str, Any]:
    """汇总输出目录里的诊断与原始数据，产出可公开的结构报告。"""
    out_dir = Path(out_dir)
    payload: Dict[str, Any] = {
        "note": "本文件由 mcdtm share 生成：值已替换为类型与长度，字段名保留。已自动扫描手机号/邮箱/身份证/URL/长串凭证，但无法穷举所有未知字段，公开前请人工检查",
        "generated_from": str(out_dir),
        "sections": {},
    }

    doctor_path = out_dir / "doctor.json"
    if doctor_path.exists():
        try:
            doctor = json.loads(doctor_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            doctor = {}
        section: Dict[str, Any] = {
            "endpoint": doctor.get("endpoint"),
            "server": doctor.get("server"),
            "protocol_version": doctor.get("protocol_version"),
            "tools_count": doctor.get("tools_count"),
            "tools": doctor.get("tools"),
            "target_tool": doctor.get("target_tool"),
            "target_found": doctor.get("target_found"),
            "input_schema": doctor.get("input_schema"),
            "call_ok": doctor.get("call_ok"),
            "call_args": doctor.get("call_args"),
            "response_type": doctor.get("response_type"),
        }
        if doctor.get("call_error"):
            section["call_error"] = _scrub_text(str(doctor["call_error"]))[:600]
        if doctor.get("sample"):
            section["sample_redacted"] = redact(_maybe_json(doctor["sample"]))
            index = build_field_index(_maybe_json(doctor["sample"]))
            section["fields"] = _freeze_index(index)
            section["suggested_mappings"] = suggest_mappings(index)
        payload["sections"]["doctor"] = section

    raw_dir = out_dir / "raw"
    if raw_dir.is_dir():
        raw_sections: Dict[str, Any] = {}
        for path in sorted(raw_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            index = build_field_index(data)
            raw_sections[path.stem] = {
                "redacted": redact(data),
                "field_count": len(index),
                "fields": _freeze_index(index)[:120],
                "suggested_mappings": suggest_mappings(index),
            }
        payload["sections"]["raw"] = raw_sections

    return payload


def _maybe_json(text: Any) -> Any:
    if isinstance(text, str):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return text


# ------------------------------------------------------------------ 后置 PII 扫描

# 最后一道防线：无论前面怎么脱敏，落盘前把整份产物再扫一遍。
# 顺序不能随意调整——先处理结构化强的（邮箱/URL），再处理宽泛的（长串/数字）。
_PII_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+"), r"\1<redacted>"),
    (re.compile(r"https?://[^\s\"'<>\\]+"), "<redacted:url>"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<redacted:email>"),
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "<redacted:phone>"),
    (re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "<redacted:idcard>"),
    (re.compile(r"(?<!\d)\d{16,19}(?!\d)"), "<redacted:cardnum>"),
    (re.compile(r"\b[A-Za-z0-9_\-]{24,}\b"), "<redacted:token_like>"),
    (re.compile(r"(?<![\d<>])\d{6,}(?![\d>])"), "<redacted:longnum>"),
]


def scan_pii(text: str) -> Tuple[str, List[str]]:
    """扫描并替换残留的个人敏感信息，返回 (清洗后文本, 命中的类型列表)。"""
    hits: List[str] = []
    for pattern, repl in _PII_PATTERNS:
        new_text, count = pattern.subn(repl, text)
        if count:
            hits.append(f"{repl.strip('<>')}x{count}")
        text = new_text
    return text, hits


def _scrub_text(text: str) -> str:
    """错误信息里可能夹带 Token / 订单号 / URL，做保守替换并截断。"""
    text, _ = scan_pii(text)
    return text[:400]


def write_share(out_dir: Path) -> Path:
    """生成 mcdtm-output/share.json 并返回路径。

    落盘前对整份产物再做一次 PII 扫描，把命中类型记进 `pii_scan`，
    便于使用者判断「是不是还有需要人工检查的地方」。
    """
    out_dir = Path(out_dir)
    payload = build_share(out_dir)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    text, hits = scan_pii(text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        # 理论上不会发生（只替换值内容），真发生了就退化为纯文本说明
        payload = {"error": "脱敏产物异常，请勿分享", "raw_length": len(text)}
    payload["pii_scan"] = {
        "performed": True,
        "hits": hits or [],
        "advice": (
            "已自动替换疑似手机号/邮箱/身份证/卡号/URL/长串凭证。"
            "由于无法穷举所有字段，公开前仍建议你人工通读一遍。"
        ),
    }
    target = out_dir / "share.json"
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return target


__all__ = [
    "build_share", "write_share", "redact",
    "build_field_index", "suggest_mappings",
]
