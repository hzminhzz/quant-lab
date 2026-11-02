"""Diagnose PIT low-vol monthly exits against historically pinned raw Binance opens.

The sparse monthly-asof Top-100 panel can omit an asset immediately after
it leaves the elected universe, even when its original Binance USD-M market
is still trading. Warden's price.shift(-1).over(asset) is NOT an exact
next-HOUR return across such a roster gap. This script reads each
needed next-hour raw archive candle only if independently SHA-pinned
in the historical PIT membership source audit.

No source file on/after 2025-07-01 is opened. This is a diagnostic
for a future event-aware execution implementation; no actual fills
are inferred or Warden Stage 3 trial is created.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import polars as pl

from scripts.build_pit_top100_proposal import (
    OLD_AUDIT,
    STORE,
    load_membership,
)
from scripts.recover_pit_funding_archives import hash_file

PIT = Path("data/exploratory/crypto_perps_pit100_1h_v1.parquet")
SOURCE_GAPS = Path(".workspace/stage1_optimization/pit_executable_gap_audit_20261102.json")
OUT = Path("docs/evidence/arxiv_stage1_20261102/pit_monthly_exit_source_audit_20261102.json")
VAL_END = datetime.fromisoformat("2025-07-01T00:00:00+00:00")


def exact_next_hour_source(
    asset: str, decision: datetime, source_manifest: dict[str, dict],
    source_root: Path, *,
    cutoff: datetime = VAL_END,
) -> dict:
    """Return validated observed NEXT-HOUR raw open, or explicit failure."""
    if decision.tzinfo is None or decision.utcoffset() != timedelta(0):
        raise ValueError("Only UTC observed source clocks supported")
    stamp = decision + timedelta(hours=1)
    if stamp >= cutoff:
        raise ValueError("Refusing protected or post-validation next-hour price")
    rel = f"symbol={asset}/year={stamp.year}/month={stamp.month}/data.parquet"
    item = source_manifest.get(rel)
    evidence = {
        "asset": asset,
        "decision": decision.isoformat(),
        "required_exit_hour": stamp.isoformat(),
        "relative_source": rel,
        "source_digest": item["sha256"] if item else None,
    }
    if item is None:
        return {**evidence, "status": "SOURCE_NOT_IN_PINNED_PRE_CUTOFF_AUDIT"}
    path = source_root / rel
    if not path.is_file():
        return {**evidence, "status": "SOURCE_FILE_MISSING"}
    if hash_file(path) != item["sha256"]:
        raise ValueError(f"Binance raw source mutated since PIT audit: {path}")
    df = (
        pl.scan_parquet(path)
        .select(
            pl.col("timestamp").cast(pl.Datetime("us", "UTC")),
            pl.col("symbol").alias("asset"),
            pl.col("open").cast(pl.Float64),
            pl.col("volume").cast(pl.Float64),
        )
        .filter(pl.col("timestamp") == stamp)
        .collect()
    )
    if df.height == 0:
        return {**evidence, "status": "HISTORICAL_NEXT_OPEN_ABSENT"}
    if df.height != 1 or df["asset"][0] != asset:
        raise ValueError("Duplicate or wrong market in raw source after SHA verification")
    price, volume = df["open"][0], df["volume"][0]
    if price is None or not 0 < float(price) < float("inf"):
        return {**evidence, "status": "UNEXECUTABLE_NEXT_OPEN"}
    if volume is None or float(volume) < 0:
        return {**evidence, "status": "INVALID_SOURCE_VOLUME"}
    return {
        **evidence,
        "status": "SOURCE_OBSERVED_NEXT_OPEN",
        "observed_next_open": float(price),
        "observed_volume": float(volume),
        "actual_fill_verified": False,
    }


def classify(
    records: list[dict], source_manifest: dict[str, dict], source_root: Path,
) -> dict:
    next_gaps = sorted(
        (e for e in records if e["reason"] == "missing_next_open"),
        key=lambda x: (x["time_utc"], x["symbol"]),
    )
    current_gaps = sorted(
        (e for e in records if e["reason"] == "missing_current_open"),
        key=lambda x: (x["time_utc"], x["symbol"]),
    )
    terminal = [e for e in records if e["reason"] == "terminal_no_next_exploratory_open"]
    mapped = {
        (x["symbol"], datetime.fromisoformat(x["time_utc"])): x
        for x in current_gaps
    }
    if len(mapped) != len(current_gaps):
        raise ValueError("Duplicate monthly source boundary")
    aligned = []
    for item in next_gaps:
        prior = datetime.fromisoformat(item["time_utc"])
        nxt = mapped.get((item["symbol"], prior + timedelta(hours=1)))
        if nxt is None:
            raise ValueError("Missing matched current-hour roster-transition observation")
        if abs(float(nxt["position_weight"]) - float(item["position_weight"])) > 1e-12:
            raise ValueError("Inconsistent delayed position across monthly boundary")
        quote = exact_next_hour_source(
            item["symbol"], prior, source_manifest, source_root
        )
        aligned.append({
            **quote,
            "position_weight": float(item["position_weight"]),
            "panel_excluded_from_next_month": True,
        })
    if len(next_gaps) != len(current_gaps):
        raise ValueError("Unmatched current and next-hour gaps")
    source_cases = dict(Counter(item["status"] for item in aligned))
    return {
        "monthly_position_boundary_pairs": len(aligned),
        "monthly_exit_quote_source": source_cases,
        "monthly_open_exposure_abs_sum": sum(
            abs(float(x["position_weight"])) for x in aligned
        ),
        "terminal_positions_without_legal_next_price": len(terminal),
        "terminal_exposure_abs_sum": sum(abs(float(x["position_weight"])) for x in terminal),
        "transition_evidence": aligned,
        "terminal_assets": sorted({x["symbol"] for x in terminal}),
        "never_infer_executable_fills_from_ohlcv": True,
    }


def study(panel: Path = PIT, gap_report: Path = SOURCE_GAPS) -> dict:
    approved = load_membership(OLD_AUDIT)
    source = {x["path"]: x for x in approved["source_shards"]}
    gaps = json.loads(gap_report.read_text())
    if hash_file(panel) != gaps["dataset_sha256"]:
        raise ValueError("PIT price source/Stage1 gap evidence SHA mismatch")
    if gaps.get("holdout_read") is not False or gaps["contract_id"] != "CRYPTO_PERPS_PIT100_1H_V1":
        raise ValueError("Wrong/unsafe causal validation gap report")
    if gaps["validation_end_exclusive"][:10] != "2025-07-01":
        raise ValueError("Wrong validation boundary")
    # Defect of sparse PIT panel itself: observed NEXT-ASSET bar may not
    # represent the actual next hour in a continuous exchange series.
    df = pl.scan_parquet(panel).select("asset", "timestamp").sort("asset", "timestamp")
    breaks = (
        df.with_columns(
            pl.col("timestamp").shift(-1).over("asset").alias("next_asset_bar")
        )
        .filter(
            pl.col("next_asset_bar").is_not_null()
            & ((pl.col("next_asset_bar") - pl.col("timestamp")) != pl.duration(hours=1))
        )
        .select(
            pl.len().alias("cross_gap_next_observed_bar_pairs"),
            (pl.col("next_asset_bar")-pl.col("timestamp"))
                .dt.total_seconds().max().alias("max_gap_seconds"),
        )
        .collect()
        .row(0, named=True)
    )
    outcome = classify(gaps["executable_gap_audit"]["events"], source, STORE)
    return {
        "status": "READ_ONLY_EXECUTION_SOURCE_BRIDGE_NOT_STAGE3",
        "contract": gaps["contract_id"],
        "dataset_sha256": gaps["dataset_sha256"],
        "asof_member_source_sha256": approved["source_manifest_sha256"],
        "asof_audit_sha256": hash_file(OLD_AUDIT),
        "reference_warden_gap_report_sha256": hash_file(gap_report),
        "raw_archive_source_root": str(STORE),
        "sparse_panel": {
            "cross_gap_next_observed_bar_pairs": int(
                breaks["cross_gap_next_observed_bar_pairs"]
            ),
            "max_gap_hours": (
                int(breaks["max_gap_seconds"] / 3600)
                if breaks["max_gap_seconds"] is not None else 0
            ),
            "shift_minus_one_is_not_exact_next_hour": (
                breaks["cross_gap_next_observed_bar_pairs"] > 0
            ),
        },
        "exit_coverage": outcome,
        "no_post_cutoff_prices_read": True,
        "no_holdout_access": True,
        "no_fills_or_stage3": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--panel", type=Path, default=PIT)
    parser.add_argument("--gap-report", type=Path, default=SOURCE_GAPS)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Immutable execution source audit already exists")
    report = study(args.panel, args.gap_report)
    payload = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=args.output.parent, delete=False) as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
        tmp = Path(fh.name)
    try:
        os.link(tmp, args.output)
    finally:
        tmp.unlink(missing_ok=True)
    print(json.dumps({
        "evidence_sha256": hashlib.sha256(payload).hexdigest(),
        "sparse_panel": report["sparse_panel"],
        "exit_summary": {
            k: v for k, v in report["exit_coverage"].items()
            if k != "transition_evidence"
        },
    }, indent=2))


if __name__ == "__main__":
    main()
