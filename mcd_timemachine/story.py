"""V2.0 沉浸式故事页：六章节滚动叙事 + 隐私友好竖版分享卡片。

与 reporter.py 的分工
--------------------
- `reporter.py` 负责「完整、可核对」的静态报告（report.md / report.html）。
- 本模块负责「好看、可体验」的故事页（story.html），两者数据同源，互不替代。

模板与资源
----------
HTML 骨架、CSS、JS 分别放在 `assets/story.html`、`assets/story.css`、`assets/story.js`，
由本模块读取后注入数据，产出一个**离线可双击打开**的单文件 HTML。

降级策略
--------
章节正文由 Python 直接渲染进 HTML，JS 只做动效增强。因此：
- 禁用 JS / JS 报错 / 老浏览器：六章内容依然完整可读；
- 系统开启「减少动态效果」：不做渐显与位移；
- 分享卡片需要 Canvas，不可用时页面其余部分不受影响。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .reporter import (
    SOURCE_LABEL,
    _coverage_lines,
    _shift_basis_text,
    _spend_note,
    _spend_title,
)

CARD_WIDTH = 1080
CARD_HEIGHT = 1920

# 生成模式
# - personal：个人完整报告，保留全部数据，页面上另有「隐私浏览」开关做临时遮挡
# - public  ：公开分享版，金额 / 门店名 / 到日日期在**生成阶段**就不写入 HTML
#             （不是靠 CSS 遮挡，文件里根本没有这些值）
PRIVACY_MODES = ("personal", "public")
_PUBLIC_PLACEHOLDER = "公开版未写入"
_REDACT_MONEY = "金额已隐藏"
_REDACT_DAY = "日期已隐藏"
_REDACT_STORE = "门店已隐藏"

# 分享卡片字段：默认全部隐藏，由用户在页面上主动勾选
PRIVACY_DEFAULTS = {"amount": False, "store": False, "date": False, "generated": False}

# ---- 脱敏登记表
# 第六轮审查指出：只在渲染完成后用正则扫 HTML，规则依赖具体文本形态，
# 将来新增一个不带 ¥ 的金额字段、或一个不在门店名单里的地址，就可能漏出去。
# 因此把判据前移到「字段」，并分成三层（见下方「生成期脱敏」一节）。
#
# 字段名里出现这些词（按 snake_case / 驼峰切分后比对单词，中文按包含判断）
# 即整字段置占位符。
_SENSITIVE_WORDS = frozenset({
    "addr", "address", "street", "detail", "detailaddress", "position", "location",
    "id", "no", "code", "sn", "orderid", "orderno", "ordercode", "ordersn",
    "phone", "mobile", "tel", "telephone", "contact",
    "lat", "lng", "lon", "latitude", "longitude", "geo", "gps", "ip",
    "openid", "unionid", "userid", "uid", "member", "memberid", "cardno",
    "invoice", "taxno", "transaction", "paymentid", "imei", "idfa", "deviceid",
    "password", "passwd", "secret", "token", "credential", "cookie", "session",
    "receiver", "recipient", "consignee", "realname", "username", "fullname",
    "nickname", "email", "mail", "wechat", "idcard", "certno", "bankcard",
    "idno", "plateno", "carno",
})
_SENSITIVE_CJK = ("地址", "住址", "收货", "收件", "送达", "寄送", "电话", "手机",
                  "号码", "会员", "账号", "密码", "编号", "单号", "支付", "发票",
                  "坐标", "定位", "经纬", "身份证", "证件", "银行卡", "邮箱",
                  "设备", "姓名", "昵称", "微信", "买家")
# 长词按「包含」判定：addr2 / mobile1 / addressLine1 / phoneNumber 这类
# 带后缀或连写的字段名，只按整词切分是漏的。短词（id / no / sn / tel / geo…）
# 仍只按整词匹配，避免误伤。
_SENSITIVE_PREFIX_WORDS = frozenset(w for w in _SENSITIVE_WORDS if len(w) >= 5)

# 公开版字段策略：**显式**列出公开版允许展示的数值字段。
#
# 这是「允许清单」而不是「禁止清单」：没登记的一律按金额抹掉（fail-closed）。
# 好处是新增金额字段无需再改规则，代价是新增计数类字段会被
# tests/test_story.py::TestStructuredRedaction 逼着来这里登记一次——
# 而这一次判断正是防止「隐私保护过度、把可读的统计也抹了」的闸门。
PUBLIC_FIELD_POLICY = {
    # 计数：保留原值，趋势图 / 条形图 / 关键数字全靠它们
    "counts": (
        "total_orders", "total_items", "span_days", "best_streak",
        "qty", "count", "orders", "items", "pages", "raw_records",
        "unique_orders", "duplicates_removed", "early_orders", "late_orders",
        "with_date", "with_amount", "with_items", "with_store",
        "actual_amount_orders", "estimated_amount_orders", "unknown_amount_orders",
        "orders_with_amount", "orders_with_date", "orders_with_items",
        "truncated",
    ),
    # 百分比 / 评分 / 可信度：保留原值
    "ratios": ("pct", "delta", "early", "late", "score", "confidence"),
}
_SAFE_NUMBER_KEYS = frozenset(
    key for group in PUBLIC_FIELD_POLICY.values() for key in group
)

# 字符串形态的金额字段（key 命中即整值置「金额已隐藏」）
_MONEY_WORDS = frozenset({"spend", "amount", "price", "money", "cost", "fee",
                          "paid", "payment", "pay", "outlay", "budget",
                          "expense", "expenditure", "refund", "discount",
                          "rebate", "subtotal", "balance"})
_MONEY_CJK = ("金额", "价格", "消费", "客单", "实付", "花费", "总价", "预算",
              "费用", "退款", "折扣", "余额")
# 名字里含金额词、但装的是计数或口径枚举，不能当金额抹掉
_MONEY_WORDS_EXCEPT = frozenset({
    "spend_basis", "amount_source", "actual_amount_orders",
    "estimated_amount_orders", "unknown_amount_orders",
    "orders_with_amount", "with_amount",
})
# 这些后缀说明装的是计数 / 比例 / 口径，不是金额（refund_count / spend_pct …），
# 避免「名字里有金额词就一律抹掉」把可读的统计也抹了。
_NON_MONEY_SUFFIX = ("_pct", "_rate", "_ratio", "_count", "_basis", "_source", "_label")
# 数值字段上更窄的一条：这几个后缀在语义上不可能是金额
_NON_MONEY_NUMBER_SUFFIX = ("_pct", "_rate", "_ratio", "_count")

_FIELD_ORDER = ("amount", "store", "date", "generated")
_FIELD_LABELS = {
    "amount": "消费金额",
    "store": "门店名称",
    "date": "消费日期",
    "generated": "生成时间",
}
_HIDDEN_TEXT = "已隐藏"

CHAPTER_TITLES = [
    "时光机启动",
    "故事的起点",
    "我的麦麦日常",
    "口味人格测试结果",
    "这一年的变化",
    "年度终章",
]


# ---------------------------------------------------------------- 资源读取

def _asset(name: str) -> str:
    """读取 assets/ 下的模板资源；importlib 不可用时回退到文件路径。"""
    try:
        from importlib.resources import files  # Python 3.9+

        return files(__package__).joinpath("assets", name).read_text(encoding="utf-8")
    except Exception:  # pragma: no cover - 仅极老环境会走到
        return (Path(__file__).parent / "assets" / name).read_text(encoding="utf-8")


def _e(text: Any) -> str:
    return (
        str(text if text is not None else "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _attr(text: Any) -> str:
    """属性值专用转义：本模块的片段统一用单引号包属性，必须再挡住单引号。

    `month` 取自订单原始日期的前 7 个字符，理论上可被上游数据污染，
    因此不能只依赖 `_e()`（它不转义 `'`）。
    """
    return _e(text).replace("'", "&#39;")


def _json_for_html(data: Any) -> str:
    """把数据塞进 <script type="application/json">，必须打断 `</script>` 逃逸。"""
    return (
        json.dumps(data, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


# ---------------------------------------------------------------- 数据载荷

def _top_store(profile: Dict[str, Any]) -> str:
    for store in profile.get("stores") or []:
        name = store.get("name")
        if name and name != "未知门店":
            return f"{name} · {store.get('count', 0)} 次"
    return ""


def build_story_payload(profile: Dict[str, Any], privacy: str = "personal") -> Dict[str, Any]:
    """故事页与分享卡片共用的最小数据集。

    只放展示必需字段；金额 / 门店 / 日期是否出现，取决于用户在页面上的勾选。

    `privacy="public"` 时（公开分享版）三项敏感字段的值在**生成期**就被替换成占位符，
    并标记 `available=False`：文件里根本没有原值，页面上的勾选框也会置灰。
    """
    public = privacy == "public"
    cov = profile.get("coverage") or {}
    summary = profile.get("summary") or {}
    tags = profile.get("tags") or []
    keywords = [k.get("word", "") for k in (profile.get("keywords") or []) if k.get("word")]
    range_text = f"{cov.get('range_start') or '—'} ~ {cov.get('range_end') or '—'}"
    store_text = _top_store(profile)
    generated_at = profile.get("generated_at", "")

    if public:
        # 公开版：原值压根没进文件（profile 已过 redact_profile），
        # 这里给一份确定的占位文案，页面上的字段行才不会出现空白
        raw_values = {k: _PUBLIC_PLACEHOLDER for k in _FIELD_ORDER}
    else:
        # 金额只在个人版里格式化：公开版的 display_spend 是字符串占位符
        amount_value = f"{_spend_title(summary)} ¥{(summary.get('display_spend') or 0):,.2f}"
        if summary.get("spend_basis", "none") == "none":
            amount_value = "暂无金额数据"
        raw_values = {
            "amount": amount_value,
            "store": store_text or "暂无门店记录",
            "date": range_text,
            "generated": generated_at or "—",
        }

    fields: Dict[str, Dict[str, Any]] = {
        key: {
            "label": _FIELD_LABELS[key],
            "value": raw_values[key],
            "hidden_text": _HIDDEN_TEXT,
            "available": not public,
        }
        for key in _FIELD_ORDER
    }

    return {
        "version": __version__,
        "title": "麦麦时光机 · 我的年度回忆",
        "persona": tags[0]["tag"] if tags else "口味待探索",
        "persona_desc": (tags[0].get("desc") or "") if tags else "",
        "keywords": keywords[:3],
        "source_label": SOURCE_LABEL.get(cov.get("source", "unknown"), "来源未知"),
        "is_demo": cov.get("source") == "demo",
        "privacy_mode": privacy,
        "redacted": public,
        "range_text": _PUBLIC_PLACEHOLDER if public else range_text,
        "generated_at": "" if public else generated_at,
        # 默认一律隐藏，用户勾选后才写入卡片
        "privacy": dict(PRIVACY_DEFAULTS),
        "fields": fields,
    }


# ---------------------------------------------------------------- 片段渲染

def _pv(text: Any, mask: str = _HIDDEN_TEXT) -> str:
    """隐私字段：页面默认显示，「隐私浏览」开关打开后才就地打码。"""
    return f"<span class='pv' data-mask='{_attr(mask)}'>{_e(text)}</span>"


_PV_MONEY_RE = re.compile(r"[¥￥][\d,]+(?:\.\d+)?")
_PV_DATE_RE = re.compile(r"\d{4}-\d{2}(?:-\d{2})?")

# 同一类信息的「其它常见写法」。既用于公开版的抹除（见下方 _VALUE_RULES），
# 也用于个人版「隐私浏览」的临时打码（_mask_text）——两边共用，避免只在
# 某一种写法上生效：审查提出的边界场景（英文日期、小数金额、全角符号）就属于这里。
_ALT_DATE_RES = (
    re.compile(r"(?<!\d)\d{4}[/.]\d{1,2}[/.]\d{1,2}(?!\d)"),           # 2026/06/27、2026.6.27
    re.compile(r"(?<!\d)\d{1,2}[/-]\d{1,2}[/-]\d{4}(?!\d)"),           # 27/06/2026、06-27-2026
    re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
               r"[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\b"),     # June 27, 2026
)
_ALT_MONEY_RES = (
    # RMB / CNY 前缀写法（不带 ¥ 符号也要认）
    re.compile(r"\b(?:RMB|CNY|rmb|cny)\s*\d[\d,]*(?:\.\d{1,2})?"),
)


def _mask_text(text: Any, extra: str = "") -> str:
    """给正文里的金额与日期打上隐私标记。

    年度回忆的文案里散落着「2026-06-27 花了 ¥129.00」这类信息，
    只标记 stats 是不够的；这里统一补标（含全角 ￥ / RMB 前缀 / 英文日期等写法），
    保证「隐私浏览」打开后全页不留具体数值。
    """
    out = _e(text)
    if extra:
        out = out.replace(_e(extra), _pv(extra), 1)
    for pattern in (_PV_MONEY_RE,) + _ALT_MONEY_RES:
        out = pattern.sub(lambda m: _pv(m.group(0)), out)
    for pattern in (_PV_DATE_RE,) + _ALT_DATE_RES:
        out = pattern.sub(lambda m: _pv(m.group(0)), out)
    return out


# ---------------------------------------------------------------- 生成期脱敏
#
# 三层，逐层收紧；任一层单独失效都不会直接泄漏（第六轮审查 P1）：
#
#   第一层 结构化（redact_profile）：在渲染**之前**把「用于渲染的数据」里的敏感
#          字段换成占位符。判据是字段名（地址 / 订单号 / 电话 / 定位 / 账号…）
#          与字段类型（数值默认按金额、日期按日粒度），所以新增同类字段会被
#          自动覆盖，不再依赖「金额一定写成 ¥123.00」这种文本假设。
#   第二层 值扫描（redact_html）：对渲染完成的 HTML（含内联 JSON 数据块）
#          再扫一遍已知形态，兜住渲染层自己拼出来的字符串。
#   第三层 自检（scrub_leaks）：从**原 profile** 反推敏感原始值，拿去比对成品
#          HTML，命中即替换；同一套函数供测试断言「零残留」。

_REDACT_ID = "编号已隐藏"
_REDACT_ADDR = "地址已隐藏"
_REDACT_FIELD = "已隐藏"

_PV_DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# 「地址：…」这类自由文本里的地址
_ADDRESS_RE = re.compile(r"(?:收货|送达|配送|寄送)?地址\s*[：:]\s*[^<>，。；;、\n]{2,40}")
# 没有「地址：」标签也认得出的中文地址（省/市 + 区/县 + 街道门牌）
_CN_ADDRESS_RE = re.compile(
    r"[\u4e00-\u9fa5]{2,6}(?:省|市|自治区)[\u4e00-\u9fa5]{2,8}(?:区|县|市|镇)"
    r"[\u4e00-\u9fa5A-Za-z0-9\-\s]{0,24}(?:路|街|道|巷|弄|号|栋|幢|楼|室|苑|园|小区|大厦|广场)")
# 订单号形态：字母前缀 + 长数字（如 DEMO202606270001）
_ORDER_ID_RE = re.compile(r"(?<![0-9A-Za-z])[A-Z]{2,}[0-9]{6,}(?![0-9A-Za-z])")
# 8 位以上纯数字（订单号 / 手机号 / 身份证等）
_LONG_DIGITS_RE = re.compile(r"(?<![0-9])\d{8,}(?![0-9])")
# 带「元 / 块」后缀的金额（没有 ¥ 符号也要抹）
_CNY_TEXT_RE = re.compile(r"\d[\d,]*(?:\.\d{1,2})?\s*(?:元|块钱|块)")
# 裸数值形态（金额的各种格式化写法会落在这里，仅用于给报错分类）
_BARE_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?%?")

# 值级规则：数据层与 HTML 层共用同一套，避免两边规则漂移。
# 注意：这些正则会被 sensitive_tokens() 用 findall 抽候选值，因此**不能有捕获组**。
_VALUE_RULES = (
    (_PV_MONEY_RE, _REDACT_MONEY),
    (_PV_DAY_RE, _REDACT_DAY),
    (_ADDRESS_RE, _REDACT_ADDR),
    (_CN_ADDRESS_RE, _REDACT_ADDR),
    (_ORDER_ID_RE, _REDACT_ID),
    (_LONG_DIGITS_RE, _REDACT_ID),
    (_CNY_TEXT_RE, _REDACT_MONEY),
) + tuple((p, _REDACT_MONEY) for p in _ALT_MONEY_RES) \
  + tuple((p, _REDACT_DAY) for p in _ALT_DATE_RES)

_KEY_SPLIT_RE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _key_words(key: Any) -> frozenset:
    """把字段名切成小写单词：`orderId` / `order_id` / `order-id` 都切成 order + id。

    同时剥掉单词尾部的数字：`addr2` → `addr`、`phone1` → `phone`。
    带序号后缀的字段名（addr1/addr2、address_line2）在真实接口里很常见，
    只看整词会把它们整片漏掉。
    """
    raw = _CAMEL_RE.sub("_", str(key))
    words = set()
    for word in _KEY_SPLIT_RE.split(raw):
        if not word:
            continue
        word = word.lower()
        words.add(word)
        stripped = word.rstrip("0123456789")
        if stripped and stripped != word:
            words.add(stripped)
    return frozenset(words)


def _flat_key(key: Any) -> str:
    """压平字段名：`addressLine1` → `addressline1`、`收货地址` → ``（中文走另一条判据）。"""
    return re.sub(r"[^0-9a-z]", "", str(key).lower())


def is_sensitive_key(key: Any) -> bool:
    """字段名是否属于「一律不写入公开版」的敏感字段（地址 / 编号 / 联系方式…）。

    三条判据，逐层放宽：
    1. 中文敏感词按包含判断（收货信息 / 身份证号）；
    2. 切分后的整词命中（orderId → order + id）；
    3. **较长**敏感词按包含判断（addressLine1 / phoneNumber / mobile1），
       短词（id / no / sn / tel / geo）不参与，避免误伤。
    """
    text = str(key)
    if any(part in text for part in _SENSITIVE_CJK):
        return True
    if _key_words(text) & _SENSITIVE_WORDS:
        return True
    flat = _flat_key(text)
    return bool(flat) and any(word in flat for word in _SENSITIVE_PREFIX_WORDS)


def is_safe_number_key(key: Any) -> bool:
    """数值字段是否允许原样出现在公开版。

    两条判据（满足其一即可）：
    1. 显式登记在 `PUBLIC_FIELD_POLICY` 里 —— 这是主判据；
    2. 字段名带明确的「非金额」后缀（`_pct` / `_rate` / `_ratio` / `_count`）——
       这是本项目自己的命名约定，语义上不可能是金额，
       用来避免「新增一个退款次数也要报错」这种过度脱敏。

    两者都不满足的数值一律按金额抹掉（fail-closed）。
    """
    text = str(key)
    if text in _SAFE_NUMBER_KEYS:
        return True
    return text.lower().endswith(_NON_MONEY_NUMBER_SUFFIX)


def is_money_key(key: Any) -> bool:
    """字符串形态的金额字段判定（数值字段走 `is_safe_number_key` 的允许清单）。"""
    text = str(key)
    if text in _MONEY_WORDS_EXCEPT:
        return False
    low = text.lower()
    if low.endswith(_NON_MONEY_SUFFIX):
        return False
    if any(part in text for part in _MONEY_CJK):
        return True
    return bool(_key_words(text) & _MONEY_WORDS)


def _scrub_text(text: Any, names: Optional[List[str]] = None) -> str:
    """抹掉一段文本里的门店名、金额、到日日期、地址与编号形态。

    数据层（自由文案）与 HTML 层共用：文本与已转义文本都替换，
    因此既能处理原始字符串，也能处理 HTML 片段。
    """
    out = str(text if text is not None else "")
    if not out:
        return out
    for name in sorted((str(n) for n in (names or []) if n), key=len, reverse=True):
        out = out.replace(_e(name), _REDACT_STORE).replace(name, _REDACT_STORE)
    for pattern, repl in _VALUE_RULES:
        out = pattern.sub(repl, out)
    return out


def store_names(profile: Dict[str, Any]) -> List[str]:
    return [s.get("name") for s in (profile.get("stores") or []) if s.get("name")]


def _redact_node(node: Any, names: List[str]) -> Any:
    if isinstance(node, dict):
        out: Dict[Any, Any] = {}
        for key, value in node.items():
            name = str(key)
            if name == "generated_at":
                # 生成时间：公开版连字段值都不留（页面另有开关，默认也不入图）
                out[key] = ""
            elif is_sensitive_key(name):
                out[key] = _REDACT_FIELD
            elif isinstance(value, (dict, list)):
                out[key] = _redact_node(value, names)
            elif isinstance(value, bool) or value is None:
                out[key] = value
            elif isinstance(value, (int, float)):
                # 允许清单之外一律按金额处理：新增金额字段无需再改规则
                out[key] = value if is_safe_number_key(name) else _REDACT_MONEY
            elif is_money_key(name):
                out[key] = _REDACT_MONEY
            else:
                out[key] = _scrub_text(value, names)
        return out
    if isinstance(node, list):
        return [_redact_node(item, names) for item in node]
    return _scrub_text(node, names)


def redact_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    """公开分享版：**在渲染之前**把敏感值从数据里拿掉，返回深拷贝（不改入参）。

    这是第一道防线。渲染层拿到的就已经是脱敏数据，所以不存在
    「渲染时忘了标隐私字段」这类遗漏；渲染完成后仍会再扫一遍 HTML 兜底。
    """
    out = _redact_node(profile, store_names(profile))
    # 年份用于标题「你的 2026 麦麦年度回忆」，本身不敏感，
    # 但 range_end 已被抹掉，这里先把年份单独留下来。
    cov = profile.get("coverage") or {}
    summary = profile.get("summary") or {}
    end = str(cov.get("range_end") or summary.get("last_date") or "")
    out["story_year"] = end[:4] if len(end) >= 4 and end[:4].isdigit() else ""
    return out


def redact_html(html: str, names: Optional[List[str]] = None) -> str:
    """第二道防线：对渲染完成的 HTML 再扫一遍已知形态，含内联 JSON 数据块。

    第一道防线是 `redact_profile()`（渲染前按字段脱敏）。这一层保留，
    是为了挡住「渲染层自己拼出来的字符串」——正文里的金额文案、月份 tooltip、
    覆盖率说明等。两层共用 `_VALUE_RULES`，规则不会漂移。
    """
    return _scrub_text(html, names or [])


def _iter_scalars(node: Any, key: str = ""):
    if isinstance(node, dict):
        for k, v in node.items():
            for item in _iter_scalars(v, str(k)):
                yield item
    elif isinstance(node, list):
        for v in node:
            for item in _iter_scalars(v, key):
                yield item
    else:
        yield key, node


def sensitive_tokens(profile: Dict[str, Any]) -> List[str]:
    """从**原 profile** 反推所有敏感原始值，供第三层自检与测试使用。

    金额会展开成渲染时可能出现的各种格式化形态（`1,234.50` / `1234.50` /
    `¥1,234.50`），因此「金额恰好写成什么格式」不再是漏检的理由。
    过短的串（< 5 字符，如 `0` / `12`）不参与比对，避免误伤无关文本。
    """
    tokens: set = set()
    for key, value in _iter_scalars(profile):
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and not is_safe_number_key(key):
            # 渲染时可能出现的形式都算上：带千分位、带小数、以及带货币符号的
            variants = ("{:,.2f}", "{:.2f}", "{:,.1f}", "{:.1f}", "{:,}", "{}")
            for fmt in variants:
                try:
                    text = fmt.format(value)
                except (ValueError, TypeError):  # pragma: no cover - 数值格式化异常
                    continue
                tokens.add(text)
                tokens.add("¥" + text)
            continue
        if isinstance(value, str):
            for pattern, _repl in _VALUE_RULES:
                tokens.update(pattern.findall(value))
    for name in store_names(profile):
        tokens.add(str(name))
    return sorted(t for t in tokens if len(t) >= 5)


def scan_leaks(html: str, profile: Dict[str, Any]) -> List[str]:
    """第三层自检：成品 HTML 里是否还能找到原 profile 的敏感原始值。"""
    return [t for t in sensitive_tokens(profile) if t in html]


def _token_kind(token: str, profile: Dict[str, Any]) -> str:
    text = str(token)
    for name in store_names(profile):
        if text == str(name):
            return "门店名"
    if any(p.fullmatch(text) for p in (_PV_MONEY_RE, _CNY_TEXT_RE) + _ALT_MONEY_RES):
        return "金额"
    if any(p.fullmatch(text) for p in (_PV_DAY_RE,) + _ALT_DATE_RES):
        return "日期"
    if _ORDER_ID_RE.fullmatch(text) or _LONG_DIGITS_RE.fullmatch(text):
        return "编号/手机号"
    if _CN_ADDRESS_RE.fullmatch(text) or _ADDRESS_RE.fullmatch(text):
        return "地址"
    if _BARE_NUMBER_RE.fullmatch(text):
        return "数值（可能为金额）"
    return "其它"


def _mask_token(token: Any) -> str:
    """报错信息里的值一律打码：**不能一边报泄漏、一边把敏感值又打到终端上**。"""
    text = str(token)
    if len(text) <= 2:
        return "·" * len(text)
    return text[:1] + "·" * (len(text) - 2) + text[-1:]


class PrivacyLeakError(RuntimeError):
    """公开版脱敏自检未通过 —— **拒绝输出**，而不是就地替换后继续写。

    这是 fail-closed 策略：如果自检发现残留却只是悄悄替换，那么将来新增字段
    或某条规则被改坏时，程序会把漏洞「遮住」而不报错，谁也不会发现。
    宁可这一步失败、不产出文件，也不要产出一份看着干净、其实没脱干净的文件。
    """

    def __init__(self, entries: List[Any], where: str = "story.html"):
        self.entries = [(str(kind), str(masked), int(size))
                        for kind, masked, size in entries]
        self.where = where
        super().__init__(
            f"{where} 仍未通过脱敏自检：{len(self.entries)} 处疑似敏感值残留，已拒绝写出文件")

    def kinds(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for kind, _masked, _size in self.entries:
            out[kind] = out.get(kind, 0) + 1
        return out

    def report(self, limit: int = 8) -> List[str]:
        """给终端看的诊断行（值已打码）。"""
        summary = "、".join(f"{k} × {v}" for k, v in self.kinds().items())
        lines = [f"命中种类：{summary}", "命中样本（已打码）："]
        for kind, masked, size in self.entries[:limit]:
            lines.append(f"  - {kind} · {size} 字符 · {masked}")
        if len(self.entries) > limit:
            lines.append(f"  … 其余 {len(self.entries) - limit} 处略")
        return lines


def verify_no_leaks(html: str, profile: Dict[str, Any],
                    where: str = "story.html") -> List[str]:
    """第三层：最终检查（不再是兜底替换）。

    检出残留即抛 `PrivacyLeakError`。返回空列表表示通过。
    """
    hits = scan_leaks(html, profile)
    if hits:
        raise PrivacyLeakError(
            [(_token_kind(t, profile), _mask_token(t), len(str(t))) for t in hits], where)
    return hits


class PublicContentError(RuntimeError):
    """公开版内容完整性检查未通过 —— 说明脱敏下手过重，把该留的内容也抹掉了。

    隐私保护不该以牺牲可读性为代价：章节、计数、趋势、人格这些与金额 /
    门店 / 日期无关的内容必须原样保留（审查第八轮 P2 的第二点）。
    """

    def __init__(self, issues: List[str], where: str = "story.html"):
        self.issues = list(issues)
        self.where = where
        super().__init__(
            f"{where} 脱敏后内容不完整：{len(self.issues)} 处缺失，已拒绝写出文件")

    def report(self, limit: int = 8) -> List[str]:
        lines = list(self.issues[:limit])
        if len(self.issues) > limit:
            lines.append(f"… 其余 {len(self.issues) - limit} 处略")
        return lines


def _section_slice(html: str, index: int) -> str:
    start = html.find(f"id='ch{index}'")
    if start < 0:
        return ""
    end = html.find("</section>", start)
    return html[start:end if end > 0 else len(html)]


def _public_shape(profile: Dict[str, Any]) -> Dict[str, Any]:
    """从**源 profile** 推出公开版应当保留的结构量。

    这里算的量全部由允许清单里的计数 / 比例推导（见 `PUBLIC_FIELD_POLICY`），
    与金额、门店名、日期无关，所以公开版必须一个不少。
    """
    s = profile.get("summary") or {}
    monthly = profile.get("monthly") or []
    tags = profile.get("tags") or []
    keywords = [k for k in (profile.get("keywords") or []) if k.get("word")]
    cats = [c for c in (profile.get("categories") or []) if (c.get("qty") or 0) > 0]
    slots = [x for x in (profile.get("slots") or []) if (x.get("count") or 0) > 0]
    weekdays = [x for x in (profile.get("weekdays") or []) if (x.get("count") or 0) > 0]
    stores = [x for x in (profile.get("stores") or [])
              if x.get("name") not in (None, "未知门店")]
    return {
        "chapters": [1, 2] if profile.get("empty") else [1, 2, 3, 4, 5, 6],
        "bars": len(slots) + len(weekdays) + min(8, len(cats)),
        "pills": (min(10, len(profile.get("top_items") or []))
                  + min(5, len(profile.get("combos") or []))),
        "persona_cards": len(tags),
        "shift_rows": len(profile.get("taste_shift") or []),
        "month_bars": len(monthly),
        "keywords": min(6, len(keywords)),
        "coverage_lines": len(_coverage_lines(profile)),
        "stores": len(stores),
        "stats": [
            (f"{s.get('total_orders', 0)} 单", "订单总数"),
            (f"{s.get('total_items', 0)} 件", "单品总数"),
            (f"{s.get('span_days', 0)} 天", "记录跨度"),
            (f"{s.get('best_streak', 0)} 天", "最长连续打卡"),
        ],
    }


def public_completeness_issues(html: str, profile: Dict[str, Any]) -> List[str]:
    """公开版是否因为「抹得太狠」丢了本该保留的内容。返回问题列表（空即通过）。"""
    want = _public_shape(profile)
    issues: List[str] = []
    expected = set(want["chapters"])

    for index in sorted(expected):
        if not _section_slice(html, index):
            issues.append(f"缺少第 {index} 章")

    hero = _section_slice(html, 1)
    for text, label in want["stats"]:
        if f"<b>{text}</b>" not in hero:
            issues.append(f"第 1 章关键数字缺失：{label} {text}")

    # 空数据时只渲染第 1、2 章，下面这些章节本就不存在 —— 按「期望章节」门控，
    # 否则会给一份正常的空报告报出一堆假缺失。
    if 3 in expected:
        ch3 = _section_slice(html, 3)
        got_bars = ch3.count("<div class='bar-head'>")
        if want["bars"] and got_bars != want["bars"]:
            issues.append(f"第 3 章条形图行数不符：应 {want['bars']}，实际 {got_bars}")
        got_pills = ch3.count("<span class='pill'>")
        if want["pills"] and got_pills != want["pills"]:
            issues.append(f"第 3 章单品/搭档胶囊数不符：应 {want['pills']}，实际 {got_pills}")

    if 4 in expected:
        ch4 = _section_slice(html, 4)
        got_persona = ch4.count("class='persona-card")
        if want["persona_cards"] and got_persona != want["persona_cards"]:
            issues.append(f"第 4 章人格卡数不符：应 {want['persona_cards']}，实际 {got_persona}")

    if 5 in expected:
        ch5 = _section_slice(html, 5)
        got_shift = ch5.count("class='shift-head'")
        if want["shift_rows"] and got_shift != want["shift_rows"]:
            issues.append(f"第 5 章口味变化行数不符：应 {want['shift_rows']}，实际 {got_shift}")
        got_bars5 = ch5.count("<div title='")
        if want["month_bars"] and got_bars5 != want["month_bars"]:
            issues.append(f"第 5 章月度趋势柱数不符：应 {want['month_bars']}，实际 {got_bars5}")
        store_fact = (f"常去门店 {want['stores']} 家" if want["stores"] else "暂无门店记录")
        if store_fact not in ch5:
            issues.append(f"第 5 章门店家数丢失（应为「{store_fact}」）")

    if 6 in expected:
        ch6 = _section_slice(html, 6)
        got_kw = ch6.count(">#")
        if want["keywords"] and got_kw < want["keywords"]:
            issues.append(f"第 6 章年度关键词缺条：应 {want['keywords']}，实际 {got_kw}")
        got_cov = ch6.count("<li><span>")
        if got_cov != want["coverage_lines"]:
            issues.append(f"第 6 章数据说明行数不符：应 {want['coverage_lines']}，实际 {got_cov}")

    return issues


def verify_public_complete(html: str, profile: Dict[str, Any],
                           where: str = "story.html") -> List[str]:
    issues = public_completeness_issues(html, profile)
    if issues:
        raise PublicContentError(issues, where)
    return issues


def _bar_rows(rows: List[Dict[str, Any]], label_key: str, value_key: str,
              unit: str, extra=None, limit: Optional[int] = None) -> str:
    items = [r for r in (rows or []) if (r.get(value_key) or 0) > 0]
    if not items:
        return ""
    if limit:
        items = items[:limit]
    top = max((r.get(value_key) or 0) for r in items) or 1
    out: List[str] = []
    for row in items:
        value = row.get(value_key) or 0
        width = int(round(value / top * 100))
        extra_text = extra(row) if extra else ""
        out.append(
            "<li><div class='bar-head'><span class='n'>"
            f"{_e(row.get(label_key) or '—')}</span>"
            f"<span class='v'>{value} {_e(unit)}{_e(extra_text)}</span></div>"
            f"<span class='track'><i class='fill' data-w='{max(width, 2)}'></i></span></li>"
        )
    return f"<ul class='bars'>{''.join(out)}</ul>"


def _month_chart(monthly: List[Dict[str, Any]]) -> str:
    if not monthly:
        return ""
    top = max((m.get("orders") or 0) for m in monthly) or 1
    cols: List[str] = []
    for m in monthly:
        ratio = (m.get("orders") or 0) / top
        height = int(6 + 90 * ratio)
        label = str(m.get("month", ""))[-2:] + "月"
        cols.append(
            f"<div title='{_attr(m.get('month'))}：{_attr(m.get('orders'))} 单'>"
            f"<i style='height:{height}px'></i><em>{_e(label)}</em></div>"
        )
    return f"<div class='month-chart'>{''.join(cols)}</div>"


# ---------------------------------------------------------------- 六章正文

def _scene(index: int, body: str, extra_class: str = "") -> str:
    title = CHAPTER_TITLES[index - 1] if 0 < index <= len(CHAPTER_TITLES) else ""
    cls = " ".join(part for part in ("scene", extra_class) if part)
    return (
        f"<section class='{cls}' id='ch{index}' data-title='{_attr(title)}'>"
        f"<div class='scene-inner'>{body}</div></section>"
    )


def _chapter_hero(profile: Dict[str, Any]) -> str:
    cov = profile.get("coverage") or {}
    s = profile.get("summary") or {}
    empty = bool(profile.get("empty"))
    source = cov.get("source", "unknown")
    badge_cls = "demo" if source == "demo" else "real"
    end = cov.get("range_end") or s.get("last_date") or ""
    # 公开版的 range_end 是占位符，年份由 redact_profile 预先留在 story_year 里
    year = profile.get("story_year")
    if year is None:
        year = end[:4] if len(end) >= 4 else ""

    stats = [
        (f"{s.get('total_orders', 0)} 单", "订单总数"),
        (f"{s.get('total_items', 0)} 件", "单品总数"),
        (f"{s.get('span_days', 0)} 天", "记录跨度"),
        (f"{s.get('best_streak', 0)} 天", "最长连续打卡"),
    ]
    stat_html = "".join(
        f"<div><b>{_e(v)}</b><span>{_e(k)}</span></div>" for v, k in stats
    )

    hero_title = f"你的 {year} 麦麦年度回忆" if year else "你的麦麦年度回忆"
    body = (
        "<p class='hero-eyebrow'>MCD TIME MACHINE</p>"
        f"<h1 class='hero-title'>{_e(hero_title)}</h1>"
        f"<p class='hero-range'>统计区间 {_pv(cov.get('range_start') or '—')} ~ "
        f"{_pv(cov.get('range_end') or '—')}</p>"
        f"<span class='badge {badge_cls}'>"
        f"{_e(SOURCE_LABEL.get(source, '来源未知'))}</span>"
        f"<span class='badge ghost'>{_e(cov.get('unique_orders', s.get('total_orders', 0)))} 单已解析</span>"
        f"<div class='hero-stats'>{stat_html}</div>"
    )
    if not empty:
        body += ("<a class='cta' href='#ch2'>进入故事</a>"
                 "<p class='hero-hint'>向下滚动即可翻页，右侧圆点可跳章；"
                 "想先核对数据可看同目录 report.html</p>")
    else:
        body += ("<a class='cta' href='#ch2'>看看发生了什么</a>")
    return _scene(1, body, "scene-hero")


def _chapter_origin(profile: Dict[str, Any]) -> str:
    memories = profile.get("memories") or []
    head = ("<p class='ch-no'>CHAPTER 02</p>"
            "<h2 class='ch-title'>故事的起点</h2>"
            "<p class='ch-sub'>从最早的一单开始，把这一年重新走一遍</p>")

    first_order = ""
    dated_first = None
    for memo in memories:
        if memo.get("title") == "记录中的第一单":
            dated_first = memo
            break
    if dated_first:
        first_order = (
            "<div class='card first-order reveal'>"
            f"<div class='date'>{_pv(dated_first.get('text', '').split('，')[0])}</div>"
            "<div class='muted' style='margin-top:0'>这是这段记录里最早的一单</div>"
            "</div>"
        )

    timeline = ""
    rest = [m for m in memories if m is not dated_first]
    if rest:
        top_store_name = _top_store(profile).split(" · ")[0]
        items = "".join(
            "<li class='reveal'><div class='t'>{title}</div>"
            "<div class='d'>{text}</div></li>".format(
                title=_e(m.get("title")),
                # 「你的主场」会把门店名写进正文，一并标记为可打码
                text=_mask_text(m.get("text"),
                                top_store_name if m.get("title") == "你的主场" else ""),
            )
            for m in rest
        )
        timeline = f"<div class='card reveal'><h3>年度回忆时间轴</h3><ul class='timeline'>{items}</ul></div>"

    if not memories:
        timeline = ("<div class='card reveal'><h3>还没有可讲的回忆</h3>"
                    "<p class='muted'>本次没有解析到带日期的订单，时间轴暂为空。</p></div>")
    return _scene(2, head + first_order + timeline)


def _chapter_daily(profile: Dict[str, Any]) -> str:
    s = profile.get("summary") or {}
    monthly = profile.get("monthly") or []
    months = len(monthly) or 1
    per_month = round((s.get("total_orders") or 0) / months, 1)

    head = ("<p class='ch-no'>CHAPTER 03</p>"
            "<h2 class='ch-title'>我的麦麦日常</h2>"
            "<p class='ch-sub'>多久吃一次、什么时候吃、常点什么</p>")

    # 公开版里 avg_order 已是占位字符串，只有个人版才做金额格式化
    avg = s.get("avg_order", 0)
    avg_text = avg if isinstance(avg, str) else "¥" + format(avg, ",.1f")
    grid = (
        "<div class='card reveal'><h3>消费节奏</h3><div class='stat-grid'>"
        f"<div><b>{per_month}</b><span>月均单量</span></div>"
        f"<div><b>{_pv(avg_text, '已隐藏')}</b>"
        f"<span>平均客单价（{_e(_spend_title(s))}）</span></div>"
        f"<div><b>{s.get('total_items', 0)}</b><span>单品总件数</span></div>"
        f"<div><b>{len(profile.get('categories') or [])}</b><span>涉及品类</span></div>"
        "</div>"
        + (f"<p class='muted'>{_e(_spend_note(s))}</p>" if _spend_note(s) else "")
        + "</div>"
    )

    slots = _bar_rows(profile.get("slots") or [], "name", "count", "单")
    slots_card = (f"<div class='card reveal'><h3>出没时段</h3>{slots}</div>"
                  if slots else "")

    week = _bar_rows(profile.get("weekdays") or [], "name", "count", "单")
    week_card = (f"<div class='card reveal'><h3>一周节奏</h3>{week}</div>" if week else "")

    cats = _bar_rows(
        profile.get("categories") or [], "name", "qty", "件",
        extra=lambda r: f" · {r.get('pct')}%", limit=8,
    )
    cats_card = (f"<div class='card reveal'><h3>品类偏好</h3>{cats}</div>" if cats else "")

    pills = "".join(
        f"<span class='pill'>{_e(i.get('name'))} <i>× {i.get('qty', 0)}</i></span>"
        for i in (profile.get("top_items") or [])[:10]
    )
    items_card = (f"<div class='card reveal'><h3>常点单品</h3><div class='pills'>{pills}</div></div>"
                  if pills else "")

    combos = "".join(
        f"<span class='pill'>{_e(c.get('pair'))} <i>{c.get('count', 0)} 次同单</i></span>"
        for c in (profile.get("combos") or [])[:5]
    )
    combo_card = (f"<div class='card reveal'><h3>黄金搭档</h3><div class='pills'>{combos}</div></div>"
                  if combos else "")

    return _scene(3, head + grid + slots_card + week_card + cats_card + items_card + combo_card)


def _chapter_persona(profile: Dict[str, Any]) -> str:
    tags = profile.get("tags") or []
    head = ("<p class='ch-no'>CHAPTER 04</p>"
            "<h2 class='ch-title'>口味人格测试结果</h2>"
            "<p class='ch-sub'>每条结论都附上可信度与数据依据，可以逐条核对</p>")

    body = ""
    for idx, tag in enumerate(tags):
        conf = int(tag.get("confidence", 0) or 0)
        cls = "persona-card persona-main reveal" if idx == 0 else "persona-card reveal"
        body += (
            f"<div class='{cls}'>"
            "<div class='persona-top'>"
            f"<span class='persona-name'>{_e(tag.get('tag'))}</span>"
            f"<span class='persona-conf'>可信度 {conf}%"
            f"（{_e(tag.get('confidence_label', '—'))}）</span>"
            "</div>"
            f"<span class='track' style='display:block;margin-top:10px'>"
            f"<i class='fill' data-w='{max(conf, 2)}'></i></span>"
            f"<div class='persona-desc'>{_e(tag.get('desc'))}</div>"
            f"<div class='persona-evid'>依据：{_e(tag.get('evidence', '—'))}</div>"
            "</div>"
        )

    if not profile.get("data_sufficient", True):
        body += ("<div class='warn reveal'>样本量低于门槛（至少 5 单且 8 件单品），"
                 "本轮只给描述、不下偏好结论。等数据再多一些，这里会换成真正的口味人格。</div>")
    else:
        sample = (tags[0].get("sample") if tags else None) or {}
        body += (f"<p class='muted reveal'>样本：{_e(sample.get('orders', '—'))} 单 / "
                 f"{_e(sample.get('items', '—'))} 件单品。"
                 "可信度会随样本量打折，样本越薄折得越狠。</p>")
    return _scene(4, head + body)


def _chapter_shift(profile: Dict[str, Any]) -> str:
    head = ("<p class='ch-no'>CHAPTER 05</p>"
            "<h2 class='ch-title'>这一年的变化</h2>"
            "<p class='ch-sub'>前半程和后半程各吃了什么，差距一目了然</p>")

    shift = profile.get("taste_shift") or []
    body = ""
    if shift:
        rows = []
        for row in shift:
            delta = row.get("delta", 0)
            cls = "up" if delta > 0 else ("down" if delta < 0 else "flat")
            sign = "+" if delta > 0 else ""
            label = f"{sign}{delta} 个百分点"
            if delta == 0:
                label = "持平"
            rows.append(
                "<li class='reveal'><div class='shift-head'>"
                f"<b>{_e(row.get('category'))}</b>"
                f"<span class='delta {cls}'>{_e(label)}</span></div>"
                "<div class='duo'><span>前</span>"
                f"<span class='track'><i class='fill' data-w='{int(row.get('early', 0))}'></i></span>"
                f"<span>{_e(row.get('early'))}%</span></div>"
                "<div class='duo'><span>后</span>"
                f"<span class='track'><i class='fill blue' data-w='{int(row.get('late', 0))}'></i></span>"
                f"<span>{_e(row.get('late'))}%</span></div></li>"
            )
        body += ("<div class='card reveal'><h3>口味变化（品类占比）</h3>"
                 f"<p class='muted'>{_mask_text(_shift_basis_text(profile))}</p>"
                 f"<ul class='shift'>{''.join(rows)}</ul></div>")
    else:
        basis = profile.get("taste_shift_basis") or {}
        reason = basis.get("reason") or "订单数不足，无法分成前后两组"
        body += (f"<div class='card reveal'><h3>口味变化</h3>"
                 f"<p class='muted'>暂不比较：{_e(reason)}。分组方式为按订单序号对半切分，"
                 "需要至少 6 单带日期的订单。</p></div>")

    chart = _month_chart(profile.get("monthly") or [])
    if chart:
        body += ("<div class='card reveal'><h3>月度订单趋势</h3>"
                 f"{chart}<p class='muted'>柱子越高表示当月订单越多，横向可滑动。</p></div>")

    s = profile.get("summary") or {}
    stores = [x for x in (profile.get("stores") or []) if x.get("name") not in (None, "未知门店")]
    facts = [
        f"记录跨度 {s.get('span_days', 0)} 天",
        f"最长连续打卡 {s.get('best_streak', 0)} 天",
        f"覆盖 {len(profile.get('monthly') or [])} 个月",
        f"常去门店 {len(stores)} 家" if stores else "暂无门店记录",
    ]
    body += ("<div class='card reveal'><h3>趣味记录</h3><div class='pills'>"
             + "".join(f"<span class='pill'>{_e(f)}</span>" for f in facts)
             + "</div></div>")
    return _scene(5, head + body)


def _chapter_finale(profile: Dict[str, Any], payload: Dict[str, Any]) -> str:
    head = ("<p class='ch-no'>CHAPTER 06</p>"
            "<h2 class='ch-title'>年度终章</h2>"
            "<p class='ch-sub'>年度关键词、下一次尝鲜，以及一张可以放心分享的卡片</p>")

    kws = "".join(
        f"<span>#{_e(k.get('word'))}<i>{_e(k.get('reason'))}</i></span>"
        for k in (profile.get("keywords") or [])[:6]
    )
    body = (f"<div class='card reveal'><h3>年度关键词</h3>"
            f"<div class='keywords'>{kws or '<span>还在探索中</span>'}</div></div>")

    recs = "".join(
        f"<div class='rec reveal'><div class='n'>{_e(r.get('name'))}</div>"
        f"<div class='r'>{_e(r.get('reason'))}</div></div>"
        for r in (profile.get("recommendations") or [])[:5]
    )
    if recs:
        body += f"<div class='card reveal'><h3>下次试试</h3>{recs}</div>"

    body += _share_panel(payload)

    lines = _coverage_lines(profile)
    body += ("<div class='card reveal'><h3>数据说明（与卡片同源）</h3><ul class='field-preview'>"
             + "".join(f"<li><span>{_mask_text(line)}</span></li>" for line in lines)
             + "</ul></div>")
    return _scene(6, head + body)


def _share_panel(payload: Dict[str, Any]) -> str:
    fields = payload.get("fields", {})
    public = bool(payload.get("redacted"))
    switches = []
    for key in _FIELD_ORDER:
        field = fields.get(key)
        if not field:
            continue
        checked = " checked" if payload.get("privacy", {}).get(key) else ""
        # 公开版里这些值压根没写进文件，勾选也没有意义，直接置灰
        disabled = " disabled" if field.get("available") is False else ""
        note = "公开版未写入" if field.get("available") is False else "默认隐藏"
        switches.append(
            f"<label class='sw'><input type='checkbox' id='f-{key}'{checked}{disabled}>"
            f"<span>展示{_e(field.get('label'))}</span><em>{_e(note)}</em></label>"
        )

    intro = (
        "竖版 1080×1920，适配常见手机屏幕。"
        "下面四项默认关闭；未勾选的字段不会画进图片，也不会写进配置。"
    )
    warn = (
        "卡片由本机 Canvas 绘制并导出，不含位置、账号等元数据；"
        "但画面上的文字就是图片的全部内容，公开前仍请自己看一眼。"
        "另外：story.html 本身包含完整数据，分享请发 PNG，不要直接转发这个 HTML 文件。"
    )
    if public:
        intro = ("竖版 1080×1920，适配常见手机屏幕。本页已是<b>公开分享版</b>："
                 "金额、门店名、到日日期在生成时就没有写入文件，因此这四项无可展示内容。")
        warn = ("卡片由本机 Canvas 绘制并导出，不含位置、账号等元数据。"
                "本页可直接分享；同目录的 report.html / report.md / profile.json "
                "仍含完整数据，不要一并发出。")

    return (
        "<div class='share-panel reveal'>"
        f"<h3 style='font-size:15px;margin-bottom:4px'>生成分享卡片</h3>"
        f"<p class='muted' style='margin-top:0'>{intro}</p>"
        f"<div class='switches'>{''.join(switches)}</div>"
        "<div class='card-preview'><canvas id='cardCanvas' width='1080' height='1920' "
        "aria-label='分享卡片预览'>当前浏览器不支持 Canvas，无法预览与导出分享卡片。</canvas></div>"
        "<ul class='field-preview' id='fieldPreview'></ul>"
        "<div class='actions'>"
        "<button type='button' id='downloadCard'>导出 share-card.png</button>"
        "<button type='button' id='copyConfig' class='ghost'>复制卡片配置</button>"
        "</div>"
        "<pre class='muted' id='configOut' style='white-space:pre-wrap;margin-top:12px'></pre>"
        f"<div class='warn'>{warn}</div>"
        "</div>"
    )


def _empty_chapters(profile: Dict[str, Any]) -> str:
    body = ("<p class='ch-no'>CHAPTER 02</p>"
            "<h2 class='ch-title'>还没有可以回放的订单</h2>"
            "<p class='ch-sub'>时光机需要至少一条订单才能启动</p>"
            "<div class='card reveal'><h3>接下来可以做什么</h3><ul class='field-preview'>"
            "<li><span>运行 <b>mcdtm doctor</b> 检查 MCP 连通性与返回结构</span></li>"
            "<li><span>运行 <b>mcdtm fetch</b> 拉取订单后再次生成</span></li>"
            "<li><span>先用 <b>mcdtm demo</b> 看一遍完整效果</span></li>"
            "</ul></div>")
    return _scene(2, body)


# ---------------------------------------------------------------- 组装

def _body_of(profile: Dict[str, Any], payload: Dict[str, Any], privacy: str) -> str:
    if profile.get("empty"):
        return (_banner(profile, privacy) + _chapter_hero(profile)
                + _empty_chapters(profile) + _footer(profile, privacy))
    return (
        _banner(profile, privacy)
        + _chapter_hero(profile)
        + _chapter_origin(profile)
        + _chapter_daily(profile)
        + _chapter_persona(profile)
        + _chapter_shift(profile)
        + _chapter_finale(profile, payload)
        + _footer(profile, privacy)
    )


def _banner(profile: Dict[str, Any], privacy: str) -> str:
    """页面顶部的模式横幅：把「这份文件里到底有什么」讲清楚。"""
    if privacy == "public":
        return (
            "<div class='mode-banner public' role='note'>"
            "<b>公开分享版</b> 金额、门店名称、到日日期已在生成时从本文件移除"
            "（保留月粒度用于趋势图）。本页可直接分享，但同目录的 "
            "report.html / report.md / profile.json 仍含完整数据，请勿一并发出。"
            "</div>"
        )
    return (
        "<div class='mode-banner personal' role='note'>"
        "<b>个人完整版</b> 本文件包含你的原始数据：累计金额、门店名称、具体日期都在里面；"
        "页面左下角「隐私浏览」开关只是<b>临时遮挡</b>，不会从文件里删掉任何内容，"
        "请勿直接转发本文件。要对外分享，请用 "
        "<code>mcdtm story --privacy public</code> 另生成一份公开版，"
        "或只发第 6 章导出的 share-card.png。"
        "</div>"
    )


def _footer(profile: Dict[str, Any], privacy: str = "personal") -> str:
    cov = profile.get("coverage") or {}
    demo = cov.get("source") == "demo"
    if privacy == "public":
        note = ("本页为公开分享版，已移除金额、门店名称与到日日期；"
                "月度趋势保留到「年-月」粒度。" if not demo else
                "本页订单由程序生成，不来自任何真实账号；且已按公开分享版移除敏感字段。")
        link = "<p>对外分享请直接发送本文件或第 6 章导出的 share-card.png。</p>"
    else:
        note = ("本页数据来自你本机通过麦当劳 MCP 拉取的订单，未上传至任何服务器；"
                "但本文件本身包含完整原始数据。" if not demo else
                "本页订单由程序生成，不来自任何真实账号。")
        link = ("<p>麦麦时光机是麦当劳程序员创意开发大赛参赛作品，由参赛者独立开发，非麦当劳官方产品。"
                "　<a href='report.html'>查看完整数据报告</a>"
                "　<a href='story-public.html'>公开分享版长什么样</a></p>")
    return (
        "<footer class='foot'>"
        f"<p>{_e(note)}</p>"
        "<p>统计范围与缺失情况见第 6 章「数据说明」；餐品信息、价格与营养数据"
        "以麦当劳官方渠道实时结果为准，本工具不构成营养或医疗建议。</p>"
        + link +
        "</footer>"
    )


def render_story(profile: Dict[str, Any], privacy: str = "personal",
                 leak_check: bool = True) -> str:
    """把 profile 渲染成单文件、离线可打开的故事页 HTML。

    `privacy="public"` 时按三层脱敏，产出的文件本身不含金额 / 门店名 / 到日日期
    （不是 CSS 遮挡）：

    1. `redact_profile()`：**渲染前**按字段名 / 字段类型把敏感值换成占位符；
    2. `redact_html()`：渲染完成后再扫一遍已知形态（含内联 JSON 数据块）；
    3. `verify_no_leaks()`：拿原 profile 反推的敏感原始值比对成品，
       **命中即抛 `PrivacyLeakError`、不返回任何 HTML**（fail-closed）；
       随后 `verify_public_complete()` 确认脱敏没把章节 / 计数 / 趋势一起抹掉。

    `leak_check=False` 只关掉第 3 步的**检查**（第 2 步照常执行）。
    它只给测试用：用来证明「前两层单独就零残留」，
    而不是让第三层默默兜住所有问题——第三层只报警，不修补。
    """
    if privacy not in PRIVACY_MODES:
        raise ValueError(f"privacy 只能是 {PRIVACY_MODES}，收到 {privacy!r}")
    source = profile
    public = privacy == "public"
    if public:
        profile = redact_profile(source)          # 第一层

    payload = build_story_payload(profile, privacy)
    cov = profile.get("coverage") or {}
    title = "麦麦时光机 · 我的年度回忆"
    if public:
        # 公开版不给统计区间：range_start / range_end 已是占位符，写进 meta 也没意义
        desc = (f"共 {cov.get('unique_orders', 0)} 单。"
                "六章节滚动故事 + 隐私友好分享卡片。")
    else:
        desc = (
            f"统计区间 {cov.get('range_start') or '—'} ~ {cov.get('range_end') or '—'}，"
            f"共 {cov.get('unique_orders', 0)} 单。六章节滚动故事 + 隐私友好分享卡片。"
        )

    template = _asset("story.html")
    html = (
        template
        .replace("__STORY_VERSION__", _e(__version__))
        .replace("__STORY_TITLE__", _e(title))
        .replace("__STORY_DESC__", _e(desc))
        .replace("__STORY_PRIVACY__", _attr(privacy))
        .replace("__STORY_CSS__", _asset("story.css"))
        .replace("__STORY_BODY__", _body_of(profile, payload, privacy))
        .replace("__STORY_DATA__", _json_for_html(payload))
        .replace("__STORY_JS__", _asset("story.js"))
    )
    if public:
        html = redact_html(html, store_names(source))   # 第二层
        if leak_check:
            verify_no_leaks(html, source)               # 第三层：检出残留即拒绝输出
        verify_public_complete(html, source)            # 脱敏不得伤到可读性
    return html


def write_story(profile: Dict[str, Any], out_dir: Path,
                privacy: str = "personal") -> Path:
    """渲染并落盘：**全部检查通过之后**才写文件。

    先渲染（脱敏自检在其中），再写临时文件、最后原子替换。这样：
    - 自检不过时一行都不写，目录里不会出现「没脱干净」的成品；
    - 也不会中途失败留下半份 HTML，或把上一次的成品改坏。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    html = render_story(profile, privacy)
    path = out_dir / "story.html"
    tmp = out_dir / "story.html.tmp"
    tmp.write_text(html, encoding="utf-8")
    try:
        os.replace(tmp, path)
    except OSError:  # pragma: no cover - 极端文件系统问题
        tmp.unlink(missing_ok=True)
        raise
    return path


__all__ = ["render_story", "write_story", "build_story_payload", "redact_html",
           "redact_profile", "scan_leaks", "verify_no_leaks", "sensitive_tokens",
           "public_completeness_issues", "verify_public_complete",
           "PrivacyLeakError", "PublicContentError", "PUBLIC_FIELD_POLICY",
           "store_names", "is_money_key", "is_sensitive_key", "is_safe_number_key",
           "CARD_WIDTH", "CARD_HEIGHT", "PRIVACY_DEFAULTS", "PRIVACY_MODES"]
