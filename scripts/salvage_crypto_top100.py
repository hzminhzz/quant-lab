#!/usr/bin/env python3
"""Salvage Top 100 liquid crypto perpetuals from workstation store into quant-lab contracts."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import polars as pl
import yaml

from warden.contracts import canonical_json, load_contract
from warden.runner import ExperimentRunner

COLUMNS = ["timestamp", "asset", "open", "high", "low", "close", "volume", "funding_rate"]


def salvage_top100():
    store = Path("/home/quant/dev/quant/ml4t-data/.data/store/crypto_futures_ohlcv_1h_BINANCE_UM_PERP")
    funding_store = Path("/home/quant/dev/quant/ml4t-data/.data/store")
    
    print("1. Identifying Top 100 liquid symbols by 2024 quote volume...")
    sym_dirs = sorted([d for d in store.iterdir() if d.is_dir() and d.name.startswith("symbol=")])
    
    valid_symbols = []
    for d in sym_dirs:
        sym = d.name.split("=")[1]
        y24, y25 = d / "year=2024", d / "year=2025"
        if y24.is_dir() and y25.is_dir() and len(list(y24.glob("month=*/data.parquet"))) == 12 and len(list(y25.glob("month=*/data.parquet"))) >= 6:
            try:
                qvol = float(pl.scan_parquet(list(y24.glob("month=*/data.parquet"))).select(pl.col("quote_volume").sum()).collect().item())
                valid_symbols.append((sym, qvol))
            except Exception:
                pass

    valid_symbols.sort(key=lambda x: x[1], reverse=True)
    top_100 = [x[0] for x in valid_symbols[:100]]
    print(f"Selected {len(top_100)} top symbols. Ranks 1-5: {top_100[:5]}")

    print("2. Scanning OHLCV partitions (2024 through 2026)...")
    ohlcv_files = []
    for s in top_100:
        for y in [2024, 2025, 2026]:
            ydir = store / f"symbol={s}" / f"year={y}"
            if ydir.is_dir():
                ohlcv_files.extend(list(ydir.glob("month=*/data.parquet")))

    ohlcv_df = pl.scan_parquet(ohlcv_files).select([
        pl.col("timestamp").cast(pl.Datetime("us", "UTC")),
        pl.col("symbol").alias("asset"),
        pl.col("open").cast(pl.Float64),
        pl.col("high").cast(pl.Float64),
        pl.col("low").cast(pl.Float64),
        pl.col("close").cast(pl.Float64),
        pl.col("volume").cast(pl.Float64),
    ]).collect()

    print("3. Scanning funding rate partitions...")
    f_files = []
    for s in top_100:
        f_dir = funding_store / f"crypto_futures_funding_{s}_PERP"
        if f_dir.is_dir():
            for y in [2024, 2025, 2026]:
                ydir = f_dir / f"year={y}"
                if ydir.is_dir():
                    f_files.extend(list(ydir.glob("month=*/data.parquet")))

    if f_files:
        f_df = pl.scan_parquet(f_files).select([
            pl.col("timestamp").cast(pl.Datetime("us", "UTC")),
            pl.col("symbol").alias("asset"),
            pl.col("funding_rate").cast(pl.Float64),
        ]).collect().sort("timestamp")
        
        print("4. Joining OHLCV and funding rates via backward join_asof...")
        panel = ohlcv_df.sort("timestamp").join_asof(
            f_df, on="timestamp", by="asset", strategy="backward"
        ).with_columns(pl.col("funding_rate").fill_null(0.0))
    else:
        panel = ohlcv_df.with_columns(funding_rate=pl.lit(0.0))

    # Basic data quality cleaning
    panel = panel.filter(
        (pl.col("open") > 0) & (pl.col("high") > 0) & (pl.col("low") > 0) & (pl.col("close") > 0) & (pl.col("volume") >= 0)
    ).sort("timestamp", "asset")

    # Split into exploratory (2024-01-01 to 2025-07-01) and holdout staging (2025-07-01 to 2026-10-24)
    exp_df = panel.filter(
        (pl.col("timestamp") >= pl.datetime(2024, 1, 1, time_zone="UTC")) &
        (pl.col("timestamp") < pl.datetime(2025, 7, 1, time_zone="UTC"))
    )
    holdout_df = panel.filter(
        (pl.col("timestamp") >= pl.datetime(2025, 7, 1, time_zone="UTC")) &
        (pl.col("timestamp") <= pl.datetime(2026, 10, 24, time_zone="UTC"))
    )

    out_exp = Path("data/exploratory/crypto_perps_top100_1h.parquet")
    out_hold = Path("data/holdout_staging/crypto_perps_top100_1h.parquet")
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
        "contract_id": "CRYPTO_PERPS_TOP100_1H_V1",
        "asset_class": "crypto_perps",
        "frequency": "1h",
        "bars_per_year": 8760,
        "exploratory_data_path": str(out_exp),
        "holdout_data_path": "data/holdout/crypto_perps_top100_1h.parquet",
        "train_start": "2024-01-01T00:00:00Z",
        "val_start": "2025-01-01T00:00:00Z",
        "val_end": "2025-07-01T00:00:00Z",
        "holdout_start": "2025-07-01T00:00:00Z",
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

    contract_path = Path("contracts/crypto_perps_top100_1h_v1.yaml")
    contract_path.write_text(yaml.dump(contract_dict, sort_keys=False))
    print(f"6. Wrote contract: {contract_path}")

    # Register in registry.db
    runner = ExperimentRunner()
    contract_obj = load_contract(contract_path)
    runner.registry.register_contract(contract_obj)
    print("7. Registered CRYPTO_PERPS_TOP100_1H_V1 in registry.db!")


if __name__ == "__main__":
    salvage_top100()
