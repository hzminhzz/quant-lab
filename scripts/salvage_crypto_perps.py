#!/usr/bin/env python3
"""Normalize read-only source bars or create the explicit synthetic benchmark."""

import argparse
import hashlib
import json
from pathlib import Path

import polars as pl
import yaml

from warden.contracts import load_contract
from warden.synthetic import generate_canonical_market_panel

COLUMNS = ["timestamp", "asset", "open", "high", "low", "close", "volume", "funding_rate"]


def normalize(panel):
    aliases = {"symbol": "asset", "ticker": "asset", "datetime": "timestamp"}
    panel = panel.rename({k: v for k, v in aliases.items() if k in panel.columns and v not in panel.columns})
    if missing := set(COLUMNS) - set(panel.columns):
        raise ValueError(f"Missing source columns: {sorted(missing)}")
    if panel.schema["timestamp"] == pl.String:
        panel = panel.with_columns(pl.col("timestamp").str.to_datetime(time_zone="UTC"))
    panel = panel.select(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC")), pl.col("asset").cast(pl.String),
        pl.col(COLUMNS[2:]).cast(pl.Float64),
    ).sort("timestamp", "asset")
    if panel.is_empty() or panel.null_count().row(0) != (0,) * len(COLUMNS):
        raise ValueError("Empty panel or null market values")
    if panel.select(pl.struct("timestamp", "asset").is_duplicated().any()).item():
        raise ValueError("Duplicate timestamp/asset keys; refusing silent data loss")
    bad = pl.any_horizontal(~pl.col(COLUMNS[2:]).is_finite())
    bad |= pl.any_horizontal(pl.col(["open", "high", "low", "close"]) <= 0)
    bad |= (pl.col("volume") < 0) | (pl.col("asset").str.len_chars() == 0)
    bad |= (pl.col("high") < pl.max_horizontal("open", "close", "low"))
    bad |= (pl.col("low") > pl.min_horizontal("open", "close", "high"))
    if panel.filter(bad).height:
        raise ValueError("Invalid prices, OHLC ordering, volume or non-finite market values")
    return panel


def load_source_panel(source_dir: Path) -> pl.DataFrame:
    s_dir = Path(source_dir)
    perps_p = list(s_dir.glob("**/perps_1h.parquet"))
    if perps_p:
        perps = pl.read_parquet(perps_p[0]).with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))
        prem_p = list(s_dir.glob("**/premium_index_8h.parquet"))
        if prem_p and "funding_rate" not in perps.columns:
            prem = pl.read_parquet(prem_p[0]).with_columns([
                pl.col("timestamp").cast(pl.Datetime("us", "UTC")),
                pl.col("premium_index_close").alias("funding_rate"),
            ]).select("timestamp", "symbol", "funding_rate")
            return perps.sort("timestamp").join_asof(
                prem.sort("timestamp"), on="timestamp", by="symbol", strategy="backward"
            ).fill_null(0.0)
        return perps
    files = sorted(list(s_dir.glob("*.parquet")) + list(s_dir.glob("**/*.parquet")))
    if not files:
        raise FileNotFoundError(f"No parquet files under {s_dir}")
    return pl.read_parquet(files[0] if len(files) == 1 else files)


def salvage(contract_path, out_dir, source_dir=None, synthetic=False, update_contract=False):
    contract = load_contract(contract_path)
    panel = generate_canonical_market_panel() if synthetic else load_source_panel(source_dir)
    splits = {"exploratory": panel.filter(pl.col("timestamp") < contract.holdout_start),
              "holdout": panel.filter(pl.col("timestamp") >= contract.holdout_start)}
    evidence = {"synthetic": synthetic, "schema": {k: str(v) for k, v in panel.schema.items()}}
    for name, frame in splits.items():
        if frame.is_empty():
            raise ValueError(f"{name} split is empty at the contract cutoff")
        output = Path(out_dir) / name / "crypto_perps_1h.parquet"
        if source_dir and output.resolve().is_relative_to(Path(source_dir).resolve()):
            raise ValueError("Output cannot be inside the read-only source directory")
        output.parent.mkdir(parents=True, exist_ok=True)
        frame.write_parquet(output)
        evidence[name] = {"path": str(output), "rows": frame.height,
                          "min": str(frame["timestamp"].min()), "max": str(frame["timestamp"].max()),
                          "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}
    if update_contract:
        document = yaml.safe_load(Path(contract_path).read_text())
        document["data_snapshot_sha256"] = evidence["exploratory"]["sha256"]
        for name in splits:
            document[f"{name}_data_path"] = evidence[name]["path"]
        Path(contract_path).write_text(yaml.safe_dump(document, sort_keys=False))
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--source-dir", type=Path)
    group.add_argument("--synthetic-fallback", action="store_true")
    parser.add_argument("--out-dir", type=Path, default=Path("data"))
    parser.add_argument("--contract", type=Path, default=Path("contracts/crypto_perps_1h_v1.yaml"))
    args = parser.parse_args()
    # Alternate output trees are isolated fixtures, not a request to rewrite the frozen contract.
    update = args.out_dir.resolve() == Path("data").resolve()
    result = salvage(args.contract, args.out_dir, args.source_dir, args.synthetic_fallback, update)
    print(json.dumps(result, indent=2))
    print("Human-only production lock: sudo ./scripts/lock_holdout_permissions.sh")


if __name__ == "__main__":
    main()
