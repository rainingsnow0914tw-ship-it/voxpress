"""Command-line entry point for VoxPress."""

from __future__ import annotations

import argparse

from voxpress import __version__
from voxpress.config import load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="voxpress")
    parser.add_argument("--version", action="store_true", help="show version and exit")
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="validate config and print privacy-safe metadata",
    )
    parser.add_argument(
        "--console", action="store_true", help="also write metadata logs to console"
    )
    parser.add_argument(
        "--diagnostics", action="store_true", help="enable hotkey metadata diagnostics"
    )
    args = parser.parse_args(argv)

    if args.version:
        print(__version__)
        return 0
    if args.check_config:
        import json

        config = load_config()
        print(json.dumps(config.public_dict(), ensure_ascii=False, indent=2))
        return 0 if not config.warnings else 1

    from voxpress.app import run

    return run(console=args.console, diagnostics=args.diagnostics)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
