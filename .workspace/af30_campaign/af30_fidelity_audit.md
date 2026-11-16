# Systematic Fidelity Audit: 25 AF30 Candidate Modules vs. Robert Carver's Primary Book Text

## Executive Summary
This audit compares every candidate strategy module ported from `quant-research` (`strategies/candidates/ts_s*.py`) directly against the primary source text of Robert Carver's *Advanced Futures Trading Strategies* (2022) extracted from the local book PDF.

### Findings Breakdown across 25 Ported Strategies:
- **Materially Divergent / Unfaithful**: 15 strategies
- **Not Verifiable (Missing Term Structure Data)**: 7 strategies
- **Partial Adaptations**: 3 strategies
- **Exact / Faithful Implementations**: **0 strategies**

---

## Master Strategy Audit Table

| Strategy | Module Filename | Book Reference | Audit Classification | Injected 3x ATR Stop? | Cash Vol Sizing (`vol_target`)? | Key Documented Divergences |
|---|---|---|---|---|---|---|
| **S01: Buy and hold, single contract** | `ts_s01_buy_hold_single_1h.py` | pp. 21-63 | **Materially Divergent** | Yes (Divergent) | No (Omitted) | • Injected 3.0x ATR-14 trailing stop and 720-hour rebalance loop.<br>• Multi-asset waterfall weighting applied instead of fixed single contract benchmark. |
| **S02: Buy and hold with risk scaling** | `ts_s02_buy_hold_risk_scaled_1h.py` | pp. 67-92 | **Materially Divergent** | Yes (Divergent) | No (Omitted) | • Omitted cash volatility target (vol_target absent); positions sized as 1.0 * weight.<br>• Injected 3.0x ATR-14 trailing stop and 720-hour rebalance loop. |
| **S03: Buy and hold with variable risk scaling** | `ts_s03_buy_hold_variable_risk_1h.py` | pp. 94-116 | **Partial Adaptation** | Yes (Divergent) | Yes | • Implements vol_target (0.10) and inverse vol, but injected 3.0x ATR-14 trailing stop.<br>• Rebalances every 720 hours (30 days) on 1h bars. |
| **S04: Buy and hold portfolio with variable risk position sizing** | `ts_s04_buy_hold_portfolio_scaling_1h.py` | pp. 119-145 | **Partial Adaptation** | Yes (Divergent) | Yes | • Implements vol_target (0.10) and waterfall weights, but omits portfolio IDM.<br>• Injected 3.0x ATR-14 trailing stop and 720-hour rebalance. |
| **S05: Slow trend following, long only** | `ts_s05_slow_trend_long_only_1h.py` | pp. 149-161 | **Materially Divergent** | Yes (Divergent) | No (Omitted) | • Substituted 16/64 hour EMA for Carver's 64/256 day moving average crossover.<br>• Normalized by fixed scale 0.01 instead of daily price volatility.<br>• Omitted cash vol sizing; injected 3.0x ATR-14 stop and 504h rebalance. |
| **S06: Slow trend following, long and short** | `ts_s06_slow_trend_long_short_1h.py` | pp. 164-176 | **Materially Divergent** | Yes (Divergent) | No (Omitted) | • Substituted 16/64 hour EMA for Carver's 64/256 day moving average crossover.<br>• Normalized by fixed scale 0.01 instead of daily price volatility.<br>• Omitted cash vol sizing; injected 3.0x ATR-14 stop and 504h rebalance. |
| **S07: Slow trend following with trend strength** | `ts_s07_slow_trend_strength_1h.py` | pp. 178-188 | **Materially Divergent** | Yes (Divergent) | No (Omitted) | • Uses 16/64 hour EMA; computes trend_norm = trend_raw / 0.01 and trend_norm * abs(trend_norm) * tanh.<br>• Carver p. 188 uses linear forecast divided by daily price volatility and scalar 1.91, not quadratic tanh.<br>• Injected 3.0x ATR-14 stop. |
| **S08: Fast trend following, long and short with trend strength** | `ts_s08_fast_trend_strength_1h.py` | pp. 191-199 | **Materially Divergent** | Yes (Divergent) | No (Omitted) | • Uses 8/32 hour EMA; computes quadratic tanh forecast instead of linear scalar 3.75.<br>• Omitted 10% buffering; injected 3.0x ATR-14 stop. |
| **S09: Multiple trend following rules** | `ts_ewmac_multi_1h.py` | pp. 202-229 | **Partial Adaptation** | No (Faithful) | No (Omitted) | • Uses only 2 speeds (8/32 and 16/64 hours) instead of Carver's 6 EWMAC variations.<br>• Omitted FDM multiplier (1.41) and Table 33 scalars.<br>• Positive: Sole module that correctly omits the 3.0x ATR trailing stop. |
| **S10: Basic carry** | `ts_s10_basic_carry_1h.py` | pp. 233-261 | **Not Verifiable (Missing Term Structure)** | Yes (Divergent) | No (Omitted) | • Requires futures term structure and roll yield curves; reads carry_rate/funding_rate which is 0.0 in non-crypto.<br>• Crashes on zero variance in non-crypto panels.<br>• Injected 3.0x ATR-14 stop. |
| **S11: Combined carry and trend** | `ts_s11_combined_carry_trend_1h.py` | pp. 264-276 | **Not Verifiable (Missing Term Structure)** | Yes (Divergent) | No (Omitted) | • Requires futures term structure and roll yield curves; reads carry_rate/funding_rate which is 0.0 in non-crypto.<br>• Injected 3.0x ATR-14 stop. |
| **S12: Adjusted trend** | `ts_s12_adjusted_trend_1h.py` | pp. 280-294 | **Not Verifiable (Missing Term Structure)** | Yes (Divergent) | No (Omitted) | • Requires futures term structure and roll yield curves; reads carry_rate/funding_rate which is 0.0 in non-crypto.<br>• Injected 3.0x ATR-14 stop. |
| **S13: Trend following and carry in different risk regimes** | `ts_s13_trend_carry_regimes_1h.py` | pp. 297-305 | **Not Verifiable (Missing Term Structure)** | Yes (Divergent) | No (Omitted) | • Requires futures term structure and roll yield curves; reads carry_rate/funding_rate which is 0.0 in non-crypto.<br>• Carry mechanism was completely deleted; stripped down to 16/64h trend with vol regimes.<br>• Injected 3.0x ATR-14 stop. |
| **S14: Spot trend** | `ts_s14_spot_trend_1h.py` | pp. 308-315 | **Materially Divergent** | Yes (Divergent) | No (Omitted) | • Evaluates 32/128h EMA on standard back-adjusted series; does not compute synthetic spot price.<br>• Injected 3.0x ATR-14 stop. |
| **S15: Accurate carry** | `ts_s15_accurate_carry_1h.py` | pp. 318-329 | **Not Verifiable (Missing Term Structure)** | Yes (Divergent) | No (Omitted) | • Requires futures term structure and roll yield curves; reads carry_rate/funding_rate which is 0.0 in non-crypto.<br>• Injected 3.0x ATR-14 stop. |
| **S16: Trend and carry allocation** | `ts_s16_trend_carry_allocation_1h.py` | pp. 332-340 | **Not Verifiable (Missing Term Structure)** | Yes (Divergent) | No (Omitted) | • Requires futures term structure and roll yield curves; reads carry_rate/funding_rate which is 0.0 in non-crypto.<br>• Injected 3.0x ATR-14 stop. |
| **S17: Normalised trend** | `ts_s17_normalised_trend_1h.py` | pp. 343-353 | **Materially Divergent** | Yes (Divergent) | No (Omitted) | • Computes trend_z = trend_raw / rolling_std(trend_raw, 240) (normalizing the signal itself).<br>• Carver pp. 343-346 constructs a normalized cumulative price series first, then applies EWMAC.<br>• Injected 3.0x ATR-14 stop. |
| **S18: Trend following asset classes** | `ts_s18_trend_asset_classes_1h.py` | pp. 356-363 | **Materially Divergent** | Yes (Divergent) | No (Omitted) | • Averages two EMA pairs (16/64 and 32/128 hours) per individual instrument.<br>• Carver pp. 356-365 builds an equal-weight composite price index for each asset class and applies EWMAC to the class index.<br>• Injected 3.0x ATR-14 stop. |
| **S19: Cross-sectional momentum** | `ts_s19_xs_momentum_1h.py` | pp. 366-375 | **Materially Divergent** | Yes (Divergent) | No (Omitted) | • Substituted 24-hour price return (close.pct_change(24)) for Carver's 12-month (252-day) momentum.<br>• Ranks hourly noise cross-sectionally. Injected 3.0x ATR-14 stop. |
| **S20: Cross-sectional carry** | `ts_s20_xs_carry_1h.py` | pp. 378-385 | **Not Verifiable (Missing Term Structure)** | Yes (Divergent) | No (Omitted) | • Requires futures term structure and roll yield curves; reads carry_rate/funding_rate which is 0.0 in non-crypto.<br>• Injected 3.0x ATR-14 stop. |
| **S21: Breakout** | `ts_breakout_20_1h.py` | pp. 389-401 | **Materially Divergent** | Yes (Divergent) | No (Omitted) | • Computes percentage pierce (close - upper) / close / 0.02, requiring 40% breakout to reach +20 forecast.<br>• Carver pp. 389-402 uses price-within-range 40 * (price - mid) / range with EWMA smoothing and cash vol sizing.<br>• Added ungrounded post-stop reset latch and injected 3.0x ATR stop. |
| **S22: Value** | `ts_s22_value_1h.py` | pp. 404-409 | **Materially Divergent** | Yes (Divergent) | No (Omitted) | • Substituted a 45-day (1080-hour) price change (-pct_change(1080)) divided by 0.1.<br>• Carver pp. 403-409 specifies a 5-year (1260 daily bars) relative value regression against asset-class mean with scalar 7.27.<br>• Injected 3.0x ATR-14 stop. |
| **S23: Acceleration** | `ts_s23_acceleration_1h.py` | pp. 411-420 | **Materially Divergent** | Yes (Divergent) | No (Omitted) | • Substituted 24-hour second difference of price (pct_change(24) - shift(24)) divided by 0.01.<br>• Carver pp. 410-422 specifies rate of change of EWMAC trend forecasts across 4 speeds with Table 100 scalars.<br>• Injected 3.0x ATR-14 stop. |
| **S26: Fast mean reversion** | `ts_s26_fast_mean_reversion_1h.py` | pp. 480-495 | **Materially Divergent** | Yes (Divergent) | No (Omitted) | • Substituted a 60-hour Z-score reversal (-zscore_60) submitting aggressive market orders.<br>• Carver pp. 480-509 specifies passive limit order market making bracketing mid-price to earn spread.<br>• Injected 3.0x ATR-14 stop. |
| **S27: Safer fast mean reversion** | `ts_s27_safer_fast_mean_reversion_1h.py` | pp. 497-508 | **Materially Divergent** | Yes (Divergent) | No (Omitted) | • Substituted a 60-hour Z-score reversal (-zscore_60) submitting aggressive market orders.<br>• Carver pp. 480-509 specifies passive limit order market making bracketing mid-price to earn spread.<br>• Injected 3.0x ATR-14 stop. |

---

## Systemic Distortion Patterns Documented

1. **Universal Injection of 3.0x ATR Trailing Stops (24 of 25 Modules)**:
   - *Book*: Strategies 1 through 20 and 22 through 27 have **zero stop-loss rules**. Carver's framework manages drawdown through continuous forecast decay and cash volatility scaling. Trailing stops are covered exclusively in Part Six (Tactics) as a discretionary risk limit.
   - *Ported Modules*: An ad-hoc $3.0	ext{x}$ ATR-14 trailing stop loop was copy-pasted into 24 of 25 files, liquidating even passive Buy & Hold portfolios on routine market pullbacks.

2. **Omission of Cash Volatility Targeting (`vol_target` Absent in 23 Modules)**:
   - *Book*: Position sizing across all trading chapters follows Carver's cash volatility formula: $	ext{Position}_i = (	ext{Forecast}_i / 10) 	imes (	au w_i / \sigma_i)$, targeting an exact annual risk percentage $	au$ (e.g. 16%–20%).
   - *Ported Modules*: Positions are sized purely as $(	ext{fc} / 20) 	imes 	ext{weight}$, with no $	au$ or volatility scaling, leaving positions micro-scaled ($<0.01$ leverage) where friction consumes all returns.

3. **Indicator & Horizon Mismatches**:
   - **S17 (Normalised Trend)**: Book computes a normalized cumulative price index ($p / \sigma$); module computes $Z$-score of the moving average crossover ($(	ext{EMA}_{16} - 	ext{EMA}_{64}) / 	ext{rolling\_std}$).
   - **S18 (Asset Class Trend)**: Book builds an equal-weight composite index for each asset class; module averages two arbitrary EMA pairs (16/64 and 32/128) per individual asset.
   - **S19 (Cross-Sectional Momentum)**: Book uses trailing 12-month (252-day) returns; module uses a 24-hour price change (`pct_change(24)`).
   - **S21 (Breakout)**: Book uses continuous price-within-range midpoint $40 	imes (p - 	ext{mid}) / 	ext{range}$ with EWMA $h/4$ smoothing; module uses raw percentage pierce divided by $0.02$.
   - **S22 (Value)**: Book uses 5-year relative value within asset classes with scalar 7.27; module uses 45-day price reversal (`-pct_change(1080)`).
   - **S23 (Acceleration)**: Book uses rate of change of EWMAC trend forecasts ($\Delta 	ext{EWMAC}$); module uses second difference of price (`pct_change(24) - shift(24)`).
   - **S26/S27 (Fast Mean Reversion)**: Book uses passive limit orders bracketing midpoint to capture spread; module uses aggressive market orders on a 60-hour Z-score.

4. **Missing Term Structure Data (6 Carry Modules)**:
   - Strategies 10, 11, 13, 15, 16, and 20 require futures term structure curves to compute roll yield. Because the local store lacked futures contract curves, they read a non-existent `carry_rate`/`funding_rate` column ($0.0$), rendering them unviable or crashing on zero variance.
