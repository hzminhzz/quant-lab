"""Compatibility CLI: migrate a disposable snapshot, never the source ledger."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from warden.registry import ResearchRegistry
from warden_ops.ledger_maintenance import rehearse as _rehearse


def rehearse(source: Path, destination: Path) -> dict:
    return _rehearse(source, destination, registry_factory=ResearchRegistry)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(rehearse(args.source, args.destination), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
