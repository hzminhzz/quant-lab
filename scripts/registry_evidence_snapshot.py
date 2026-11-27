"""Compatibility CLI for the shared read-only WAL-consistent snapshot owner."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from warden_ops.ledger_maintenance import backup_readonly

__all__ = ["backup_readonly", "main"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(backup_readonly(args.source, args.destination), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
