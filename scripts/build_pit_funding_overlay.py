"""Build independent, versioned PIT funding overlay and historical settlement ledger.

The upstream PIT price panel, Warden contracts, original funding data
stores and old validation attempts remain IMMUTABLE. This is a research-only
source repair for a future *separately approved* event-aware contract.
Data were fetched from Binance official monthly archives with SHA checks.

Do NOT feed this hourly funding_rate feature into the original fixed-8h
cashflow simulator as though it were a factual event-only payment stream.
Use derived observed settlement ledger for a new, approved simulator.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

import polars as pl
import yaml

from scripts.recover_pit_funding_archives import (
    ARTIFACT,
    CUTOFF,
    DEFAULT_CACHE,
    FIRST,
    hash_file,
)

PROPOSAL = Path("docs/contract-proposals/CRYPTO_PERPS_PIT100_1H_V1.yaml")
SOURCE_REPORT = Path("docs/evidence/arxiv_stage1_20261102/pit_source_provenance.json.gz")
PINNED_SOURCE_REPORT_SHA = "93d4af8a31fe2d4009fb59b2f7df29cae687fe20ad8002be1e41beac198b5326"
STORE_ROOT = Path("/home/quant/dev/quant/ml4t-data/.data/store")
BASE_OUTPUT = Path("data/exploratory/crypto_perps_pit100_funding_overlay_v2.parquet")
EVENT_OUTPUT = Path("data/exploratory/crypto_perps_pit100_event_ledger_v2.parquet")
EVIDENCE = Path("docs/evidence/arxiv_stage1_20261102/pit_funding_overlay_v2_20261102.json")


def write_once_parquet(df: pl.DataFrame, dest: Path) -> None:
    if dest.exists():
        raise FileExistsError(f"PIT funding overlay output is immutable: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=dest.parent, prefix=".pitfund-", suffix=".parquet", delete=False,
    ) as fh:
        tmp = Path(fh.name)
    try:
        df.write_parquet(tmp, compression="zstd", statistics=True)
        with tmp.open("rb") as fh:
            os.fsync(fh.fileno())
        os.link(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


def original_event_sources(
    root: Path,
    source_receipt: Path,
    *,
    verify_sha: bool = True,
) -> tuple[pl.DataFrame, dict]:
    if hash_file(source_receipt) != PINNED_SOURCE_REPORT_SHA:
        raise ValueError("Original PIT funding-source inventory changed")
    with gzip.open(source_receipt, "rt") as fh:
        lineage = json.load(fh)
    sources = lineage["funding_sources"]
    paths = []
    for entry in sources:
        rel = Path(entry["path"])
        if rel.is_absolute() or ".." in rel.parts or "year=2026" in rel.parts:
            raise ValueError("Funding-source inventory path escapes approved dates")
        file = root / rel
        if not file.is_file():
            raise FileNotFoundError(f"Historical funding source missing: {file}")
        if verify_sha and hash_file(file) != entry["sha256"]:
            raise ValueError(f"Historical funding source SHA mismatch: {file}")
        # Enforce actual vintage, never inspect data from protected months.
        yr = next((int(x[5:]) for x in rel.parts if x.startswith("year=")), None)
        mo = next((int(x[6:]) for x in rel.parts if x.startswith("month=")), None)
        if yr is None or mo is None or (yr, mo) > (2025, 6):
            raise ValueError(f"Protected funding source discovered: {file}")
        paths.append(file)
    if not paths:
        raise ValueError("No original source events")
    events = (
        pl.scan_parquet([str(p) for p in paths], hive_partitioning=False)
        .select(
            pl.col("timestamp").cast(pl.Datetime("us", "UTC")),
            pl.col("symbol").alias("asset"),
            pl.col("funding_rate").cast(pl.Float64),
            pl.col("funding_interval_hours").cast(pl.Int64),
        )
        .filter((pl.col("timestamp") >= FIRST) & (pl.col("timestamp") < CUTOFF))
        .sort("timestamp", "asset")
        .collect(engine="streaming")
    )
    return events, {
        "verified_original_source_files": len(paths),
        "original_source_receipt_sha256": PINNED_SOURCE_REPORT_SHA,
        "original_events": events.height,
        "source_sha_reverified": verify_sha,
    }


def assemble_full_ledger(
    original: pl.DataFrame, recovered: pl.DataFrame, approved_assets: list[str],
) -> tuple[pl.DataFrame, dict]:
    required = {"timestamp", "asset", "funding_rate", "funding_interval_hours"}
    for frame in (original, recovered):
        if required - set(frame.columns):
            raise ValueError(f"Missing actual funding event fields: {required - set(frame.columns)}")
        if frame["timestamp"].max() >= CUTOFF:
            raise ValueError("Unapproved post-validation funding event detected")
    orig_assets = set(original["asset"].unique().to_list())
    rec_assets = set(recovered["asset"].unique().to_list())
    # An asset can have canonical events outside its selected PIT months.
    # Asset-name overlap is valid; overlapping settlement timestamps are not.
    if not (orig_assets | rec_assets) <= set(approved_assets):
        raise ValueError("Funding source contains non-PIT symbol")
    original = original.with_columns(pl.lit("CANONICAL_LOCAL").alias("event_source"))
    recovered = recovered.with_columns(pl.lit("BINANCE_VISION_SHA_VERIFIED").alias("event_source"))
    events = pl.concat([original, recovered], how="vertical").sort("timestamp", "asset")
    if events.select(pl.struct(["timestamp", "asset"]).is_duplicated().any()).item():
        raise ValueError("Duplicate settlement timestamp per symbol")
    unknown_event_rate = int(events["funding_rate"].null_count())
    unknown_event_interval = int(events["funding_interval_hours"].null_count())
    # A true observed settlement rate with an UNKNOWN interval is still
    # observed source evidence. Preserve its null interval rather than
    # guessing 8h. A null/nonfinite RATE, however, is not cash evidence.
    invalid = events.filter(
        (~pl.col("funding_rate").is_finite().fill_null(False))
        | (
            pl.col("funding_interval_hours").is_not_null()
            & ~pl.col("funding_interval_hours").is_in([1, 2, 4, 8])
        )
    )
    if invalid.height:
        raise ValueError(
            "Source has invalid settlement amounts/intervals requiring quarantine: "
            f"bad={invalid.height}, null_rates={unknown_event_rate}"
        )
    return events, {
        "source_original_assets": len(orig_assets),
        "source_recovered_assets": len(rec_assets),
        "overlapping_source_asset_names": len(orig_assets & rec_assets),
        "distinct_assets": events["asset"].n_unique(),
        "event_count": events.height,
        "original_event_count": original.height,
        "recovered_event_count": recovered.height,
        "funding_intervals": dict(sorted(
            Counter(str(x) for x in events["funding_interval_hours"].to_list()).items()
        )),
        "observed_events_with_unknown_interval": unknown_event_interval,
        "observed_events_with_unknown_rate": unknown_event_rate,
    }


def overlay_prices(panel: pl.DataFrame, events: pl.DataFrame) -> tuple[pl.DataFrame, dict]:
    if not {"timestamp", "asset", "funding_observed", "funding_event_time", "funding_rate"} <= set(panel.columns):
        raise ValueError("Missing baseline PIT funding fields")
    if panel["timestamp"].max() >= CUTOFF:
        raise ValueError("PIT panel crosses protected boundary")
    times = events.rename(
        {
            "timestamp": "new_event_time",
            "funding_rate": "new_rate",
            "funding_interval_hours": "new_interval",
            "event_source": "new_source",
        }
    ).sort("new_event_time", "asset")
    before_count = int((~panel["funding_observed"]).sum())
    original_price = panel.select("timestamp", "asset", "open", "high", "low", "close", "volume")
    joined = panel.sort("timestamp", "asset").join_asof(
        times, left_on="timestamp", right_on="new_event_time",
        by="asset", strategy="backward", tolerance="16h",
    )
    # Existing observed data MUST remain byte/logically identical.
    compared = joined.filter(pl.col("funding_observed"))
    diffs = compared.filter(
        (pl.col("funding_rate") != pl.col("new_rate"))
        | (pl.col("funding_event_time") != pl.col("new_event_time"))
    )
    if diffs.height:
        raise ValueError(f"Recovered event ledger conflicts with {diffs.height} original source rows")
    prior_source = (
        pl.col("funding_source") if "funding_source" in joined.columns
        else pl.lit("CANONICAL_LOCAL")
    )
    full = joined.with_columns(
        pl.when(pl.col("funding_observed"))
        .then(pl.col("funding_rate"))
        .otherwise(pl.col("new_rate")).alias("funding_rate"),
        pl.when(pl.col("funding_observed"))
        .then(pl.col("funding_event_time"))
        .otherwise(pl.col("new_event_time")).alias("funding_event_time"),
        pl.when(pl.col("funding_observed"))
        .then(prior_source)
        .otherwise(pl.col("new_source")).alias("funding_source"),
        pl.col("new_interval").alias("funding_interval_hours"),
    ).with_columns(
        pl.col("funding_rate").is_not_null().alias("funding_observed"),
        ((pl.col("timestamp") - pl.col("funding_event_time")).dt.total_seconds() / 3600.0)
        .alias("funding_age_hours"),
    ).drop("new_rate", "new_event_time", "new_interval", "new_source")
    if full.height != panel.height:
        raise ValueError("Funding overlay changed asset-hour count")
    if not full.select("timestamp", "asset", "open", "high", "low", "close", "volume").equals(
        original_price.sort("timestamp", "asset"), null_equal=True
    ):
        raise ValueError("Funding overlay modified source OHLCV")
    missing = full.filter(~pl.col("funding_observed"))
    residual = (
        missing.group_by("asset").agg(
            pl.len().alias("missing_rows"),
            pl.col("timestamp").min().alias("first_missing"),
            pl.col("timestamp").max().alias("last_missing"),
        ).sort("missing_rows", descending=True)
    )
    by_source = full.group_by("funding_source").len().sort("len", descending=True)
    return full.sort("timestamp", "asset"), {
        "input_rows": panel.height,
        "original_missing_rows": before_count,
        "after_overlay_missing_rows": missing.height,
        "recovered_rows": before_count - missing.height,
        "coverage_pct": 100 * (full.height - missing.height) / full.height,
        "funding_source_counts": by_source.to_dicts(),
        "remaining_missing_by_asset": [
            {k: str(v) if isinstance(v, datetime) else v for k, v in x.items()}
            for x in residual.to_dicts()
        ],
        "OHLCV_unchanged": True,
        "nonfuture_asof_join": True,
        "funding_missing_never_imputed": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--panel-path", required=True, type=Path)
    parser.add_argument("--recovered-cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--recovery-report", type=Path, default=ARTIFACT)
    parser.add_argument("--source-root", type=Path, default=STORE_ROOT)
    parser.add_argument("--out-panel", type=Path, default=BASE_OUTPUT)
    parser.add_argument("--out-ledger", type=Path, default=EVENT_OUTPUT)
    parser.add_argument("--evidence", type=Path, default=EVIDENCE)
    args = parser.parse_args()
    for path in (args.out_panel, args.out_ledger, args.evidence):
        if path.exists():
            raise FileExistsError(f"Write-once PIT overlay evidence already exists: {path}")
    proposed = yaml.safe_load(PROPOSAL.read_text())
    if hash_file(args.panel_path) != proposed["data_snapshot_sha256"]:
        raise ValueError("PIT input no longer matches frozen unsigned proposal")
    recovery = json.loads(args.recovery_report.read_text())
    if recovery["input_panel_sha256"] != proposed["data_snapshot_sha256"]:
        raise ValueError("Recovery receipt refers to different PIT dataset")
    rec_path = args.recovered_cache / "recovered_funding_events.parquet"
    if hash_file(rec_path) != recovery["derived_events_parquet_sha256"]:
        raise ValueError("Recovered official event ledger changed after download")
    for rec in recovery["archives"]:
        if rec["status"] == "VERIFIED":
            source = Path(rec["cache_path"])
            if not source.is_file() or hash_file(source) != rec["sha256"]:
                raise ValueError(f"Binance recovered ZIP cache changed: {source}")
            if rec["official_checksum_status"] != "MATCH":
                raise ValueError("Recovered ZIP lacked official checksum integrity")
    panel = pl.read_parquet(args.panel_path).sort("timestamp", "asset")
    original, sources = original_event_sources(args.source_root, SOURCE_REPORT)
    recovered = pl.read_parquet(rec_path)
    ledger, event_info = assemble_full_ledger(
        original, recovered, panel["asset"].unique().to_list(),
    )
    enriched, overlay_info = overlay_prices(panel, ledger)
    write_once_parquet(ledger, args.out_ledger)
    write_once_parquet(enriched, args.out_panel)
    report = {
        "schema": "quantlab-binance-pit-funding-overlay-v2",
        "status": "RESEARCH_ONLY_UNSIGNED_NOT_WARDEN_STAGE3",
        "old_contract_proposal": proposed["contract_id"],
        "original_pit_sha256": proposed["data_snapshot_sha256"],
        "official_recovery_report_sha256": hash_file(args.recovery_report),
        "input_canonical_sources": sources,
        "event_ledger": {
            **event_info, "path": str(args.out_ledger),
            "sha256": hash_file(args.out_ledger),
        },
        "panel_overlay": {
            **overlay_info, "path": str(args.out_panel),
            "sha256": hash_file(args.out_panel),
        },
        "execution_contract": (
            "No registered Warden contract supports actual per-asset event-time "
            "settlement and missing event fail-closed; cannot Stage3 with existing fixed 8h."
        ),
        "protected_holdout_read": False,
        "original_market_store_modified": False,
        "source_approval": False,
    }
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    content = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
    with tempfile.NamedTemporaryFile(dir=args.evidence.parent, delete=False) as fh:
        fh.write(content)
        fh.flush()
        os.fsync(fh.fileno())
        tmp = Path(fh.name)
    try:
        os.link(tmp, args.evidence)
    finally:
        tmp.unlink(missing_ok=True)
    print(json.dumps({
        "evidence": str(args.evidence),
        "evidence_sha256": hash_file(args.evidence),
        "event_count": ledger.height,
        "panel_coverage": overlay_info["coverage_pct"],
        "remaining_unknown": overlay_info["after_overlay_missing_rows"],
        "missing_by_asset": overlay_info["remaining_missing_by_asset"],
        "output_panel_sha256": report["panel_overlay"]["sha256"],
        "output_ledger_sha256": report["event_ledger"]["sha256"],
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
