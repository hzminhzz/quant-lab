# STAGE 1 result — HYP-SOC-BTA-TIMENQ-20261031

**Scientific classification: REJECT_FROZEN_NEGATIVE_EVIDENCE**

- Mechanism: NQ Monday 10:00-22:00 Chicago pure-time CFD hypothesis.
- Source (untrusted publisher evidence): https://breakouttradingacademy.com/i-tested-300-nasdaq-time-strategies-i-found-the-1-outlier/
- Original 2026-10-30 captured SHA256: `481214f3307b889043db45d61ba54380d02deab2acb7068ea4ec0afb8a249700`. Historical text vintage is not independently attested.
- Frozen hypothesis: `.workspace/hypotheses/HYP-SOC-BTA-TIMENQ-20261031.md`.
- Contract: `AUTO_INDICES_1H_V2_H24_E24_F5_S10_V1`, pinned verified-market exploratory data, no holdout access.
- Experiment: `experiments/active/bta_nq_monday_time_1h_20261031.yaml`.
- Strategy: `strategies/candidates/bta_nq_monday_time_1h_20261031.py`.
- Unit tests: `tests/experiments/test_bta_nq_monday_time_1h_20261031.py`, passed in local bounded test suite.
- Guarded durable verification: `warden-verify-68f34d4836180db321`; Warden status PASS, prefix_invariance=true, rows=55766.
- Guarded durable native Stage1: `warden-screen-01c5116050c30d8277`, JSON output logged in systemd journal, idempotent immutable job.
- Native Stage1 passed: `false`.

| Native metric | Value |
|---|---:|
| profit_factor | 1.098250867135826 |
| bootstrap_p_value | 0.025 |
| time_in_market | 0.09284451095089104 |
| avg_holding_bars | 10.459770114942529 hourly bars |
| conditional_edge | 0.00009695917874604243 net return fraction |
| trade_edge | 0.0010141707202172255 net return fraction |

**Decision:** Native Stage1 REJECT because PF<1.15, despite p=0.025. Source searched 276 time windows and never fixed clock timezone; selected weekday/time is post-selection evidence, not OOS. Do not adjust the 10:00-22:00 Chicago window. No Stage3.

Warden's Stage 1 success is not a Stage 3 result, a deflated-Sharpe qualification, a protected-holdout evaluation or proof of executable broker/follower PnL. One native selection attempt is consumed for this exact frozen hypothesis; no rebranding or tuning. Full trials increment: **0**. Live orders: **0**.
