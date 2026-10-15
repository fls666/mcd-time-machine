"""麦麦时光机命令行入口。

    mcdtm demo              用虚构示例数据跑通全流程（无需 Token）
    mcdtm tools             列出麦当劳 MCP 当前可用的工具
    mcdtm fetch             通过 MCP 拉取真实订单与菜单数据
    mcdtm analyze           分析并生成报告
    mcdtm all               fetch + analyze 一步到位
    mcdtm inspect orders    查看某个工具的原始返回，便于排查字段
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .analyzer import analyze
from .collector import collect, load_normalized
from .demo_data import generate
from .mcp_client import DEFAULT_ENDPOINT, McpClient, McpError, TOOLS
from .reporter import print_console, write_reports

DEFAULT_OUT = Path("mcdtm-output")


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


def _run_analysis(orders: List[Dict[str, Any]], out_dir: Path, extras: Dict[str, Any]) -> None:
    profile = analyze(orders, extras or None)
    (out_dir / "orders.normalized.json").write_text(
        json.dumps(orders, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    paths = write_reports(profile, out_dir)
    print_console(profile)
    print()
    print(f"报告已生成：{paths['html']}  {paths['md']}")


# ---------------------------------------------------------------- 子命令

def cmd_demo(args: argparse.Namespace) -> int:
    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    orders = generate(orders=args.count, seed=args.seed)
    print(f"[demo] 已生成 {len(orders)} 条虚构订单（示例数据，非真实账号数据）")
    _run_analysis(orders, out_dir, {})
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


def cmd_fetch(args: argparse.Namespace) -> int:
    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    client = McpClient(token=args.token, endpoint=args.endpoint)
    client.initialize()
    print("[fetch] MCP 会话已建立")
    bundle = collect(client, out_dir, with_optional=not args.minimal)
    orders = bundle.get("normalized_orders", [])
    print(f"[fetch] 解析到 {len(orders)} 条订单，原始数据已保存到 {out_dir / 'raw'}")
    if not orders:
        print("[fetch] 未解析出订单结构，请执行：mcdtm inspect orders 查看原始返回，")
        print("        或把 raw/orders.json 的内容结构反馈给项目 Issue。")
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    out_dir: Path = args.out
    orders = load_normalized(out_dir)
    _run_analysis(orders, out_dir, _load_extras(out_dir))
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
    _run_analysis(orders, args.out, _load_extras(args.out))
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    path: Path = args.out / "raw" / f"{args.key}.json"
    if not path.exists():
        print(f"找不到 {path}。可先执行 `mcdtm fetch`。")
        return 1
    text = path.read_text(encoding="utf-8")
    limit = args.limit
    print(text[:limit])
    if len(text) > limit:
        print(f"\n... 已截断（共 {len(text)} 字符），用 --limit 调整")
    return 0


# ---------------------------------------------------------------- 参数

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcdtm",
        description="麦麦时光机：把麦当劳历史订单变成年度回忆与口味画像",
    )
    parser.add_argument("--version", action="version", version=f"mcdtm {__version__}")
    parser.add_argument("--token", default=None, help="麦当劳 MCP Token（默认读环境变量 MCD_MCP_TOKEN）")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT,
                        help=f"MCP 接入地址，默认 {DEFAULT_ENDPOINT}")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="输出目录，默认 ./mcdtm-output")

    sub = parser.add_subparsers(dest="command", required=True)

    p_demo = sub.add_parser("demo", help="用虚构示例数据跑通全流程")
    p_demo.add_argument("--count", type=int, default=160, help="示例订单条数")
    p_demo.add_argument("--seed", type=int, default=1024, help="随机种子")
    p_demo.set_defaults(func=cmd_demo)

    p_tools = sub.add_parser("tools", help="列出 MCP 可用工具")
    p_tools.set_defaults(func=cmd_tools)

    p_fetch = sub.add_parser("fetch", help="拉取真实数据")
    p_fetch.add_argument("--minimal", action="store_true", help="只拉订单，不拉菜单等增强数据")
    p_fetch.set_defaults(func=cmd_fetch)

    p_analyze = sub.add_parser("analyze", help="分析并生成报告")
    p_analyze.set_defaults(func=cmd_analyze)

    p_all = sub.add_parser("all", help="fetch + analyze")
    p_all.add_argument("--minimal", action="store_true", help="只拉订单")
    p_all.set_defaults(func=cmd_all)

    p_inspect = sub.add_parser("inspect", help="查看原始返回数据")
    p_inspect.add_argument("key", help="数据键名，如 orders / meals / nutrition")
    p_inspect.add_argument("--limit", type=int, default=4000, help="打印字符数上限")
    p_inspect.set_defaults(func=cmd_inspect)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except McpError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n已取消", file=sys.stderr)
        return 130


__all__ = ["main", "build_parser"]
