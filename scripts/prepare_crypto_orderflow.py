"""Build/audit a versioned 1h order-flow enrichment for existing frozen Top-100 panel.

Pure, read-only audit unless --prepare. New research lineage only; NEVER
rewrite original Warden dataset, contract, manifest, registry or holdout.
Source hourly Binance fields are observed for completed 1h bars, not quotes.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import tempfile
from pathlib import Path

import polars as pl

BASE_SHA = "a7e13ed71f8ac09a11f98bdb6d711d93f91a4021105141a2070fd79696af99bc"
BASE_FILE = Path("data/exploratory/crypto_perps_top100_1h.parquet")
RAW_ROOT = Path("/home/quant/dev/quant/ml4t-data/.data/store/crypto_futures_ohlcv_1h_BINANCE_UM_PERP")
OUTPUT = Path("data/exploratory/crypto_perps_top100_1h_orderflow_v1.parquet")
VAL_END = "2025-07-01T00:00:00+00:00"
FEATURES = ("quote_volume", "trade_count", "taker_buy_base_volume", "taker_buy_quote_volume")
PRICE_COLS = ("open", "high", "low", "close", "volume")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for blob in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(blob)
    return h.hexdigest()


def source_paths(root: Path, symbols: list[str]) -> list[Path]:
    out: list[Path] = []
    for sym in sorted(symbols):
        folder = root / f"symbol={sym}"
        if not folder.is_dir():
            raise FileNotFoundError(f"No canonical Binance hourly source for {sym}")
        for p in folder.glob("year=*/month=*/data.parquet"):
            parts = p.relative_to(folder).parts
            year, month = int(parts[0].split("=")[1]), int(parts[1].split("=")[1])
            if year in (2023, 2024) or (year == 2025 and month <= 6):
                out.append(p)
    if not out:
        raise FileNotFoundError("No research-window source partitions")
    return sorted(out)


def enrich(base: pl.DataFrame, raw: pl.DataFrame) -> pl.DataFrame:
    if "symbol" in raw.columns:
        raw = raw.rename({"symbol": "asset"})
    needed_base = {"timestamp", "asset", *PRICE_COLS, "funding_rate"}
    needed_raw = {"timestamp", "asset", *PRICE_COLS, *FEATURES}
    if not needed_base.issubset(base.columns):
        raise ValueError(f"Missing frozen panel fields: {sorted(needed_base-set(base.columns))}")
    if not needed_raw.issubset(raw.columns):
        raise ValueError(f"Missing raw observed fields: {sorted(needed_raw-set(raw.columns))}")
    raw = raw.with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))
    if raw.select(pl.struct(["asset", "timestamp"]).is_duplicated().any()).item():
        raise ValueError("Duplicate raw asset/timestamp keys; refusing deduplication")
    if base.select(pl.struct(["asset", "timestamp"]).is_duplicated().any()).item():
        raise ValueError("Duplicate frozen asset/timestamp keys")
    keep = ["timestamp", "asset", *PRICE_COLS, *FEATURES]
    joined = base.join(raw.select(keep), on=["timestamp", "asset"], how="left", suffix="_raw")
    if joined.height != base.height:
        raise ValueError("Data join changed the frozen population")
    missing = joined.filter(pl.any_horizontal(*[pl.col(c).is_null() for c in FEATURES]))
    if missing.height:
        raise ValueError(f"Missing raw source fields for {missing.height} frozen bars")
    mismatches = joined.filter(pl.any_horizontal(*[
        (pl.col(c)-pl.col(f"{c}_raw")).abs() >
        (pl.lit(1e-8) + pl.col(c).abs() * 1e-9)
        for c in PRICE_COLS
    ]))
    if mismatches.height:
        raise ValueError(f"OHLCV disagreement on {mismatches.height} frozen bars")
    # The canonical feed contains a market-wide zero-trade hour at
    # 2024-10-28 20:00Z, confirmed for BTC against Binance's official
    # daily 1h archive. Preserve the factual zeros but mark the ATS/NTR
    # feature undefined. Zero activity is not evidence of zero toxicity.
    no_trade = (
        (pl.col("volume") == 0)
        & (pl.col("quote_volume") == 0)
        & (pl.col("trade_count") == 0)
        & (pl.col("taker_buy_base_volume") == 0)
        & (pl.col("taker_buy_quote_volume") == 0)
        & (pl.col("open") == pl.col("high"))
        & (pl.col("open") == pl.col("low"))
        & (pl.col("open") == pl.col("close"))
    )
    joined = joined.with_columns((~no_trade).alias("flow_valid"))
    bad = joined.filter(
        ((pl.col("quote_volume") <= 0) & ~no_trade)
        | ((pl.col("trade_count") <= 0) & ~no_trade)
        | ((pl.col("volume") <= 0) & ~no_trade)
        | (pl.col("taker_buy_base_volume") < 0)
        | (pl.col("taker_buy_quote_volume") < 0)
        | (pl.col("taker_buy_base_volume") > pl.col("volume") * 1.0000001)
        | (pl.col("taker_buy_quote_volume") > pl.col("quote_volume") * 1.0000001)
        | pl.any_horizontal(*[~pl.col(c).is_finite() for c in FEATURES if c != "trade_count"])
    )
    if bad.height:
        issues = {
            "nonpositive_quote_volume": bad.filter(pl.col("quote_volume") <= 0).height,
            "nonpositive_trade_count": bad.filter(pl.col("trade_count") <= 0).height,
            "negative_taker_base": bad.filter(pl.col("taker_buy_base_volume") < 0).height,
            "negative_taker_quote": bad.filter(pl.col("taker_buy_quote_volume") < 0).height,
            "taker_base_exceeds_total": bad.filter(
                pl.col("taker_buy_base_volume") > pl.col("volume") * 1.0000001
            ).height,
            "taker_quote_exceeds_total": bad.filter(
                pl.col("taker_buy_quote_volume") > pl.col("quote_volume") * 1.0000001
            ).height,
        }
        samples = bad.select("timestamp", "asset", "quote_volume", "trade_count",
                             "taker_buy_base_volume", "taker_buy_quote_volume").head(5).to_dicts()
        raise ValueError(
            f"Invalid trade count/quote/taker observations on {bad.height} bars; "
            f"breakdown={issues}; examples={samples}"
        )
    return joined.drop([f"{c}_raw" for c in PRICE_COLS]).select(
        "timestamp", "asset", *PRICE_COLS, "funding_rate", *FEATURES, "flow_valid"
    ).sort("timestamp", "asset")


def audit(base_path: Path, root: Path) -> tuple[pl.DataFrame, dict]:
    observed_sha = sha256(base_path)
    if observed_sha != BASE_SHA:
        raise ValueError(f"Frozen base SHA mismatch: {observed_sha} != {BASE_SHA}")
    base = pl.read_parquet(base_path)
    if base["timestamp"].max().isoformat() >= VAL_END or base.height == 0:
        raise ValueError("Frozen panel violates historical validation cutoff")
    files = source_paths(root, base["asset"].unique().to_list())
    raw = pl.scan_parquet([str(p) for p in files], hive_partitioning=False).select(
        ["timestamp", "symbol", *PRICE_COLS, *FEATURES]
    ).filter(pl.col("timestamp") < pl.lit(VAL_END).str.to_datetime(time_zone="UTC")).collect()
    full = enrich(base, raw)
    h = hashlib.sha256()
    for path in files:
        rel = path.relative_to(root)
        h.update(str(rel).encode("utf-8") + b"\0")
        h.update(sha256(path).encode("ascii") + b"\n")
    data_buffer = io.BytesIO()
    full.write_parquet(data_buffer, compression="zstd", statistics=True)
    payload = data_buffer.getvalue()
    return full, {
        "lineage": "CRYPTO_PERPS_TOP100_ORDERFLOW_1H_H72_V1_PROPOSAL",
        "base_path": str(base_path), "base_sha256": observed_sha,
        "raw_root": str(root), "raw_source_file_count": len(files),
        "raw_file_digest_chain_sha256": h.hexdigest(),
        "row_count": full.height, "symbols": full["asset"].n_unique(),
        "first_bar": str(full["timestamp"].min()),
        "last_bar": str(full["timestamp"].max()),
        "end_exclusive": VAL_END,
        "derived_sha256": hashlib.sha256(payload).hexdigest(),
        "derived_bytes": len(payload),
        "source_fields": list(FEATURES),
        "no_trade_bars": full.filter(~pl.col("flow_valid")).height,
        "no_trade_timestamps": [
            str(x) for x in full.filter(~pl.col("flow_valid"))["timestamp"].unique().to_list()
        ],
        "contract_approved": False,
    }


def prepare_once(path: Path, df: pl.DataFrame, expected_sha: str) -> None:
    buf = io.BytesIO()
    df.write_parquet(buf, compression="zstd", statistics=True)
    payload = buf.getvalue()
    if hashlib.sha256(payload).hexdigest() != expected_sha:
        raise ValueError("Non-reproducible serialization digest")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if sha256(path) != expected_sha:
            raise FileExistsError(f"Refusing to overwrite an existing different file: {path}")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        tmp.write(payload)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    try:
        os.link(tmp_path, path)  # atomic create only, never overwrite
    except FileExistsError:
        if sha256(path) != expected_sha:
            raise
    finally:
        tmp_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-panel", type=Path, default=BASE_FILE)
    parser.add_argument("--raw-root", type=Path, default=RAW_ROOT)
    parser.add_argument("--prepare", action="store_true")
    args = parser.parse_args()
    result, evidence = audit(args.base_panel, args.raw_root)
    if args.prepare:
        prepare_once(OUTPUT, result, evidence["derived_sha256"])
    evidence["prepared"] = args.prepare
    evidence["output"] = str(OUTPUT)
    print(json.dumps(evidence, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
