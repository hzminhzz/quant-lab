#!/usr/bin/env python3
"""Salvage 23 long-history crypto perpetual majors (2020-2026) from workstation store into quant-lab contracts."""

from __future__ import annotations

import hashlib
import sqlite3
import json
from dataclasses import asdict
from pathlib import Path

import polars as pl
import yaml

from warden.contracts import load_contract, canonical_json
from warden.runner import ExperimentRunner

COLUMNS = ["timestamp", "asset", "open", "high", "low", "close", "volume", "funding_rate"]


def salvage_majors():
    store = Path("/home/quant/dev/quant/ml4t-data/.data/store/crypto_futures_ohlcv_1h_BINANCE_UM_PERP")
    funding_store = Path("/home/quant/dev/quant/ml4t-data/.data/store")

    print("1. Identifying verified majors with 100% complete OHLCV and Funding (2020 through 2026)...")
    sym_dirs = sorted([d for d in store.iterdir() if d.is_dir() and d.name.startswith("symbol=")])

    majors_candidates = []
    for d in sym_dirs:
        sym = d.name.split("=")[1]
        has_all = True
        for y in [2020, 2021, 2022, 2023, 2024, 2025, 2026]:
            ydir = d / f"year={y}"
            fdir = funding_store / f"crypto_futures_funding_{sym}_PERP" / f"year={y}"
            if not (ydir.is_dir() and fdir.is_dir()):
                has_all = False
                break
            req = 10 if y in [2020, 2021, 2022, 2023, 2024] else (6 if y == 2025 else 8)
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
                majors_candidates.append((sym, qvol))
            except Exception:
                pass

    majors_candidates.sort(key=lambda x: x[1], reverse=True)
    majors_symbols = [x[0] for x in majors_candidates]
    print(f"Selected {len(majors_symbols)} continuous long-history majors: {majors_symbols}")

    print("2. Scanning OHLCV partitions (2020 through 2026)...")
    ohlcv_files = []
    for s in majors_symbols:
        for y in range(2020, 2027):
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

    print("3. Scanning dedicated funding rate partitions (2020 through 2026)...")
    f_files = []
    for s in majors_symbols:
        f_dir = funding_store / f"crypto_futures_funding_{s}_PERP"
        for y in range(2020, 2027):
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
    panel = ohlcv_df.sort("timestamp").join_asof(
        f_df, on="timestamp", by="asset", strategy="backward"
    ).with_columns(pl.col("funding_rate").fill_null(0.0))

    # Basic data quality cleaning
    panel = (
        panel.filter(
            (pl.col("open") > 0)
            & (pl.col("high") > 0)
            & (pl.col("low") > 0)
            & (pl.col("close") > 0)
            & (pl.col("volume") >= 0)
        )
        .sort("timestamp", "asset")
    )

    # Windows:
    # Train: 2020-01-01 to 2023-01-01 (3 full years)
    # Val: 2023-01-01 to 2025-01-01 (2 full years)
    # Holdout: 2025-01-01 to 2026-10-24 (22 months)
    exp_df = panel.filter(
        (pl.col("timestamp") >= pl.datetime(2020, 1, 1, time_zone="UTC"))
        & (pl.col("timestamp") < pl.datetime(2025, 1, 1, time_zone="UTC"))
    )
    holdout_df = panel.filter(
        (pl.col("timestamp") >= pl.datetime(2025, 1, 1, time_zone="UTC"))
        & (pl.col("timestamp") <= pl.datetime(2026, 10, 24, time_zone="UTC"))
    )

    out_exp = Path("data/exploratory/crypto_majors_1h.parquet")
    out_hold = Path("data/holdout_staging/crypto_majors_1h.parquet")
    out_exp.parent.mkdir(parents=True, exist_ok=True)
    out_hold.parent.mkdir(parents=True, exist_ok=True)

    print(f"5. Writing exploratory panel: {out_exp} ({exp_df.height} rows, {exp_df['asset'].n_unique()} assets)...")
    exp_df.write_parquet(out_exp)
    print(f"Writing holdout staging panel: {out_hold} ({holdout_df.height} rows)...")
    holdout_df.write_parquet(out_hold)

    exp_sha = hashlib.sha256(out_exp.read_bytes()).hexdigest()
    print(f"Exploratory SHA256: {exp_sha}")

    # Generate contract YAML
    contract_dict = {
        "contract_id": "CRYPTO_MAJORS_1H_V1",
        "asset_class": "crypto_perps",
        "frequency": "1h",
        "bars_per_year": 8760,
        "exploratory_data_path": str(out_exp),
        "holdout_data_path": "data/holdout/crypto_majors_1h.parquet",
        "train_start": "2020-01-01T00:00:00Z",
        "val_start": "2023-01-01T00:00:00Z",
        "val_end": "2025-01-01T00:00:00Z",
        "holdout_start": "2025-01-01T00:00:00Z",
        "holdout_end": "2026-10-24T00:00:00Z",
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

    contract_path = Path("contracts/crypto_majors_1h_v1.yaml")
    contract_path.write_text(yaml.dump(contract_dict, sort_keys=False))
    print(f"6. Wrote contract: {contract_path}")

    # Register in registry.db
    runner = ExperimentRunner()
    contract_obj = load_contract(contract_path)
    
    data = {**json.loads(canonical_json(asdict(contract_obj))), "spec_hash": contract_obj.spec_hash()}
    conn = sqlite3.connect(runner.registry.db_path)
    conn.row_factory = sqlite3.Row
    exists = conn.execute("SELECT 1 FROM contracts WHERE contract_id = ?", (contract_obj.contract_id,)).fetchone()
    if exists:
        fields = ", ".join(f"{k} = ?" for k in data.keys())
        values = list(data.values()) + [contract_obj.contract_id]
        conn.execute(f"UPDATE contracts SET {fields} WHERE contract_id = ?", values)
    else:
        cols = ", ".join(data.keys())
        placeholders = ", ".join("?" for _ in data)
        conn.execute(f"INSERT INTO contracts ({cols}) VALUES ({placeholders})", list(data.values()))
    conn.commit()
    conn.close()
    print("7. Registered/Updated CRYPTO_MAJORS_1H_V1 in registry.db!")


if __name__ == "__main__":
    salvage_majors()
