---
hypothesis_id: HYP-SOC-QP-BTC-OVERNIGHT-20261031
contract_id: AUTO_CRYPTO_PERPS_TOP100_1H_V1_H24_E24_F45_S20_V1
title: Bitcoin NYSE-close 10-day-max weekday overnight continuation
strategy_family: rules_ts
economic_mechanism: >
  US institutional ETF close and subsequent thinner BTC overnight liquidity
  can produce delayed risk transfer and price continuation in trend regimes.
counterparty_or_friction: >
  Institutional rebalancers and slower overnight inventory-repricing counterparties,
  not guaranteed to transfer from Gemini spot to Binance perpetuals.
expected_holding_bars: 18
falsification_criteria: >
  Native rules_ts rejects if profit factor below 1.15, block bootstrap p>=0.05,
  time in market below 5%, or trade edge <= 1.5x contract one-way cost.
  Any Stage1 pass is exploratory only; Stage3 remains guarded separately.
source_provenance: https://quantpedia.com/how-to-profitably-trade-bitcoins-overnight-sessions/; transcribed source analysis https-original seen 2026-10-30; see module docstring and Batch6 evidence; historical vintage not independently archived
llm_model_id: openai/gpt-6
status: QUEUED
---

# Pre-registered BTC MAX(10) NYSE overnight adaptation

Original Quantpedia article published 2024-11-12 by Cyril Dujava. It examined
Gemini BTC and the 5/10/20/30/40/50-day MAX trend rule under NYSE trading
session closes, eventually selecting MAX(10), with overnight entry at NYSE close
16:00 New York and exit at 10:00 the next open. It combines Friday-Monday,
Monday-Tuesday, Tuesday-Wednesday windows. Original source URL above.
Publisher content is third-party untrusted evidence; claims are not independent
Warden results. Publisher's historical in-/out-of-sample claims are not
independent confirmation for this source-selected parameter.

## Frozen source-versus-adaptation accounting (BEFORE any economic screen)

| Item | Source original | This candidate |
|---|---|---|
| Asset | Gemini BTC, optional CFD/ETF/futures discussion | Binance BTCUSDT USD-M perpetual |
| MAX rule | Local 10-day high at NYSE close | Latest ten completed regular NYSE-session 16:00 prices, incl current |
| Entry | NYSE regular close 16:00 Eastern | Closed 15:00 hourly bar; native NEXT_BAR at 16:00 NY |
| Exit | Next NYSE open 10:00 Eastern | 09:00 signal bar; native NEXT_BAR 10:00 NY |
| Days | Fri-Mon, Mon-Tue, Tue-Wed | Only Mon-Tue and Tue-Wed. Friday 66h > 24h label, excluded |
| Short/sizing | Long BTC | Long 1x or flat, no shorting or leverage |
| Stops | None published for final seasonal strategy | None; exact native market costs |
| Holiday/DST | NYSE calendar | XNYS regular 16:00 close, early closes skipped, DST by IANA |
| Costs | Not author-frozen | Native 4.5bps fee + 2.0bps slippage per side and 1bps/8h funding |

**This is an exploratory adaptation, NOT a source-faithful replication.**
The Friday omission changes strategy sample and source-side search multiplicity
(6 MAX lookbacks, day combinations); no inference that rules existed before
2024-11-12 for pre-publication 2024 validation observations.

## Falsification and baselines

Baseline A: BTCUSDT cash/flat seasonal exposure without MAX filter, same Monday
and Tuesday overnight windows. Baseline B: BTCUSDT buy-and-hold (descriptive).
Warden Stage1 native gate governs. No discretionary parameter optimization.
Validation 2024-07-01 .. 2025-07-01 is partly prior to publication and
thus exploratory, not prospective; holdout sealed.
