"""麦麦时光机命令行入口。

    mcdtm demo              用虚构示例数据跑通全流程（无需 Token）
    mcdtm doctor            诊断真实 MCP 连通性与返回结构
    mcdtm tools             列出麦当劳 MCP 当前可用的工具
    mcdtm fetch             通过 MCP 拉取真实订单与菜单数据（自动翻页）
    mcdtm analyze           分析并生成报告
    mcdtm all               fetch + analyze 一步到位
    mcdtm story             用已有 profile.json 重新生成沉浸式故事页 story.html
    mcdtm inspect orders    查看某个工具的原始返回，便于排查字段

每次分析都会产出三份同源文件：report.md（完整报告）、report.html（完整报告网页）、
story.html（V2.0 六章节滚动故事 + 隐私友好分享卡片）。

story.html 有两种生成模式，用 `--privacy` 选择：
    personal（默认）个人完整版：保留金额 / 门店 / 日期，页面另有「隐私浏览」临时遮挡开关；
    public          公开分享版：金额 / 门店名 / 到日日期在生成时就从 HTML 里移除，可直接分享。

公开版是 fail-closed 的：生成之后会跑两道检查——脱敏自检（成品里是否还有敏感值残留）
与内容完整性检查（章节 / 计数是否被误抹）。任一项不通过就**直接报错、不写出文件**
（退出码 3 = 脱敏自检未过，4 = 内容完整性未过），而不是悄悄替换后继续输出。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .analyzer import analyze
from .collector import build_coverage, collect, load_coverage, load_normalized
from .demo_data import generate
from .mcp_client import DEFAULT_ENDPOINT, McpClient, McpError, TOOLS
from .reporter import print_console, write_reports
from .share import write_share
from .story import PRIVACY_MODES, PublicContentError, PrivacyLeakError

DEFAULT_OUT = Path("mcdtm-output")


def configure_windows_console() -> None:
    """Windows 控制台默认用 GBK，中文输出可能抛 UnicodeEncodeError。

    这里把标准流切到 UTF-8；失败时静默降级，不影响主流程。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError, OSError):
            pass


# ---------------------------------------------------------------- 辅助

def _load_extras(out_dir: Path) -> Dict[str, Any]:
    """把 fetch 阶段落盘的原始数据重新读出来，供推荐逻辑使用。"""
    extras: Dict[str, Any] = {}
    for key in ("meals", "nutrition", "coupons", "points", "campaign"):
        path = out_dir / "raw" / f"{key}.json"
        if path.exists():
            try:
                extras[key] = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
    return extras


def _run_analysis(
    orders: List[Dict[str, Any]],
    out_dir: Path,
    extras: Dict[str, Any],
    coverage: Dict[str, Any],
    privacy: str = "personal",
) -> None:
    profile = analyze(orders, extras or None, coverage)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "orders.normalized.json").write_text(
        json.dumps(orders, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "provenance.json").write_text(
        json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    paths = write_reports(profile, out_dir, privacy)
    print_console(profile)
    print()
    print(f"报告已生成：{paths['html']}  {paths['md']}")
    if paths.get("story"):
        print(f"沉浸式故事：{paths['story']}（手机竖屏浏览，第 6 章可导出分享卡片）")
        _print_privacy_hint(privacy)


def _print_privacy_hint(privacy: str) -> None:
    if privacy == "public":
        print("生成模式：公开分享版 —— 金额、门店名称、到日日期已在生成时移除，本文件可直接分享。")
        print("已通过两道检查：脱敏自检（0 处敏感值残留）+ 内容完整性（章节与计数齐备）；"
              "任一项不通过都不会写出文件。")
        print("注意：同目录的 report.html / report.md / profile.json 仍含完整数据，不要一并发出。")
    else:
        print("生成模式：个人完整版 —— story.html 内含金额、门店与具体日期，请勿直接转发；")
        print("要对外分享，可另跑 `mcdtm story --privacy public`，或只发第 6 章导出的 share-card.png。")


# ---------------------------------------------------------------- 子命令

def cmd_demo(args: argparse.Namespace) -> int:
    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    orders = generate(orders=args.count, seed=args.seed)
    coverage = build_coverage(
        source="demo",
        pages=1,
        raw_records=len(orders),
        orders=orders,
        warnings=[
            "本报告的订单由程序生成，不来自任何真实账号，仅用于演示输出效果",
            "运行 `mcdtm fetch` 可替换为你的真实订单数据",
        ],
        tools={"demo_data.generate": "ok"},
    )
    print(f"[demo] 已生成 {len(orders)} 条虚构订单（示例数据，非真实账号数据）")
    _run_analysis(orders, out_dir, {}, coverage, getattr(args, "privacy", "personal"))
    return 0


def cmd_tools(args: argparse.Namespace) -> int:
    client = McpClient(token=args.token, endpoint=args.endpoint)
    client.initialize()
    tools = client.list_tools()
    print(f"共 {len(tools)} 个工具：")
    for tool in tools:
        name = tool.get("name", "?")
        desc = (tool.get("description") or "").splitlines()[:1]
        mark = "  *" if name in TOOLS.values() else "   "
        print(f"{mark} {name:<28} {desc[0] if desc else ''}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """真实 MCP 联调诊断：把连通性、工具 schema 与首屏返回结构打印出来。"""
    client = McpClient(token=args.token, endpoint=args.endpoint)
    report = client.diagnose(tool=args.tool)
    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "doctor.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"接入地址   {report['endpoint']}")
    print(f"服务端     {report.get('server')}")
    print(f"协议版本   {report.get('protocol_version')}")
    print(f"工具总数   {report.get('tools_count')}")
    print(f"目标工具   {report.get('target_tool')} "
          f"（{'存在' if report.get('target_found') else '不存在'}）")
    print(f"入参 schema {json.dumps(report.get('input_schema', {}), ensure_ascii=False)[:400]}")
    if report.get("call_ok"):
        print(f"调用成功   参数={report.get('call_args')} "
              f"类型={report.get('response_type')}")
        print("返回样例：")
        print(str(report.get("sample", ""))[:1200])
    else:
        print(f"调用失败   {report.get('call_error')}")
    print(f"\n完整诊断已写入 {out_dir / 'doctor.json'}")
    return 0 if report.get("call_ok") else 1


def cmd_fetch(args: argparse.Namespace) -> int:
    out_dir: Path = args.out
    client = McpClient(token=args.token, endpoint=args.endpoint)
    client.initialize()
    print("[fetch] MCP 会话已建立")
    bundle = collect(
        client,
        out_dir,
        with_optional=not args.minimal,
        max_pages=args.max_pages,
        page_size=args.page_size,
    )
    orders = bundle["orders"]
    cov = bundle["coverage"]
    print(f"[fetch] 翻页 {cov['pages']} 页，原始 {cov['raw_records']} 条，"
          f"去重后 {cov['unique_orders']} 单")
    print(f"[fetch] 有日期 {cov['with_date']}，有金额 {cov['with_amount']}，"
          f"有明细 {cov['with_items']}")
    for warn in cov["warnings"]:
        print(f"[warn] {warn}")
    if not orders:
        print("[fetch] 未解析出订单，请执行：mcdtm doctor 查看原始返回结构。")
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    out_dir: Path = args.out
    orders = load_normalized(out_dir)
    _run_analysis(orders, out_dir, _load_extras(out_dir), load_coverage(out_dir),
                  getattr(args, "privacy", "personal"))
    return 0


def cmd_story(args: argparse.Namespace) -> int:
    """用已生成的 profile.json 单独重刷故事页（不重新联网、不重新分析）。"""
    out_dir: Path = args.out
    profile_path = out_dir / "profile.json"
    if not profile_path.exists():
        print(f"找不到 {profile_path}。请先执行 `mcdtm analyze`（或 `mcdtm demo`）。")
        return 1
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"profile.json 解析失败：{exc}")
        return 1
    privacy = getattr(args, "privacy", "personal")
    from .story import write_story

    target = write_story(profile, out_dir, privacy)
    print(f"已生成 {target}")
    _print_privacy_hint(privacy)
    return 0


def cmd_all(args: argparse.Namespace) -> int:
    rc = cmd_fetch(args)
    if rc != 0:
        return rc
    try:
        orders = load_normalized(args.out)
    except FileNotFoundError as exc:
        print(exc)
        return 1
    _run_analysis(orders, args.out, _load_extras(args.out), load_coverage(args.out),
                  getattr(args, "privacy", "personal"))
    return 0


def cmd_share(args: argparse.Namespace) -> int:
    """导出脱敏后的结构报告，用于排障求助（公开前仍需人工检查）。"""
    out_dir: Path = args.out
    if not out_dir.exists():
        print(f"找不到 {out_dir}。请先执行 `mcdtm doctor` 或 `mcdtm fetch`。")
        return 1
    target = write_share(out_dir)
    print(f"已生成 {target}")
    if not (out_dir / "doctor.json").exists() and not (out_dir / "raw").is_dir():
        print("提示：目录里还没有 doctor.json 或 raw/，请先跑 `mcdtm doctor`。")
        return 1
    print("内容仅含字段名与值的类型/长度，已扫描手机号、邮箱、身份证、URL 与长串凭证。")
    print(f"大小：{target.stat().st_size} 字节")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    path: Path = args.out / "raw" / f"{args.key}.json"
    if not path.exists():
        print(f"找不到 {path}。可先执行 `mcdtm fetch`。")
        return 1
    text = path.read_text(encoding="utf-8")
    print(text[: args.limit])
    if len(text) > args.limit:
        print(f"\n... 已截断（共 {len(text)} 字符），用 --limit 调整")
    return 0


# ---------------------------------------------------------------- 参数

def _common_parser() -> argparse.ArgumentParser:
    """子命令通用参数：用 SUPPRESS，未指定时不覆盖全局值。"""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--token", default=argparse.SUPPRESS,
                        help="麦当劳 MCP Token（建议改用环境变量 MCD_MCP_TOKEN）")
    common.add_argument("--endpoint", default=argparse.SUPPRESS,
                        help=f"MCP 接入地址，默认 {DEFAULT_ENDPOINT}")
    common.add_argument("--out", type=Path, default=argparse.SUPPRESS,
                        help=f"输出目录，默认 {DEFAULT_OUT}")
    common.add_argument("--privacy", choices=list(PRIVACY_MODES), default=argparse.SUPPRESS,
                        help="story.html 生成模式：personal=个人完整版（默认），"
                             "public=公开分享版（生成时即移除金额/门店名/到日日期）")
    return common


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcdtm",
        description="麦麦时光机：把麦当劳历史订单变成年度回忆与口味画像",
    )
    parser.add_argument("--version", action="version", version=f"mcdtm {__version__}")
    parser.add_argument("--token", default=None,
                        help="麦当劳 MCP Token（建议改用环境变量 MCD_MCP_TOKEN）")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT,
                        help=f"MCP 接入地址，默认 {DEFAULT_ENDPOINT}")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help="输出目录，默认 ./mcdtm-output")
    parser.add_argument("--privacy", choices=list(PRIVACY_MODES), default="personal",
                        help="story.html 生成模式：personal=个人完整版（默认，含原始数据），"
                             "public=公开分享版（金额/门店名/到日日期在生成时即移除）")

    sub = parser.add_subparsers(dest="command", required=True)
    common = _common_parser()

    p_demo = sub.add_parser("demo", parents=[common], help="用虚构示例数据跑通全流程")
    p_demo.add_argument("--count", type=int, default=160, help="示例订单条数")
    p_demo.add_argument("--seed", type=int, default=1024, help="随机种子")
    p_demo.set_defaults(func=cmd_demo)

    p_doctor = sub.add_parser("doctor", parents=[common],
                              help="诊断真实 MCP 连通性与返回结构")
    p_doctor.add_argument("--tool", default=TOOLS["orders"], help="要探测的工具名")
    p_doctor.set_defaults(func=cmd_doctor)

    p_tools = sub.add_parser("tools", parents=[common], help="列出 MCP 可用工具")
    p_tools.set_defaults(func=cmd_tools)

    p_fetch = sub.add_parser("fetch", parents=[common], help="拉取真实数据（自动翻页）")
    p_fetch.add_argument("--minimal", action="store_true", help="只拉订单，不拉菜单等增强数据")
    p_fetch.add_argument("--max-pages", type=int, default=30, help="最大翻页数，默认 30")
    p_fetch.add_argument("--page-size", type=int, default=100, help="每页条数，默认 100")
    p_fetch.set_defaults(func=cmd_fetch)

    p_analyze = sub.add_parser("analyze", parents=[common], help="分析并生成报告")
    p_analyze.set_defaults(func=cmd_analyze)

    p_story = sub.add_parser(
        "story", parents=[common],
        help="用已有 profile.json 重新生成沉浸式故事页 story.html")
    p_story.set_defaults(func=cmd_story)

    p_all = sub.add_parser("all", parents=[common], help="fetch + analyze")
    p_all.add_argument("--minimal", action="store_true", help="只拉订单")
    p_all.add_argument("--max-pages", type=int, default=30, help="最大翻页数")
    p_all.add_argument("--page-size", type=int, default=100, help="每页条数")
    p_all.set_defaults(func=cmd_all)

    p_inspect = sub.add_parser("inspect", parents=[common], help="查看原始返回数据")
    p_inspect.add_argument("key", help="数据键名，如 orders / meals / nutrition")
    p_inspect.add_argument("--limit", type=int, default=4000, help="打印字符数上限")
    p_inspect.set_defaults(func=cmd_inspect)

    p_share = sub.add_parser(
        "share", parents=[common],
        help="导出脱敏后的结构报告 share.json（求助用，公开前请人工检查）")
    p_share.set_defaults(func=cmd_share)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    configure_windows_console()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except PrivacyLeakError as exc:
        # fail-closed：报错但不产出文件。诊断行里的值一律打码，
        # 否则「报泄漏」本身又成了新的泄漏点。
        print(f"[错误] {exc}", file=sys.stderr)
        for line in exc.report():
            print("        " + line, file=sys.stderr)
        print("        已按 fail-closed 策略放弃输出：目录里不会出现这份公开版，"
              "若原本存在同名文件，那是上一次生成的结果。", file=sys.stderr)
        print("        排查方向：新增字段没被脱敏规则覆盖。可先用 "
              "`mcdtm story --privacy personal` 生成个人完整版自查，"
              "并把上面（已打码的）命中行反馈给作者。", file=sys.stderr)
        return 3
    except PublicContentError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        for line in exc.report():
            print("        " + line, file=sys.stderr)
        print("        这是「隐私保护过度」而不是泄漏：脱敏可能把与金额 / 门店 / 日期无关的"
              "内容也抹掉了，因此同样不下发文件，以免你分享出一份残缺的页面。", file=sys.stderr)
        return 4
    except McpError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n已取消", file=sys.stderr)
        return 130


__all__ = ["main", "build_parser"]
