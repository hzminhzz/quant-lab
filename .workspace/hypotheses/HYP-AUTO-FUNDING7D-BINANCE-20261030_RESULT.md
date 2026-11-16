# Autonomous funding-level carry research — 2026-10-30

**Verdict:** Stage 1 REJECT (native Warden). Positive modeled validation returns are exploratory and cannot be promoted.

- Source: https://delphicalpha.substack.com/p/strategy-crypto-funding-carry-makes
- Source published: 2026-10-26; captured 2026-10-29; source text SHA256 `c3c72c88e4cdbe99963132df779225b0f30d40ece3034a09cfa3b0178ea400af`.
- Contract: `AUTO_CRYPTO_PERPS_TOP100_1H_V1_H24_E24_F45_S20_V1`.
- Parent contract: `CRYPTO_PERPS_TOP100_1H_V1`. Original snapshot SHA256 retained: `a7e13ed71f8ac09a11f98bdb6d711d93f91a4021105141a2070fd79696af99bc`.
- Contract changes: label horizon 4h -> 24h; embargo 2h -> 24h; taker floor 4 -> 4.5 bps per side; slippage floor 2 unchanged; identical train, validation, holdout dates and data bytes.
- Contract activation mode: explicit user-delegated research-only approval, **unsigned independent signature**. Existing Warden manifest entries preserved and a two-file additive pin performed. No holdout permission was granted or used.
- Prior related selection attempts when proposed: 5. A new contract ID is **not** independent selection evidence.
- Hypothesis: `HYP-AUTO-FUNDING7D-BINANCE-20261030`.
- Experiment: `experiments/active/auto_funding_level_7d_1d_20261030.yaml`.
- Strategy: `strategies/candidates/auto_funding_level_carry_7d_1d.py`.
- Selection attempt: `ATT-abe185af756c1b98-001`, outcome REJECT, append-only.
- Warden full trials: 0. Protected holdout reads: 0.

## Strategy and known source deviations

Author's exact original universe: 32 USDT perpetuals simultaneously available at Binance and Hyperliquid, at least 60 days of history and $5M median daily turnover. Inferred cross-venue historical membership is **not available in the current lake**, so this adaptation uses 88 Binance USDT perpetuals available on the pinned snapshot's 2023-01-01 inception date. This difference is fundamental: results do not reproduce the source's published 1.13 OOS Sharpe.

Frozen source mechanism: long cheapest third of seven-day-average funding, short most expensive third, inverse previous 30 daily-return standard deviation weights, 50/50 gross long/short; daily UTC rebalance. The current implementation uses previously closed hourly funding observations, 60 previous daily quote-volume observations to screen liquidity, source $5M filter, 1h Warden NEXT_BAR lag and actual settlement funding in native simulation.

## Verified research results

| Check | Result |
|---|---:|
| Native Warden causal verify | PASS, 1,926,144 market rows |
| Prefix-invariance smoke | PASS, 2,856-hour prefix |
| Warden Stage1 profit factor | **1.13036 — fails minimum 1.15** |
| Warden gross active block bootstrap (24h) | p=0.0 at sampled resolution; NOT a guarantee |
| Gross conditional edge | 0.457 basis points per active hour |
| Time in market | 100% |
| Warden holding proxy | 188.2 hours (turnover-based diagnostic, not contractual holding) |
| Net compound validation return | +48.60%, July 2024 to June 2025 |
| Net max drawdown | -4.62% |
| Hourly annualized net Sharpe (overlap caution) | 4.10 |
| Read-only net moving-block bootstrap | No nonpositive mean among 1,000 draws at each 24h, 168h, 720h block length |
| Full Warden backtest or qualification | NOT RUN (Stage1 rejected) |

The high hourly Sharpe and modest modeled drawdown are **unverified economic claims** given the source-after-validation timing, substantial cross-venue universe adaptation, hourly forward-filled settlement-rate data, and possible cross-contract selection bias. They do not override the PF gate. Funding rate timestamp availability and adverse fill/market impact are unresolved.

## Scientific decision

Keep as **PROMISING EXPLORATORY NEAR-MISS / NATIVE STAGE1_REJECT**, not an approved Warden candidate. Do not loosen PF, manipulate costs or vary family parameters to force a pass. Future truly new ideas should be predeclared with a matching contract, and any pass must inherit parent dataset trial multiplicity. Do not unseal production holdout based on this report.
