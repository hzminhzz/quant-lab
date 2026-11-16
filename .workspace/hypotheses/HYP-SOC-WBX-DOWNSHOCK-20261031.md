---
hypothesis_id: HYP-SOC-WBX-DOWNSHOCK-20261031
contract_id: AUTO_CRYPTO_PERPS_TOP100_1H_V1_H24_E24_F45_S20_V1
title: Four-hour reversal after panic sell-volume events in BTC ETH LTC XRP perpetuals
strategy_family: rules_ts
economic_mechanism: >
  Rare downside jumps accompanied by abnormally elevated actual traded
  hourly volume and closes close to the hourly low may mark transient
  liquidity-taking sell pressure and position de-risking. Passive
  inventory takers require a temporary price concession, giving
  subsequent four-hour mean reversion. Liquidation itself is unobserved.
counterparty_or_friction: >
  Panic sellers, levered de-risking takers and temporary passive liquidity
  providers; this is a hypothesis, not measured liquidation-flow evidence.
expected_holding_bars: 4
falsification_criteria: >
  Frozen source-inspired adaptation. Warden rules_ts Stage1 REJECT if
  PF below 1.15, time-in-market below 5%, bootstrap p >= 0.05, or
  trade-edge below 1.5x approved one-way cost. Native Stage3 requires
  NetSR >1.0, 2xCostSR>0.50, lag+1 SR>0, DSR>=0.90,
  six-check audit, and no protected holdout. Any Stage1 REJECT frozen
  permanently; no threshold tuning/inversion or sibling re-registration.
source_provenance: >
  https://doi.org/10.1016/j.najef.2022.101733
  Wen/Bouri/Xu/Zhao 2022 spot-market intraday return evidence;
  read-only RePEc bibliographic mirror HTML captured 2026-10-31
  07:55 UTC sha256 c3cf332fb56ee98889343524f3842e6e0d0e8a38fd1c225518ccd3acd0550076.
  Independent frozen rule document AI-research-loop Git commit
  1c8a95a research/independent-crypto-reversal-b16
llm_model_id: openai/gpt-6
status: QUEUED
---

# Frozen research adaptation, not source reproduction

Fully prespecified at AI-research-loop Git commit `1c8a95a` before any
conditional return evaluation, per
`examples/STRATEGY_RESEARCH_20261031_WBX_DOWNSHOCK.md`.

## Source and transfer limitations

Wen et al. (2022) reports short-horizon intraday reversal conditional
on jumps/liquidity and possible investor overreaction, not a complete
four-hour Binance perpetual execution protocol. Our rules, parameters,
universe, four-hour hold and Warden fees are new and declared before
testing. Related prior crypto mean-reversion work, including the quarantined
residual-reversal family and a frozen stochastic BTC study, is search
context, not authorization to rerun those historical strategies. Exact
lineages are recorded in the separately frozen source provenance.

## Fixed features and targets

- Four original-publisher-related assets: BTCUSDT, ETHUSDT, LTCUSDT,
  XRPUSDT, each weight +0.25; never short; max gross 1.0.
- At close of hour t, log return below -1.5 times std of *previous*
  72 completed hourly returns; current hour volume at least 1.5
  times prior 72-hour median; close location <= 0.35 from low to high.
- If flat and signal TRUE, desired long position for bars t..t+3,
  desired flat at t+4; exit bar never resets timer or reenters.
- Warden one-bar lag (next open) applied by contract, not candidate.
  No stop/TP, no intrabar inference, no missing-bar filling.
- 24/7 UTC; one-way cost 6.5bps, 13bps roundtrip + point-in-time
  funding settlement, 2x-cost and delayed execution Stage3 gates.
- Original author searched jump, liquidity and sessions: true
  selection count unknown. Native Warden trial count not a complete
  adjustment; no claim of independent OOS proof.

## Comparison and gate

Cash/flat, equal-weight unconditional long 4 names, and predeclared
simple negative-return-only event with same 4h hold as descriptive
baselines, never alternative selected after validation. Stage0 economic
volatility exceeds cost hurdle by frozen 4h horizon. Only true native
verify/screen/backtest receipts count. No live orders or qualification.
