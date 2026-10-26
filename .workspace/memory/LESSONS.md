# Quantitative Research Lessons (`LESSONS.md`)

## HYP-2026-001 (Rules-Based Intraday Volatility Breakout)
- **Finding:** Volatility breakouts on 1-hour crypto perpetuals generated modest gross Sharpe (~0.88 at $z=2.2$), but excessive annualized turnover (~135x-195x) and round-trip transaction costs (6 bps taker fee + slippage) drove net Sharpe negative (-0.40 to -1.41) and collapsed 2x-cost stress Sharpe to -1.18.
- **Falsification Lesson:** Low-breadth ($N=4$) time-series breakout rules without volume/orderbook filtering fail after realistic trading frictions. Do not loosen costs or lower friction floors to force a strategy into production.

## HYP-2026-002 (Cross-Sectional Funding Rate & Reversal Model)
- **Linear-First Baseline (`RUN-ML-001`):** Regularized cross-sectional Ridge regression ($\alpha=10$) on 4-hour forward returns achieved out-of-sample Mean IC of `0.0383` and Newey-West HAC $t$-statistic of `2.96` across 4 purged walk-forward folds. It survived 2x transaction costs (`2xCostSR = 5.98`) and cleared the contract-wide multiple testing hurdle (`DSR = 0.978` at $N=4, K_{\text{eff}} = 2.58$).
- **Nonlinear Ablation (`RUN-ML-002`):** Unconstrained LightGBM decision trees overfit the noisy feature space, causing OOS IC to drop to `0.0120` ($t = 1.20$), failing the Stage 1 gate. The falsification funnel rejected it early, saving compute and logging the trial to penalize future search.
- **Methodology Lesson:** Financial ML follows the Linear-First Rule: always establish a regularized linear model baseline first. When trees fail Stage 1, do not invent unprincipled feature permutations; accept the falsification.

## HYP-2026-003 (RSI Pullback + Rising EMA + 10-Bar Rolling VWAP Discount on Hourly NQ)
- **Replication Scope & Domain Translation:** QuantifiedStrategies Substack post 218001957 reported a trade-level Profit Factor of 3.54 (79.5% win rate, 215 trades) on daily QQQ (1999–2026, 0 bps fees). This experiment evaluated whether the economic mechanism survives translation into an hourly futures context (`INDICES_1H_V1`, NQ, 2024–2025) with causal 1-bar execution delay and exchange frictions (1.0 bps one-way).
- **Accounting Note on Profit Factor:** Warden calculates Profit Factor as the ratio of positive to negative bar returns ($\sum r_t^+ / \sum |r_t^-|$), which differs from discrete trade-level PnL ratios.
- **Out-of-Sample Results (Hourly NQ):**
  - **Trial 1 (Baseline Without VWAP, `RUN-HYP003-001`):** Net Sharpe `-0.25`, Bar PF `0.9774`, Max Drawdown `7.97%`, turnover `585x/yr`. 2x cost Net Sharpe dropped to `-0.81` (Bar PF `0.9297`, MaxDD `8.92%`).
  - **Trial 2 (+ 10-Bar Rolling VWAP Filter, `RUN-HYP003-002`):** Net Sharpe `-0.12`, Bar PF `0.9736` at 1x cost, Max Drawdown `5.45%`, turnover `83.6x/yr`. Under 2x cost stress, Bar PF improved to `0.9445` (vs. 0.9297 in baseline) due to an 85.7% turnover reduction that limited fee drag.
  - **Trial 3 (Parameter Neighborhood Perturbation, `RUN-HYP003-003`):** Net Sharpe remained negative (`-0.12`), stability ratio negative (`-41.2`), DSR probability `0.349`.
- **Scientific Takeaway on the 10-Bar Rolling VWAP Filter:**
  - The rolling VWAP discount filter **genuinely reduces maximum drawdown** in this hourly setup (-31.6% at 1x cost from 7.97% to 5.45%, -37.4% at 2x cost from 8.92% to 5.58%) and cuts turnover by **85.7%** (from 585x to 83.6x/year).
  - For Profit Factor, effects diverge by cost regime: at baseline 1x cost, the filter slightly degraded bar-level Profit Factor (0.9774 -> 0.9736); under 2x cost stress, it improved bar-level Profit Factor (0.9297 -> 0.9445) solely via turnover/fee reduction. Neither cleared the Stage 1 threshold ($PF \ge 1.15$).
- **Governance:** Status: **REJECTED (3-Trial Budget Exhausted)** for contract `INDICES_1H_V1`. Does not claim direct rejection of the source's uncosted daily ETF trade claim.

## HYP-2026-003B (10-Year Dukascopy Hourly NQ, 2.5-Year Out-of-Sample Validation)
- **Dataset & Scope:** Evaluated on the 10-year macro history for global index futures (`contracts/indices_1h_v2.yaml`, 2015–2025, 302,036 rows), with an out-of-sample validation period spanning 2.5 years (`2023-01-01` to `2025-07-01`, 14,882 hourly bars) on NQ.
- **Ablation Trials & Slot Allocation (`registry.db`):**
  - `RUN-HYP003B-001` (Slot 1): Consumed by cross-contract return-stream alignment failure before `INDICES_1H_V2` was established.
  - `RUN-HYP003B-002` (Slot 2, Baseline Without VWAP): Gross Sharpe `+0.72`, 1x Net Sharpe `-0.09`, Bar PF `0.9918`, Max Drawdown `11.43%`, turnover `656.5x/yr`. 2x cost Net Sharpe dropped to `-0.90` (Bar PF `0.9234`, MaxDD `21.53%`).
  - `RUN-HYP003B-003` (Slot 3, + 10-Bar Rolling VWAP & Parameter Grid): Gross Sharpe dropped to `-0.82`, 1x Net Sharpe `-0.94`, Bar PF `0.7680`, Max Drawdown `13.11%`, turnover `48.0x/yr`. 2x cost Net Sharpe was `-1.06` (Bar PF `0.7438`, MaxDD `13.95%`). Parameter neighborhood grid was bundled into this run.
- **Neighborhood Stability Sign Caveat:** The reported parameter stability ratio of `1.1937` (`min_neighbor_SR / max_neighbor_SR`) is an artifact of dividing negative Sharpe numbers ($-1.15 / -0.96$). It indicates that all neighboring parameter configurations uniformly lose money, not a passing robustness metric.
- **Scientific Takeaway on the 10-Bar Rolling VWAP Filter (2023–2025 Market):**
  - **Profit Factor:** Genuinely **fails to improve and severely degrades** out-of-sample Profit Factor (0.9918 down to 0.7680 at 1x costs; 0.9234 down to 0.7438 at 2x costs). In strong hourly bull markets (2023–2025 NQ rally), requiring price to drop 80 bps below the 10-bar rolling VWAP does not identify temporary liquidity exhaustions; it filters out healthy shallow dip-buys and selects for sharp momentum cascades with negative conditional forward returns.
  - **Max Drawdown:** Fails to improve at baseline 1x cost (drawdown widened from 11.43% to 13.11%). Apparent drawdown improvement at 2x cost (13.95% vs. 21.53%) is strictly mechanical due to avoiding 656x/year turnover fee drag.
- **Status:** **REJECTED (3-Trial Budget Exhausted on INDICES_1H_V2)**.

## HYP-2026-004 (Cross-Sectional Amihud Illiquidity Premium on Crypto Perps)
- **Source & Mechanism:** Adapted from `dev/quant/quant-research/research_inbox/crypto_xs_amihud_illiquidity_premium_basket_v1` (Amihud 2002, Acharya & Pedersen 2005). Posited that altcoins experiencing elevated trailing Amihud price impact trade at liquidity discounts that yield positive forward return premia.
- **Linear-First Baseline (`RUN-ML-003`):** Ridge regression cross-sectional factor model on 19 major crypto perpetuals (`CRYPTO_PERPS_1H_V1`) across 4 purged walk-forward folds.
- **Empirical Falsification at Stage 1:**
  - Out-of-sample Rank IC: `Mean IC = -0.0023`, Newey-West HAC `t = -0.18`.
  - Failed the Stage 1 statistical gate ($\text{Mean IC} \ge 0.015, t_{\text{HAC}} \ge 2.0$).
  - Fast-failed as `REJECTED_EARLY` at Stage 1 before wasteful portfolio simulation.
- **Scientific Takeaway:** The equity illiquidity premium does not transfer to 24/7 crypto perpetuals. In crypto derivatives, lower liquidity does not reflect an unpriced structural risk premium that normalizes; it reflects ongoing adverse selection, wider spreads, and momentum decay. Status: **REJECTED**.

## HYP-2026-005 (Cross-Sectional Betting Against Beta / Low-Beta Anomaly on Crypto Perps)
- **Source & Mechanism:** Adapted from `dev/quant/quant-research/research_inbox/crypto_xs_betting_against_beta_basket_v1` (Frazzini & Pedersen 2014, Asness et al. 2020). Posited that retail borrowing constraints and lottery demand bid up high-beta tokens, creating a premium for longing low-beta and shorting high-beta tokens.
- **Walk-Forward In-Sample Overfitting Discovery:**
  - Across 4 purged walk-forward folds, Ridge regression trained on 2020–2024 learned strictly negative coefficients on low-volatility features because the historical training regime was heavily dominated by high-beta upside momentum runs.
  - When applied to the 2025 validation period, the model's momentum-continuation predictions inverted, producing a negative out-of-sample Rank IC of `-0.0556` ($t_{\text{HAC}} = -3.66$).
- **Scientific Takeaway:** The Betting Against Beta factor suffers severe regime instability in crypto perpetuals. A linear model trained on multi-year bull runs fits historical high-beta momentum, and collapses out-of-sample when the macro regime shifts. Fast-failed at Stage 1 as `REJECTED_EARLY`. Status: **REJECTED**.

## Code Review of HYP-2026-004 / HYP-2026-005 (2026-10-26)

- **Verdicts stand, modules were not promoted.** Both runs died at Stage 1 (`RUN-ML-003` IC `-0.0023` / HAC `t=-0.18`; `RUN-ML-004` IC `-0.0556` / HAC `t=-3.66`), with zero-variance return vectors (1,400 validation bars, all zeros) — they contribute nothing to $K_{\text{eff}}$.
- **Defect — target/PnL basis mismatch (both modules):** the Ridge label is close-to-close `close[t+h-1]/close[t-1]-1` while executed PnL is open-to-open with a 1-bar delay. IC and position scores are therefore measured against a target one bar earlier than the tradeable horizon. Recorded verdicts stay valid as *rejections*; the modules must not be reused for a promotion attempt without fixing the label basis. Marked with `shortcut:` in both files.
- **Defect — HYP-2026-005 was not a beta test:** `rv_168` (rolling std of returns) was labelled `feat_low_beta`, but no market-return regression exists anywhere in the module. The tested factor was **low volatility + 72-bar reversal**, not Betting-Against-Beta. Feature renamed `feat_low_vol_slow`; the hypothesis title is retained for the immutable ledger, but its conclusion must be read as "low-vol/reversal fails in crypto perps", not "BAB fails".
- **Duplication risk:** both modules re-implement fold fitting inline and leave `fit_predict_fold` + module-level `pipeline`/`model` singletons unused. If either hypothesis is revived, collapse to the protocol path.
