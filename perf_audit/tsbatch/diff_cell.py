"""Locate the first differing positions cell for one module/panel pair (debug aid)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vec_verify as V  # noqa: E402

WT = V.WT


def main() -> None:
    module = sys.argv[1] if len(sys.argv) > 1 else "ts_london_orb_1h"
    panel_name = sys.argv[2] if len(sys.argv) > 2 else "synthetic_stop_trigger"
    cfg = V.MODULES[module]
    contract = V.load_contract(cfg["contract"])
    params = V.spec_params(cfg["spec"])
    old = V.load_module(V.OLD / f"{module}.py", "old")
    new = V.load_module(WT / "strategies" / "candidates" / f"{module}.py", "new")
    panel = V.panel_sets(module, contract, params)[panel_name]
    po = old.generate_positions(panel, contract, **params)["positions"]
    pn = new.generate_positions(panel, contract, **params)["positions"]
    bad = np.argwhere(po != pn)
    print("mismatches", bad.shape[0], "of", po.size)
    ts = panel["timestamp"].unique().sort().to_list()
    assets = sorted(panel["asset"].unique().to_list())
    for r, c in bad[:12]:
        print(f"row={r} ts={ts[r]} asset={assets[c]} old={po[r, c]!r} new={pn[r, c]!r}")
    if bad.shape[0]:
        r0 = int(bad[0][0])
        lo = max(0, r0 - 6)
        print("old rows", lo, "..", r0 + 3, "\n", po[lo : r0 + 4])
        print("new rows", lo, "..", r0 + 3, "\n", pn[lo : r0 + 4])
        hours = np.array([t.hour for t in ts])
        print("hours", hours[lo : r0 + 4])
        print("close", panel.filter(pl.col("timestamp").is_in(ts[lo : r0 + 4])).pivot(index="timestamp", on="asset", values="close").sort("timestamp").to_numpy()[:, 1:])


if __name__ == "__main__":
    main()
