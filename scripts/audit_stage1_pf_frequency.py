"""Independent frequency calibration for Warden's frozen bar-return profit factor.

No changes to src/warden, thresholds, or historical selection evidence.
Normal iid return assumptions are illustrative, not a universal mapping.
"""

from __future__ import annotations

import inspect
import json
import math

from scipy.optimize import brentq
from scipy.stats import norm

from warden.gates import evaluate_stage1_rules_ts


def expected_bar_profit_factor(annualized_sharpe: float, bars_per_year: int) -> float:
    """Analytical E[positive normal return]/E[negative magnitude]."""
    z = annualized_sharpe / math.sqrt(bars_per_year)
    gain = norm.pdf(z) + z * norm.cdf(z)
    loss = norm.pdf(z) - z * norm.cdf(-z)
    return float(gain / loss)


def implied_sharpe(profit_factor: float, bars_per_year: int) -> float:
    return float(brentq(lambda sr: expected_bar_profit_factor(sr, bars_per_year) - profit_factor,
                        0.0, 30.0))


def main() -> None:
    gate = inspect.signature(evaluate_stage1_rules_ts)
    pf = float(gate.parameters["min_profit_factor"].default)
    rows = []
    for frequency, periods in (("1d_252", 252), ("1h_8760", 8760), ("15m_35040", 35040)):
        rows.append({
            "frequency": frequency,
            "bars_per_year": periods,
            "expected_pf_at_sr1": round(expected_bar_profit_factor(1, periods), 5),
            "expected_pf_at_sr2": round(expected_bar_profit_factor(2, periods), 5),
            "implied_annual_sr_for_pf_gate": round(implied_sharpe(pf, periods), 4),
        })
    print(json.dumps({"frozen_pf_gate": pf, "assumption": "iid normal per-bar returns",
                      "results": rows}, indent=2))


if __name__ == "__main__":
    main()
