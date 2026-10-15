"""报告渲染：终端简报 + Markdown + 可分享的 HTML 卡片。

报告开头固定输出「数据说明」区块：数据来源（真实 MCP / 示例 Demo）、统计区间、
订单覆盖与字段完整度、以及本次采集的告警。宁可显得啰嗦，也不让报告看起来比它
实际更完整。
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Dict, List

BRAND_RED = "#DA291C"
BRAND_YELLOW = "#FFC72C"

SOURCE_LABEL = {
    "mcp": "真实数据 · 来自麦当劳 MCP",
    "demo": "示例数据 · 虚构生成，非真实订单",
    "unknown": "来源未知",
}

SPEND_BASIS_LABEL = {
    "actual": "均为已确认的实际支付金额",
    "mixed": "以实际支付金额为主",
    "estimated": "全部为按单价×数量估算的商品金额",
    "unlabeled": "金额口径未被标注",
    "none": "没有任何金额记录",
}


def _spend_note(summary: Dict[str, Any]) -> str:
    """把金额口径讲清楚：实付 / 估算 / 缺失各多少。"""
    basis = summary.get("spend_basis", "none")
    parts = [SPEND_BASIS_LABEL.get(basis, "")]
    if summary.get("estimated_amount_orders"):
        parts.append(f"{summary['estimated_amount_orders']} 单为估算值")
    if summary.get("unknown_amount_orders"):
        parts.append(f"{summary['unknown_amount_orders']} 单无金额")
    parts = [p for p in parts if p]
    return "（" + "，".join(parts) + "）" if parts else ""


def _esc(text: Any) -> str:
    return html.escape(str(text if text is not None else ""))


def _bar(value: float, maximum: float, width: int = 24, char: str = "#") -> str:
    if maximum <= 0:
        return ""
    filled = int(round(value / maximum * width))
    if value <= 0:
        return ""
    return char * max(filled, 1)


def _coverage_of(profile: Dict[str, Any]) -> Dict[str, Any]:
    return profile.get("coverage") or {}


STOP_REASON_LABEL = {
    "exhausted": "服务端已返回全部数据",
    "last_page": "最后一页不满，判定已到底",
    "duplicate": "翻页返回重复数据，服务端可能忽略分页参数",
    "max_pages": "已达到分页上限",
}


def _coverage_lines(profile: Dict[str, Any]) -> List[str]:
    cov = _coverage_of(profile)
    total = cov.get("unique_orders", profile.get("summary", {}).get("total_orders", 0)) or 0
    lines = [
        f"数据来源   {SOURCE_LABEL.get(cov.get('source', 'unknown'), '来源未知')}",
        f"统计区间   {cov.get('range_start') or '—'}  ~  {cov.get('range_end') or '—'}",
        f"订单覆盖   {total} 单（原始 {cov.get('raw_records', total)} 条，"
        f"去重 {cov.get('duplicates_removed', 0)} 条，翻页 {cov.get('pages', 0)} 页）",
        f"字段完整   有日期 {cov.get('with_date', 0)}/{total}，"
        f"有金额 {cov.get('with_amount', 0)}/{total}，"
        f"有明细 {cov.get('with_items', 0)}/{total}",
    ]
    if cov.get("source") == "demo":
        lines.append("覆盖范围   示例数据由程序生成，仅用于演示输出效果")
    elif cov.get("coverage_confirmed") is False:
        lines.append(f"覆盖范围   未确认（{STOP_REASON_LABEL.get(cov.get('stop_reason', ''), '原因未知')}），"
                     "统计可能不完整")
    elif cov.get("coverage_confirmed"):
        lines.append("覆盖范围   已确认（服务端已给出终止信号）")
    est = cov.get("estimated_amount_orders", 0)
    if est:
        lines.append(
            f"金额口径   实付 {cov.get('actual_amount_orders', 0)} 单，"
            f"估算 {est} 单（估算 = 单价×数量，未含优惠券/配送费/退款）"
        )
    for warn in cov.get("warnings", [])[:3]:
        lines.append(f"提示       {warn}")
    return lines


# ---------------------------------------------------------------- 终端输出

def print_console(profile: Dict[str, Any]) -> None:
    line = "=" * 56
    print(line)
    print("麦麦时光机 · 你的麦当劳消费回忆")
    print(line)
    for text in _coverage_lines(profile):
        print(text)
    print()

    if profile.get("empty"):
        print("没有解析到任何订单。先跑 `mcdtm fetch` 拉数据，或用 `mcdtm doctor` 诊断。")
        print(line)
        return

    s = profile["summary"]
    print(f"订单总数   {s['total_orders']} 单")
    print(f"累计消费   ¥{s['display_spend']:,.2f}  {_spend_note(s)}")
    print(f"平均客单   ¥{s['avg_order']:,.2f}")
    print(f"单品总数   {s['total_items']} 件")
    print(f"最长连续   {s['best_streak']} 天")
    print()

    if profile.get("keywords"):
        print("-- 年度关键词 --------------------------------------------------")
        print("  " + "　".join(f"#{k['word']}" for k in profile["keywords"]))
        print()

    print("-- 口味画像 ----------------------------------------------------")
    for tag in profile["tags"]:
        conf = int(tag.get("confidence", 0))
        print(f"  {tag['tag']:<8} 可信度 {conf:>3}%  {_bar(conf, 100, 16):<16} {tag['desc']}")
        if tag.get("evidence"):
            print(f"  {'':<8} 依据：{tag['evidence']}")
    if not profile.get("data_sufficient", True):
        print("  注：数据量较少，画像仅供参考")
    print()

    if profile.get("taste_shift"):
        print("-- 口味变化（前 / 后半程占比）------------------------------------")
        for row in profile["taste_shift"]:
            trend = "上升" if row["delta"] > 0 else ("下降" if row["delta"] < 0 else "持平")
            print(f"  {row['category']:<6} {row['early']:>5}%  ->  {row['late']:>5}%"
                  f"   {trend} {abs(row['delta'])} 个百分点")
        print()

    print("-- 品类偏好 ----------------------------------------------------")
    top = max((c["qty"] for c in profile["categories"]), default=1)
    for cat in profile["categories"]:
        print(f"  {cat['name']:<6} {_bar(cat['qty'], top):<24} {cat['qty']:>3} 件  {cat['pct']:>5}%")
    print()

    print("-- 出没时段 ----------------------------------------------------")
    top = max((x["count"] for x in profile["slots"]), default=1)
    for slot in profile["slots"]:
        print(f"  {slot['name']:<6} {_bar(slot['count'], top):<24} {slot['count']:>3} 单")
    print()

    print("-- 常点 TOP5 ---------------------------------------------------")
    for i, item in enumerate(profile["top_items"][:5], 1):
        print(f"  {i}. {item['name']:<16} x{item['qty']:<4} ¥{item['spend']:,.2f}")
    print()

    print("-- 黄金搭档 ----------------------------------------------------")
    for combo in profile["combos"][:3]:
        print(f"  {combo['pair']}   ({combo['count']} 次同单)")
    print()

    print("-- 下次试试 ----------------------------------------------------")
    for rec in profile["recommendations"][:5]:
        print(f"  * {rec['name']}：{rec['reason']}")
    print(line)


# ---------------------------------------------------------------- Markdown

def render_markdown(profile: Dict[str, Any]) -> str:
    cov = _coverage_of(profile)
    out: List[str] = []
    w = out.append

    w("# 麦麦时光机 · 我的麦当劳年度报告")
    w("")
    if cov.get("source") == "demo":
        w("> **本报告为示例数据演示**，订单由程序生成，不来自任何真实账号。")
        w("")

    w("## 〇、数据说明")
    w("")
    for text in _coverage_lines(profile):
        w(f"- {text}")
    if cov.get("tools"):
        w("")
        w("| MCP Tool | 状态 |")
        w("| --- | --- |")
        for tool, status in cov["tools"].items():
            w(f"| `{tool}` | {status} |")
    w("")

    if profile.get("empty"):
        w("暂无可分析的订单数据。")
        return "\n".join(out) + "\n"

    s = profile["summary"]
    w("## 一、数字总览")
    w("")
    w("| 指标 | 数值 |")
    w("| --- | --- |")
    w(f"| 订单总数 | {s['total_orders']} 单 |")
    w(f"| 累计消费 | ¥{s['display_spend']:,.2f} {_spend_note(s)} |")
    w(f"| 平均客单价 | ¥{s['avg_order']:,.2f} |")
    w(f"| 单品总数 | {s['total_items']} 件 |")
    w(f"| 月均消费 | ¥{s['avg_per_month']:,.2f} |")
    w(f"| 最长连续打卡 | {s['best_streak']} 天 |")
    w("")

    if profile.get("keywords"):
        w("## 二、我的年度关键词")
        w("")
        for k in profile["keywords"]:
            w(f"- **#{k['word']}** —— {k['reason']}")
        w("")

    w("## 三、口味画像")
    w("")
    for tag in profile["tags"]:
        w(f"- **{tag['tag']}**（可信度 {tag.get('confidence', 0)}%）：{tag['desc']}"
          f"　—— 依据：{tag.get('evidence', '—')}")
    if not profile.get("data_sufficient", True):
        w("")
        w("> 数据量较少，画像仅供参考。")
    w("")

    if profile.get("taste_shift"):
        w("## 四、这一年的口味变化")
        w("")
        w("| 品类 | 前半程占比 | 后半程占比 | 变化 |")
        w("| --- | ---: | ---: | ---: |")
        for row in profile["taste_shift"]:
            w(f"| {row['category']} | {row['early']}% | {row['late']}% | "
              f"{row['delta']:+} 个百分点 |")
        w("")

    w("## 五、品类偏好")
    w("")
    w("| 品类 | 件数 | 占比 | 消费 |")
    w("| --- | ---: | ---: | ---: |")
    for cat in profile["categories"]:
        w(f"| {cat['name']} | {cat['qty']} | {cat['pct']}% | ¥{cat['spend']:,.2f} |")
    w("")

    w("## 六、消费趋势")
    w("")
    w("| 月份 | 订单数 | 消费 |")
    w("| --- | ---: | ---: |")
    for m in profile["monthly"]:
        w(f"| {m['month']} | {m['orders']} | ¥{m['spend']:,.2f} |")
    w("")

    w("## 七、年度回忆")
    w("")
    for memo in profile["memories"]:
        w(f"- **{memo['title']}**：{memo['text']}")
    w("")

    w("## 八、常点单品")
    w("")
    w("| 排名 | 单品 | 件数 | 消费 |")
    w("| ---: | --- | ---: | ---: |")
    for i, item in enumerate(profile["top_items"], 1):
        w(f"| {i} | {item['name']} | {item['qty']} | ¥{item['spend']:,.2f} |")
    w("")

    w("## 九、黄金搭档")
    w("")
    for combo in profile["combos"]:
        w(f"- {combo['pair']} —— {combo['count']} 次同单")
    w("")

    w("## 十、下次试试")
    w("")
    for rec in profile["recommendations"]:
        w(f"- **{rec['name']}**：{rec['reason']}")
    w("")
    w("---")
    w("")
    w("本报告由「麦麦时光机」基于麦当劳中国 MCP 开放能力本地生成，"
      "数据仅保存在本机，不构成营养或医疗建议。餐品信息与价格以麦当劳官方渠道实时结果为准。")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- HTML

_CSS = """
:root{--red:#DA291C;--yellow:#FFC72C;--ink:#1a1a1a;--sub:#6b6b6b;--line:#ececec;--bg:#fafafa;}
*{box-sizing:border-box;}
body{margin:0;padding:32px 16px;background:var(--bg);color:var(--ink);
 font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei","Segoe UI",sans-serif;
 -webkit-font-smoothing:antialiased;}
.wrap{max-width:860px;margin:0 auto;}
.hero{background:linear-gradient(135deg,var(--red),#b81f14);color:#fff;border-radius:20px;
 padding:36px 32px;position:relative;overflow:hidden;}
.hero:after{content:"";position:absolute;right:-70px;top:-70px;width:220px;height:220px;
 border-radius:50%;background:var(--yellow);opacity:.22;}
.hero h1{margin:0 0 6px;font-size:30px;letter-spacing:1px;}
.hero p{margin:0;opacity:.9;font-size:14px;}
.hero .range{margin-top:18px;font-size:13px;opacity:.85;}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-top:22px;}
.stat{background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.22);
 border-radius:14px;padding:14px 16px;}
.stat .v{font-size:24px;font-weight:700;}
.stat .k{font-size:12px;opacity:.85;margin-top:4px;}
.demo-banner{background:#fff5e6;border:1px solid #ffcf8a;color:#8a5a00;border-radius:14px;
 padding:12px 16px;margin-top:16px;font-size:13px;line-height:1.6;}
.card{background:#fff;border:1px solid var(--line);border-radius:18px;padding:24px;margin-top:18px;}
.card h2{margin:0 0 18px;font-size:18px;display:flex;align-items:center;gap:8px;}
.card h2:before{content:"";width:4px;height:18px;background:var(--red);border-radius:2px;}
.badge{display:inline-block;font-size:12px;padding:4px 10px;border-radius:999px;margin-left:8px;}
.badge.real{background:#eaf7ee;color:#1a7f37;border:1px solid #b7e2c2;}
.badge.demo{background:#fff1d6;color:#8a5a00;border:1px solid #ffd28a;}
.kv{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px 20px;
 font-size:13px;color:var(--sub);line-height:1.7;}
.kv b{color:var(--ink);font-weight:600;}
.warn{margin-top:12px;font-size:12px;color:#9a5b00;background:#fff9ef;
 border:1px dashed #ffd9a8;border-radius:10px;padding:10px 12px;line-height:1.7;}
.muted{margin-top:12px;font-size:12px;color:var(--sub);}
.tagrow{margin:0 0 18px;}
.tagrow-h{display:flex;justify-content:space-between;align-items:baseline;font-size:14px;}
.tagrow-h b{color:var(--red);font-size:15px;}
.conf{font-size:12px;color:var(--sub);flex:none;}
.tagbar{height:6px;background:#f2f2f2;border-radius:999px;overflow:hidden;margin:8px 0 6px;}
.tagbar i{display:block;height:100%;border-radius:999px;
 background:linear-gradient(90deg,var(--yellow),var(--red));}
.tagdesc{font-size:13px;color:var(--ink);}
.tagevid{font-size:12px;color:var(--sub);margin-top:4px;}
.tags{display:flex;flex-wrap:wrap;gap:10px;}
.tag{background:#fff8e6;border:1px solid #ffe1a6;color:#8a5a00;border-radius:999px;
 padding:8px 14px;font-size:13px;}
.tag b{color:var(--red);}
.kw{display:flex;flex-wrap:wrap;gap:10px;}
.kw span{background:#fff;border:1px solid var(--line);border-radius:12px;padding:10px 14px;
 font-size:14px;font-weight:600;}
.kw span i{display:block;font-style:normal;font-weight:400;font-size:12px;
 color:var(--sub);margin-top:3px;}
.row{display:flex;align-items:center;gap:12px;margin:10px 0;font-size:13px;}
.row .label{width:74px;flex:none;color:var(--sub);}
.row .track{flex:1;height:10px;background:#f2f2f2;border-radius:999px;overflow:hidden;}
.row .fill{height:100%;background:var(--red);border-radius:999px;}
.row .val{width:112px;text-align:right;flex:none;color:var(--sub);}
.shift{display:flex;align-items:center;gap:10px;font-size:13px;margin:8px 0;}
.shift .c{width:60px;flex:none;}
.shift .n{width:110px;text-align:right;flex:none;}
.shift .up{color:#c0392b;font-weight:600;}
.shift .down{color:#1a7f37;font-weight:600;}
.pill{display:inline-block;background:#f6f6f6;border-radius:8px;padding:6px 10px;
 margin:4px 6px 0 0;font-size:13px;}
.timeline{border-left:2px solid var(--line);margin-left:8px;padding-left:18px;}
.timeline .item{position:relative;padding:8px 0;}
.timeline .item:before{content:"";position:absolute;left:-25px;top:14px;width:10px;height:10px;
 border-radius:50%;background:var(--yellow);border:2px solid var(--red);}
.timeline .t{font-weight:600;font-size:14px;}
.timeline .d{font-size:13px;color:var(--sub);margin-top:2px;}
.rec{border:1px dashed #ffd9a8;background:#fffaf1;border-radius:12px;padding:12px 14px;margin-top:10px;}
.rec .n{font-weight:700;}
.rec .r{font-size:13px;color:var(--sub);margin-top:4px;}
table.tools{width:100%;border-collapse:collapse;font-size:12px;margin-top:12px;}
table.tools td{border-top:1px solid var(--line);padding:6px 4px;color:var(--sub);}
.foot{margin:22px 0 0;font-size:12px;color:#999;text-align:center;line-height:1.7;}
"""


def render_html(profile: Dict[str, Any]) -> str:
    cov = _coverage_of(profile)
    parts: List[str] = []
    a = parts.append

    a('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">')
    a('<meta name="viewport" content="width=device-width,initial-scale=1">')
    a("<title>麦麦时光机 · 我的麦当劳年度报告</title>")
    a(f"<style>{_CSS}</style></head><body><div class='wrap'>")

    is_demo = cov.get("source") == "demo"
    a('<div class="hero">')
    a("<h1>麦麦时光机</h1>")
    a("<p>把吃过的每一单，变成一份年度回忆</p>")
    range_text = (
        f"{cov.get('range_start') or '—'} ~ {cov.get('range_end') or '—'}"
        if not profile.get("empty") else "暂无数据"
    )
    a(f"<div class='range'>统计区间 {_esc(range_text)}　"
      f"生成于 {_esc(profile.get('generated_at', ''))}</div>")
    a("</div>")

    if is_demo:
        a('<div class="demo-banner"><b>这是示例数据报告。</b>'
          '订单由程序生成，不来自任何真实账号，仅用于演示麦麦时光机的输出效果。</div>')

    # 数据说明（永远置顶）
    total = cov.get("unique_orders", profile.get("summary", {}).get("total_orders", 0)) or 0
    badge_cls = "demo" if is_demo else "real"
    a('<div class="card"><h2>数据说明'
      f"<span class='badge {badge_cls}'>"
      f"{_esc(SOURCE_LABEL.get(cov.get('source', 'unknown'), '来源未知'))}</span></h2>")
    a("<div class='kv'>")
    a(f"<div>统计区间：<b>{_esc(cov.get('range_start') or '—')} ~ "
      f"{_esc(cov.get('range_end') or '—')}</b></div>")
    a(f"<div>订单覆盖：<b>{total} 单</b>"
      f"（原始 {cov.get('raw_records', total)} 条，去重 {cov.get('duplicates_removed', 0)} 条）</div>")
    a(f"<div>翻页情况：<b>{cov.get('pages', 0)} 页</b>"
      f"{'（已达上限，可能不完整）' if cov.get('truncated') else ''}</div>")
    a(f"<div>有日期：<b>{cov.get('with_date', 0)}/{total}</b></div>")
    a(f"<div>有金额：<b>{cov.get('with_amount', 0)}/{total}</b></div>")
    a(f"<div>有明细：<b>{cov.get('with_items', 0)}/{total}</b></div>")
    if cov.get("source") == "demo":
        a("<div>覆盖范围：示例数据由程序生成，仅用于演示</div>")
    elif cov.get("coverage_confirmed") is False:
        a(f"<div class='warn'>覆盖范围<b>未确认</b>："
          f"{_esc(STOP_REASON_LABEL.get(cov.get('stop_reason', ''), '原因未知'))}，"
          "统计可能不完整。</div>")
    est = cov.get("estimated_amount_orders", 0)
    if est:
        a(f"<div class='warn'>金额口径：实付 <b>{cov.get('actual_amount_orders', 0)}</b> 单，"
          f"估算 <b>{est}</b> 单。估算按「单价 × 数量」计算，"
          "未包含优惠券、配送费与退款，累计消费可能与实际支出有偏差。</div>")
    a("</div>")
    if cov.get("tools"):
        a("<table class='tools'>")
        for tool, status in cov["tools"].items():
            a(f"<tr><td>{_esc(tool)}</td><td>{_esc(status)}</td></tr>")
        a("</table>")
    if cov.get("warnings"):
        a("<div class='warn'>")
        for warn in cov["warnings"][:5]:
            a(f"提示：{_esc(warn)}<br>")
        a("</div>")
    if cov.get("with_amount", 0) and total and cov["with_amount"] < total:
        a(f"<div class='warn'>有 {total - cov['with_amount']} 单缺少金额信息，"
          "「累计消费」可能低于实际值。</div>")
    a("</div>")

    if profile.get("empty"):
        a('<div class="card"><h2>暂无可分析的订单</h2>'
          "<p>本次未解析到订单数据。请运行 <code>mcdtm doctor</code> 检查 MCP 返回结构。</p></div>")
        a("</div></body></html>")
        return "\n".join(parts)

    s = profile["summary"]
    a('<div class="card"><h2>数字总览</h2><div class="stats">')
    for value, key in (
        (f"{s['total_orders']} 单", "订单总数"),
        (f"¥{s['display_spend']:,.0f}", "累计消费"),
        (f"¥{s['avg_order']:.1f}", "平均客单价"),
        (f"{s['total_items']} 件", "单品总数"),
        (f"{s['best_streak']} 天", "最长连续打卡"),
    ):
        a(f"<div class='stat'><div class='v'>{_esc(value)}</div>"
          f"<div class='k'>{_esc(key)}</div></div>")
    a("</div>")
    if _spend_note(s):
        a(f"<div class='muted'>{_esc(_spend_note(s))}</div>")
    a("</div>")

    if profile.get("keywords"):
        a('<div class="card"><h2>我的年度关键词</h2><div class="kw">')
        for k in profile["keywords"]:
            a(f"<span>#{_esc(k['word'])}<i>{_esc(k['reason'])}</i></span>")
        a("</div></div>")

    a('<div class="card"><h2>口味画像</h2>')
    for tag in profile["tags"]:
        conf = int(tag.get("confidence", 0))
        a("<div class='tagrow'>")
        a(f"<div class='tagrow-h'><b>{_esc(tag['tag'])}</b>"
          f"<span class='conf'>可信度 {conf}%</span></div>")
        a(f"<div class='tagbar'><i style='width:{max(conf, 2)}%'></i></div>")
        a(f"<div class='tagdesc'>{_esc(tag['desc'])}</div>")
        if tag.get("evidence"):
            a(f"<div class='tagevid'>依据：{_esc(tag['evidence'])}</div>")
        a("</div>")
    if not profile.get("data_sufficient", True):
        a("<div class='warn'>数据量较少，画像仅供参考。</div>")
    a("</div>")

    if profile.get("taste_shift"):
        a('<div class="card"><h2>这一年的口味变化</h2>')
        for row in profile["taste_shift"]:
            cls = "up" if row["delta"] > 0 else ("down" if row["delta"] < 0 else "")
            sign = "+" if row["delta"] > 0 else ""
            a(f"<div class='shift'><span class='c'>{_esc(row['category'])}</span>"
              f"<span class='n'>{row['early']}% → {row['late']}%</span>"
              f"<span class='{cls}'>{sign}{row['delta']} 个百分点</span></div>")
        a("</div>")

    def _chart(title: str, rows: List[Dict[str, Any]], unit: str) -> None:
        if not rows:
            return
        a(f'<div class="card"><h2>{_esc(title)}</h2>')
        top = max((r.get("qty", r.get("count", r.get("orders", 0))) for r in rows), default=1)
        for r in rows:
            name = r.get("name") or r.get("month")
            qty = r.get("qty", r.get("count", r.get("orders", 0)))
            pct = int(round(qty / top * 100)) if top else 0
            extra = ""
            if r.get("pct") is not None:
                extra = f"{r['pct']}%"
            elif r.get("spend") is not None:
                extra = f"¥{r['spend']:,.0f}"
            a(f"<div class='row'><span class='label'>{_esc(name)}</span>"
              f"<span class='track'><span class='fill' style='width:{pct}%'></span></span>"
              f"<span class='val'>{qty} {_esc(unit)} {_esc(extra)}</span></div>")
        a("</div>")

    _chart("品类偏好", profile["categories"], "件")
    _chart("出没时段", profile["slots"], "单")
    _chart("一周节奏", profile["weekdays"], "单")
    _chart("消费趋势", profile["monthly"], "单")

    a('<div class="card"><h2>年度回忆</h2><div class="timeline">')
    for memo in profile["memories"]:
        a(f"<div class='item'><div class='t'>{_esc(memo['title'])}</div>"
          f"<div class='d'>{_esc(memo['text'])}</div></div>")
    a("</div></div>")

    a('<div class="card"><h2>常点单品</h2>')
    for item in profile["top_items"]:
        a(f"<span class='pill'>{_esc(item['name'])} × {item['qty']}</span>")
    a("</div>")

    if profile["combos"]:
        a('<div class="card"><h2>黄金搭档</h2>')
        for combo in profile["combos"]:
            a(f"<span class='pill'>{_esc(combo['pair'])} · {combo['count']} 次</span>")
        a("</div>")

    stores = [x for x in profile["stores"] if x["name"] and x["name"] != "未知门店"]
    if stores:
        a('<div class="card"><h2>常去门店</h2>')
        for store in stores:
            a(f"<span class='pill'>{_esc(store['name'])} · {store['count']} 次</span>")
        a("</div>")

    a('<div class="card"><h2>下次试试</h2>')
    for rec in profile["recommendations"]:
        a(f"<div class='rec'><div class='n'>{_esc(rec['name'])}</div>"
          f"<div class='r'>{_esc(rec['reason'])}</div></div>")
    a("</div>")

    a('<p class="foot">本报告由「麦麦时光机」基于麦当劳中国 MCP 开放能力本地生成，'
      '数据仅保存于本机，不上传、不外传。<br>'
      '统计范围与缺失情况见上方「数据说明」；餐品信息、价格与营养数据以麦当劳官方渠道实时结果为准，'
      '本报告不构成营养或医疗建议。<br>'
      '麦麦时光机是麦当劳程序员创意开发大赛参赛作品，由参赛者独立开发，非麦当劳官方产品。</p>')

    a("</div></body></html>")
    return "\n".join(parts)


# ---------------------------------------------------------------- 落盘

def write_reports(profile: Dict[str, Any], out_dir: Path) -> Dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "report.md"
    html_path = out_dir / "report.html"
    json_path = out_dir / "profile.json"
    md_path.write_text(render_markdown(profile), encoding="utf-8")
    html_path.write_text(render_html(profile), encoding="utf-8")
    json_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"md": md_path, "html": html_path, "json": json_path}


__all__ = ["print_console", "render_markdown", "render_html", "write_reports"]
