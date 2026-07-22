"""CLI for inspecting and installing FDE host compatibility bundles."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional, Sequence

from .compiler import CompatibilityCompiler
from .registry import CompatibilityRegistry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fde-compat")
    subparsers = parser.add_subparsers(dest="command", required=True)
    matrix = subparsers.add_parser("matrix", help="输出当前宿主能力与兼容策略")
    matrix.add_argument("--json", action="store_true")
    check = subparsers.add_parser("check", help="检查一个宿主是否覆盖 FDE 硬契约")
    check.add_argument("--host", required=True)
    install = subparsers.add_parser("install", help="生成宿主专用角色和运行契约")
    install.add_argument("--host", required=True)
    install.add_argument("--target", required=True)
    install.add_argument("--mode", choices=("fail", "overwrite"), default="fail")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    registry = CompatibilityRegistry.default()
    if args.command == "matrix":
        rows = registry.matrix()
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            for row in rows:
                status = "兼容" if row["contract_compatible"] else "缺能力"
                print(
                    f"{row['platform_id']:<28} {row['compatibility_mode']:<20} {status}"
                )
        return 0
    if args.command == "check":
        result = registry.assess(args.host)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["contract_compatible"] else 2
    root = Path(__file__).resolve().parents[2]
    installed = CompatibilityCompiler(root, registry).install(
        args.host, args.target, args.mode
    )
    print(json.dumps({"host": args.host, "files": installed}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
