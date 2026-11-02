"""Reconstruct as-of monthly Binance USD-M liquid coin membership without future availability.

Read *only* original Binance 1h monthly shards dated 2022-12 through
2025-06. No future-horizon membership filter, no protected 2025-07+ price
reads, no evaluation contract changes, and no Stage 1 screening.

Selection at UTC month start uses ONLY quote-volume from the prior 30 days,
>= 21 days of hourly history and >= $25,000 average hourly notional.
Source-file SHA256 Merkle input is retained in a write-once audit archive.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl

from warden.contracts import load_contract
from warden.provenance import resolve_dataset

STORE = Path("/home/quant/dev/quant/ml4t-data/.data/store/crypto_futures_ohlcv_1h_BINANCE_UM_PERP")
CUTOFF = datetime(2025, 7, 1, tzinfo=UTC)
FIRST_MONTH = datetime(2023, 1, 1, tzinfo=UTC)
FIRST_SCAN = datetime(2022, 12, 1, tzinfo=UTC)
MIN_HOURS = 21 * 24
MIN_AVG_QUOTE_USD = 25000.0


def eligible_source_files(store: Path) -> list[Path]:
    """Never enumerate or open a market-data shard at or after the holdout start."""
    if not store.is_dir():
        raise FileNotFoundError(store)
    files: list[Path] = []
    for sym in sorted(store.glob("symbol=*")):
        if not sym.is_dir():
            continue
        for year in (2022, 2023, 2024, 2025):
            year_dir = sym / f"year={year}"
            if not year_dir.is_dir():
                continue
            for month_dir in sorted(year_dir.glob("month=*")):
                try:
                    month = int(month_dir.name.removeprefix("month="))
                except ValueError:
                    continue
                first = datetime(year, month, 1, tzinfo=UTC)
                if FIRST_SCAN <= first < CUTOFF:
                    candidate = month_dir / "data.parquet"
                    if candidate.is_file():
                        files.append(candidate)
    if not files:
        raise ValueError("No pre-cutoff observed Binance monthly source files")
    return sorted(files)


def month_starts() -> list[datetime]:
    result = []
    year, month = 2023, 1
    while (year, month) < (2025, 7):
        result.append(datetime(year, month, 1, tzinfo=UTC))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return result


def asof_liquidity_rank(
    daily: pl.DataFrame, asof: datetime, *, top_n: int = 100,
    min_hours: int = MIN_HOURS, min_avg_quote_usd: float = MIN_AVG_QUOTE_USD,
) -> pl.DataFrame:
    """Only historical daily aggregates strictly before asof may affect rank."""
    if top_n < 1 or min_hours < 1 or min_avg_quote_usd <= 0:
        raise ValueError("Unreasonable PIT liquidity rules")
    left = asof - timedelta(days=30)
    result = (
        daily.filter((pl.col("day") >= left) & (pl.col("day") < asof))
        .group_by("asset")
        .agg(
            pl.col("quote_usd").sum().alias("quote_usd_30d"),
            pl.col("hours").sum().alias("hours_30d"),
        )
        .filter(
            (pl.col("hours_30d") >= min_hours)
            & (pl.col("quote_usd_30d") / pl.col("hours_30d") >= min_avg_quote_usd)
        )
        .sort(["quote_usd_30d", "asset"], descending=[True, False])
        .head(top_n)
    )
    return result


def source_file_manifest(files: list[Path], store: Path) -> tuple[list[dict], str]:
    entries = []
    for file in files:
        # Bytes not mtimes: the source may be reexported with different content.
        digest = hashlib.sha256(file.read_bytes()).hexdigest()
        entries.append({
            "path": str(file.relative_to(store)),
            "sha256": digest,
            "size": file.stat().st_size,
        })
    serialized = (json.dumps(entries, sort_keys=True, separators=(",", ":")) + "\n").encode()
    return entries, hashlib.sha256(serialized).hexdigest()


def run(store: Path = STORE, contract_path: Path = Path("contracts/crypto_perps_top100_1h_v1.yaml")) -> dict:
    contract = load_contract(contract_path)
    if contract.val_end != CUTOFF:
        raise ValueError("Unexpected protected holdout boundary")
    observed = resolve_dataset(contract, Path("."))
    if not observed.eligible_market:
        raise ValueError("Frozen comparison source is not SHA-pinned market data")
    files = eligible_source_files(store)

    # Project timestamp/symbol/quoted turnover only. Never load a protected
    # date or 2025-07+ partition; this is an AS-OF MEMBERSHIP audit, not PnL.
    daily = (
        pl.scan_parquet([str(x) for x in files], hive_partitioning=False)
        .select("timestamp", pl.col("symbol").alias("asset"), "quote_volume")
        .filter((pl.col("timestamp") >= FIRST_SCAN) & (pl.col("timestamp") < CUTOFF))
        .filter(
            pl.col("quote_volume").is_finite()
            & (pl.col("quote_volume") >= 0)
        )
        .with_columns(day=pl.col("timestamp").dt.truncate("1d"))
        .group_by("asset", "day")
        .agg(
            pl.col("quote_volume").sum().alias("quote_usd"),
            pl.len().alias("hours"),
        )
        .collect(engine="streaming")
    )
    full = pl.scan_parquet(observed.path).select(pl.col("asset").unique()).collect()
    original = set(full["asset"].to_list())
    if len(original) != 100:
        raise ValueError("Expected exactly 100 frozen hindsight-selected assets")

    months = []
    ever_selected: set[str] = set()
    for when in month_starts():
        ranked = asof_liquidity_rank(daily, when)
        symbols = ranked["asset"].to_list()
        overlap = len(set(symbols) & original)
        ever_selected.update(symbols)
        rows = ranked.to_dicts()
        months.append({
            "asof_utc": when.isoformat(),
            "eligible_selected": len(rows),
            "hindsight_top100_overlap": overlap,
            "hindsight_only": len(original - set(symbols)),
            "asof_only": len(set(symbols) - original),
            "symbols": symbols,
            "ranked": rows,
        })
    manifest, root_hash = source_file_manifest(files, store)
    return {
        "schema": "quantlab-asof-binance-top100-membership-v1",
        "purpose": "Historical PIT liquidity membership audit only; not a Warden screen or authorized contract",
        "hindsight_panel_sha256": observed.sha256,
        "window": {
            "first_source": FIRST_SCAN.isoformat(),
            "first_rank": FIRST_MONTH.isoformat(),
            "holdout_start_exclusive": CUTOFF.isoformat(),
            "months": len(months),
        },
        "policy": {
            "selection_time": "First UTC hour of each calendar month",
            "lookback_days": 30,
            "min_history_hours": MIN_HOURS,
            "min_quote_usd_per_observed_hour": MIN_AVG_QUOTE_USD,
            "max_selected": 100,
            "ranking": "Prior 30 calendar days quoted volume descending, ties by asset ascending",
        },
        "total_observed_assets_daily": int(daily["asset"].n_unique()),
        "frozen_hindsight_symbols": sorted(original),
        "distinct_asof_selected_symbols": len(ever_selected),
        "asof_selected_not_in_hindsight_panel": sorted(ever_selected - original),
        "original_hindsight_not_selected_any_month": sorted(original - ever_selected),
        "months": months,
        "source_manifest_sha256": root_hash,
        "source_shard_count": len(manifest),
        "source_shards": manifest,
        "limitations": [
            "Historical Binance store completeness is not guaranteed to include every delisted perp",
            "Prior-30-day volume available at the month boundary is used; avoids 2024 full-year rank",
            "Monthly symbols are a predeclared membership rule, not an executable backtest",
            "No protected 2025-07+ data or venue fills included",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Historical PIT source audit is write-once")
    report = run()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(report, sort_keys=True, separators=(",", ":"), default=str)+"\n").encode()
    with args.output.open("xb") as fh, gzip.GzipFile(fileobj=fh,mode="wb",mtime=0,filename="") as z:
        z.write(raw)
    overlap = [m["hindsight_top100_overlap"] for m in report["months"]]
    print(json.dumps({
        "schema": report["schema"],
        "source_shards": report["source_shard_count"],
        "source_manifest_sha256": report["source_manifest_sha256"],
        "hindsight_panel_sha256": report["hindsight_panel_sha256"],
        "asof_distinct_assets": report["distinct_asof_selected_symbols"],
        "asof_not_in_hindsight": len(report["asof_selected_not_in_hindsight_panel"]),
        "overlap_min": min(overlap),
        "overlap_max": max(overlap),
        "overlap_mean": sum(overlap)/len(overlap),
        "month_head": [
            {"asof":m["asof_utc"],"selected":m["eligible_selected"],"overlap":m["hindsight_top100_overlap"]}
            for m in report["months"][:3]
        ],
        "month_tail": [
            {"asof":m["asof_utc"],"selected":m["eligible_selected"],"overlap":m["hindsight_top100_overlap"]}
            for m in report["months"][-3:]
        ],
        "archived_bytes": args.output.stat().st_size,
        "archive_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
    }, indent=2))


if __name__ == "__main__":
    main()
