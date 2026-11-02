"""Write-once PIT Warden Stage 1 evidence export, including full IC vector.

Read-only access to the isolated cumulative registry. Reconciles the four
older locally screened attempts via their immutable 7-attempt archive rather
than deleting, resetting, or importing source trials. This is *not* a Warden
qualify/backtest operation and does not inspect any protected price data.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import sqlite3
from pathlib import Path

import numpy as np

from warden.contracts import load_contract
from warden.gates import compute_hac_tstat
from warden.provenance import resolve_dataset

HYP = "HYP-ARXIV-XS-LOWVOL-PIT100-20261102"
DB = Path(".workspace/stage1_optimization/registry.db")
OLD_ARCHIVE = Path("docs/evidence/arxiv_stage1_20261102/selection_attempts.json.gz")
OLD_ARCHIVE_SHA = "4723cc14b70938de1ed04fbad279432f8b4f01971e9506fe905ff483ba31d772"
OUTPUT = Path("docs/evidence/arxiv_stage1_20261102/pit100_lowvol_stage1_receipt.json.gz")


def export() -> dict:
    if OUTPUT.exists():
        raise FileExistsError("Stage 1 receipt exists; evidence is append-only")
    if hashlib.sha256(OLD_ARCHIVE.read_bytes()).hexdigest() != OLD_ARCHIVE_SHA:
        raise ValueError("Historical arXiv selection archive changed")
    with gzip.open(OLD_ARCHIVE, "rt") as fd:
        prior = json.load(fd)
    before = prior["records"]
    if len(before) != 7 or len({r["attempt_id"] for r in before}) != 7:
        raise ValueError("Historical seven attempts are incomplete")
    with sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM selection_attempts WHERE hypothesis_id=?", (HYP,)
        ).fetchall()
        totals = {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("hypotheses", "trials", "selection_attempts")
        }
        recent = conn.execute(
            "SELECT attempt_id FROM selection_attempts "
            "WHERE hypothesis_id LIKE 'HYP-ARXIV-%'"
        ).fetchall()
    if len(rows) != 1:
        raise ValueError("Expected exactly one PIT Stage 1 attempt")
    rec = dict(rows[0])
    if rec["outcome"] != "PASS" or rec["stage"] != "STAGE_1_SIGNAL":
        raise ValueError("PIT outcome was not a legitimate Warden Stage 1 PASS")
    if rec["strategy_family"] != "cross_sectional_ml" or rec["kind"] != "standalone_screen":
        raise ValueError("Incorrect strategy family or attempt kind")
    ret_blob = rec["returns_json"]
    if hashlib.sha256(ret_blob.encode()).hexdigest() != rec["returns_sha256"]:
        raise ValueError("Recorded Warden IC vector hash mismatch")
    ic = np.asarray(json.loads(ret_blob), dtype=np.float64)
    if not len(ic) or not np.isfinite(ic).all():
        raise ValueError("PIT IC vector missing/nonfinite")
    metrics = json.loads(rec["diagnostics_json"])
    if not (math.isclose(float(np.mean(ic)), float(metrics["mean_ic"]), abs_tol=1e-12)
            and math.isclose(compute_hac_tstat(ic), float(metrics["ic_hac_tstat"]),
                             abs_tol=1e-10)):
        raise ValueError("Recorded Warden statistics cannot be independently reproduced")
    # Inspect nonoverlapping four-hour label phases; descriptive diagnosis
    # only, never another Warden selection attempt or trial.
    phase = [
        {
            "phase": i,
            "samples": len(ic[i::4]),
            "mean_ic": float(np.mean(ic[i::4])),
            "hac_t": float(compute_hac_tstat(ic[i::4])),
        }
        for i in range(4)
    ]
    contract = load_contract("contracts/crypto_perps_pit100_1h_v1.yaml")
    dataset = resolve_dataset(contract, Path("."))
    if not dataset.eligible_market or dataset.sha256 != metrics["dataset"]["sha256"]:
        raise ValueError("Contract manifest/source digest mismatch")
    # The old 4 experiments are archived elsewhere, not present in the
    # current 153/234 snapshot; keep both explicit and non-overlapping.
    old_ids = {r["attempt_id"] for r in before}
    present_ids = {r["attempt_id"] for r in recent}
    archived_only = sorted(old_ids - present_ids)
    if len(archived_only) != 4:
        raise ValueError("Unexpected overlapping history; do not silently double-count")
    doc = {
        "schema": "quantlab-pit-lowvol-stage1-receipt-v1",
        "source": "Unchanged Warden ExperimentRunner Stage 1 cross-sectional signal screen",
        "attempt": rec,
        "contract_spec_hash": contract.spec_hash(),
        "data_sha256": dataset.sha256,
        "recorded_observations": len(ic),
        "nonoverlapping_four_hour_ic_phases": phase,
        "original_seven_attempt_archive_sha256": OLD_ARCHIVE_SHA,
        "historical_archive_only_attempts": archived_only,
        "research_snapshot_counts_after": totals,
        "combined_distinct_research_attempts": totals["selection_attempts"] + len(archived_only),
        "hypothesis_is_descendant_of": "HYP-ARXIV-XS-LOWVOL-20261102",
        "limits": [
            "This is the same previously inspected validation time interval, not an untouched time holdout",
            "Factor was originally selected from 10 training-only probes, correlated earlier 4h and 24h screens",
            "Historical source funding missing 14.39% and has variable settlement intervals",
            "No delisting-complete market data or observed executable venue fills",
            "No Stage 3 trial, protected data read, or live authorization",
        ],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n").encode()
    with OUTPUT.open("xb") as fd, gzip.GzipFile(fileobj=fd, mode="wb", mtime=0, filename="") as gz:
        gz.write(encoded)
    return {
        "hypothesis": HYP,
        "attempt_id": rec["attempt_id"],
        "result": rec["outcome"],
        "observations": len(ic),
        "mean_ic": metrics["mean_ic"],
        "hac_t": metrics["ic_hac_tstat"],
        "four_hour_nonoverlap": phase,
        "snapshot_counts": totals,
        "combined_distinct_attempts": doc["combined_distinct_research_attempts"],
        "data_sha256": dataset.sha256,
        "archive": str(OUTPUT),
        "archive_sha256": hashlib.sha256(OUTPUT.read_bytes()).hexdigest(),
        "archive_bytes": OUTPUT.stat().st_size,
        "no_holdout_read": True,
    }


def main() -> None:
    print(json.dumps(export(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
