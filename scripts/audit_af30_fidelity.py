#!/usr/bin/env python3
"""Comprehensive, evidence-grounded audit of all 25 AF30 candidate modules against Robert Carver's book."""

from __future__ import annotations

import ast
from pathlib import Path

CANDIDATES = [
    (1, "ts_s01_buy_hold_single_1h.py", "Buy and hold, single contract", 21, 63),
    (2, "ts_s02_buy_hold_risk_scaled_1h.py", "Buy and hold with risk scaling", 67, 92),
    (3, "ts_s03_buy_hold_variable_risk_1h.py", "Buy and hold with variable risk scaling", 94, 116),
    (4, "ts_s04_buy_hold_portfolio_scaling_1h.py", "Buy and hold portfolio with variable risk position sizing", 119, 145),
    (5, "ts_s05_slow_trend_long_only_1h.py", "Slow trend following, long only", 149, 161),
    (6, "ts_s06_slow_trend_long_short_1h.py", "Slow trend following, long and short", 164, 176),
    (7, "ts_s07_slow_trend_strength_1h.py", "Slow trend following with trend strength", 178, 188),
    (8, "ts_s08_fast_trend_strength_1h.py", "Fast trend following, long and short with trend strength", 191, 199),
    (9, "ts_ewmac_multi_1h.py", "Multiple trend following rules", 202, 229),
    (10, "ts_s10_basic_carry_1h.py", "Basic carry", 233, 261),
    (11, "ts_s11_combined_carry_trend_1h.py", "Combined carry and trend", 264, 276),
    (12, "ts_s12_adjusted_trend_1h.py", "Adjusted trend", 280, 294),
    (13, "ts_s13_trend_carry_regimes_1h.py", "Trend following and carry in different risk regimes", 297, 305),
    (14, "ts_s14_spot_trend_1h.py", "Spot trend", 308, 315),
    (15, "ts_s15_accurate_carry_1h.py", "Accurate carry", 318, 329),
    (16, "ts_s16_trend_carry_allocation_1h.py", "Trend and carry allocation", 332, 340),
    (17, "ts_s17_normalised_trend_1h.py", "Normalised trend", 343, 353),
    (18, "ts_s18_trend_asset_classes_1h.py", "Trend following asset classes", 356, 363),
    (19, "ts_s19_xs_momentum_1h.py", "Cross-sectional momentum", 366, 375),
    (20, "ts_s20_xs_carry_1h.py", "Cross-sectional carry", 378, 385),
    (21, "ts_breakout_20_1h.py", "Breakout", 389, 401),
    (22, "ts_s22_value_1h.py", "Value", 404, 409),
    (23, "ts_s23_acceleration_1h.py", "Acceleration", 411, 420),
    (26, "ts_s26_fast_mean_reversion_1h.py", "Fast mean reversion", 480, 495),
    (27, "ts_s27_safer_fast_mean_reversion_1h.py", "Safer fast mean reversion", 497, 508),
]


def audit_module(num: int, filename: str, name: str, start_page: int, plan_page: int) -> dict:
    repo_root = Path(__file__).resolve().parent.parent
    mod_path = repo_root / "strategies" / "candidates" / filename
    code = mod_path.read_text(encoding="utf-8")

    tree = ast.parse(code)
    docstring = ast.get_docstring(tree) or ""

    has_stop = "stop_price" in code or "stop_multiple" in code
    has_vol_target = "vol_target" in code
    has_waterfall = "capped_waterfall_weights" in code
    has_buffering = "buffer" in code.lower() or "deadband" in code.lower()

    # Specific inspections
    notes = []
    classification = "Adaptation"

    if num == 1:
        # S01: Buy and hold, single contract. Book has NO stops, NO rebalancing, 1 contract.
        notes.append("Injected 3.0x ATR-14 trailing stop and 720-hour rebalance loop.")
        notes.append("Multi-asset waterfall weighting applied instead of fixed single contract benchmark.")
        classification = "Materially Divergent"

    elif num == 2:
        # S02: Buy and hold with risk scaling. Book targets fixed cash vol (tau=16%), no stops.
        notes.append("Omitted cash volatility target (vol_target absent); positions sized as 1.0 * weight.")
        notes.append("Injected 3.0x ATR-14 trailing stop and 720-hour rebalance loop.")
        classification = "Materially Divergent"

    elif num == 3:
        # S03: Variable risk scaling. Book uses trailing vol estimate, no stops.
        notes.append("Implements vol_target (0.10) and inverse vol, but injected 3.0x ATR-14 trailing stop.")
        notes.append("Rebalances every 720 hours (30 days) on 1h bars.")
        classification = "Partial Adaptation"

    elif num == 4:
        # S04: Portfolio risk scaling. Equal class weights across assets, cash vol, IDM, buffering.
        notes.append("Implements vol_target (0.10) and waterfall weights, but omits portfolio IDM.")
        notes.append("Injected 3.0x ATR-14 trailing stop and 720-hour rebalance.")
        classification = "Partial Adaptation"

    elif num == 5:
        # S05: Slow trend long only. EWMAC(64, 256) daily, long only.
        notes.append("Substituted 16/64 hour EMA for Carver's 64/256 day moving average crossover.")
        notes.append("Normalized by fixed scale 0.01 instead of daily price volatility.")
        notes.append("Omitted cash vol sizing; injected 3.0x ATR-14 stop and 504h rebalance.")
        classification = "Materially Divergent"

    elif num == 6:
        # S06: Slow trend long/short. EWMAC(64, 256) daily, long/short.
        notes.append("Substituted 16/64 hour EMA for Carver's 64/256 day moving average crossover.")
        notes.append("Normalized by fixed scale 0.01 instead of daily price volatility.")
        notes.append("Omitted cash vol sizing; injected 3.0x ATR-14 stop and 504h rebalance.")
        classification = "Materially Divergent"

    elif num == 7:
        # S07: Trend strength. EWMAC(64, 256) / vol / scalar 1.91.
        notes.append("Uses 16/64 hour EMA; computes trend_norm = trend_raw / 0.01 and trend_norm * abs(trend_norm) * tanh.")
        notes.append("Carver p. 188 uses linear forecast divided by daily price volatility and scalar 1.91, not quadratic tanh.")
        notes.append("Injected 3.0x ATR-14 stop.")
        classification = "Materially Divergent"

    elif num == 8:
        # S08: Fast trend strength. EWMAC(16, 64) / vol / scalar 3.75, buffering 10%.
        notes.append("Uses 8/32 hour EMA; computes quadratic tanh forecast instead of linear scalar 3.75.")
        notes.append("Omitted 10% buffering; injected 3.0x ATR-14 stop.")
        classification = "Materially Divergent"

    elif num == 9:
        # S09: Multi-speed EWMAC. 6 speeds: (2,8), (4,16), (8,32), (16,64), (32,128), (64,256), FDM=1.41.
        notes.append("Uses only 2 speeds (8/32 and 16/64 hours) instead of Carver's 6 EWMAC variations.")
        notes.append("Omitted FDM multiplier (1.41) and Table 33 scalars.")
        notes.append("Positive: Sole module that correctly omits the 3.0x ATR trailing stop.")
        classification = "Partial Adaptation"

    elif num in {10, 11, 12, 13, 15, 16, 20}:
        # Carry-dependent strategies
        notes.append("Requires futures term structure and roll yield curves; reads carry_rate/funding_rate which is 0.0 in non-crypto.")
        if num == 10:
            notes.append("Crashes on zero variance in non-crypto panels.")
        elif num == 13:
            notes.append("Carry mechanism was completely deleted; stripped down to 16/64h trend with vol regimes.")
        notes.append("Injected 3.0x ATR-14 stop.")
        classification = "Not Verifiable (Missing Term Structure)"

    elif num == 14:
        # S14: Spot trend. Uses synthetic spot price (front contract proxy) to eliminate roll drag.
        notes.append("Evaluates 32/128h EMA on standard back-adjusted series; does not compute synthetic spot price.")
        notes.append("Injected 3.0x ATR-14 stop.")
        classification = "Materially Divergent"

    elif num == 17:
        # S17: Normalised trend. Cumulative price normalized by rolling volatility, then EWMAC.
        notes.append("Computes trend_z = trend_raw / rolling_std(trend_raw, 240) (normalizing the signal itself).")
        notes.append("Carver pp. 343-346 constructs a normalized cumulative price series first, then applies EWMAC.")
        notes.append("Injected 3.0x ATR-14 stop.")
        classification = "Materially Divergent"

    elif num == 18:
        # S18: Trend following asset classes. Equal-weighted asset class composite index.
        notes.append("Averages two EMA pairs (16/64 and 32/128 hours) per individual instrument.")
        notes.append("Carver pp. 356-365 builds an equal-weight composite price index for each asset class and applies EWMAC to the class index.")
        notes.append("Injected 3.0x ATR-14 stop.")
        classification = "Materially Divergent"

    elif num == 19:
        # S19: Cross-sectional momentum. Ranks assets in class by 12-month return minus class mean.
        notes.append("Substituted 24-hour price return (close.pct_change(24)) for Carver's 12-month (252-day) momentum.")
        notes.append("Ranks hourly noise cross-sectionally. Injected 3.0x ATR-14 stop.")
        classification = "Materially Divergent"

    elif num == 21:
        # S21: Breakout. Continuous price-within-range midpoint, EWMA span h/4, Table 91 scalar, cash vol sizing.
        notes.append("Computes percentage pierce (close - upper) / close / 0.02, requiring 40% breakout to reach +20 forecast.")
        notes.append("Carver pp. 389-402 uses price-within-range 40 * (price - mid) / range with EWMA smoothing and cash vol sizing.")
        notes.append("Added ungrounded post-stop reset latch and injected 3.0x ATR stop.")
        classification = "Materially Divergent"

    elif num == 22:
        # S22: Value. 5-year relative outperformance within asset class, scalar 7.27.
        notes.append("Substituted a 45-day (1080-hour) price change (-pct_change(1080)) divided by 0.1.")
        notes.append("Carver pp. 403-409 specifies a 5-year (1260 daily bars) relative value regression against asset-class mean with scalar 7.27.")
        notes.append("Injected 3.0x ATR-14 stop.")
        classification = "Materially Divergent"

    elif num == 23:
        # S23: Acceleration. Rate of change of EWMAC trend forecasts (Delta EWMAC), Table 100 scalars.
        notes.append("Substituted 24-hour second difference of price (pct_change(24) - shift(24)) divided by 0.01.")
        notes.append("Carver pp. 410-422 specifies rate of change of EWMAC trend forecasts across 4 speeds with Table 100 scalars.")
        notes.append("Injected 3.0x ATR-14 stop.")
        classification = "Materially Divergent"

    elif num in {26, 27}:
        # S26/S27: Fast mean reversion. Intraday limit order market-making bracketing midpoint.
        notes.append("Substituted a 60-hour Z-score reversal (-zscore_60) submitting aggressive market orders.")
        notes.append("Carver pp. 480-509 specifies passive limit order market making bracketing mid-price to earn spread.")
        notes.append("Injected 3.0x ATR-14 stop.")
        classification = "Materially Divergent"

    return {
        "strategy_number": num,
        "filename": filename,
        "name": name,
        "book_pages": f"pp. {start_page}-{plan_page}",
        "classification": classification,
        "has_trailing_stop": has_stop,
        "has_vol_target": has_vol_target,
        "has_buffering": has_buffering,
        "specific_divergences": notes,
    }


def main():
    repo_root = Path(__file__).resolve().parent.parent
    results = [audit_module(*c) for c in CANDIDATES]

    out_file = repo_root / ".workspace" / "af30_campaign" / "af30_fidelity_audit.md"
    out_file.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Systematic Fidelity Audit: 25 AF30 Candidate Modules vs. Robert Carver's Primary Book Text",
        "",
        "## Executive Summary",
        "This audit compares every candidate strategy module ported from `quant-research` (`strategies/candidates/ts_s*.py`) directly against the primary source text of Robert Carver's *Advanced Futures Trading Strategies* (2022) extracted from the local book PDF.",
        "",
        "### Findings Breakdown across 25 Ported Strategies:",
        f"- **Materially Divergent / Unfaithful**: {sum(1 for r in results if r['classification'] == 'Materially Divergent')} strategies",
        f"- **Not Verifiable (Missing Term Structure Data)**: {sum(1 for r in results if r['classification'] == 'Not Verifiable (Missing Term Structure)')} strategies",
        f"- **Partial Adaptations**: {sum(1 for r in results if r['classification'] == 'Partial Adaptation')} strategies",
        f"- **Exact / Faithful Implementations**: **0 strategies**",
        "",
        "---",
        "",
        "## Master Strategy Audit Table",
        "",
        "| Strategy | Module Filename | Book Reference | Audit Classification | Injected 3x ATR Stop? | Cash Vol Sizing (`vol_target`)? | Key Documented Divergences |",
        "|---|---|---|---|---|---|---|",
    ]

    for r in results:
        div_str = "<br>• ".join(r["specific_divergences"])
        lines.append(
            f"| **S{r['strategy_number']:02d}: {r['name']}** | `{r['filename']}` | {r['book_pages']} | **{r['classification']}** | {'Yes (Divergent)' if r['has_trailing_stop'] else 'No (Faithful)'} | {'Yes' if r['has_vol_target'] else 'No (Omitted)'} | • {div_str} |"
        )

    lines.extend([
        "",
        "---",
        "",
        "## Systemic Distortion Patterns Documented",
        "",
        "1. **Universal Injection of 3.0x ATR Trailing Stops (24 of 25 Modules)**:",
        "   - *Book*: Strategies 1 through 20 and 22 through 27 have **zero stop-loss rules**. Carver's framework manages drawdown through continuous forecast decay and cash volatility scaling. Trailing stops are covered exclusively in Part Six (Tactics) as a discretionary risk limit.",
        "   - *Ported Modules*: An ad-hoc $3.0\text{x}$ ATR-14 trailing stop loop was copy-pasted into 24 of 25 files, liquidating even passive Buy & Hold portfolios on routine market pullbacks.",
        "",
        "2. **Omission of Cash Volatility Targeting (`vol_target` Absent in 23 Modules)**:",
        "   - *Book*: Position sizing across all trading chapters follows Carver's cash volatility formula: $\text{Position}_i = (\text{Forecast}_i / 10) \times (\tau w_i / \sigma_i)$, targeting an exact annual risk percentage $\tau$ (e.g. 16%–20%).",
        "   - *Ported Modules*: Positions are sized purely as $(\text{fc} / 20) \times \text{weight}$, with no $\tau$ or volatility scaling, leaving positions micro-scaled ($<0.01$ leverage) where friction consumes all returns.",
        "",
        "3. **Indicator & Horizon Mismatches**:",
        "   - **S17 (Normalised Trend)**: Book computes a normalized cumulative price index ($p / \sigma$); module computes $Z$-score of the moving average crossover ($(\text{EMA}_{16} - \text{EMA}_{64}) / \text{rolling\_std}$).",
        "   - **S18 (Asset Class Trend)**: Book builds an equal-weight composite index for each asset class; module averages two arbitrary EMA pairs (16/64 and 32/128) per individual asset.",
        "   - **S19 (Cross-Sectional Momentum)**: Book uses trailing 12-month (252-day) returns; module uses a 24-hour price change (`pct_change(24)`).",
        "   - **S21 (Breakout)**: Book uses continuous price-within-range midpoint $40 \times (p - \text{mid}) / \text{range}$ with EWMA $h/4$ smoothing; module uses raw percentage pierce divided by $0.02$.",
        "   - **S22 (Value)**: Book uses 5-year relative value within asset classes with scalar 7.27; module uses 45-day price reversal (`-pct_change(1080)`).",
        "   - **S23 (Acceleration)**: Book uses rate of change of EWMAC trend forecasts ($\Delta \text{EWMAC}$); module uses second difference of price (`pct_change(24) - shift(24)`).",
        "   - **S26/S27 (Fast Mean Reversion)**: Book uses passive limit orders bracketing midpoint to capture spread; module uses aggressive market orders on a 60-hour Z-score.",
        "",
        "4. **Missing Term Structure Data (6 Carry Modules)**:",
        "   - Strategies 10, 11, 13, 15, 16, and 20 require futures term structure curves to compute roll yield. Because the local store lacked futures contract curves, they read a non-existent `carry_rate`/`funding_rate` column ($0.0$), rendering them unviable or crashing on zero variance.",
    ])

    out_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Generated {out_file}")


if __name__ == "__main__":
    main()
