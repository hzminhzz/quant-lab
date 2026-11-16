---
hypothesis_id: HYP-SOC-BTA-NQMON-20261030
contract_id: AUTO_INDICES_1H_V2_H24_E24_F5_S10_V1
title: Monday Nasdaq Bollinger ATR breakout with delayed CFD fills (BTA source adaptation)
strategy_family: rules_ts
economic_mechanism: >
  Monday NQ price discovery after the weekend can exhibit persistent upside
  continuation when the completed-hour high exceeds a volatility-adjusted
  lower Bollinger reference, reflecting delayed hedging and directional
  index flows rather than pure overnight noise. This is a falsifiable
  hypothesis, not an observed or verified alpha.
counterparty_or_friction: >
  Counterparties are short-term mean reversion sellers, passive liquidity
  providers and market participants reducing post-weekend short exposure.
  Costs: 0.5bps taker + 1.0bps slippage per CFD execution side; next-hour
  execution instead of source's intrabar futures stop can introduce
  adverse gap and selection effects. No bid/ask quotes or true stop fills.
expected_holding_bars: 23
falsification_criteria: >
  One frozen configuration only. Native Warden rules_ts Stage1 must exceed
  net profit factor 1.15, block-bootstrap p below 0.05, time-in-market at
  least 5 percent and trade edge beyond its contract cost floor.
  Abort on feature-prefix divergence, missing NQ observations or
  execution-clock mismatch. Any Stage1 pass is retrospective only and
  NOT independent OOS: publisher investigated roughly 1800 Bollinger
  variations, selected Monday, and the same market snapshot already has
  30 related Warden selection attempts. Do not trade or open holdout.
source_provenance: >
  Primary original source https://breakouttradingacademy.com/steal-this-monday-nasdaq-strategy-super-simple/
  by Breakout Trading Academy, published 2026-07-16T18:44:44Z and independently
  captured 2026-10-30T07:25:00Z; captured article text SHA256
  4b60f2ce4f0350e36016bf63f375fcc52bc8ca19649f1a5c9e03fa45944e15b4.
  Related prior source https://breakouttradingacademy.com/i-tested-1800-bollinger-bands-strategies/
  documents source-side 1800 parameter trials, Monday selection and further
  cross-index checks. Original trades NQ futures with stop orders if reached
  in next 1h bar; this research adapts to NQ CFD bar-confirmed high[t]
  and NEXT_BAR open execution at t+1, assuming a long-side buy stop, whose
  direction is not explicitly listed in the source table. This is NOT
  a literal reproduction, nor a holdout/out-of-sample result.
llm_model_id: GPT-6
strategy_module: strategies/candidates/bta_monday_nq_bollinger_cfd_1h.py
---

# Frozen source rules and adapted research contract

| Component | Original publisher | Predeclared NQ CFD adaptation |
| --- | --- | --- |
| Market | E-mini NASDAQ futures | NQ index CFD proxy, not CME settlement |
| Direction | Source table does not explicitly specify buy/long | Long-only inferred from upward threshold; never claim verbatim |
| Chart | 60-minute futures bars | 1h NQ CFD bars, UTC start-labeled |
| Initiation | Lower Bollinger(10, 1 standard deviation) | Rolling population std(10) of completed closes through t-1 |
| Space | 0.8 x ATR(5) | Simple mean of five completed 1h true ranges through t-1 |
| Trigger | Stop if next bar hits price | Observe high[t]>=threshold[t-1] after bar t closes, order NEXT_BAR[t+1] |
| Day filter | Monday only | Monday America/Chicago (DST-aware), one entry per day |
| Entry interval | 00:00-23:00 Chicago | 00:00-21:00 Chicago to preserve next-bar fill and >=1h holding |
| Exit | 23:00 Chicago same day | Desired target=0 at 22:00 Chicago, next-bar flatten at 23:00 |
| Sizing | Futures point/$ fixed position unspecified | +1.0 NQ-only gross weight; other assets zero |
| Costs | Futures and actual stop fills unknown | Contract charges 0.5bps fee + 1.0bps slippage *per side* |
| Data | Author says 15y futures | Warden pinned 2015-2025 CFD exploratory NQ panel |
| Contract | N/A | New H24/E24 inherits INDICES_1H_V2 split and pinned SHA |
| Prior trials | 1800 source-side Bollinger variants | 30 related Warden selection attempts on original snapshot |

## Data and statistical protections

The original article was published **after** the entire exploratory Warden
train/validation interval (2015-01-01 through 2025-06-30). Validation is
post-publication retrospective hypothesis checking, not independent OOS
confirmation. The author selected the Monday model from a larger exploration;
neither Warden's new contract ID nor the published audit resets effective
multiplicity. Source's original intrabar stop execution is *not* implemented
in native Warden and an unfavorable outcome can be due to this delayed
adaptation rather than falsifying the futures result.

No parameter grid or related same-family revisions are authorized here.
If Stage1 fails, record negative evidence and move to another independent
source. If Stage1 passes, require a separate audited decision on contract-wide
DSR and source-side search history before any further backtest.

## Predeclared research sequence

1. Read-only Warden governance guard and pinned dataset/contract digest.
2. Deterministic strategy unit/prefix/date tests, no validation outcome inspection.
3. Register this exact hypothesis once via guarded Warden.
4. Verify native Warden causality; submit durable job if long.
5. Screen precisely one configuration, with guarded exclusive lock.
6. Archive all results; never call `warden qualify`, open holdout or send orders.
