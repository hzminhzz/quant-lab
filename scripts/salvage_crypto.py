#!/usr/bin/env python3
"""Unified salvage tool for crypto perpetual contracts (Majors and Top 100) from workstation store."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

import polars as pl
import yaml

from warden.contracts import canonical_json, load_contract
from warden.runner import ExperimentRunner

COLUMNS = ["timestamp", "asset", "open", "high", "low", "close", "volume", "funding_rate"]


def salvage_crypto_universe(preset: str = "majors") -> None:
    store = Path("/home/quant/dev/quant/ml4t-data/.data/store/crypto_futures_ohlcv_1h_BINANCE_UM_PERP")
    funding_store = Path("/home/quant/dev/quant/ml4t-data/.data/store")

    if preset == "majors":
        contract_id = "CRYPTO_MAJORS_1H_V1"
        out_exp_path = Path("data/exploratory/crypto_majors_1h.parquet")
        out_hold_path = Path("data/holdout_staging/crypto_majors_1h.parquet")
        contract_yaml = Path("contracts/crypto_majors_1h_v1.yaml")
        start_year, n_assets = 2020, 23
        t_start, v_start, v_end, h_end = "2020-01-01T00:00:00Z", "2023-01-01T00:00:00Z", "2025-01-01T00:00:00Z", "2026-10-24T00:00:00Z"
        split_dt = pl.datetime(2025, 1, 1, time_zone="UTC")
        begin_dt = pl.datetime(2020, 1, 1, time_zone="UTC")
    elif preset == "top100":
        contract_id = "CRYPTO_PERPS_TOP100_1H_V1"
        out_exp_path = Path("data/exploratory/crypto_perps_top100_1h.parquet")
        out_hold_path = Path("data/holdout_staging/crypto_perps_top100_1h.parquet")
        contract_yaml = Path("contracts/crypto_perps_top100_1h_v1.yaml")
        start_year, n_assets = 2023, 100
        t_start, v_start, v_end, h_end = "2023-01-01T00:00:00Z", "2024-07-01T00:00:00Z", "2025-07-01T00:00:00Z", "2026-10-24T00:00:00Z"
        split_dt = pl.datetime(2025, 7, 1, time_zone="UTC")
        begin_dt = pl.datetime(2023, 1, 1, time_zone="UTC")
    else:
        raise ValueError(f"Unknown preset: '{preset}'. Must be 'majors' or 'top100'")

    print(f"1. Scanning {preset} universe (start year: {start_year}, target assets: {n_assets})...")
    sym_dirs = sorted([d for d in store.iterdir() if d.is_dir() and d.name.startswith("symbol=")])

    valid = []
    for d in sym_dirs:
        sym = d.name.split("=")[1]
        has_all = True
        for y in range(start_year, 2027):
            ydir = d / f"year={y}"
            fdir = funding_store / f"crypto_futures_funding_{sym}_PERP" / f"year={y}"
            if not (ydir.is_dir() and fdir.is_dir()):
                has_all = False
                break
            req = 10 if y < 2025 else (6 if y == 2025 else 8)
            if len(list(ydir.glob("month=*/data.parquet"))) < req or len(list(fdir.glob("month=*/data.parquet"))) < req:
                has_all = False
                break
        if has_all:
            try:
                qvol = float(
                    pl.scan_parquet(list((d / "year=2024").glob("month=*/data.parquet")))
                    .select(pl.col("quote_volume").sum())
                    .collect()
                    .item()
                )
                valid.append((sym, qvol))
            except Exception:
                pass

    valid.sort(key=lambda x: x[1], reverse=True)
    selected_symbols = [x[0] for x in valid[:n_assets]]
    print(f"Selected {len(selected_symbols)} verified symbols. Ranks 1-5: {selected_symbols[:5]}")

    print("2. Collecting OHLCV partitions...")
    ohlcv_files = []
    for s in selected_symbols:
        for y in range(start_year, 2027):
            ydir = store / f"symbol={s}" / f"year={y}"
            if ydir.is_dir():
                ohlcv_files.extend(list(ydir.glob("month=*/data.parquet")))

    ohlcv_df = (
        pl.scan_parquet(ohlcv_files)
        .select([
            pl.col("timestamp").cast(pl.Datetime("us", "UTC")),
            pl.col("symbol").alias("asset"),
            pl.col("open").cast(pl.Float64),
            pl.col("high").cast(pl.Float64),
            pl.col("low").cast(pl.Float64),
            pl.col("close").cast(pl.Float64),
            pl.col("volume").cast(pl.Float64),
        ])
        .collect()
    )

    print("3. Collecting funding rate partitions...")
    f_files = []
    for s in selected_symbols:
        f_dir = funding_store / f"crypto_futures_funding_{s}_PERP"
        for y in range(start_year, 2027):
            ydir = f_dir / f"year={y}"
            if ydir.is_dir():
                f_files.extend(list(ydir.glob("month=*/data.parquet")))

    f_df = (
        pl.scan_parquet(f_files)
        .select([
            pl.col("timestamp").cast(pl.Datetime("us", "UTC")),
            pl.col("symbol").alias("asset"),
            pl.col("funding_rate").cast(pl.Float64),
        ])
        .collect()
        .sort("timestamp")
    )

    print("4. Joining OHLCV and funding rates via backward join_asof...")
    panel = (
        ohlcv_df.sort("timestamp")
        .join_asof(f_df, on="timestamp", by="asset", strategy="backward")
        .with_columns(pl.col("funding_rate").fill_null(0.0))
        .filter(
            (pl.col("open") > 0)
            & (pl.col("high") > 0)
            & (pl.col("low") > 0)
            & (pl.col("close") > 0)
            & (pl.col("volume") >= 0)
        )
        .sort("timestamp", "asset")
    )

    exp_df = panel.filter((pl.col("timestamp") >= begin_dt) & (pl.col("timestamp") < split_dt))
    holdout_df = panel.filter(
        (pl.col("timestamp") >= split_dt) & (pl.col("timestamp") <= pl.datetime(2026, 10, 24, time_zone="UTC"))
    )

    out_exp_path.parent.mkdir(parents=True, exist_ok=True)
    out_hold_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"5. Writing exploratory panel: {out_exp_path} ({exp_df.height} rows)...")
    exp_df.write_parquet(out_exp_path)
    print(f"Writing holdout staging panel: {out_hold_path} ({holdout_df.height} rows)...")
    holdout_df.write_parquet(out_hold_path)

    exp_sha = hashlib.sha256(out_exp_path.read_bytes()).hexdigest()

    contract_dict: dict[str, Any] = {
        "contract_id": contract_id,
        "asset_class": "crypto_perps",
        "frequency": "1h",
        "bars_per_year": 8760,
        "exploratory_data_path": str(out_exp_path),
        "holdout_data_path": f"data/holdout/{out_exp_path.name}",
        "train_start": t_start,
        "val_start": v_start,
        "val_end": v_end,
        "holdout_start": v_end,
        "holdout_end": h_end,
        "n_folds": 4,
        "label_horizon_bars": 4,
        "embargo_bars": 2,
        "execution_delay_bars": 1,
        "execution_mode": "NEXT_BAR",
        "taker_fee_bps": 4.0,
        "slippage_bps": 2.0,
        "funding_bps_per_8h": 1.0,
        "min_expected_move_to_cost_ratio": 2.5,
        "data_snapshot_sha256": exp_sha,
    }

    contract_yaml.write_text(yaml.dump(contract_dict, sort_keys=False))
    print(f"6. Wrote contract YAML: {contract_yaml}")

    runner = ExperimentRunner()
    contract_obj = load_contract(contract_yaml)
    data = {**json.loads(canonical_json(asdict(contract_obj))), "spec_hash": contract_obj.spec_hash()}

    conn = sqlite3.connect(runner.registry.db_path)
    cols = ", ".join(data.keys())
    placeholders = ", ".join("?" for _ in data)
    conn.execute(f"INSERT OR REPLACE INTO contracts ({cols}) VALUES ({placeholders})", list(data.values()))
    conn.commit()
    conn.close()
    print(f"7. Registered/Updated {contract_id} in registry.db (spec_hash: {contract_obj.spec_hash()})!")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preset", choices=["majors", "top100"], default="majors", help="Universe preset to salvage")
    args = parser.parse_args()
    salvage_crypto_universe(args.preset)


if __name__ == "__main__":
    main()
