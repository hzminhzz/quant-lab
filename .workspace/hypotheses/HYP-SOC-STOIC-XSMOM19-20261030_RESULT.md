# Stoic XSMOM 10d/21d — registered source-based adaptation

**Disposition:** Warden Stage 1 **REJECT** — profit factor threshold not met.  
**Research date:** 2026-10-30 (Asia/Bangkok); Warden validation: 2024-07-01 through 2025-06-30.  
**Primary source:** https://stoicresearch.substack.com/p/14-cross-sectional-momentum  
**Hypothesis:** `HYP-SOC-STOIC-XSMOM19-20261030`  
**Experiment:** `experiments/active/social_stoic_xsmom19_20261030.yaml`  
**Strategy:** `strategies/candidates/social_stoic_xsmom_10d21d.py`  
**Warden selection attempt:** `ATT-13ebd05b1001965b-001` (append-only, REJECT).  
**No protected holdout reads. No Warden full backtest trials. No parameter revisions.**

## Predeclared original-source rule and adaptation

The source reports a long/short, 20-coin cross-sectional momentum strategy selected on a 90-configuration training grid. Its best-train candidate used a 10-day trailing log return score, top 25% long and bottom 25% short, a 21-day rebalance, 50% gross long exposure and 50% gross short exposure. Original source: 20 coins including PAXG, with 0.10% per unit turnover costs.

The pinned `CRYPTO_PERPS_TOP100_1H_V1` exploratory dataset contains 19/20 of these author-named coins at its first timestamp (2023-01-01); **PAXG is absent**. The adaptation fixes those 19 symbols ex ante, retains five longs and five shorts, and uses an hourly implementation of prior 240 completed hours of log-return score sampled once at 00:00 UTC, 21-day rebalances, and Warden's next-bar execution. Missing symbols are not imputed. This is a **19-coin source-based adaptation**, **not an exact reproduction** of the published 20-coin book.

Source published June 2026, **after** this 2024–25 validation window. Warden's validation is retrospective discovery evidence and cannot be treated as unseen OOS. The author's reported 2.26 test Sharpe was selected from 90 train configurations and is not a replicated result here.

## Verified native execution

- Worktree protected-contract integrity verified.
- Real 19-instrument hourly panel generated 21,888 position rows. All finite; long gross 0.5 and short gross 0.5; exactly 5 longs and 5 shorts when active.
- Position prefix-invariance (4,001-hour cut): exact bitwise match to the corresponding full-history prefix.
- Native `warden verify`: **PASS**, 415,872 panel rows, verified-market provenance, causal prefix-invariance, cached features.
- Native `warden screen`: **REJECT**; one selection attempt persisted.

| Warden Stage 1 metric | Observed | Required |
|---|---:|---:|
| Profit factor (hourly net returns) | 1.0718465 | >=1.15 |
| Bootstrap p-value (24-hour blocks, gross active returns) | 0.005 | <0.05 |
| Gross conditional edge | 0.5686 bp/active hour | positive / trade-edge gate |
| Time in market | 100% | >=5% |
| Warden holding proxy | 730 hourly bars | diagnostic |

Native bootstrap uses **24-hour blocks**, shorter than the 504-hour nominal rotation. Do not mistake its p=0.005 for independent 21-day evidence.

## Read-only post-screen robustness checks (NOT Warden gate revisions)

Based on the already-recorded 8,760 hourly Stage 1 net returns and identical frozen positions, no fresh strategy rule or trial:

| Modeled cost assumption | Compound net return | Annualized hourly Sharpe | PF | Maximum drawdown |
|---|---:|---:|---:|---:|
| Warden floor, 6 bps/side | +60.75% | 2.235 | 1.072 | -11.55% |
| Source turnover cost, 10 bps/side | +59.22% | 2.192 | 1.070 | -11.59% |
| Warden 2x cost, 12 bps/side | +58.45% | 2.170 | 1.070 | -11.62% |

Turnover: 24 gross notional units across the validation year and 18 nonzero rebalance transitions. The source-cost and double-cost lines are **diagnostic arithmetic**, not approved Warden Stage 2 backtests. They do not repair the failed profit-factor gate.

Robustness to 21-day dependence:
- Circular moving-block bootstrap of recorded **net** hourly returns, 2,000 fixed-seed replicates: p(mean<=0)≈0.001 using 504-hour blocks; ≈0.002 using 730-hour blocks.
- **Only 17** non-overlapping 21-day blocks; 12/17 positive, one-sided sign-test p≈0.0717. The selection history, low independent sample count, and retrospectively selected original strategy substantially weaken inference. The block bootstrap's positive results are exploratory, not conclusive.

## Non-negotiable blockers before qualification

1. **Native Stage 1 rejects PF=1.072.** Do not promote or relabel as PASS, and do not lower its 1.15 threshold.
2. **Source after validation:** no independent validation window in the original dataset. The protected 2025-26 file was not opened, and cannot be claimed unseen merely because this post was published in 2026.
3. **Missing original PAXG:** full-fidelity source replication requires a properly documented and point-in-time eligible symbol history.
4. **Horizon misalignment:** contract `label_horizon_bars=4` versus 504-hour holding. A separately approved new contract / research governance decision is required before any higher-stage qualification using overlapping labels or purging rules.
5. **Execution model:** source claims 10 bps per unit turnover, Warden floor is 6 bps per side; no measured order-book spread, impact, or Hyperliquid cross-venue basis. Future true execution evaluation requires appropriate data/contract review.
6. **Post-selection:** 90 source grid configurations and relevant internal prior attempts are not independent discoveries. Any subsequent promotion must account for the effective selection count.

**Decision:** Keep as *PROMISING EXPLORATORY LEAD / STAGE1_REJECT*, not as a candidate that passed Warden. Freeze these results and do not retune this exact source after seeing the validation outcome. Continue extracting **distinct predeclared strategies** under the existing research budget.
