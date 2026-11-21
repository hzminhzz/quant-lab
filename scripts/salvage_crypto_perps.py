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
from warden_evidence.market_validation import (
    build_dataset_manifest,
    validate_ohlcv_panel,
    write_contract_and_manifest_atomic,
)

COLUMNS = ["timestamp", "asset", "open", "high", "low", "close", "volume", "funding_rate"]


def normalize(panel: pl.DataFrame, asset_class: str = "crypto_perps") -> pl.DataFrame:
    return validate_ohlcv_panel(panel, asset_class)


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
    files = sorted(s_dir.rglob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"No parquet files under {s_dir}")
    return pl.read_parquet(files[0] if len(files) == 1 else files)


def salvage(contract_path, out_dir, source_dir=None, synthetic=False, update_contract=False):
    contract = load_contract(contract_path)
    raw_panel = generate_canonical_market_panel() if synthetic else load_source_panel(source_dir)
    panel = normalize(raw_panel, asset_class=contract.asset_class)
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
        cpath = Path(contract_path)
        document = yaml.safe_load(cpath.read_text())
        provenance = "synthetic" if synthetic else "verified_market"
        document["data_snapshot_sha256"] = evidence["exploratory"]["sha256"]
        document["data_provenance"] = provenance
        for name in splits:
            document[f"{name}_data_path"] = evidence[name]["path"]
        manifest_path = cpath.parent / "datasets" / f"{contract.contract_id}.json"
        manifest_doc = build_dataset_manifest(
            contract_id=contract.contract_id,
            path=document["exploratory_data_path"],
            sha256=document["data_snapshot_sha256"],
            provenance=provenance,
            source="synthetic_canonical" if synthetic else str(source_dir),
            pipeline="scripts/salvage_crypto_perps.py",
        )
        write_contract_and_manifest_atomic(cpath, document, manifest_path, manifest_doc)
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
