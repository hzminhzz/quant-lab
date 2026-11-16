# Frozen native Stage-1 result: HYP-SOC-QC-DUALTHRUST-ES-20261031

Status: **REJECT**. One predeclared governed adaptation, never retry/tune.

- Original QuantConnect SPY hourly dual-thrust article: https://www.quantconnect.com/learning/articles/investment-strategy-library/dual-thrust-trading-algorithm
- Source captured 2026-10-30 17:38:27Z; SHA256 b5c79f8dc6e44ff9974436cba63d723fde902f0a2a2a5b73834c223909bdefac. Original SPY result Sharpe -0.17.
- New (non-source-equivalent) ES CFD 30m RTH closed-bar next-bar adaptation; original intrabar crossings, ETF sessions and fills are NOT replicated.
- Contract AUTO_INDICES_30M_V1_H24_E24_F5_S10_V1, approved source SHA25f8c39d... data, 1.5bps/side, 24-bar label and embargo, 1-bar execution lag.
- Hypothesis: .workspace/hypotheses/HYP-SOC-QC-DUALTHRUST-ES-20261031.md
- Strategy: strategies/candidates/qc_dual_thrust_es_cfd_30m_20261031.py
- Experiment: experiments/active/qc_dual_thrust_es_cfd_30m_20261031.yaml
- Four new local tests PASS; Warden causal verification PASS on 109100 source market rows, durable verify warden-verify-7100ca519352f1a14d.
- Native Warden Stage1 durable job warden-screen-8a1d29a406f48a808f; immutable attempt **ATT-56ec259523a085e2-001**, stage STAGE_1_SIGNAL, outcome REJECT.
- Exact net PF **1.1163047509558468**, bootstrap p **0.05**, time in market **0.01859123605657296**, average hold **5.404040404040404 thirty-minute bars** (NOT 5.4 hours); conditional_edge **0.00013576079497891182**, trade_edge **0.000733656821350685**.
- FAIL: PF<1.15, p not <0.05, TiM<0.05. A screen exit code 1 is an ordinary scientific reject; not an infrastructure failure.
- No Stage3, holdout access or live orders. Source variant is terminal negative evidence; further work must select a truly different mechanism.

The shared campaign was at 7/20 native source screens before this Warden attempt, 8/20 afterward. All counts are derived from read-only native registry and not replenished.
