---
hypothesis_id: HYP-SOC-BTA-INDEXDOWN-20261031
contract_id: INDICES_1D_V1
title: ES CFD delayed long after a fully observed red daily bar
strategy_family: rules_ts
economic_mechanism: >
  Broad equity-index risk premium and inventory rebalancing can induce
  a temporary price rebound following an index down day. On ES CFD
  daily bars, a completed red candle predicts more favorable next-bar
  long returns net of costs than a zero-position baseline. This is a
  falsifiable original ChatGPT-generated conjecture.
counterparty_or_friction: >
  Liquidity takers selling during a down day and overnight financing
  flows supply the potential next-session rebound to passive liquidity
  takers. The source is only a tendency; the bar-rule and next-open
  execution are researcher-specified. Sunday CFD bars and cash index
  settlement mismatch preclude SPY/S&P replication.
expected_holding_bars: 1
falsification_criteria: >
  Verify causal closed-bar signal and native NEXT_BAR execution. Reject
  at Stage1 if PF <1.15, block bootstrap p>=0.05, time-in-market<0.05,
  or conditional trade edge <=1.5x the injected one-way cost. Do not
  adjust the red-bar definition or add filters after seeing results.
  Do not claim live/OOS/Stage3 qualification if it passes.
source_provenance: >
  Original Breakout Trading Academy
  https://breakouttradingacademy.com/big-index-trading-secret-to-get-real-results/
  published 2022-12-01, captured 2026-10-30T17:38Z,
  source excerpt hash SHA256
  a5c0671d0136297977aebd4e3b81bc708fdd46cb56a063e263827ec20d205b5a.
  Author says a negative prior-day index close predicts a positive next
  day; no entry, exit, position sizing or fee model was given. The
  source has six separate tendencies and unreported selection testing.
  Our ES CFD bar-red next-bar position is explicitly NOT author code,
  with no independently validated historical source vintage.
llm_model_id: GPT-6
strategy_module: strategies/candidates/bta_index_down_es_cfd_1d_20261031.py
status: QUEUED
---

# Frozen research-only original conjecture
- Market: ES CFD daily bars from pinned INDICES_1D_V1 dataset only.
- Signal: at bar t close, if close[t] < open[t], desired ES weight +1; else 0.
- Native execution: bar t+1 open for any weight change; continuous exposure
  through consecutive red days; source never specified this convention.
- Holding: expected 1 daily bar conditional on a red daily candle; source
  did not specify stops or holdings. Contract label5/embargo pre-approved.
- Cash exposure: +1 long ES or 0, no gross leverage beyond one.
- Costs: contract 0.5bps taker +0.5bps slippage each side, round-trip 2bps.
- Leakage: no bar t entry; all features use bar t or earlier; no filters
  chosen from 2023-2025 validation. Truncate-prefix causal checks required.
- Benchmark: unconditional ES buy-and-hold equal notional and cash.
- Failure: native Stage1 PF<1.15 or p>=0.05 or TiM<5% or low conditional
  trade edge. Any rejection freezes this exact source+rule variant.
- Source selection debt: six published time/price 'tendencies'; trials
  and author data-mining history unspecified, must be counted before Stage3.
- Independent scientific risk: ES CFD nightly/weekend session differs from
  official cash index, so Stage1 cannot validate the author's cash claim.
