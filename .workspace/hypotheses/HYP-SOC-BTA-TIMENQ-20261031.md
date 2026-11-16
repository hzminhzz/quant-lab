---
hypothesis_id: HYP-SOC-BTA-TIMENQ-20261031
contract_id: AUTO_INDICES_1H_V2_H24_E24_F5_S10_V1
title: NQ 1h calendar-time Monday 10-22 Chicago window
strategy_family: rules_ts
economic_mechanism: >
  Monday inventory rebalancing and beginning-of-week institutional
  buy flow can create a predictable net long NQ index premium during
  a 12-hour daytime-to-evening window. This is an exploratory CFD
  application of a selected futures calendar tendency.
counterparty_or_friction: >
  Weekend short-hedge unwinds and market-maker risk unwinding could
  temporarily transfer directional risk premium to long NQ positions.
  Source uses NQ futures and does not name its clock timezone; Chicago
  interpretation is a researcher-created adaptation.
expected_holding_bars: 12
falsification_criteria: >
  Warden prefix verify must pass before one standalone Stage1 screen.
  Reject native rules_ts if PF<1.15, bootstrap p>=0.05, time-in-market
  <0.05, or edge <=1.5x per-side 1.5bps costs. Never adjust selected
  Monday time window, timezone, or result after screening. Even PASS
  cannot overcome original author's 276-grid search or missing
  independent post-publication OOS without corrected Stage3 DSR.
source_provenance: >
  Original BTA article published 2026-06-17
  https://breakouttradingacademy.com/i-tested-300-nasdaq-time-strategies-i-found-the-1-outlier/
  captured 2026-10-30T17:38:21Z, SHA256
  481214f3307b889043db45d61ba54380d02deab2acb7068ea4ec0afb8a249700.
  The publisher tested 276 NQ futures 60-minute time combinations and
  promoted Monday 10:00-22:00 after filtering. Current page does NOT
  disclose platform clock timezone or tick/fill assumptions.
  This frozen researcher adaptation maps clock to America/Chicago,
  emits signal one bar before target fills, and uses NQ CFD contract
  NEXT_BAR at 10:00 and 22:00 Chicago. No original strategy fidelity
  or untouched retrospective evaluation is claimed.
llm_model_id: GPT-6
strategy_module: strategies/candidates/bta_nq_monday_time_1h_20261031.py
status: QUEUED
---

# Pre-registered original-versus-adapted execution
- Original: NQ futures pure long-only Monday 10:00-22:00 hourly
  session window; NO source stop; author selected via 276-window scan.
- Adaptation: NQ CFD, 1h bars, Chicago DST. At 09:00 Monday bar-close
  emit desired +1 (native next-open 10:00 entry); at 21:00 Monday
  emit desired 0 (native next-open 22:00 exit). Only one trade per
  calendar Monday, no other data filters and no tuning.
- Holding 12 hourly bars, approved label24/embargo24, UTC timestamps.
- No intrabar stop; future broker costs/spread differ from index futures.
- Native 0.5bps taker +1bps slippage one way, 3bps roundtrip.
- Cash and unconditional 12h hold on other weekdays are comparison
  baselines for subsequent evaluation, NOT posthoc selection.
- Source selection debit at least 276 published time-window cells;
  this is historical exploratory data, no out-of-sample claim.
- If Stage1 PASS, Stage3 requires DSR attribution and publisher
  multiplicity reconciliation before interpreting chance evidence.
