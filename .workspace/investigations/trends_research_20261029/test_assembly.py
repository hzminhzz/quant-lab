"""End-to-end differential test of the composite assembly against the upstream code.

`trading_book.main()` builds SIGNAL in a block that is inline, not a function:

    Trend_sign = trend_sign(normalise(TS, cap=None), normalise(XS, cap=None))
    Skew_sign  = normalise(-skew)
    VoV_sign   = normalise(VoV)
    Sign_raw   = 0.25*Trend + 0.25*Carry + 0.25*Skew + 0.25*VoV
    fdm        = pooled_fdm(per-date mean of each sleeve)
    SIGNAL     = normalise(Sign_raw * fdm).ewm_mean(alpha=0.5).clip(+-20)

This test reimplements that block *literally from the source*, calling the upstream
functions, and compares the result with the SIGNAL column my module produces from the same
sleeve inputs. It exercises assembly order, the FDM wiring, the normaliser and the smoother
on identical adapted inputs — everything except the inputs themselves, which need the
private Norgate panel.

Run: uv run python .workspace/investigations/trends_research_20261029/test_assembly.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path("strategies/candidates")))
sys.path.insert(0, str(Path("strategies/candidates/carver_daily")))

from s183_reference import _pooled_fdm, _tb, build_signal_frame

from warden.contracts import load_contract

SIGNAL_CAP = 20.0


def upstream_signal(feat: pl.DataFrame, tb, fdm_mode: str = "mine") -> pl.DataFrame:
    """Literal transcription of trading_book.main()'s assembly block.

    `fdm_mode='mine'` uses `_pooled_fdm`, which test_parity.py already proves equal to the
    upstream `pooled_fdm` on four sleeves; the upstream function hard-codes a length-4 weight
    vector, so it cannot run on the three sleeves that remain without carry.
    """
    grid = pl.Series("timestamp", sorted(feat["timestamp"].unique().to_list()))
    assets = sorted(feat["asset"].unique().to_list())

    panels = {}
    for c in ("Trend_sign", "Skew_sign", "VoV_sign"):
        wide = feat.pivot(on="asset", index="timestamp", values=c).sort("timestamp")
        panels[c] = np.column_stack([wide[a].to_numpy() for a in assets]).astype(float)

    means = np.column_stack([np.nanmean(panels[c], axis=1) for c in panels])
    fdm = _pooled_fdm(means, tb) if fdm_mode == "mine" else tb.pooled_fdm(means)
    fdm_df = pl.DataFrame({"timestamp": grid, "fdm": fdm})

    # `normalise_signal` is stateful over the sequence it receives, and upstream runs it on
    # each instrument's OWN bars (book_one is per instrument). Feeding it the union grid with
    # NaN gaps changes the rolling mean-abs, so the per-asset slicing is part of the contract.
    out = []
    for a in assets:
        f = feat.filter(pl.col("asset") == a).sort("timestamp").select("timestamp", "Trend_sign", "Skew_sign", "VoV_sign")
        f = f.with_columns(
            Sign_raw=0.25 * pl.col("Trend_sign")
            + 0.25 * pl.col("Skew_sign")
            + 0.25 * pl.col("VoV_sign")
        )
        f = f.join(fdm_df, on="timestamp", how="left").with_columns(
            fdm_norm=pl.col("Sign_raw") * pl.col("fdm")
        )
        f = f.with_columns(FDM_MASTER=tb.normalise_signal(f["fdm_norm"]))
        f = f.with_columns(
            SIGNAL=pl.col("FDM_MASTER")
            .ewm_mean(alpha=0.5, adjust=False, min_samples=1, ignore_nulls=False)
            .clip(-SIGNAL_CAP, SIGNAL_CAP)
        )
        out.append(f.with_columns(asset=pl.lit(a)).select("timestamp", "asset", "SIGNAL"))
    return pl.concat(out).sort("timestamp", "asset")


def main() -> int:
    tb = _tb()
    contract = load_contract(Path("contracts/multi_asset_1d_v1.yaml"))
    panel = pl.read_parquet(contract.exploratory_data_path)
    feat = build_signal_frame(panel, contract)

    mine = feat.select("timestamp", "asset", "SIGNAL").sort("timestamp", "asset")
    theirs = upstream_signal(feat, tb)

    joined = mine.join(theirs, on=["timestamp", "asset"], how="inner", suffix="_up")
    a, b = joined["SIGNAL"].to_numpy(), joined["SIGNAL_up"].to_numpy()
    both = np.isfinite(a) & np.isfinite(b)
    diff = np.abs(a[both] - b[both])
    print(f"rows compared: {int(both.sum())} of {joined.height}")
    print(f"max |diff|   : {float(diff.max()):.3e}")
    print(f"mean |diff|  : {float(diff.mean()):.3e}")
    ok = bool(diff.max() < 1e-9)
    print("assembly matches the upstream block:", ok)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
