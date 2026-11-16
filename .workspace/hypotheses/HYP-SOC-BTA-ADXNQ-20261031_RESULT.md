# STAGE 1 result — HYP-SOC-BTA-ADXNQ-20261031

**Scientific classification: REJECT_FROZEN_NEGATIVE_EVIDENCE**

- Mechanism: BTA ADX NQ next-bar CFD breakout adaptation.
- Source (untrusted publisher evidence): https://breakouttradingacademy.com/nasdaq-hitter-algo-breakout-strategy-very-profitable/
- Original 2026-10-30 captured SHA256: `d1e1880870d996e801f3ffe7865be4e829abc8e1de3e470509ac0ea2679fe6fd`. Historical text vintage is not independently attested.
- Frozen hypothesis: `.workspace/hypotheses/HYP-SOC-BTA-ADXNQ-20261031.md`.
- Contract: `AUTO_INDICES_30M_V1_H24_E24_F5_S10_V1`, pinned verified-market exploratory data, no holdout access.
- Experiment: `experiments/active/bta_adx_nq_cfd_30m_20261031.yaml`.
- Strategy: `strategies/candidates/bta_adx_nq_cfd_30m_20261031.py`.
- Unit tests: `tests/experiments/test_bta_adx_nq_cfd_30m_20261031.py`, passed in local bounded test suite.
- Guarded durable verification: `warden-verify-cfffb08ad67ead3d98`; Warden status PASS, prefix_invariance=true, rows=109100.
- Guarded durable native Stage1: `warden-screen-9b59ceb71f65eb4259`, JSON output logged in systemd journal, idempotent immutable job.
- Native Stage1 passed: `false`.

| Native metric | Value |
|---|---:|
| profit_factor | 1.0083915792116112 |
| bootstrap_p_value | 0.095 |
| time_in_market | 0.2003335997498002 |
| avg_holding_bars | 10.558608058608058 30-minute bars |
| conditional_edge | 0.000035938088102177985 net return fraction |
| trade_edge | 0.0003794561866466229 net return fraction |

**Decision:** PF below 1.15 and bootstrap p >= 0.05. Do not revise N=8, ADX14, trading windows, or no-stop adaptation. The source's futures intrabar stop and $1000 loss stop were never reproduced. Publisher selected threshold/period from multiple index variants.

Warden's Stage 1 success is not a Stage 3 result, a deflated-Sharpe qualification, a protected-holdout evaluation or proof of executable broker/follower PnL. One native selection attempt is consumed for this exact frozen hypothesis; no rebranding or tuning. Full trials increment: **0**. Live orders: **0**.
