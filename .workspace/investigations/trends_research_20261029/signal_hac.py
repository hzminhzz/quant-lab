"""Overlap-robust IC inference for the S183 trend sleeve (ml4t-diagnostic).

A 21-bar forward return sampled every bar overlaps ~21x, so the naive t-stat from
`analyze_signal` is inflated by roughly sqrt(horizon). `compute_ic_hac_stats` with
`label_horizon=H` corrects for it (Bartlett kernel, 20 effective lags at H=21).

Writes reproduction/signal_hac.json. Caches the signal frame so re-runs are instant.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import polars as pl
from ml4t.diagnostic.api import analyze_signal, compute_ic_hac_stats

sys.path.insert(0, str(Path("strategies/candidates")))
sys.path.insert(0, str(Path("strategies/candidates/carver_daily")))

from s183_composite import build_signal_frame

from warden.contracts import load_contract

OUT = Path(".workspace/investigations/trends_research_20261029/reproduction")
HORIZONS = (1, 5, 21)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    contract = load_contract(Path("contracts/multi_asset_1d_v1.yaml"))
    panel = pl.read_parquet(contract.exploratory_data_path)
    cache = OUT / "signal_frame.parquet"
    if cache.exists():
        feat = pl.read_parquet(cache)
    else:
        feat = build_signal_frame(panel, contract)
        feat.write_parquet(cache)

    factor = feat.select(
        pl.col("timestamp").alias("date"), "asset", pl.col("f_trend").alias("factor")
    ).drop_nulls()
    prices = panel.select(
        pl.col("timestamp").alias("date"), "asset", pl.col("close").alias("price")
    )
    sig = analyze_signal(factor, prices, periods=HORIZONS, factor_col="factor",
                         date_col="date", asset_col="asset", price_col="price", min_assets=10)

    out = {}
    for label, series in sig.ic_series.items():
        h = int(label.rstrip("D"))
        st = compute_ic_hac_stats(np.asarray(series, dtype=float), label_horizon=h)
        out[label] = {
            "mean_ic": float(st["mean_ic"]),
            "hac_t": float(st["t_stat"]),
            "hac_p": float(st["p_value"]),
            "naive_t": float(st["naive_t_stat"]),
            "n_periods": int(st["n_periods"]),
            "effective_lags": int(st["effective_lags"]),
            "monotonicity": float((sig.monotonicity or {}).get(label, float("nan"))),
            "turnover": float((sig.turnover or {}).get(label, float("nan"))),
            "ic_ir": float((sig.ic_ir or {}).get(label, float("nan"))),
        }
        print(label, out[label])
    (OUT / "signal_hac.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
