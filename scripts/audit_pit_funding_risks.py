"""Audit gaps and settlement interval assumptions in the PIT crypto source proposal.

Never imputes missing funding as zero. Preserves original Binance event times
rather than treating every crypto perpetual as an eight-hour schedule.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path

import polars as pl
import yaml

from scripts.build_pit_top100_proposal import ROOT, SOURCE_REPORT


def funding_risk_report(
    panel_path: Path,
    proposed_contract: Path,
    source_provenance: Path = SOURCE_REPORT,
) -> dict:
    contract = yaml.safe_load(proposed_contract.read_text())
    if contract.get("data_snapshot_sha256") != hashlib.sha256(panel_path.read_bytes()).hexdigest():
        raise ValueError("PIT data no longer matches its proposal hash")
    if contract["data_provenance"] != "verified_market":
        raise ValueError("Unknown source identity")
    with gzip.open(source_provenance, "rt") as f:
        lineage = json.load(f)
    if lineage["data_sha256"] != contract["data_snapshot_sha256"]:
        raise ValueError("Derived dataset and provenance archive disagree")

    lf = pl.scan_parquet(panel_path)
    gross = lf.select(
        pl.len().alias("rows"),
        pl.col("asset").n_unique().alias("assets"),
        (~pl.col("funding_observed")).sum().alias("missing_funding"),
        ((~pl.col("funding_observed")) & (pl.col("timestamp").dt.hour() % 8 == 0)).sum().alias("missing_eighth_hour_funding"),
    ).collect().row(0, named=True)
    by_year = (
        lf.group_by(pl.col("timestamp").dt.year().alias("year"))
        .agg(
            pl.len().alias("rows"),
            (~pl.col("funding_observed")).sum().alias("missing_funding"),
        )
        .sort("year")
        .collect()
        .to_dicts()
    )
    by_month = (
        lf.group_by(pl.col("timestamp").dt.truncate("1mo").alias("month"))
        .agg(
            pl.len().alias("rows"),
            (~pl.col("funding_observed")).sum().alias("missing_funding"),
            pl.col("asset").n_unique().alias("assets"),
        )
        .sort("month")
        .collect()
        .to_dicts()
    )
    by_asset = (
        lf.group_by("asset")
        .agg(
            pl.len().alias("rows"),
            (~pl.col("funding_observed")).sum().alias("missing"),
        )
        .sort("missing", descending=True)
        .head(20)
        .collect()
        .to_dicts()
    )
    # Market source funding intervals change over time. This is a source
    # diagnostic, not permission to alter frozen Warden settlement rules.
    files = [ROOT / e["path"] for e in lineage["funding_sources"]]
    event_interval_counts: Counter[str] = Counter()
    if files:
        events = (
            pl.scan_parquet([str(f) for f in files], hive_partitioning=False)
            .group_by("funding_interval_hours")
            .agg(pl.len().alias("events"))
            .collect(engine="streaming")
        )
        event_interval_counts.update({
            str(row["funding_interval_hours"]): int(row["events"])
            for row in events.to_dicts()
        })
    return {
        "contract_id": contract["contract_id"],
        "source_sha256": contract["data_snapshot_sha256"],
        "rows": int(gross["rows"]),
        "assets": int(gross["assets"]),
        "missing_funding_rows": int(gross["missing_funding"]),
        "missing_funding_pct": float(gross["missing_funding"] / gross["rows"] * 100),
        "missing_8h_settlement_rows": int(gross["missing_eighth_hour_funding"]),
        "funding_interval_hours_counts": dict(event_interval_counts),
        "yearly": by_year,
        "monthly": by_month,
        "top_missing_assets": by_asset,
        "scope": "Source quality of a proposed dataset, NOT executable strategy performance",
        "no_zero_funding_imputation": True,
        "no_protected_data_access": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("PIT funding audit is immutable")
    report = funding_risk_report(
        Path("data/exploratory/crypto_perps_pit100_1h_v1.parquet"),
        Path("docs/contract-proposals/CRYPTO_PERPS_PIT100_1H_V1.yaml"),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, default=str, sort_keys=True)+"\n")
    print(json.dumps({
        k:v for k,v in report.items()
        if k not in ("monthly","top_missing_assets")
    },indent=2, default=str,sort_keys=True))


if __name__ == "__main__":
    main()
