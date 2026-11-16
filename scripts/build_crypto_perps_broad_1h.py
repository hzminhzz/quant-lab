#!/usr/bin/env python3
"""Build the wide crypto-perp 1h panel proposed in
`.workspace/plans/xs_momentum_long_history/CONTRACT_PROPOSAL.md`.

Read-only by default. `--check` prints what would be built; `--execute` writes the two parquet files
and the sha256 pin. The contract `CRYPTO_PERPS_BROAD_1H_V1` does not exist yet, so `--execute` is a
deliberate, separate step.

Inputs (qualified in the ml4t-data lake, never copied from a consumer):
  prices   <lake>/crypto_futures_ohlcv_1h_BINANCE_UM_PERP     864 symbols, 2020-01 .. 2026-10
  funding  <lake>/crypto_futures_funding_<SYM>_PERP            530 stores, 8h settlements

Funding settlements carry millisecond jitter (00:00:00.005). Every settlement is bucketed into the
8-hour window that contains it and the window total is placed on the window's boundary bar, because
warden charges crypto_perps 1h funding only on bars where `hour % 8 == 0`
(`_funding_settlement_mask`). A symbol that settles hourly or 4-hourly therefore still pays its full
funding, on the bars the harness can charge. A bar with no settlement gets funding_rate = 0.0.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import polars as pl

LAKE = Path("/home/quant/dev/quant/ml4t-data/.data/store")
PRICES = LAKE / "crypto_futures_ohlcv_1h_BINANCE_UM_PERP"
FUNDING_GLOB = "crypto_futures_funding_{sym}_PERP"
REPO = Path(__file__).resolve().parents[1]
OUT_EXPLORATORY = REPO / "data" / "exploratory" / "crypto_perps_broad_1h.parquet"
OUT_HOLDOUT = REPO / "data" / "holdout" / "crypto_perps_broad_1h.parquet"

START = "2021-01-01T00:00:00Z"
HOLDOUT_START = "2025-07-01T00:00:00Z"
END = "2026-10-26T00:00:00Z"
MIN_BARS = 168
FUNDING_FALLBACK_PER_8H = 0.0001   # the contract's declared 1.0 bps per 8h, for symbols with no store


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _prices() -> pl.LazyFrame:
    return (
        pl.scan_parquet(str(PRICES / "symbol=*" / "**" / "*.parquet"))
        .select("timestamp", "symbol", "open", "high", "low", "close", "volume", "quote_volume")
        .with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))
        .filter((pl.col("timestamp") >= pl.lit(START).str.to_datetime(time_zone="UTC"))
                & (pl.col("timestamp") < pl.lit(END).str.to_datetime(time_zone="UTC")))
    )


def _funding(symbol: str) -> pl.DataFrame | None:
    store = LAKE / FUNDING_GLOB.format(sym=symbol)
    if not store.is_dir():
        return None
    return (
        pl.scan_parquet(str(store / "year=*" / "month=*" / "data.parquet"))
        .select("timestamp", "funding_rate")
        .with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")).dt.truncate("8h"))
        .group_by("timestamp")
        .agg(pl.col("funding_rate").sum())
        .collect()
        .with_columns(pl.lit(symbol).alias("symbol"))
        .select("timestamp", "symbol", "funding_rate")
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the CRYPTO_PERPS_BROAD_1H_V1 panel (read-only by default).")
    ap.add_argument("--execute", action="store_true", help="write the parquet files and the sha256 pin")
    ap.add_argument("--force", action="store_true", help="overwrite an existing panel")
    ap.add_argument("--sample-funding", type=int, default=3, help="symbols whose funding join is validated in --check")
    args = ap.parse_args()

    counts = (
        _prices().group_by("symbol").agg(pl.len().alias("bars"),
                                        pl.col("timestamp").min().alias("t0"),
                                        pl.col("timestamp").max().alias("t1")).collect()
    )
    keep = counts.filter(pl.col("bars") >= MIN_BARS)
    dropped = counts.height - keep.height
    # Principal decision 2026-10-28, revised: keep every symbol, and charge the ones without a
    # funding store the contract's declared constant (1.0 bps per 8h) instead of 0.0. Funded-only
    # (530 of 863) would have dropped 26 of the 31 delisted names, which is the survivorship realism
    # the broad panel exists for. A uniform constant never understates funding; it only declines to
    # model dispersion for names whose history the venue did not publish.
    funded = [s for s in keep["symbol"].to_list() if (LAKE / FUNDING_GLOB.format(sym=s)).is_dir()]
    unfunded = keep.height - len(funded)
    dead = keep.filter(pl.col("t1") < pl.col("t1").max())
    print(f"symbols in window: {counts.height}  dropped (< {MIN_BARS} bars): {dropped}")
    print(f"kept: {keep.height}  |  of those, no funding store (declared constant used): {unfunded}")
    print(f"symbols whose data ends before the panel end (delisted/halted): {dead.height}")
    print(f"span: {counts['t0'].min()} .. {counts['t1'].max()}")
    print(f"bars: min {keep['bars'].min()}  median {keep['bars'].median()}  total {keep['bars'].sum():,}")

    have_funding = sum(1 for s in keep["symbol"].to_list() if (LAKE / FUNDING_GLOB.format(sym=s)).is_dir())
    print(f"funding: observed store for {have_funding}/{keep.height} symbols; "
          f"the other {keep.height - have_funding} are charged the declared {FUNDING_FALLBACK_PER_8H:.4f} per 8h")

    for sym in keep["symbol"].to_list()[: args.sample_funding]:
        f = _funding(sym)
        if f is None:
            print(f"  {sym}: no funding store")
            continue
        print(f"  {sym}: {f.height:,} settlements, {f['timestamp'].min()} .. {f['timestamp'].max()}, "
              f"median rate {f['funding_rate'].median():+.6f}")

    if not args.execute:
        print("\n--check only: nothing written. Re-run with --execute to build the panel.")
        return 0

    for out in (OUT_EXPLORATORY, OUT_HOLDOUT):
        if out.exists() and not args.force:
            raise SystemExit(f"refusing to overwrite {out} (pass --force)")

    prices = _prices().filter(pl.col("symbol").is_in(keep["symbol"].to_list())).collect()
    symbols = keep["symbol"].to_list()
    frames, declared = [], []
    for s in symbols:
        f = _funding(s)
        if f is None:
            declared.append(s)
        else:
            frames.append(f)
    if declared:
        # No published funding history: charge the contract constant on every settlement bar the
        # symbol actually trades, so the cost is carried rather than silently dropped.
        frames.append(
            prices.filter(pl.col("symbol").is_in(declared))
            .filter(pl.col("timestamp").dt.hour() % 8 == 0)
            .select("timestamp", "symbol")
            .unique(subset=["timestamp", "symbol"], keep="first")
            .with_columns(pl.lit(FUNDING_FALLBACK_PER_8H, dtype=pl.Float64).alias("funding_rate"))
            .select("timestamp", "symbol", "funding_rate")
        )
    print(f"funding rows: observed for {len(symbols) - len(declared)} symbols, declared constant for {len(declared)}")
    funding = pl.concat(frames)
    panel = (
        prices.join(funding, on=["timestamp", "symbol"], how="left")
        .with_columns(pl.col("funding_rate").fill_null(0.0))
        .rename({"symbol": "asset"})
        .select("timestamp", "asset", "open", "high", "low", "close", "volume", "quote_volume", "funding_rate")
        .sort("timestamp", "asset")
    )
    split = pl.lit(HOLDOUT_START).str.to_datetime(time_zone="UTC")
    blocked: list[Path] = []
    for out, df in ((OUT_EXPLORATORY, panel.filter(pl.col("timestamp") < split)),
                    (OUT_HOLDOUT, panel.filter(pl.col("timestamp") >= split))):
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            df.write_parquet(out, compression="zstd")
        except PermissionError:
            # data/holdout is root:trader by design; only the principal may create the sealed file.
            blocked.append(out)
            print(f"BLOCKED {out}  rows={df.height:,}  (partition is root:trader - the principal must "
                  f"create it, or open the window with scripts/lock_holdout_permissions.sh)")
            continue
        print(f"wrote {out}  rows={df.height:,}  symbols={df['asset'].n_unique()}  sha256={_sha256(out)}")
    if blocked:
        print("\nexploratory panel is written; the holdout file is deliberately not, and must not be "
              "created by an agent.")

    report: dict[str, int | str] = {"exploratory_rows": panel.filter(pl.col("timestamp") < split).height,
              "holdout_rows": panel.filter(pl.col("timestamp") >= split).height,
              "symbols": panel["asset"].n_unique()}
    if OUT_EXPLORATORY.is_file():
        report["exploratory_sha256"] = _sha256(OUT_EXPLORATORY)
    if OUT_HOLDOUT.is_file():
        report["holdout_sha256"] = _sha256(OUT_HOLDOUT)
    print(json.dumps(report, indent=2))
    return 3 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
