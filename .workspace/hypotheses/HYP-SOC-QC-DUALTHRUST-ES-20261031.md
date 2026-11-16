---
hypothesis_id: HYP-SOC-QC-DUALTHRUST-ES-20261031
contract_id: AUTO_INDICES_30M_V1_H24_E24_F5_S10_V1
title: ES intraday reversal after a completed-bar Dual Thrust excursion
strategy_family: rules_ts
economic_mechanism: >-
  Large intraday moves exceeding a four-session volatility-sensitive range
  can persist as dealers hedge option and inventory exposure, with reversal
  upon crossing the opposite range boundary.
counterparty_or_friction: >-
  Liquidity providers and short-term directional hedgers bearing inventory
  risk when a broad index crosses previous trading-session extremes.
expected_holding_bars: 6
falsification_criteria: >-
  Native Warden Stage 1 REJECT if net profit factor below 1.15, bootstrap
  p-value >= 0.05, time in market below 0.05 or trade edge below 1.5 times
  contract one-way cost. Stage 3 requires DSR, 1.5x and 2x costs, extra-bar
  latency, source and CFD calendar stress before any paper qualification.
source_provenance: >-
  Original QuantConnect tutorial https://www.quantconnect.com/learning/articles/investment-strategy-library/dual-thrust-trading-algorithm
  publisher capture sha256 b5c79f8dc6e44ff9974436cba63d723fde902f0a2a2a5b73834c223909bdefac at 2026-10-30T17:38:27.152695Z;
  primary source lines documented in research report and source-captured archive.
llm_model_id: gpt-6
strategy_module: strategies/candidates/qc_dual_thrust_es_cfd_30m_20261031.py
status: QUEUED
---

# Source fidelity and frozen adaptation

Original tested SPY equity hourly, HH-LC/HC-LL range over four full prior days,
cap=day_open+0.5*range, floor=day_open-0.5*range, reversing position on
boundary crossing. Original published SPY Sharpe -0.17 and dd 41.1%;
the source's favorable-futures comments are NOT proof of alpha in ES CFD.

**NEW ADAPTATION, NOT ORIGINAL SOURCE PERFORMANCE:** ES CFD 30-minute bars,
08:30-15:00 America/Chicago RTH. Only 13-bar *complete* prior trading sessions
are eligible for the trailing 4-session range. Current session open comes
from its first completed 08:30 30-minute bar. On any completed 30m RTH bar
before 14:30, close > cap sets +1, close < floor sets -1; otherwise retain
same-day target. Emit flat at 14:30, native NEXT_BAR executes at 15:00.
No overnight exposure. One-asset gross target always <=1.0; no scaling.

Source parameters: N=4, K1=K2=0.5. Researchers predeclared ES, 30m,
Chicago session, bar-confirmation and same-session flat **before any
market results were evaluated**. Source has no CFD financing or bid/ask
stop simulation; native contract taker0.5 + slippage1.0 bps per side.
Source preselection and ETF/CFD transfer enter DSR/fidelity debt.

Baseline: unconditional same-session ES long and cash; prior similar BTA
index breakout failures are NOT assumed independent confirmation.
Expected holding six half-hour bars; contract label horizon 24 bars,
embargo 24, execution delay one. Reject no-trade and too-low TiM as
scientific rejection, not engineering pass. Do not tune after rejection.
Protected holdout access and live orders prohibited.
