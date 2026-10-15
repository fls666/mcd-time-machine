"""报告渲染：终端简报 + Markdown + 可分享的 HTML 卡片。"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Dict, List

BRAND_RED = "#DA291C"
BRAND_YELLOW = "#FFC72C"


def _esc(text: Any) -> str:
    return html.escape(str(text if text is not None else ""))


def _bar(value: float, maximum: float, width: int = 24, char: str = "#") -> str:
    if maximum <= 0:
        return ""
    filled = int(round(value / maximum * width))
    return char * max(filled, 1 if value > 0 else 0)


# ---------------------------------------------------------------- 终端输出

def print_console(profile: Dict[str, Any]) -> None:
    if profile.get("empty"):
        print("没有解析到任何订单。先跑 `mcdtm fetch` 拉数据，或用 `mcdtm inspect orders` 看原始结构。")
        return

    s = profile["summary"]
    line = "=" * 56
    print(line)
    print("麦麦时光机 · 你的麦当劳消费回忆")
    print(line)
    print(f"统计区间   {s['first_date']}  ~  {s['last_date']}   (跨度 {s['span_days']} 天)")
    print(f"订单总数   {s['total_orders']} 单")
    print(f"累计消费   ¥{s['total_spend']:,.2f}")
    print(f"平均客单   ¥{s['avg_order']:,.2f}")
    print(f"单品总数   {s['total_items']} 件")
    print(f"最长连续   {s['best_streak']} 天")
    print()

    print("-- 口味画像 ----------------------------------------------------")
    for tag in profile["tags"]:
        print(f"  {tag['tag']:<8} {_bar(float(tag['score']), 100):<24} {tag['desc']}")
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
    if profile.get("empty"):
        return "# 麦麦时光机\n\n暂无可分析的订单数据。\n"

    s = profile["summary"]
    out: List[str] = []
    w = out.append

    w("# 麦麦时光机 · 我的麦当劳年度报告")
    w("")
    w(f"> 统计区间：{s['first_date']} ~ {s['last_date']}（跨度 {s['span_days']} 天）  ")
    w(f"> 生成时间：{profile['generated_at']}")
    w("")
    w("## 一、数字总览")
    w("")
    w("| 指标 | 数值 |")
    w("| --- | --- |")
    w(f"| 订单总数 | {s['total_orders']} 单 |")
    w(f"| 累计消费 | ¥{s['total_spend']:,.2f} |")
    w(f"| 平均客单价 | ¥{s['avg_order']:,.2f} |")
    w(f"| 单品总数 | {s['total_items']} 件 |")
    w(f"| 月均消费 | ¥{s['avg_per_month']:,.2f} |")
    w(f"| 最长连续打卡 | {s['best_streak']} 天 |")
    w("")
    w("## 二、口味画像")
    w("")
    for tag in profile["tags"]:
        w(f"- **{tag['tag']}**（{tag['score']}）：{tag['desc']}")
    w("")
    w("## 三、品类偏好")
    w("")
    w("| 品类 | 件数 | 占比 | 消费 |")
    w("| --- | ---: | ---: | ---: |")
    for cat in profile["categories"]:
        w(f"| {cat['name']} | {cat['qty']} | {cat['pct']}% | ¥{cat['spend']:,.2f} |")
    w("")
    w("## 四、消费趋势")
    w("")
    w("| 月份 | 订单数 | 消费 |")
    w("| --- | ---: | ---: |")
    for m in profile["monthly"]:
        w(f"| {m['month']} | {m['orders']} | ¥{m['spend']:,.2f} |")
    w("")
    w("## 五、年度回忆")
    w("")
    for memo in profile["memories"]:
        w(f"- **{memo['title']}**：{memo['text']}")
    w("")
    w("## 六、常点单品")
    w("")
    w("| 排名 | 单品 | 件数 | 消费 |")
    w("| ---: | --- | ---: | ---: |")
    for i, item in enumerate(profile["top_items"], 1):
        w(f"| {i} | {item['name']} | {item['qty']} | ¥{item['spend']:,.2f} |")
    w("")
    w("## 七、黄金搭档")
    w("")
    for combo in profile["combos"]:
        w(f"- {combo['pair']} —— {combo['count']} 次同单")
    w("")
    w("## 八、下次试试")
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
.card{background:#fff;border:1px solid var(--line);border-radius:18px;padding:24px;margin-top:18px;}
.card h2{margin:0 0 18px;font-size:18px;display:flex;align-items:center;gap:8px;}
.card h2:before{content:"";width:4px;height:18px;background:var(--red);border-radius:2px;}
.tags{display:flex;flex-wrap:wrap;gap:10px;}
.tag{background:#fff8e6;border:1px solid #ffe1a6;color:#8a5a00;border-radius:999px;
 padding:8px 14px;font-size:13px;}
.tag b{color:var(--red);}
.row{display:flex;align-items:center;gap:12px;margin:10px 0;font-size:13px;}
.row .label{width:74px;flex:none;color:var(--sub);}
.row .track{flex:1;height:10px;background:#f2f2f2;border-radius:999px;overflow:hidden;}
.row .fill{height:100%;background:var(--red);border-radius:999px;}
.row .val{width:96px;text-align:right;flex:none;color:var(--sub);}
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
.foot{margin:22px 0 0;font-size:12px;color:#999;text-align:center;line-height:1.7;}
"""


def render_html(profile: Dict[str, Any]) -> str:
    if profile.get("empty"):
        return "<html><body><p>暂无可分析的订单数据。</p></body></html>"

    s = profile["summary"]
    parts: List[str] = []
    a = parts.append

    a('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">')
    a('<meta name="viewport" content="width=device-width,initial-scale=1">')
    a("<title>麦麦时光机 · 我的麦当劳年度报告</title>")
    a(f"<style>{_CSS}</style></head><body><div class='wrap'>")

    # Hero
    a('<div class="hero">')
    a("<h1>麦麦时光机</h1>")
    a("<p>把吃过的每一单，变成一份年度回忆</p>")
    a(f"<div class='range'>统计区间 {_esc(s['first_date'])} ~ {_esc(s['last_date'])}　"
      f"跨度 {s['span_days']} 天　生成于 {_esc(profile['generated_at'])}</div>")
    a('<div class="stats">')
    for value, key in (
        (f"{s['total_orders']} 单", "订单总数"),
        (f"¥{s['total_spend']:,.0f}", "累计消费"),
        (f"¥{s['avg_order']:.1f}", "平均客单价"),
        (f"{s['total_items']} 件", "单品总数"),
        (f"{s['best_streak']} 天", "最长连续打卡"),
    ):
        a(f"<div class='stat'><div class='v'>{_esc(value)}</div><div class='k'>{_esc(key)}</div></div>")
    a("</div></div>")

    # 口味画像
    a('<div class="card"><h2>口味画像</h2><div class="tags">')
    for tag in profile["tags"]:
        a(f"<span class='tag'><b>{_esc(tag['tag'])}</b> · {_esc(tag['desc'])}</span>")
    a("</div></div>")

    def _chart(title: str, rows: List[Dict[str, Any]], unit: str) -> None:
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

    # 年度回忆
    a('<div class="card"><h2>年度回忆</h2><div class="timeline">')
    for memo in profile["memories"]:
        a(f"<div class='item'><div class='t'>{_esc(memo['title'])}</div>"
          f"<div class='d'>{_esc(memo['text'])}</div></div>")
    a("</div></div>")

    # 常点单品
    a('<div class="card"><h2>常点单品</h2>')
    for item in profile["top_items"]:
        a(f"<span class='pill'>{_esc(item['name'])} × {item['qty']}</span>")
    a("</div>")

    # 黄金搭档
    if profile["combos"]:
        a('<div class="card"><h2>黄金搭档</h2>')
        for combo in profile["combos"]:
            a(f"<span class='pill'>{_esc(combo['pair'])} · {combo['count']} 次</span>")
        a("</div>")

    # 常去门店
    if profile["stores"] and profile["stores"][0]["count"]:
        a('<div class="card"><h2>常去门店</h2>')
        for store in profile["stores"]:
            if store["name"]:
                a(f"<span class='pill'>{_esc(store['name'])} · {store['count']} 次</span>")
        a("</div>")

    # 推荐
    a('<div class="card"><h2>下次试试</h2>')
    for rec in profile["recommendations"]:
        a(f"<div class='rec'><div class='n'>{_esc(rec['name'])}</div>"
          f"<div class='r'>{_esc(rec['reason'])}</div></div>")
    a("</div>")

    a('<p class="foot">本报告由「麦麦时光机」基于麦当劳中国 MCP 开放能力本地生成，'
      '数据仅保存于本机，不上传、不外传。<br>'
      '餐品信息、价格与营养数据以麦当劳官方渠道实时结果为准，本报告不构成营养或医疗建议。<br>'
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
