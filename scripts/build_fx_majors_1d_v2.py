#!/usr/bin/env python3
"""Build the FX_MAJORS_1D_V2 exploratory panel from the Dukascopy daily stores.

Why this exists instead of reusing `salvage_multi_asset.py`: V1's panel is resampled from the
LSE minute stores, which carry level-shifted bars (see .workspace/fx_stage3/decision_record.md).
The Dukascopy daily stores are clean on every affected date and are the FX authority for daily
bars, so the panel is rebuilt from them.

Two deliberate omissions:

* **No holdout file is written.** The holdout range is counted and reported, never materialised
  here: creating an unpinned holdout lets `resolve_dataset(role="holdout")` accept any bytes as
  verified market data, and sealing is the principal's job.
* **No cost change.** The contract keeps V1's taker_fee_bps + slippage_bps floor. NEXT_BAR fills
  at the open mid, so the half-spread is unmodelled; that is disclosed, not silently re-priced.

Usage:
    uv run python scripts/build_fx_majors_1d_v2.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import polars as pl
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from salvage_multi_asset import FX_PAIRS

from warden_evidence.market_validation import (
    build_dataset_manifest,
    validate_ohlcv_panel,
    write_contract_and_manifest_atomic,
)

STORE = Path("/home/quant/dev/quant/ml4t-data/.data/store")
CONTRACT = Path("contracts/fx_majors_1d_v2.yaml")
OUT = Path("data/exploratory/fx_majors_1d_v2.parquet")
MANIFEST = Path("contracts/datasets/FX_MAJORS_1D_V2.json")
HOLDOUT_START = "2025-07-01T00:00:00Z"


def load_pair(store: Path, pair: str) -> tuple[pl.DataFrame, list[str]]:
    files = sorted((store / f"forex_daily_{pair}").rglob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"missing Dukascopy daily store forex_daily_{pair}")
    df = pl.concat([pl.read_parquet(f) for f in files])
    df = df.with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))
    # the tick store carries out-of-range years; the daily stores must not
    years = df["timestamp"].dt.year()
    if (years < 2000).any() or (years > 2100).any() or df["timestamp"].is_null().any():
        raise ValueError(f"forex_daily_{pair}: implausible timestamps")
    # The store directory is the authoritative pair name. One store disagrees in its `symbol`
    # column (forex_daily_USDJPY labels every row "JPY"), so the label is taken from the store
    # name and the disagreement is recorded — never silently rewritten without a trace.
    labels = sorted(df["symbol"].unique().to_list()) if "symbol" in df.columns else [pair]
    parts = {pair[:3], pair[3:]}
    if not (set(labels) == {pair} or set(labels) <= parts):
        raise ValueError(f"forex_daily_{pair}: symbol labels {labels} do not belong to {pair}")
    return df, ([] if labels == [pair] else labels)


def build(store: Path = STORE) -> tuple[pl.DataFrame, dict]:
    frames, mismatches = [], {}
    for pair in FX_PAIRS:
        df, labels = load_pair(store, pair)
        if labels:
            mismatches[pair] = labels
        frames.append(df.select(
            pl.lit(pair).alias("asset"), "timestamp", "open", "high", "low", "close", "volume"))
    panel = pl.concat(frames)
    panel = validate_ohlcv_panel(panel, "fx", max_return_threshold=0.20)

    cutoff = pl.lit(HOLDOUT_START).str.to_datetime(time_zone="UTC")
    exploratory = panel.filter(pl.col("timestamp") < cutoff)
    if exploratory.filter(pl.col("timestamp") >= cutoff).height:
        raise ValueError("exploratory split contains holdout rows")
    coverage = {a: int(exploratory.filter(pl.col("asset") == a).height) for a in sorted(FX_PAIRS)}
    counts = {k: v for k, v in coverage.items() if v == 0}
    if counts:
        raise ValueError(f"pairs with no exploratory rows: {sorted(counts)}")

    # calibration: bars per year, and the single-bar count the contract must not exceed
    span_days = (exploratory["timestamp"].max() - exploratory["timestamp"].min()).days
    bpy = exploratory.height / len(FX_PAIRS) / (span_days / 365.25)
    stats = {
        "rows": exploratory.height,
        "assets": len(FX_PAIRS),
        "min": str(exploratory["timestamp"].min()),
        "max": str(exploratory["timestamp"].max()),
        "bars_per_year": round(bpy, 2),
        "holdout_rows_available": panel.height - exploratory.height,
        "per_asset": coverage,
        "source_symbol_label_mismatch": mismatches,
    }
    return exploratory.sort("timestamp", "asset"), stats


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--store-dir", type=Path, default=STORE)
    p.add_argument("--out", type=Path, default=OUT)
    p.add_argument("--manifest", type=Path, default=MANIFEST)
    p.add_argument("--contract", type=Path, default=CONTRACT)
    a = p.parse_args(argv)

    if not a.contract.is_file():
        raise FileNotFoundError(f"Contract YAML not found: {a.contract}")

    panel, stats = build(a.store_dir)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    panel.write_parquet(a.out)
    sha = hashlib.sha256(a.out.read_bytes()).hexdigest()

    manifest_doc = build_dataset_manifest(
        contract_id="FX_MAJORS_1D_V2",
        path=str(a.out),
        sha256=sha,
        provenance="verified_market",
        source="ml4t-data Dukascopy daily OHLCV store (forex_daily_*)",
        pipeline="scripts/build_fx_majors_1d_v2.py",
        recorded_at="2026-10-28T00:00:00+00:00",
        recorded_by="agent:fx_stage3",
        attestation=(
            "Dukascopy is the daily FX authority; OHLCV only, so execution_mode is "
            "NEXT_BAR. Digest verified independently against manifest and contract pin."
        ),
        coverage=stats,
    )
    contract_doc = yaml.safe_load(a.contract.read_text())
    contract_doc["exploratory_data_path"] = str(a.out)
    contract_doc["data_snapshot_sha256"] = sha
    contract_doc["data_provenance"] = "verified_market"
    write_contract_and_manifest_atomic(
        a.contract,
        contract_doc,
        a.manifest,
        manifest_doc,
        repo_root=a.contract.parent.parent if a.contract.parent.name == "contracts" else None,
    )

    print(json.dumps({"panel": str(a.out), "sha256": sha, "stats": stats}, indent=2))
    print(f"pin this in {a.contract}: data_snapshot_sha256: {sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
