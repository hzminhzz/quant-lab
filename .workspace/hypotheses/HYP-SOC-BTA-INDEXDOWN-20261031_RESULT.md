# STAGE 1 result — HYP-SOC-BTA-INDEXDOWN-20261031

**Scientific classification: PRELIMINARY_STAGE1_PASS_UNQUALIFIED**

- Mechanism: ES next-open long following a red daily index candle.
- Source (untrusted publisher evidence): https://breakouttradingacademy.com/big-index-trading-secret-to-get-real-results/
- Original 2026-10-30 captured SHA256: `a5c0671d0136297977aebd4e3b81bc708fdd46cb56a063e263827ec20d205b5a`. Historical text vintage is not independently attested.
- Frozen hypothesis: `.workspace/hypotheses/HYP-SOC-BTA-INDEXDOWN-20261031.md`.
- Contract: `INDICES_1D_V1`, pinned verified-market exploratory data, no holdout access.
- Experiment: `experiments/active/bta_index_down_es_cfd_1d_20261031.yaml`.
- Strategy: `strategies/candidates/bta_index_down_es_cfd_1d_20261031.py`.
- Unit tests: `tests/experiments/test_bta_index_down_es_cfd_1d_20261031.py`, passed in local bounded test suite.
- Guarded durable verification: `warden-verify-1fa223190d364f0461`; Warden status PASS, prefix_invariance=true, rows=3111.
- Guarded durable native Stage1: `warden-screen-bf18f8984c89556595`, JSON output logged in systemd journal, idempotent immutable job.
- Native Stage1 passed: `true`.

| Native metric | Value |
|---|---:|
| profit_factor | 1.3598363108787053 |
| bootstrap_p_value | 0 |
| time_in_market | 0.45629820051413883 |
| avg_holding_bars | 1.7574257425742574 daily bars |
| conditional_edge | 0.0010928703154558582 net return fraction |
| trade_edge | 0.0019206384256773745 net return fraction |

**Decision:** Native Stage 1 preliminary PASS only. Publisher gave a tendency, not the actual execution; ES CFD Sunday settlement and cash-index reference mismatch, unquantified author selection history and costs must be reconciled before Stage 3. Bootstrap p=0.0 is a finite-sample report, NOT mathematically zero. No trade authorization. Frozen exact ES red-bar -> NEXT_BAR variant.

Warden's Stage 1 success is not a Stage 3 result, a deflated-Sharpe qualification, a protected-holdout evaluation or proof of executable broker/follower PnL. One native selection attempt is consumed for this exact frozen hypothesis; no rebranding or tuning. Full trials increment: **0**. Live orders: **0**.
