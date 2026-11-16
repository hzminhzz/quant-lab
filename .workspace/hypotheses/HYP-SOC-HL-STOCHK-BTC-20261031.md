---
hypothesis_id: HYP-SOC-HL-STOCHK-BTC-20261031
contract_id: AUTO_CRYPTO_PERPS_TOP100_1H_V1_H24_E24_F45_S20_V1
title: BTC hourly Stoch-K oversold upward-cross mean reversion
strategy_family: rules_ts
economic_mechanism: >
  Forced BTC perpetual liquidations create transient oversold price
  dislocations; resumption above Stochastic K20 may indicate exhausted
  selling that mean-reverts inside a daily holding window.
counterparty_or_friction: >
  Liquidating leveraged holders and liquidity-taking panic sellers;
  this counterparty mechanism is conjectural and historically unproven.
expected_holding_bars: 24
falsification_criteria: >
  Native rules_ts REJECT if PF < 1.15, time-in-market < 5 percent,
  bootstrap p >= 0.05, or trade-edge <= 1.5x approved one-way cost.
  Negative published Bybit OOS prior reduces prior belief. If Stage1
  rejects this fixed candidate, freeze it without tuning/relabeling.
source_provenance: https://github.com/SeifBoukerdenna/hl/blob/88151f9f9845f20636b43fd3f70d133243d60382/docs/hypotheses/H018.md ; Git blob SHA1 622bc86b73f5e97ec6c4db018a867ab0e51242da
llm_model_id: openai/gpt-6
status: QUEUED
---

# Preregistered H018 BTCUSDT 1h transfer — source-faithful signals, different exchange

## Original author rules and negative source selection
Pinned H018 original Bybit BTC perpetual 1h thesis authored 2026-05-24.
K_t = 100 * (close_t - rolling_low14) / (rolling_high14 - rolling_low14).
Long at K crossing upward through 20 (previous <20, current >=20).
Exit at K>80 or after 24 completed one-hour bars. Size = 50% equity.
No short, no ATR stop, no trailing, no parameters optimized here.
First-party source itself reports 404 trades, OOS mean Sharpe -0.12
and 24/45 negative CPCV paths; author had 45-path CPCV and a Reddit
source-derived index rule with different parameters. This was a prior
REJECT and is not prospective independent alpha. Warden source-side
search adjustment must not ignore these 45 published paths.

## Research adaptation frozen before data evaluation
Source venue Bybit 1h BTCUSDT perp -> research venue Binance USD-M
BTCUSDT perp 1h. No change to K14/20/80, 24-bar maximum exit or
long-only 50%-equity sizing. NEXT_BAR entry and exit delay 1 bar;
source study's exact fill convention is not independently verified.
Native approved cost = 4.5 bps taker + 2.0 bps slippage per side,
13.0 bps round trip, plus available funding. Source period
2024-11 to 2026-05 overlaps Warden validation 2024-07 to 2025-07,
so do not present this as a new untouched holdout.

Position target is emitted only after the final completed signal bar;
Warden adds execution delay, no manual pre-lag. On a K>80 exit or
24-bar timeout, desired weight goes flat; no same-bar reentry.
BTCUSDT present and data SHA preflight verified; no broker access.
Risk: maximum 50% unlevered notional, long-only; no stop or
live allocation. Source was already falsified and serves only as a
cross-exchange falsification test, not a promising thesis.

## Baselines, multiplicity, decision
Baselines: zero-exposure/cash, equal-time BTC long-only exposure and
a frozen simple one-day mean-reversion signal (comparative research
only, NOT selection from a tuned neighbor grid). Other existing
crypto mean-reversion screens are related family evidence, not free
new OOS. Prior author evaluation tested 45 CPCV paths; this must
be disclosed in downstream DSR, PBO/multiplicity analysis.
No Stage3 or holdout access without subsequent scientifically valid
Warden stage qualification.
