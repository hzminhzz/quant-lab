#!/usr/bin/env python3
"""Unified multi-asset data salvage tool: crypto_perps, fx, indices from ml4t-data."""

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import polars as pl
import yaml

from warden.contracts import load_contract

COLUMNS = ["timestamp", "asset", "open", "high", "low", "close", "volume", "funding_rate"]

FX_PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "CADJPY", "GBPJPY"]
INDEX_SYMBOLS = ["ES", "NQ", "DAX", "NIKKEI225", "EUSIDXEUR", "HK33"]
METALS_SYMBOLS = ["XAUUSD", "XAGUSD"]
COMMODITIES_SYMBOLS = ["CL"]
CRYPTO_1M_SYMBOLS = [
    "BTCUSDT_PERP",
    "ETHUSDT_PERP",
    "SOLUSDT_PERP",
    "BNBUSDT_PERP",
    "DOGEUSDT_PERP",
    "XRPUSDT_PERP",
    "ADAUSDT_PERP",
    "AVAXUSDT_PERP",
]


def normalize_panel(panel: pl.DataFrame) -> pl.DataFrame:
    aliases = {"symbol": "asset", "ticker": "asset", "datetime": "timestamp"}
    panel = panel.rename({k: v for k, v in aliases.items() if k in panel.columns and v not in panel.columns})
    if "funding_rate" not in panel.columns:
        panel = panel.with_columns(funding_rate=pl.lit(0.0))
    if missing := set(COLUMNS) - set(panel.columns):
        raise ValueError(f"Missing source columns: {sorted(missing)}")
    if panel.schema["timestamp"] == pl.String:
        panel = panel.with_columns(pl.col("timestamp").str.to_datetime(time_zone="UTC"))
    panel = panel.select(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC")),
        pl.col("asset").cast(pl.String),
        pl.col(COLUMNS[2:]).cast(pl.Float64),
    ).sort("timestamp", "asset")
    if panel.is_empty() or panel.null_count().row(0) != (0,) * len(COLUMNS):
        raise ValueError("Empty panel or null market values")
    if panel.select(pl.struct("timestamp", "asset").is_duplicated().any()).item():
        raise ValueError("Duplicate timestamp/asset keys; refusing silent data loss")
    bad = pl.any_horizontal(~pl.col(COLUMNS[2:]).is_finite())
    bad |= pl.any_horizontal(pl.col(["open", "high", "low", "close"]) <= 0)
    bad |= (pl.col("volume") < 0) | (pl.col("asset").str.len_chars() == 0)
    bad |= pl.col("high") < pl.max_horizontal("open", "close", "low")
    bad |= pl.col("low") > pl.min_horizontal("open", "close", "high")
    if panel.filter(bad).height:
        raise ValueError("Invalid prices, OHLC ordering, volume or non-finite market values")
    return panel


def load_fx_panel(store_dir: Path, pairs: list[str] = FX_PAIRS, freq: str = "15m") -> pl.DataFrame:
    frames = []
    for sym in pairs:
        p = store_dir / f"forex_minute_{sym}"
        if not p.exists():
            continue
        files = [f for f in p.rglob("*.parquet") if "year=2024" in str(f) or "year=2025" in str(f)]
        if not files:
            continue
        df = pl.scan_parquet(files).collect()
        resampled = (
            df.sort("timestamp")
            .group_by_dynamic("timestamp", every=freq, group_by="symbol")
            .agg([
                pl.col("open").first(),
                pl.col("high").max(),
                pl.col("low").min(),
                pl.col("close").last(),
                pl.col("volume").sum().fill_null(0.0),
            ])
        )
        frames.append(resampled)
    if not frames:
        raise FileNotFoundError(f"No minute FX data found under {store_dir}")
    panel = pl.concat(frames).rename({"symbol": "asset"}).with_columns(funding_rate=pl.lit(0.0))
    return normalize_panel(panel)


def load_indices_panel(
    store_dir: Path,
    symbols: list[str] = INDEX_SYMBOLS,
    freq: str | None = "1h",
    min_year: int = 2015,
) -> pl.DataFrame:
    frames = []
    for sym in symbols:
        p = store_dir / f"index_minute_{sym}"
        if not p.exists():
            continue
        files = [f for f in p.rglob("*.parquet") if any(f"year={y}" in str(f) for y in range(min_year, 2027))]
        if not files:
            continue
        df = pl.scan_parquet(files).collect()
        if freq and freq != "1m":
            resampled = (
                df.sort("timestamp")
                .group_by_dynamic("timestamp", every=freq, group_by="symbol")
                .agg([
                    pl.col("open").first(),
                    pl.col("high").max(),
                    pl.col("low").min(),
                    pl.col("close").last(),
                    pl.col("volume").sum().fill_null(0.0),
                ])
            )
            frames.append(resampled)
        else:
            frames.append(df)
    if not frames:
        raise FileNotFoundError(f"No minute Index data found under {store_dir}")
    panel = pl.concat(frames).rename({"symbol": "asset"}).with_columns(funding_rate=pl.lit(0.0))
    return normalize_panel(panel)


def load_metals_panel(store_dir: Path, symbols: list[str] = METALS_SYMBOLS) -> pl.DataFrame:
    frames = []
    std_cols = ["timestamp", "symbol", "open", "high", "low", "close", "volume"]
    for sym in symbols:
        p = store_dir / f"forex_minute_{sym}"
        if not p.exists():
            continue
        files = [f for f in p.rglob("*.parquet") if "year=2024" in str(f) or "year=2025" in str(f)]
        if not files:
            continue
        m_frames = [
            pl.read_parquet(f).select(std_cols).with_columns([
                pl.col("timestamp").cast(pl.Datetime("us", "UTC")),
                pl.col("volume").cast(pl.Float64),
                pl.col(["open", "high", "low", "close"]).cast(pl.Float64),
            ]) for f in files
        ]
        frames.append(pl.concat(m_frames))
    if not frames:
        raise FileNotFoundError(f"No minute Metals data found under {store_dir}")
    panel = pl.concat(frames).rename({"symbol": "asset"}).with_columns(funding_rate=pl.lit(0.0))
    return normalize_panel(panel)


def load_commodities_panel(store_dir: Path, symbols: list[str] = COMMODITIES_SYMBOLS) -> pl.DataFrame:
    frames = []
    std_cols = ["timestamp", "symbol", "open", "high", "low", "close", "volume"]
    for sym in symbols:
        p = store_dir / f"commodity_minute_{sym}"
        if not p.exists():
            continue
        files = [f for f in p.rglob("*.parquet") if "year=2024" in str(f) or "year=2025" in str(f)]
        if not files:
            continue
        c_frames = [
            pl.read_parquet(f).select(std_cols).with_columns([
                pl.col("timestamp").dt.replace_time_zone("UTC").cast(pl.Datetime("us", "UTC")),
                pl.col("volume").cast(pl.Float64),
                pl.col(["open", "high", "low", "close"]).cast(pl.Float64),
            ]) for f in files
        ]
        frames.append(pl.concat(c_frames))
    if not frames:
        raise FileNotFoundError(f"No minute Commodity data found under {store_dir}")
    panel = pl.concat(frames).rename({"symbol": "asset"}).with_columns(funding_rate=pl.lit(0.0))
    return normalize_panel(panel)
def load_crypto_1m_panel(store_dir: Path, symbols: list[str] = CRYPTO_1M_SYMBOLS) -> pl.DataFrame:
    frames = []
    for s in symbols:
        m_p = store_dir / f"crypto_futures_minute_{s}"
        if not m_p.exists():
            continue
        m_files = [f for f in m_p.rglob("*.parquet") if any(f"year={y}" in str(f) for y in (2024, 2025, 2026))]
        if not m_files:
            continue
        df_m = pl.scan_parquet(m_files).select(["timestamp", "symbol", "open", "high", "low", "close", "volume"]).collect()
        df_m = df_m.with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))

        f_p = store_dir / f"crypto_futures_funding_{s}"
        f_files = [f for f in f_p.rglob("*.parquet") if any(f"year={y}" in str(f) for y in (2024, 2025, 2026))] if f_p.exists() else []
        if f_files:
            df_f = pl.scan_parquet(f_files).select(["timestamp", "funding_rate"]).collect()
            df_f = df_f.with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))
            joined = df_m.sort("timestamp").join_asof(df_f.sort("timestamp"), on="timestamp", strategy="backward").fill_null(0.0)
        else:
            joined = df_m.with_columns(funding_rate=pl.lit(0.0))
        frames.append(joined)
    if not frames:
        raise FileNotFoundError(f"No minute Crypto futures data found under {store_dir}")
    panel = pl.concat(frames).rename({"symbol": "asset"}).sort("timestamp", "asset")
    return normalize_panel(panel)


def salvage_dataset(
    contract_path: Path,
    out_dir: Path,
    panel: pl.DataFrame,
    update_contract: bool = False,
) -> dict[str, Any]:
    contract = load_contract(contract_path)
    splits = {
        "exploratory": panel.filter(pl.col("timestamp") < contract.holdout_start),
        "holdout": panel.filter(pl.col("timestamp") >= contract.holdout_start),
    }
    evidence: dict[str, Any] = {"contract_id": contract.contract_id, "schema": {k: str(v) for k, v in panel.schema.items()}}
    out_dir = Path(out_dir)

    for name, frame in splits.items():
        if frame.is_empty():
            raise ValueError(f"{name} split is empty at contract cutoff {contract.holdout_start}")
        dest_filename = Path(getattr(contract, f"{name}_data_path")).name
        out_path = out_dir / name / dest_filename
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            frame.write_parquet(out_path)
            actual_path = str(out_path)
            sha = hashlib.sha256(out_path.read_bytes()).hexdigest()
        except PermissionError:
            # When holdout directory is root:trader 0750 locked, stage to holdout_staging
            staged = out_dir / f"{name}_staging" / dest_filename
            staged.parent.mkdir(parents=True, exist_ok=True)
            frame.write_parquet(staged)
            actual_path = str(staged)
            sha = hashlib.sha256(staged.read_bytes()).hexdigest()
            evidence["staging_note"] = f"Holdout locked; saved to {staged}"

        evidence[name] = {
            "path": actual_path,
            "rows": frame.height,
            "min": str(frame["timestamp"].min()),
            "max": str(frame["timestamp"].max()),
            "sha256": sha,
        }

    if update_contract:
        doc = yaml.safe_load(Path(contract_path).read_text())
        doc["data_snapshot_sha256"] = evidence["exploratory"]["sha256"]
        Path(contract_path).write_text(yaml.safe_dump(doc, sort_keys=False))

    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset-class", choices=["fx", "indices", "indices_1m", "metals_1m", "commodities_1m", "crypto_1m", "all"], default="all")
    parser.add_argument("--store-dir", type=Path, default=Path("/home/quant/dev/quant/ml4t-data/.data/store"))
    parser.add_argument("--out-dir", type=Path, default=Path("data"))
    args = parser.parse_args()

    results = {}
    if args.asset_class in {"fx", "all"}:
        print("Salvaging FX Majors (15m)...")
        fx_panel = load_fx_panel(args.store_dir)
        res_fx = salvage_dataset(
            Path("contracts/fx_majors_15m_v1.yaml"),
            args.out_dir,
            fx_panel,
            update_contract=True,
        )
        results["fx"] = res_fx

    if args.asset_class in {"indices", "all"}:
        print("Salvaging 10-Year Global Indices (1h, 2015-2026)...")
        idx_panel = load_indices_panel(args.store_dir, min_year=2015)
        res_idx = salvage_dataset(
            Path("contracts/indices_1h_v1.yaml"),
            args.out_dir,
            idx_panel,
            update_contract=True,
        )
        results["indices"] = res_idx
    if args.asset_class in {"indices_1m", "all"}:
        print("Salvaging All 6 Dukascopy Global Indices 1-Minute (2024-2026)...")
        idx_1m_panel = load_indices_panel(args.store_dir, symbols=INDEX_SYMBOLS, freq="1m", min_year=2024)
        res_idx_1m = salvage_dataset(
            Path("contracts/indices_1m_v1.yaml"),
            args.out_dir,
            idx_1m_panel,
            update_contract=True,
        )
        results["indices_1m"] = res_idx_1m

    if args.asset_class in {"metals_1m", "all"}:
        print("Salvaging Dukascopy Precious Metals 1-Minute (Gold & Silver)...")
        metals_panel = load_metals_panel(args.store_dir, symbols=METALS_SYMBOLS)
        res_metals = salvage_dataset(
            Path("contracts/metals_1m_v1.yaml"),
            args.out_dir,
            metals_panel,
            update_contract=True,
        )
        results["metals_1m"] = res_metals

    if args.asset_class in {"commodities_1m", "all"}:
        print("Salvaging Dukascopy Energy Commodities 1-Minute (WTI Crude Oil CL)...")
        comm_panel = load_commodities_panel(args.store_dir, symbols=COMMODITIES_SYMBOLS)
        res_comm = salvage_dataset(
            Path("contracts/commodities_1m_v1.yaml"),
            args.out_dir,
            comm_panel,
            update_contract=True,
        )
        results["commodities_1m"] = res_comm
    if args.asset_class in {"crypto_1m", "all"}:
        print("Salvaging Top 8 Crypto Perpetuals 1-Minute (BTC, ETH, SOL, BNB, DOGE, XRP, ADA, AVAX)...")
        crypto_panel = load_crypto_1m_panel(args.store_dir, symbols=CRYPTO_1M_SYMBOLS)
        res_crypto = salvage_dataset(
            Path("contracts/crypto_perps_1m_v1.yaml"),
            args.out_dir,
            crypto_panel,
            update_contract=True,
        )
        results["crypto_1m"] = res_crypto
    print(json.dumps(results, indent=2))
    print("\nSalvage complete. To lock holdout staging into production holdout:")
    print("  sudo cp -r data/holdout_staging/* data/holdout/ && sudo ./scripts/lock_holdout_permissions.sh ./data/holdout")

if __name__ == "__main__":
    main()
