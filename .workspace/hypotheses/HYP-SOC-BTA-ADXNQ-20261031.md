---
hypothesis_id: HYP-SOC-BTA-ADXNQ-20261031
contract_id: AUTO_INDICES_30M_V1_H24_E24_F5_S10_V1
title: NQ 30m ADX trend-confirmed same-day breakout after bar confirmation
strategy_family: rules_ts
economic_mechanism: >
  Concentrated directional futures/CFD order flow after a trend-strength
  expansion can persist through the Chicago daytime session; a high-close
  breakout with ADX above 17.5 may capture slow institutional rebalancing.
  This is an original researcher conjecture, not evidence of profitability.
counterparty_or_friction: >
  Index liquidity providers temporarily warehouse aggressive order flow,
  while lagged followers pay spread/slippage. NQ futures intrabar buy stop
  and USD1000 per-contract stop are NOT available in the NQ CFD next-bar
  portfolio-weight execution model; this tests a different bar-confirmed
  no-stop variant. Conservative cost floor is 0.5bps fee +1bps slippage
  on every change of weight.
expected_holding_bars: 14
falsification_criteria: >
  Technical verify and prefix invariance must PASS before native Stage1.
  Native rules_ts requires PF >=1.15, bootstrap p<0.05, time-in-market
  >=0.05 and trade edge >1.5x one-way costs; any failure is REJECT.
  One registered selection attempt only; no lookback or ADX adjustment
  after screen. Any Stage1 pass is exploratory, not original-source
  replication or permission to access holdout or trade live.
source_provenance: >
  Original BTA 2024-02-14 source
  https://breakouttradingacademy.com/nasdaq-hitter-algo-breakout-strategy-very-profitable/
  refreshed first-party original article 2026-10-30/31; archive record
  d1e1880870d996e801f3ffe7865be4e829abc8e1de3e470509ac0ea2679fe6fd.
  Original uses NQ 30m futures 24h session, ADX >17.5, highest CLOSE of
  N previous bars, stop entry during 08:00-15:00 America/Chicago,
  futures USD1000 stop, end-of-day close. N NOT DISCLOSED; threshold
  calibrated on other index futures and prior model-selection counts
  unspecified. Proposed bar-confirmed CFD adaptation fixes N=8 and
  Wilder ADX(14) before viewing outcomes, enters at native NEXT_BAR
  after high touch, flatten desired at 14:30 Chicago and next bar
  at 15:00, explicitly omits futures stop. Not identical author rule.
llm_model_id: GPT-6
strategy_module: strategies/candidates/bta_adx_nq_cfd_30m_20261031.py
status: QUEUED
---

# Immutable exploratory rule sheet

| Parameter | Original BTA | Predeclared CFD adaptation |
| --- | --- | --- |
| Market | NQ futures | verified NQ CFD 30-minute source |
| Session | 24h bars, entry 08:00-15:00 Chicago | 08:00-14:00 Chicago signal, next 30m bar fill; desired flatten 14:30 |
| Direction | long only | +1 NQ, others 0, max one daily entry |
| Entry | intrabar buy stop at highest prior N closes | after completed 30m high > prior 8 closes, next-bar fill |
| Filter | ADX >17.5, period unspecified | Wilder ADX(14) >17.5 at current complete bar |
| Exit | end of day | flat desired on 14:30 Chicago, filled next 30m |
| Futures loss stop | $1,000 per NQ | UNMODELED; this is not source-faithful execution |
| Holding | intraday | expected <=14 half-hour bars, label24, embargo24 |
| Costs | not stated | 0.5bps fee +1bps slippage each side; no actual quote-side fills |
| Selection debt | ADX calibrated across multiple index futures; N optimised and undisclosed | one frozen N=8 and one frozen ADX(14) hypothesis; publisher trials unresolved |

No tuning, search grid or derived performance claims. Validation was not inspected
before these parameters were frozen. Source publication is historical but current
page content has no independent point-in-time article-vintage attestation.
Source media and author trade claims cannot count as evidence of this CFD edge.
Original intrabar sequencing and futures dollar-stop require native event/tick
simulation before any source-fidelity claims.
