# Quantitative Research Lessons (`LESSONS.md`)

## HYP-2026-001 (Rules-Based Intraday Volatility Breakout)
- **Finding:** Volatility breakouts on 1-hour crypto perpetuals generated modest gross Sharpe (~0.88 at $z=2.2$), but excessive annualized turnover (~135x-195x) and round-trip transaction costs (6 bps taker fee + slippage) drove net Sharpe negative (-0.40 to -1.41) and collapsed 2x-cost stress Sharpe to -1.18.
- **Falsification Lesson:** Low-breadth ($N=4$) time-series breakout rules without volume/orderbook filtering fail after realistic trading frictions. Do not loosen costs or lower friction floors to force a strategy into production.

## HYP-2026-002 (Cross-Sectional Funding Rate & Reversal Model)
- **Linear-First Baseline (`RUN-ML-001`):** Regularized cross-sectional Ridge regression ($\alpha=10$) on 4-hour forward returns achieved out-of-sample Mean IC of `0.0383` and Newey-West HAC $t$-statistic of `2.96` across 4 purged walk-forward folds. It survived 2x transaction costs (`2xCostSR = 5.98`) and cleared the contract-wide multiple testing hurdle (`DSR = 0.978` at $N=4, K_{\text{eff}} = 2.58$).
- **Nonlinear Ablation (`RUN-ML-002`):** Unconstrained LightGBM decision trees overfit the noisy feature space, causing OOS IC to drop to `0.0120` ($t = 1.20$), failing the Stage 1 gate. The falsification funnel rejected it early, saving compute and logging the trial to penalize future search.
- **Methodology Lesson:** Financial ML follows the Linear-First Rule: always establish a regularized linear model baseline first. When trees fail Stage 1, do not invent unprincipled feature permutations; accept the falsification.
