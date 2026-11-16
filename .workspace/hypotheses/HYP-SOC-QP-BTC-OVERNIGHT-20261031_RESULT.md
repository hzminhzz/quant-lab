# Stage 1 scientific rejection — HYP-SOC-QP-BTC-OVERNIGHT-20261031

Frozen researcher adaptation of Quantpedia's Bitcoin MAX(10) NYSE-close-to-open study.
**Not** a Gemini replication: Binance BTCUSDT perpetual, Monday and Tuesday
NYSE-close entries only. Friday-to-Monday excluded because its 66h source
holding period exceeds the approved 24h label. Time zones and XNYS holidays
handled causally; original study publication 2024-11-12 and six lookback
variants imply prior author-side selection. Original primary:
https://quantpedia.com/how-to-profitably-trade-bitcoins-overnight-sessions/
Contemporary response-body SHA256 on 2026-10-30:
`e9bc9d539c8d3ceff2e7f880ac9ae1e30080e693092b631747165ff5d157ecb3`
Current HTTP body not independently historically archived.

## Native immutable result

- Hypothesis: `HYP-SOC-QP-BTC-OVERNIGHT-20261031`
- Contract: `AUTO_CRYPTO_PERPS_TOP100_1H_V1_H24_E24_F45_S20_V1`
- Spec: `experiments/active/qp_btc_overnight_max10_1h_20261031.yaml`
- Module: `strategies/candidates/qp_btc_overnight_max10_1h_20261031.py`
- Tests: `tests/experiments/test_qp_btc_overnight_max10_1h_20261031.py` (2 passed)
- Native verify durable job: `warden-verify-89d85c6bf852747560`
- Native verify: `{"status":"PASS","prefix_invariance":true,"cached":true,"rows":21888,"data_provenance":"verified_market","market_eligible":true}`
- Native screen durable job: `warden-screen-8b8051d4c92dbd96a8`
- Selection attempt: `ATT-40545e258d2c604a-001`, `STAGE_1_SIGNAL`, outcome `REJECT`.
- Native **screen stdout** (process exit 1 is a scientific REJECT, not a tool outage):

```json
{"data_provenance":"verified_market","market_eligible":true,"metrics":{"avg_holding_bars":18.0,"bootstrap_p_value":0.295,"conditional_edge":6.866344947819863e-05,"profit_factor":0.9929682983958957,"time_in_market":0.05342465753424658,"trade_edge":0.0012359420906075753},"notes":"PF=0.99, Edge=0.7bps, TiM=5.3%, Holding=18.0h","passed":false,"provenance_reason":"verified market data","strategy_family":"rules_ts"}
```

Falsified by PF=0.99297 < 1.15 AND block bootstrap p=0.295 >=0.05.
Time in market=5.34% clears the >=5% minimum, and trade edge=12.36bps
clears 1.5x the 6.5bps one-way cost (9.75bps). This is NOT an endorsed
trading edge; all conjuncts must pass. Native per-side cost: 4.5bps taker,
2.0bps slippage, plus declared 1bps/8h funding. Average hold is 18 hours.

This result is frozen. **Do not re-screen, tune, invert, rename, or advance
this strategy.** Negative outcome applies only to this particular adaptation,
not the published Gemini or weekend-inclusive strategy.

## Experiment ledger accounting

Before: 145 hypotheses, 71 full trials, 180 selection attempts, 9/20 source screens.
After: 146 hypotheses, 71 full trials, 181 selection attempts, 10/20 source screens.
No holdout read, no Stage3 trial, no broker order.
