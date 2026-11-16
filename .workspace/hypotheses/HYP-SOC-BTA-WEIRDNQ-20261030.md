---
hypothesis_id: HYP-SOC-BTA-WEIRDNQ-20261030
contract_id: AUTO_INDICES_30M_V1_H24_E24_F5_S10_V1
title: BTA genetic breakout of NQ 30-minute CFD after prior HLC3/open divergence
strategy_family: rules_ts
economic_mechanism: >
  A high breakout after an unusual divergence between the prior completed
  bar typical price and the preceding 35 opens can reflect short-lived
  persistent index order flow; a causal bar-confirmed NQ CFD entry might
  carry a positive directional premium through the remainder of the
  US trading day. The genetic algorithm formula itself has no compelling
  economic explanation. This is deliberately an unvalidated conjecture.
counterparty_or_friction: >
  Short-term liquidity suppliers and closing-out shorts are the assumed
  counterparties. Source originally uses intrabar stop orders and a
  $1,000 stop per E-mini NQ contract, both unavailable in Warden's
  weight-based next-bar CFD simulator; the adaptation omits the futures
  stop explicitly, incurs adverse delayed-market entry, and is not a
  source-faithful performance replication. Fee 0.5bps + slippage
  1.0bps each side, future quote impact unavailable.
expected_holding_bars: 22
falsification_criteria: >
  Native Warden rules_ts Stage1 PASS only if profit factor >= 1.15,
  200-block bootstrap p < 0.05, time-in-market >= 0.05 and trade
  edge > 1.5x one-way fee+slippage floor. If technical prefix
  invariance, missing bars or contract digest invalid, block before
  screening. If Stage1 REJECT, freeze/mark negative; do not retune
  35-bar lookback or trading session, no Stage3. If Stage1 PASS,
  declare exploratory only because original strategy formula was
  discovered via thousands of genetic-algorithm candidates, and
  source-text 2026 capture does not prove 2024 publication-vintage
  rules unchanged. No protected holdout or live orders.
source_provenance: >
  Primary original Breakout Trading Academy
  https://breakouttradingacademy.com/weird-nasdaq-algo-strategy/
  original published 2024-05-22T13:19:31Z, read 2026-10-30
  near 08:19Z, primary text SHA256
  6e919373c1d70ed055ff543d57d77beeb1629b7283012159ff268e01128d4326.
  Author original long NQ futures 30-minute, entry 07:00-18:00
  Eastern at prior high + abs(typical price - highest open 35),
  close same session, $1k stop per E-mini contract, GA optimized
  across thousands candidates. CFD adaptation uses previous completed
  bar HLC3 in the ambiguous typical-price term; observes confirmed
  30m high cross, fills at Warden NEXT_BAR 30m open, 17:30 Eastern
  desired flatten for 18:00 nextbar execution, no original futures
  stop. Different execution strategy.
llm_model_id: GPT-6
strategy_module: strategies/candidates/bta_weird_nq_ga_cfd_30m.py
---

# Source-bound fixed research protocol

| Component | Original source | Frozen CFD adaptation |
| --- | --- | --- |
| Market | E-mini NQ futures | Verified-market NQ CFD only |
| Timeframe | 30 minutes | NQ 30-minute UTC start-labeled bars |
| Direction | Long only | Long only, target gross +1 |
| Entry window | 07:00–18:00 US Eastern | 07:00–17:00 ET for delayed fill |
| Entry level | Last high + abs(typical price – highest open previous 35) | Prior high + abs(prior HLC3 – prior 35-bar high open) |
| Order fill | Intrabar stop entry at level | Current-bar high cross, Warden market NEXT_BAR |
| Exit | End of 18:00 ET session | Desired flat 17:30 ET; executes 18:00 ET |
| Stop | $1,000 futures per E-mini NQ | OMITTED in CFD signal test; original risk isn't reproduced |
| Fees | Author doesn't show brokerage/slippage data | 0.5bps taker +1bps slippage per side |
| Label | Source same-day only | New H24 half-hour label and E24 embargo |
| Search family | GA across thousands of original trials | Native Warden prior 0 same-panel attempts; original GA multiplicity unresolved |

The source is dated May 2024 but captured in October 2026 and the historical
publisher formula vintage is not independently attested. The Warden 2023–25
validation includes 2023–May 2024 predating the publisher article and later
observations that may have been influenced by its publicity. Such evaluation
is retrospective and not independently untouched OOS. A Stage1 PASS is not
sufficient for Stage3/holdout without family-wide DSR including source GA
model search and independently validated fill/cost assumptions.

The existing approved parent `INDICES_30M_V1` has label16/embargo4.
Bounded principal-delegated factory created new governed
`AUTO_INDICES_30M_V1_H24_E24_F5_S10_V1`, retaining data SHA256
`25f8c39dc245f0fe6070ce3fe3cd4063fd3093f7c951d9ed017f36367f23ff8f`,
identical data/split/holdout references, H24/E24,
NEXT_BAR=1, fee0.5bps and slippage1bps each side.
Protected evaluator code was never modified.

One fixed candidate, no grid. First nine unit/DST/causality tests, Warden
integrity, register hypothesis, guarded durable verify, then ONE guarded
durable Stage1 screen. No automatic parameter or timing rewrites.
