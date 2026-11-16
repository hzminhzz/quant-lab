---
hypothesis_id: HYP-SOC-QC-TOM-ES-20261030
contract_id: INDICES_1D_V1
title: ES CFD NYSE turn-of-month calendar flow (QuantConnect SPY-source adaptation)
strategy_family: rules_ts
economic_mechanism: >
  Institutional pension and cash-flow reinvestment and portfolio month-end
  rebalancing can create predictable long-side demand for diversified US
  equity index exposure over the first few NYSE trading sessions of a
  new month. A delayed, next-session ES CFD proxy may participate in
  the latter part of that temporary positive expected-return window.
counterparty_or_friction: >
  Investors whose rebalance takes place at month end, post-month-end
  equity-index liquidity providers, inventory and overnight gaps, and
  FX/CFD spread/financing differences versus SPY stocks. Contract's
  verified-market costs are 0.5bps taker plus 0.5bps slippage per
  side under mandatory NEXT_BAR execution. Trading ahead of Monday
  NYSE sessions may fill at partial Sunday CFD bars; fidelity risk.
expected_holding_bars: 5
falsification_criteria: >
  ONE predefined 3-NYSE-session turn-of-month candidate. Abort if
  calendar timestamps cannot be mapped or end-of-validation positions
  spill into the sealed holdout. Native Warden Stage1 rules_ts
  profit_factor >= 1.15, bootstrap p <0.05, time-in-market >=0.05 and
  positive trade edge beyond cost. Stage1 fail => STOP, do not tune
  number of sessions, invert trade or adapt to NQ after seeing returns.
  A Stage1 pass is retrospective and cannot qualify without multiple
  testing correction and source/execution fidelity assessment.
source_provenance: >
  Original public QuantConnect research article
  https://www.quantconnect.com/research/15377/turn-of-the-month-in-equity-indexes/
  article first-party public capture on 2026-10-30 UTC (publication
  timestamp not independently available); webpage visible method
  purchases SPY one minute after the NYSE final-month-session open,
  then describes liquidation after three trading days. Actual sample
  OnData code increments counter on every callback, so precise source
  exit semantics are ambiguous. This adaptation buys ES CFD from the
  close of the last XNYS monthly session at delayed Warden NEXT_BAR
  fill and targets flat at the close of the third XNYS trading
  session of next month, again with Warden NEXT_BAR exit. The
  2015-2025 Warden exploratory bars include partial Sunday CFD
  sessions. No assertion that historical article text was frozen
  before protected validation, and no source backtest result reused.
llm_model_id: GPT-6
strategy_module: strategies/candidates/quantconnect_turn_month_es_cfd_1d.py
---

# Preregistered source-to-CFD adaptation

| Field | Original QuantConnect SPY strategy | Frozen Warden research signal |
| --- | --- | --- |
| Instrument | SPY equity ETF | ES CFD price proxy, one asset |
| Cause | Pension monthly cash inflows / rebalancing | Same conjectured US index demand |
| Start | Last NYSE trading day, buy 1min after SPY open | Desired ES long on close of last NYSE daily bar, Warden fills NEXT_BAR |
| Finish | Text: hold 3 trading days; code: OnData callbacks count | Desired ES flat on close of 3rd NYSE trading session of new month, Warden fills NEXT_BAR |
| Calendar | US stock exchange session scheduling | Exact installed XNYS exchange_calendars timetable, holidays included |
| Weekend | No equity bars on Sunday | ES CFD has partial Sunday 1d bars; may become execution day after Fri EOM |
| Horizon | Approx 3 NYSE sessions | Up to 5 ES snapshot daily bars as verified from ex-ante trading calendar |
| Size | SPY full allocated equity | ES CFD +1 gross weight; other assets zero |
| Data contract | Original QuantConnect historical SPY | INDICES_1D_V1 verified-market 2015–2025 ES data |
| Costs | Original assumptions not verified | Warden 0.5bps fee +0.5bps slippage each side |
| Publication | Undated accessible original discussion | Current observed 2026-10-30, historical source vintage UNKNOWN |

No future observed prices or source performance are used to mark the third
NYSE session; exchange dates follow the deterministic public calendar.
The strategy intentionally avoids opening a final 2025-06-30 month-end
position, since the 3rd July NYSE session lies beyond the exploratory
data and the protected holdout must remain unopened.

This is one independent economic mechanism vs the previously rejected
Monday NASDAQ Bollinger/ATR source-adaptation. Exactly one Warden Stage1
screen; no grid, inverted variations or data-dependent tuning.
The parent `INDICES_1D_V1` had six prior registered Stage1 selection
attempts at the time of read-only preflight (not a reset). Any Stage1
pass would remain exploratory because source rules are not identical,
the source-side historical search effort is unknown, and Sunday CFD
execution may invalidate observed returns.

## Execution policy

1. Five-bar source horizon matches the original human-approved daily contract.
2. Native prefix-invariance verifier, exchange-calendar unit tests and
   immutable dataset SHA must PASS before screening.
3. Register exact hypothesis and source-provenance with Warden once.
4. Guarded native verify, then guarded single native Stage1 screen.
5. Archive negative outcomes and proceed to another independent source.
   No full backtest unless Stage1 passes and selection gates permit.
6. Do not access holdout, qualify, trade, or modify Warden core.
