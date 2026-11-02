# Train-only factor selection — frozen before Warden validation (2026-11-02)

## Protocol

- Dataset: observed Binance USD-M Top-100 hourly, exact exploratory SHA-256 `a7e13ed71f8ac09a11f98bdb6d711d93f91a4021105141a2070fd79696af99bc`.
- Data actually loaded: timestamps **2023-01-01 through 2024-06-30 23:00 UTC only**; `pl.scan_parquet().filter(timestamp < val_start).collect()`. No 2024-07-01 to 2025-07-01 Stage 1 validation returns or protected holdout accessed during this discovery step.
- All features computed using past OHLCV/funding known at each completed hourly decision bar. Label: future open-to-open log return `Open[t+5]/Open[t+1]`, matching frozen 4h label horizon and 1h execution delay, requiring a continuous observed path.
- Each of ten **predeclared** candidate signals is scored as per-hour cross-sectional Spearman IC among symbols whose as-of 24h average dollar volume is at least $25,000/hour; at least 30 observable symbols per hour.
- Training history split into two contiguous halves of 6,564 hours each. A candidate is eligible if **both halves** mean IC >0.015 and HAC t >2.0. Highest minimum-half IC selected. No automatic sign flip after viewing any results.
- All ten training-only factor probes below are disclosed in selection-debt context. Source lineage overlap is explicit and no novelty beyond earlier similar hypotheses claimed.

## Training-only results

| Prespecified factor | First half IC | Second half IC | First-half HAC t | Second-half HAC t |
|---|---:|---:|---:|---:|
| 1h residual reversal | 0.03499 | 0.02324 | 18.22 | 11.67 |
| 6h residual reversal | 0.04586 | 0.03211 | 14.87 | 10.00 |
| 24h residual momentum | -0.03745 | -0.03309 | -11.03 | -9.56 |
| Close-location continuation | -0.02362 | -0.01370 | -14.70 | -8.00 |
| High-volume wick absorption | 0.01804 | 0.01082 | 11.47 | 6.87 |
| High-volume 1h reversal | 0.02523 | 0.01983 | 14.60 | 11.47 |
| High-volume 6h continuation | -0.03821 | -0.02729 | -16.16 | -11.01 |
| **Inverse 24h realized volatility** | **0.04950** | **0.04737** | **10.33** | **10.85** |
| Funding contrarian | -0.00840 | 0.00096 | -2.99 | 0.40 |
| Volatility-normalized 24h trend | -0.03859 | -0.03562 | -11.14 | -10.23 |

**Frozen selection:** `inverse_volatility_quality`, represented by `HYP-ARXIV-XS-LOWVOL-20261102`, a clearly identified **descendant of prior HYP-2026-031**, whose older screen was REJECT. This new four-hour executable, fold-local single-feature Ridge specification is distinct but not an independent discovery and inherits historical family search debt.

The existing **HYP-2026-128** residual reversal lineage had a recorded Stage 1 PASS but is quarantined; it cannot be revived through the superficially strong 1h/6h reversal training signals. Training-only significance is not OOS validation and does not imply trading profit. The research must still fail closed under Warden and include full historical search adjustment and realistic taker costs.

## Reproduction

`uv run python -m scripts.train_only_factor_discovery`, which creates an ignored, write-once JSON audit at `.workspace/stage1_optimization/train_only_factor_audit.json`. Full factor definitions are in the script; no validation series is read by it.
