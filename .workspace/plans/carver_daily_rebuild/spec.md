# Spec: Robert Carver AF30 Daily Directional Strategy Suite Rebuild

> ALIGN artifact. WHAT/WHY end-state — never HOW. Durable contract; the
> `decompose` step decomposes this into milestones/issues without asking the user
> anything. Every section must be verifiable. No TODO/??? placeholders.

## Objective

Author and verify a clean, book-faithful suite of Robert Carver's 17 daily directional strategies (S01–S09, S12, S14, S17–S19, S21–S23) under `strategies/candidates/carver_daily/` grounded in *Advanced Futures Trading Strategies* (2022) with distinct chapter-level formulas, and execute their Stage-1 signal screens on `MULTI_ASSET_1D_V1`.

## Why

The 25 candidate modules inherited from `quant-research` (`ts_s01`–`ts_s27`) were discovered through direct source audits to be compromised by four systemic fidelity distortions:
1. Hourly timeframe compression on daily futures strategies, generating ~300x annual turnover that erased positive gross edge.
2. An ad-hoc $3.0\text{x}$ ATR trailing stop loop copy-pasted into 24 of 25 modules (including passive Buy & Hold benchmark strategies).
3. The near-universal omission of Carver's cash volatility targeting ($\tau = 16\%\text{--}20\%$), sizing positions via arbitrary micro-weights ($<0.01$ leverage).
4. Heuristic indicator approximations (e.g. S21 requiring an impossible 40% channel pierce to reach full forecast; S22 substituting a 45-day price change for a 5-year relative value regression; S23 substituting price second-differences for EWMAC rate of change).

Without building a clean, faithful strategy suite grounded directly in the primary book text, future research and portfolio construction will continue to evaluate distorted local heuristics rather than Robert Carver's actual quantitative strategies.

## Acceptance criteria

1. **Clean Directory Isolation**: All 17 new strategy modules exist in `strategies/candidates/carver_daily/` (`s01_buy_hold_single.py` through `s23_acceleration.py`). The legacy candidate files in `strategies/candidates/ts_s*.py` and their references in completed runs (`RUN-HYP068-*`, `RUN-HYP091-*` through `RUN-HYP094-*`) remain bit-for-bit identical and untouched.
2. **Book Formula Verification**: Each module implements the primary mathematical formulas from its respective book chapter:
   - **S01 (p. 21)**: Single-contract benchmark without risk scaling, rebalancing, or stop loss.
   - **S02 (p. 67)**: Fixed cash volatility scaling ($\tau = 16\%$) using long-term historical volatility; no stop loss.
   - **S03 (p. 94)**: Variable cash volatility scaling ($\tau = 16\%$) using 60-day trailing daily volatility ($\sigma_{\% t}$); no stop loss.
   - **S04 (p. 119)**: Multi-asset risk-weighted portfolio with dynamic cash volatility scaling and portfolio IDM; no stop loss.
   - **S05 (p. 149)**: EWMAC(64, 256) daily trend filter, long-only constraint ($\max(\text{trend}, 0)$), dynamic cash vol sizing; no stop loss.
   - **S06 (p. 164)**: EWMAC(64, 256) daily trend filter, symmetric long/short ($\text{sign}(\text{trend})$), dynamic cash vol sizing; no stop loss.
   - **S07 (p. 178)**: EWMAC(64, 256) linear trend strength divided by daily price volatility and scalar $1.91$, capped at $[-20, 20]$, dynamic cash vol sizing; no stop loss.
   - **S08 (p. 191)**: EWMAC(16, 64) fast trend strength divided by daily price volatility and scalar $3.75$, capped at $[-20, 20]$, $10\%$ continuous weight buffering; no stop loss.
   - **S09 (p. 202)**: Full 6-speed EWMAC ensemble ((2,8), (4,16), (8,32), (16,64), (32,128), (64,256)) with Table 33 scalars, combined with $\text{FDM} = 1.41$, capped at $[-20, 20]$, dynamic cash vol sizing, $10\%$ buffering; no stop loss.
   - **S12 (p. 280)**: EWMAC trend forecasts adjusted by rolling daily price volatility regimes with Table 45 scalars; no stop loss.
   - **S14 (p. 308)**: Spot trend proxy using front-contract price relative to moving averages to eliminate back-adjustment roll drag; no stop loss.
   - **S17 (p. 343)**: Normalized trend constructing a cumulative normalized price index ($p / \sigma_t$) first, then evaluating EWMAC on the normalized series; no stop loss.
   - **S18 (p. 356)**: Asset-class composite trend constructing equal-weighted composite normalized price indices per asset class, evaluating EWMAC on the class index, and allocating to constituent instruments; no stop loss.
   - **S19 (p. 366)**: Cross-sectional relative momentum ranking instruments within asset classes by 12-month (252-day) return minus asset-class mean, holding long top half and short bottom half; no stop loss.
   - **S21 (pp. 389–402)**: Breakout 80 evaluating continuous price-within-range midpoint $40 \times (p_t - \text{midpoint}_t) / \text{range}_t$ where rolling extrema ($\text{roll\_max}_t = \max(p_{t-h+1}, \dots, p_t)$, $\text{roll\_min}_t = \min(p_{t-h+1}, \dots, p_t)$) are computed strictly from **daily closing prices** $p_t$ over $h=80$ days (not OHLC highs/lows), smoothed by 20-day EWMA (span $= h/4 = 20$), scaled by Table 91 scalar $0.73$, capped at $[-20, 20]$, with dynamic cash volatility sizing, $10\%$ buffering around held positions, and zero trailing stop loss (following Strategy 9/8 position management under NEXT_BAR execution).
   - **S22 (p. 404)**: Relative value measuring 5-year (1,260-day) outperformance within asset class, scaled by $7.27$, capped at $[-20, 20]$, cash vol sizing, $10\%$ buffering; no stop loss.
   - **S23 (p. 411)**: Acceleration evaluating rate of change of EWMAC trend forecasts ($\Delta\text{EWMAC}$) across speeds 8, 16, 32, 64 with Table 100 scalars, cash vol sizing, $10\%$ buffering; no stop loss.
3. **Automated Unit & Property Test Suite**: `tests/experiments/test_carver_daily_suite.py` passes 100% of cases verifying each module against its cited book equations, checking boundary outputs, buffering deadbands, and active session masking.
4. **Causal Prefix Invariance**: `verify_prefix_invariance` passes for all 17 feature pipelines on `MULTI_ASSET_1D_V1` with zero lookahead leakage.
5. **Warden Worktree Integrity**: `uv run warden check-worktree` exits 0 with all LOC budgets compliant (`src/warden/` $\le 2400$ LOC).
6. **Governed Hypotheses Registration & Screening**:
   - Hypotheses `HYP-2026-095` through `HYP-2026-111` are registered on `MULTI_ASSET_1D_V1` in `registry.db` with `trials_used = 0`.
   - `warden screen` executes successfully for all 17 specs on `MULTI_ASSET_1D_V1`, recording exactly 17 `standalone_screen` rows in `selection_attempts`.
   - Results are persisted to `.workspace/af30_campaign/carver_daily_screens.json` and summarized in `.workspace/af30_campaign/carver_daily_screens_report.md`.
7. **Zero Trial Burn**: No `warden backtest` is executed in this work unit; all 17 hypotheses retain their complete 3-trial budget for subsequent research.
8. **Atomicity in Error Paths**: If any module computation or data loading raises an error during verification or screening, the registry operation fails closed without partially inserting invalid hypothesis or selection attempt records.

## In scope

- Implementing 17 clean daily strategy modules in `strategies/candidates/carver_daily/`:
  - `s01_buy_hold_single.py`
  - `s02_buy_hold_risk_scaled.py`
  - `s03_buy_hold_variable_risk.py`
  - `s04_buy_hold_portfolio_scaling.py`
  - `s05_slow_trend_long_only.py`
  - `s06_slow_trend_long_short.py`
  - `s07_slow_trend_strength.py`
  - `s08_fast_trend_strength.py`
  - `s09_ewmac_multi.py`
  - `s12_adjusted_trend.py`
  - `s14_spot_trend.py`
  - `s17_normalised_trend.py`
  - `s18_trend_asset_classes.py`
  - `s19_xs_momentum.py`
  - `s21_breakout.py`
  - `s22_value.py`
  - `s23_acceleration.py`
- Unit testing all 17 modules in `tests/experiments/test_carver_daily_suite.py`.
- Formulating hypothesis YAMLs (`HYP-2026-095` … `HYP-2026-111`) and experiment screen YAMLs (`experiments/active/carver_daily/`).
- Running `warden verify` and `warden screen` across all 17 strategies on `MULTI_ASSET_1D_V1`.
- Compiling Stage-1 screen metrics into `.workspace/af30_campaign/carver_daily_screens.json` and a Markdown summary report.

## Out of scope

- The 6 carry-dependent strategies (S10, S11, S13, S15, S16, S20) — deferred to a separate project requiring futures curve / term-structure data modeling.
- The 2 fast mean-reversion strategies (S26, S27) — deferred to a dedicated microstructure work unit requiring L2 passive limit-order queue modeling.
- S24, S25, and S28–S30 (unported).
- Modifying legacy candidate files (`ts_s01`–`ts_s27`).
- Executing full `warden backtest` trials (trials are preserved for subsequent approved research).
- Reading, evaluating, or unsealing holdout data (`data/holdout/`).

## Constraints

- Primary Mathematical Authority: Robert Carver, *Advanced Futures Trading Strategies* (2022) extracted from `/home/quant/books/book/Advanced Futures Trading Strategies (Robert Carver).pdf`. `quant-research` is a secondary reference only.
- Target Contract: `MULTI_ASSET_1D_V1` ($312\text{ bars/year}$, 17 instruments, 2015–2025 exploratory).
- Evidence & Data Boundary: `MULTI_ASSET_1D_V1` is sourced from generic minute OHLCV stores (resampled CFD/cash/futures proxies), not an identified continuous-futures dataset with contract multipliers. Acceptance verifies the mathematical fidelity of Carver's formulas on this multi-asset panel, but does not claim exact replication of Carver's published futures portfolio performance or discrete contract accounting.
- Position Sizing Convention: Dimensionless normalized portfolio weights as a continuous adaptation of Carver cash volatility targeting.
- Python Environment: Python 3.13 via `uv run` in `/home/quant/dev/quant-lab`.

## Environment / prior art

- Primary Source Book: `/home/quant/books/book/Advanced Futures Trading Strategies (Robert Carver) (z-library.sk, 1lib.sk, z-lib.sk).pdf` (700 pages).
- Detailed Fidelity Audit: `.workspace/af30_campaign/af30_fidelity_audit.md` (identifying exact divergences across all 25 candidate files).
- Working Breakout 80 Prototype: `strategies/candidates/carver_breakout_daily.py` (demonstrated $+1.27$ Net Sharpe improvement on `MULTI_ASSET_1D_V1`).
- Multi-Asset Daily Contract: `contracts/multi_asset_1d_v1.yaml` (53,504 exploratory rows, SHA-256 `8f0ae063...`).
- Coverage Audit: `.workspace/af30_campaign/multi_asset_coverage_audit.md`.

## Existing surface (must not break)

- `contracts/multi_asset_1d_v1.yaml` and its companion dataset manifests must remain intact.
- Completed trial runs in `registry.db` (`RUN-HYP068-*`, `RUN-HYP091-*`, `RUN-HYP092-*`, `RUN-HYP093-*`, `RUN-HYP094-*`) must remain reproducible.
- Existing tests (194 tests in `tests/`) must pass 100% clean without regressions.
- Warden core LOC budget must remain $\le 2,400$ lines.

## Open questions

None blocking. The carry strategies (S10, S11, S13, S15, S16, S20) and fast mean reversion (S26, S27) are explicitly deferred and assigned to future dedicated work units.
