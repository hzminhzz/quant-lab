# HYP-SOC-BTA-NQMON-20261030 — native Stage 1 outcome

**Terminal verdict: STAGE_1_REJECT (one attempt, no Stage 3).**

The user approved continued autonomous strategy research and bounded creation of new research-only data contracts. This 2026-10-30 experiment is a deliberately declared **bar-confirmed NQ CFD adaptation**, NOT a faithful replication of the original E-mini NQ intra-bar stop-entry strategy.

## Original source and frozen adaptation

- [BTA original](https://breakouttradingacademy.com/steal-this-monday-nasdaq-strategy-super-simple/), published 2026-07-16, captured 2026-10-30 UTC with text SHA256 `4b60f2ce4f0350e36016bf63f375fcc52bc8ca19649f1a5c9e03fa45944e15b4`.
- Author's rules: 60m E-mini NQ; stop at lower Bollinger(10,1SD)+0.8×ATR(5), Mondays 00:00–23:00 Chicago, same-day 23:00 exit. The source table does **not** explicitly say long/short; long entry was assumed for this research adaptation.
- Author also tested ~1,800 source-side Bollinger variants and subsequently selected Monday; other robustness claims do not erase selection multiplicity.
- Research adaptation: `NQ` CFD only; complete previous bars used for indicators; current-bar high touch triggers desired long after bar close; **Warden NEXT_BAR** 1-hour-later market fill. One entry/Monday. Chicago DST-aware exit intended 23:00 using desired target 0 at 22:00. No true stop-order fills, bid-ask quotes, leverage assumptions or real orders.
- Registered hypothesis `HYP-SOC-BTA-NQMON-20261030`, strategy `strategies/candidates/bta_monday_nq_bollinger_cfd_1h.py`, experiment `experiments/active/bta_monday_nq_20261030.yaml`.
- Delegated research-only derived contract `AUTO_INDICES_1H_V2_H24_E24_F5_S10_V1`, same parent verified SHA256 `03a9d948793542cba79f485d7a1fa39a3435252069bf9d131596303ec315ca55`, label=24 hourly bars, embargo=24, NEXT_BAR=1, fee=0.5bp+slippage=1.0bp **per side**, exact original exploratory/validation/holdout split. Unsigned but principal-delegated, not independently HMAC signed. There were 30 preexisting selection attempts related to the same underlying dataset at proposal time.

## Verification and screen

- Five deterministic tests passed: causal prior rollups, DST Chicago exit and weekly schedule, no-trigger control, prefix invariance, wrong contract/universe failure.
- Native Warden verify durable job: `warden-verify-c1131e6f588571f4d2`, status PASS, prefix_invariance=true, cached=true, verified_market, 55,766 rows.
- Native Warden screen durable job: `warden-screen-1a63932004c10bcaf6` (terminal process exit code 1, **scientific rejection**, not a transport error).
- Immutable Stage1 attempt: `ATT-f6e9f68b4805e076-001`, outcome REJECT.

| Metric | Result | Native rule |
| --- | ---: | ---: |
| Net profit factor | 1.1384176199013911 | >=1.15 (FAIL) |
| Bootstrap p-value | 0.01 | <0.05 |
| Time in market | 0.17106516120255746 | >=0.05 |
| Conditional edge | 0.00011243723189027725 | Positive |
| Trade edge | 0.0021668937793413584 | Positive |
| Average holding bars | 19.272030651340994 | Informational |

**Disposition:** Reject original frozen CFD adaptation. Do NOT tune the 0.8 ATR multiplier or reverse after seeing this near-miss. Do NOT run Warden Stage 3 for this failed Stage 1, and do not claim the author's intrabar futures strategy was falsified. The author published only after Warden validation ended in June 2025, so results are retrospective, not independent OOS. The 1,800 original exploratory variants and 30 prior related Warden attempts remain a multiple-testing issue.

## Guardrails

No protected holdout opened; zero new full backtest trials; no live trading orders. Quant-lab's other unrelated dirty modifications and divergent main branch were not reset, stashed, or pushed. The only new protected files were appended via the delegated immutable contract compiler and verified against the Warden manifest.
