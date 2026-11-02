"""Export only current research campaign's Warden Stage 1 receipts, with vectors.

This is a READ-ONLY evidence export from two isolated ledgers. It never
imports new attempts into the authoritative source registry. The gzip archive
is deterministic and preserves the exact canonical Warden return JSON.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
from pathlib import Path

ROOT = Path(".")
OUTPUT = Path("docs/evidence/arxiv_stage1_20261102/selection_attempts.json.gz")
SOURCES = (
    Path("registry/registry.db"),
    Path(".workspace/stage1_optimization/registry.db"),
)
IDS = {
    "HYP-ARXIV-BTCETH-4H-SIGN-20261102",
    "HYP-ARXIV-BTCETH-4H-BAND-20261102",
    "HYP-ARXIV-CRYPTO-6H-EQUAL-20261102",
    "HYP-ARXIV-CRYPTO-6H-VOLSCALE-20261102",
    "HYP-ARXIV-XS-PRICE-PRESSURE-20261102",
    "HYP-ARXIV-XS-LOWVOL-20261102",
    "HYP-ARXIV-XS-LOWVOL-24H-20261102",
}


def export() -> dict:
    if OUTPUT.exists():
        raise FileExistsError("Receipt archive is write-once")
    if len(IDS) != 7:
        raise ValueError("Unexpected scope")
    records: dict[str, dict] = {}
    for db_path in SOURCES:
        with sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            values = conn.execute(
                "SELECT * FROM selection_attempts WHERE hypothesis_id LIKE 'HYP-ARXIV-%'"
            ).fetchall()
            for row in values:
                row_dict = dict(row)
                if row_dict["hypothesis_id"] not in IDS:
                    continue
                if row_dict["stage"] != "STAGE_1_SIGNAL" or row_dict["kind"] != "standalone_screen":
                    raise ValueError("Unexpected non-screen evidence")
                ret = row_dict["returns_json"]
                if hashlib.sha256(ret.encode()).hexdigest() != row_dict["returns_sha256"]:
                    raise ValueError("Warden return-vector hash disagrees")
                if row_dict["returns_observed"] != 1 or not json.loads(ret):
                    raise ValueError("Missing observed return/IC vector")
                aid = row_dict["attempt_id"]
                if aid in records:
                    raise ValueError("Duplicate attempt in source histories")
                row_dict["origin"] = str(db_path)
                records[aid] = row_dict
    if len(records) != len(IDS) or {r["hypothesis_id"] for r in records.values()} != IDS:
        raise ValueError("Missing one or more immutable Warden receipts")

    payload = {
        "schema": "quant-lab-arxiv-stage1-receipts-v1",
        "description": "Seven append-only Warden Stage 1 attempts, including the exact return/IC streams",
        "not_authoritative": "Captured from isolated worktree research ledgers; no live registry mutations",
        "records": [records[key] for key in sorted(records)],
    }
    data = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("xb") as fd, gzip.GzipFile(fileobj=fd, mode="wb", mtime=0, filename="") as z:
        z.write(data)
    return {
        "records": len(records),
        "uncompressed_bytes": len(data),
        "archive_bytes": OUTPUT.stat().st_size,
        "archive_sha256": hashlib.sha256(OUTPUT.read_bytes()).hexdigest(),
        "path": str(OUTPUT),
        "outcomes": {
            "PASS": sum(v["outcome"] == "PASS" for v in records.values()),
            "REJECT": sum(v["outcome"] == "REJECT" for v in records.values()),
        },
    }


def main() -> None:
    print(json.dumps(export(), indent=2))


if __name__ == "__main__":
    main()
