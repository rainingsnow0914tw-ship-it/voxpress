"""Backward-compatible application entry point."""

from __future__ import annotations


def main() -> int:
    from voxpress.app import run

    return run()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
