"""Command-line entry point for the Warden research harness."""

from __future__ import annotations

import argparse

from warden import __version__


def build_parser() -> argparse.ArgumentParser:
    description = (
        "Deterministic governance harness for agent-native quantitative "
        "research."
    )
    parser = argparse.ArgumentParser(
        prog="warden",
        description=description,
    )
    _ = parser.add_argument(
        "--version",
        action="version",
        version=f"warden {__version__}",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse CLI arguments; subcommands land in TKT-07."""
    _ = build_parser().parse_args(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
