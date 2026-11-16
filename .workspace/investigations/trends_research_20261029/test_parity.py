"""Differential tests: my transcribed algebra vs the upstream reference functions.

The upstream pipeline cannot be run end-to-end (Norgate panel absent), but every piece I
re-implemented rather than imported *can* be compared against the repo's own function on
identical inputs. Anything that fails here is a port defect; anything that passes narrows
the failure to the data or the strategy itself.

Run: uv run python .workspace/investigations/trends_research_20261029/test_parity.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path("strategies/candidates")))
sys.path.insert(0, str(Path("strategies/candidates/carver_daily")))

from s183_reference import _idm, _pooled_fdm, _tb

PORTFOLIO = Path("data/external/trends-research/Live/3_Portfolio/portfolio.py")


def _portfolio():
    spec = importlib.util.spec_from_file_location("trends_portfolio", PORTFOLIO)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check(name: str, ok: bool, detail: str) -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {name:22} {detail}")
    return ok


def main() -> int:
    tb, pf = _tb(), _portfolio()
    rng = np.random.default_rng(7)
    ok = True

    # 1. IDM: my loop vs portfolio.ewm_corr_sum + idm_from
    T, K = 900, 6
    rets = rng.normal(0, 0.01, (T, K)) + rng.normal(0, 0.004, (T, 1))  # correlated
    rets[:60] = np.nan
    active = np.isfinite(rets)
    wcw, n_act, _, _act = pf.ewm_corr_sum(rets, active, 512, 256)
    ref = pf.idm_from(wcw)
    mine, mine_n = _idm(rets, active)
    diff = np.nanmax(np.abs(ref - mine))
    ok &= check("idm", diff < 1e-9, f"max|diff|={diff:.2e}, n_act match={np.array_equal(n_act, mine_n)}")

    # 2. FDM: my generalised form vs pooled_fdm on four sleeves
    means = np.column_stack([rng.normal(size=800) for _ in range(4)])
    means[:300] = np.nan
    ref_fdm = tb.pooled_fdm(means)
    mine_fdm = _pooled_fdm(means, tb)
    diff = np.nanmax(np.abs(ref_fdm - mine_fdm))
    ok &= check("pooled_fdm", diff < 1e-9, f"max|diff|={diff:.2e}")

    # 3. Sizing algebra: reference k*pointsize*price vs my (S/10)*tau*w*idm/rel_vol
    S, tau, w, idm, price, vol_abs = 7.3, 0.20, 1.0 / 6.0, 1.8, 4200.0, 38.0
    sigma = vol_abs * 1.0 * np.sqrt(256)  # price_vol_curr_ann(pointsize=1)
    k = (S / 10.0) * (tau * w * idm) / sigma
    ref_weight = k * 1.0 * price
    rel_vol = vol_abs * np.sqrt(256) / price
    mine_weight = (S / 10.0) * tau * w * idm / rel_vol
    diff = abs(ref_weight - mine_weight)
    ok &= check("sizing algebra", diff < 1e-12, f"ref={ref_weight:.6f} mine={mine_weight:.6f}")

    # 4. GDD gate shape: the reference expression is inline in portfolio.simulate, so test
    # its properties rather than re-deriving it. gdd(dd) = 1 - 0.5/(1 + exp(-10*(-0.10-dd))).
    def gdd_expr(dd: float) -> float:
        return 1.0 - (1.0 - 0.50) / (1.0 + np.exp(-10.0 * (-0.10 - dd)))

    at_threshold, at_floor, at_zero = gdd_expr(-0.10), gdd_expr(-1e6), gdd_expr(0.0)
    # deeper drawdown (more negative dd) must cut size: gdd increases with dd
    mono = all(gdd_expr(a) < gdd_expr(b) for a, b in zip(np.linspace(-0.5, 0.1, 20)[:-1],
                                                         np.linspace(-0.5, 0.1, 20)[1:]))
    ok &= check("gdd properties",
                abs(at_threshold - 0.75) < 1e-12 and abs(at_floor - 0.50) < 1e-12
                and at_zero > at_threshold and mono,
                f"gdd(-0.10)={at_threshold:.6f} gdd(0)={at_zero:.4f} floor={at_floor:.4f} monotone={mono}")
    # cross-check against the independent audit of the repo: neutral gates 0.75 vol x 0.866 dd
    ok &= check("neutral gate product", abs(at_zero - 0.866) < 1e-3
                and abs(0.75 * at_zero - 0.6495) < 1e-3, f"vol 0.75 x dd {at_zero:.4f} = {0.75*at_zero:.4f}")

    print("\nALL PASS" if ok else "\nDEFECTS FOUND")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
