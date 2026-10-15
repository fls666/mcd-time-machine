#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""麦麦时光机 · Windows 一键联调向导

作用：把「装环境 → 配 Token → 跑诊断 → 导出脱敏结构」串成一条命令。

用法（在项目根目录执行）：

    python scripts\\windows_wizard.py

向导会依次做四件事：
    1. 检查 Python 版本（要求 3.9+）
    2. 让你粘贴 MCP Token（输入时不回显），可选永久写入用户环境变量
    3. 调用 mcdtm doctor，打印连通性诊断
    4. 调用 mcdtm share，生成脱敏后的 mcdtm-output\\share.json（公开前仍建议人工检查）

Token 不会被写入任何项目文件，也不会打印到屏幕。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# 让 Windows 控制台正确显示中文
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError, OSError):
        pass

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ENV_NAME = "MCD_MCP_TOKEN"

LINE = "-" * 66


def step(n: int, text: str) -> None:
    print(f"\n{LINE}\n[{n}/4] {text}\n{LINE}")


def ask_yes_no(question: str, default: bool = True) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        raw = input(f"{question} {suffix} ").strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("  请输入 y 或 n")


def check_python() -> bool:
    step(1, "检查 Python 环境")
    print(f"  版本      : {sys.version.split()[0]}")
    print(f"  可执行程序: {sys.executable}")
    if sys.version_info < (3, 9):
        print("\n  [失败] 需要 Python 3.9 或更高版本。")
        print("  请到 https://www.python.org/downloads/windows/ 下载安装，")
        print("  安装时务必勾选「Add python.exe to PATH」。")
        return False
    print("  [通过] 版本满足要求（本项目零第三方依赖，无需 pip install）")
    return True


def setup_token() -> str:
    step(2, "配置 MCP Token")
    print("  还没有 Token？按以下步骤获取：")
    print("    1. 浏览器打开 https://open.mcd.cn/mcp")
    print("    2. 用手机号登录麦当劳开放平台")
    print("    3. 进入控制台，创建 / 激活 MCP 凭证，复制 Token")
    print()

    existing = os.environ.get(ENV_NAME, "").strip()
    if existing:
        print(f"  检测到环境变量 {ENV_NAME} 已存在（长度 {len(existing)}）。")
        if ask_yes_no("  直接使用它？"):
            return existing

    try:
        import getpass
        token = getpass.getpass("  请粘贴 Token（输入不回显，回车确认）: ").strip()
    except Exception:  # 某些终端不支持 getpass
        token = input("  请粘贴 Token: ").strip()

    if not token:
        print("  [失败] Token 为空。")
        sys.exit(1)
    if len(token) < 8:
        print("  [警告] Token 看起来太短，请确认是否复制完整。")

    os.environ[ENV_NAME] = token  # 立即对本次运行生效

    if ask_yes_no("\n  是否永久写入 Windows 用户环境变量？（以后新开窗口也能用）"):
        if sys.platform == "win32":
            try:
                # setx 有 1024 字符限制，Token 通常远小于此
                subprocess.run(
                    ["setx", ENV_NAME, token],
                    check=True, capture_output=True, text=True,
                )
                print(f"  已写入用户环境变量 {ENV_NAME}。")
                print("  注意：当前已打开的窗口不会自动生效，新开一个窗口即可；")
                print("       本向导已在当前进程内设置好，可继续。")
            except Exception as exc:
                print(f"  写入失败（{exc}），你可以手动执行：")
                print(f'    setx {ENV_NAME} "你的Token"')
        else:
            print("  非 Windows 系统，跳过永久写入。请自行写入 shell 配置文件：")
            print(f'    export {ENV_NAME}="你的Token"')
    else:
        print("  本次仅在当前进程生效。")

    return token


def run_doctor() -> bool:
    step(3, "诊断真实 MCP 连通性")
    from mcd_timemachine.cli import main as cli_main

    print("  正在连接 https://mcp.mcd.cn ...\n")
    code = cli_main(["doctor", "--out", "mcdtm-output"])
    print()
    if code == 0:
        print("  [通过] 已成功取到 order-list 的真实返回。")
        return True
    print("  [未通过] 请看上面的报错，对照 docs/WINDOWS_SETUP.md 的排障表。")
    return False


def run_share(doctor_ok: bool) -> None:
    step(4, "导出脱敏结构报告")
    if not doctor_ok:
        print("  跳过：诊断未通过，没有可导出的返回结构。")
        print("  请先对照 docs\\WINDOWS_SETUP.md 的排障表解决连通性问题。")
        return

    from mcd_timemachine.cli import main as cli_main

    code = cli_main(["share", "--out", "mcdtm-output"])
    if code == 0:
        print("\n  把 mcdtm-output\\share.json 的内容贴到 Issue 即可，")
        print("  里面只有字段名和值的类型与长度，已扫描手机号、邮箱、URL 等敏感串。")


def main() -> int:
    print("=" * 66)
    print("  麦麦时光机 · Windows 一键联调向导")
    print("=" * 66)

    if not check_python():
        return 1
    setup_token()
    ok = run_doctor()
    run_share(ok)

    print(f"\n{LINE}")
    if ok:
        print("联调完成。下一步：")
        print("  python -m mcd_timemachine fetch     # 拉取真实订单（自动翻页）")
        print("  python -m mcd_timemachine analyze   # 生成年度报告")
        print("  或一步到位：python -m mcd_timemachine all")
        print("\n报告在 mcdtm-output\\report.html，双击即可用浏览器打开。")
    else:
        print("联调未通过。请阅读 docs\\WINDOWS_SETUP.md 的「排障对照表」，")
        print("重点确认：Token 是否完整复制、环境变量是否在新窗口生效、网络是否可达。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
