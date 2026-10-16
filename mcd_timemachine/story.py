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

    amount_value = f"{_spend_title(summary)} ¥{(summary.get('display_spend') or 0):,.2f}"
    if summary.get("spend_basis", "none") == "none":
        amount_value = "暂无金额数据"

    raw_values = {
        "amount": amount_value,
        "store": store_text or "暂无门店记录",
        "date": range_text,
        "generated": generated_at or "—",
    }
    if public:
        # 公开版：值不进文件，也不进卡片
        raw_values = {k: _PUBLIC_PLACEHOLDER for k in raw_values}

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


_PV_MONEY_RE = re.compile(r"¥[\d,]+(?:\.\d+)?")
_PV_DATE_RE = re.compile(r"\d{4}-\d{2}(?:-\d{2})?")


def _mask_text(text: Any, extra: str = "") -> str:
    """给正文里的金额与日期打上隐私标记。

    年度回忆的文案里散落着「2026-06-27 花了 ¥129.00」这类信息，
    只标记stats 是不够的；这里统一补标，保证「隐私浏览」打开后全页不留具体数值。
    """
    out = _e(text)
    if extra:
        out = out.replace(_e(extra), _pv(extra), 1)
    out = _PV_MONEY_RE.sub(lambda m: _pv(m.group(0)), out)
    out = _PV_DATE_RE.sub(lambda m: _pv(m.group(0)), out)
    return out


# ---------------------------------------------------------------- 生成期脱敏

_PV_DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def store_names(profile: Dict[str, Any]) -> List[str]:
    return [s.get("name") for s in (profile.get("stores") or []) if s.get("name")]


def redact_html(html: str, names: Optional[List[str]] = None) -> str:
    """公开分享版：把敏感值从 HTML 里**真正抹掉**，而不是靠 CSS 遮挡。

    - 金额：`¥1,234.00` → 「金额已隐藏」
    - 到日日期：`2026-06-27` → 「日期已隐藏」；保留 `2026-06` 月粒度，
      否则第 5 章的月度趋势与口味变化分组依据会失去意义（页面会写明这一点）
    - 门店名：按 profile 中的门店名逐个替换为「门店已隐藏」（保留次数与家数）

    同时对整份 HTML（含内联 JSON 数据块）生效，避免漏改某个字段。
    """
    out = _PV_MONEY_RE.sub(_REDACT_MONEY, html)
    out = _PV_DAY_RE.sub(_REDACT_DAY, out)
    for name in sorted((n for n in (names or []) if n), key=len, reverse=True):
        out = out.replace(_e(name), _REDACT_STORE).replace(name, _REDACT_STORE)
    return out


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
    year = ""
    end = cov.get("range_end") or s.get("last_date") or ""
    if len(end) >= 4:
        year = end[:4]

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

    grid = (
        "<div class='card reveal'><h3>消费节奏</h3><div class='stat-grid'>"
        f"<div><b>{per_month}</b><span>月均单量</span></div>"
        f"<div><b>{_pv('¥' + format(s.get('avg_order', 0), ',.1f'), '已隐藏')}</b>"
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


def render_story(profile: Dict[str, Any], privacy: str = "personal") -> str:
    """把 profile 渲染成单文件、离线可打开的故事页 HTML。

    `privacy="public"` 时在**生成阶段**就把金额 / 门店名 / 到日日期从 HTML 里抹掉，
    产出的文件本身不含这些值（不是 CSS 遮挡）。
    """
    if privacy not in PRIVACY_MODES:
        raise ValueError(f"privacy 只能是 {PRIVACY_MODES}，收到 {privacy!r}")
    payload = build_story_payload(profile, privacy)
    cov = profile.get("coverage") or {}
    title = "麦麦时光机 · 我的年度回忆"
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
    if privacy == "public":
        # 连同内联 JSON 一起兜底一遍，防止将来新增字段时漏改
        html = redact_html(html, store_names(profile))
    return html


def write_story(profile: Dict[str, Any], out_dir: Path,
                privacy: str = "personal") -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = Path(out_dir) / "story.html"
    path.write_text(render_story(profile, privacy), encoding="utf-8")
    return path


__all__ = ["render_story", "write_story", "build_story_payload", "redact_html",
           "store_names", "CARD_WIDTH", "CARD_HEIGHT", "PRIVACY_DEFAULTS",
           "PRIVACY_MODES"]
