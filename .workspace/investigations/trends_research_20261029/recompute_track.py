"""Recompute the trends-research published paper-trading track from docs/data/pnl.

Independent check of the README's paper-trading table. Reads only the cloned repo's
published JSON.

Provenance note: the split below is by *publication* date taken from git history, which
establishes only when a day-file entered the repository. 171 day-files (2026-01-02 ..
2026-08-28) were added in the initial commit c199db6 on 2026-08-30; the 42 later files
were added one per session. That is evidence about publishing, not about whether orders
were placed or when the P&L was computed: the pre-inception window is a bulk backfill
over data that already existed, and the post-inception window is consistent with a
forward record but does not prove one. Execution provenance stays UNVERIFIED.

Usage: uv run python .workspace/investigations/trends_research_20261029/recompute_track.py
"""

from __future__ import annotations

import json
import math
import statistics as st
from pathlib import Path
from typing import Any

REPO = Path("data/external/trends-research")
DATA = REPO / "docs/data"
# Initial commit c199db6 (2026-08-30) adds 2026-01-02 .. 2026-08-28 in one go.
BACKFILL_END = "2026-08-28"


def load() -> list[tuple[str, dict[str, Any]]]:
    rows = []
    for f in sorted((DATA / "pnl").glob("*.json")):
        d = json.loads(f.read_text())
        if d.get("book"):
            rows.append((d["date"], d["book"]))
    return rows


def stats(rets: list[float], label: str) -> None:
    m, s = st.mean(rets), st.pstdev(rets)
    cum, peak, dd = 1.0, 1.0, 0.0
    for r in rets:
        cum *= 1 + r
        peak = max(peak, cum)
        dd = min(dd, cum / peak - 1)
    ann = ((cum ** (252 / len(rets))) - 1) * 100
    print(
        f"{label:<34} n={len(rets):3d} Sharpe={m / s * math.sqrt(252):6.2f} "
        f"ann_ret={ann:7.2f}% ann_vol={s * math.sqrt(252) * 100:6.2f}% maxDD={dd * 100:6.2f}%"
    )


def main() -> None:
    rows = load()
    if not rows:
        raise SystemExit(f"no pnl day files under {DATA}")
    net = [(d, b["closing_equity_USD"] / b["opening_equity_USD"] - 1) for d, b in rows]
    gross = [(d, b["gross_pnl_USD"] / b["opening_equity_USD"]) for d, b in rows]

    print(f"window {net[0][0]} .. {net[-1][0]}  ({len(net)} sessions)")
    print(f"equity {rows[0][1]['opening_equity_USD']:,.0f} -> {rows[-1][1]['closing_equity_USD']:,.0f}")
    print(f"trading cost {sum(b['trading_cost_USD'] for _, b in rows):,.0f}   "
          f"cash interest {sum(b['interest_USD'] for _, b in rows):,.0f}\n")

    stats([r for _, r in net], "net incl. interest")
    stats([r for _, r in gross], "gross (ex-interest)")

    cut = next(i for i, (d, _) in enumerate(net) if d > BACKFILL_END)
    print()
    stats([r for _, r in net[:cut]], f"backfilled {net[0][0]}..{net[cut - 1][0]}")
    stats([r for _, r in net[cut:]], f"post-inception {net[cut][0]}..{net[-1][0]}")

    # README claim: 145 days (2 Jan - 26 May 2026), Sharpe +0.44, vol 8.56%, maxDD -4.33%
    end = next(i for i, (d, _) in enumerate(net) if d > "2026-05-26")
    print()
    stats([r for _, r in net[:end]], "README window 2026-01-02..05-26")
    print("README claims for that window:      Sharpe=  0.44 ann_vol=  8.56% maxDD= -4.33%")

    meta = json.loads((DATA / "latest.json").read_text())["meta"]
    print(f"\nrepo's own meta: net_sharpe={meta['net_sharpe']} "
          f"ann_ret={meta['net_ann_ret']} ann_vol={meta['net_ann_vol']} "
          f"maxDD={meta['max_drawdown']} sessions={meta['sessions']}")


if __name__ == "__main__":
    main()
