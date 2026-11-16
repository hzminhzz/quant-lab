---
hypothesis_id: HYP-SOC-ALG-OVERSOLD-DAX-20261031
contract_id: AUTO_INDICES_1H_V2_H24_E24_F5_S10_V1
title: DAX one-hour close-location oversold rebound with volatility cap
strategy_family: rules_ts
economic_mechanism: >-
  Weak DAX candles near the hourly lows may overshoot as mechanical hedgers
  transfer inventory to liquidity providers in otherwise restrained volatility.
counterparty_or_friction: >-
  Price-insensitive index-hedging sellers, with temporary inventory premium
  paid to patient liquidity suppliers.
expected_holding_bars: 16
falsification_criteria: >-
  Reject if native Stage1 PF<1.15 or bootstrap p>=0.05 or time in market
  <5 percent or trade edge<=1.5x one-way costs. Stage3 must separately
  test cost/latency, DSR, source parameter selection and intrabar stop fidelity.
source_provenance: >-
  https://www.algomatictrading.com/post/oversold-intraday-on-dax ;
  2024-06-01 publication, 2025-05-10 update, raw HTML SHA256
  09749d54e9967710e5c7993fceb928f3442ea562749bf6d96039779e5e815a7a
  captured on 2026-10-30.
llm_model_id: gpt-6
strategy_module: strategies/candidates/alg_oversold_dax_1h_20261031.py
status: QUEUED
---

# Source vs adaptation, frozen before results

Original publisher: IG DAX CFD in CET 1-hour bars; IBS=(close-low)/
max(high-low,0.001) <=0.05, RSI2<50, BollingerBandwidth50<0.06.
Enter long 1 contract; exit if completed hourly low > previous close;
also place a real broker STOP of 0.5% intrabar. Publisher tested without
commissions, reported mean 16h26m holding time and OOS claim requiring
independent provenance review.

**NOT SOURCE REPLICATION:** The native Warden does not support intrabar
broker stop priority. This candidate uses a completed one-hour bar's
low below (entry SIGNAL close *0.995) to emit a flat signal, executed
one hour LATER by native Warden. This may be optimistic or pessimistic,
and the signal close is not the actual future entry fill. RSI2 uses
Wilder smoothing with causal two-delta seed, and BB width uses
4*populationSTD50/SMA50: confirm platform equivalence before Stage3.
Signals compute at completed bars, no pre-lag; exit recovery low > last
close. +1 DAX or cash, no shorts, no more than 1.0x target gross.

Contract 1h 24-bar label, 24-bar embargo, 1-bar execution lag, fee
0.5bps+slippage1.0bps each side. No broker overnight swaps included.
Expected holding16 one-hour bars; longer tail requires Stage3 attribution.
Baseline cash, unconditional DAX long, and IBS-only would be independent
later checks; do not use them to retune failed Stage1.
Original vendor tested 2162 trades in 1990-2024 and omitted fees;
source-side model search count is unobserved. Native contract history
has 51.25 effective prior selection attempts; no fresh budget granted.
No protected holdout or live orders.
