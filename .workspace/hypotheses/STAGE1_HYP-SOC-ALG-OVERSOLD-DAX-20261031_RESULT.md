# Frozen native Stage-1 result: HYP-SOC-ALG-OVERSOLD-DAX-20261031

Status: **REJECT**. One frozen 1h DAX source-inspired adaptation. Do NOT retune or repeat.

- Original source https://www.algomatictrading.com/post/oversold-intraday-on-dax ; original article published June 1 2024 and updated May 10 2025.
- Raw original HTML bytes fetched 2026-10-30 18:46Z SHA256 09749d54e9967710e5c7993fceb928f3442ea562749bf6d96039779e5e815a7a, 919667 bytes.
- Original source: ProRealTime IG DAX 1h CET, IBS<=.05, RSI2<50, BBW50<.06, exit low>prior close; actual broker intrabar 0.5% stop, no commissions.
- Explicit adaptation: Wilder RSI2 seed, Bollinger width 4*STD/SMA, bar-low 0.5% stop trigger against **signal** close, Warden NEXT_BAR liquidation. This is **NOT** equivalent to original executable broker stop, not source profitability.
- Contract AUTO_INDICES_1H_V2_H24_E24_F5_S10_V1; 1.5bps per side and source data pinned; prior source-family effective selection floor 51.25.
- Hypothesis .workspace/hypotheses/HYP-SOC-ALG-OVERSOLD-DAX-20261031.md
- Strategy strategies/candidates/alg_oversold_dax_1h_20261031.py
- Experiment experiments/active/alg_oversold_dax_1h_20261031.yaml
- Four DAX-specific unit tests and four Dual-Thrust regression tests PASS. First verification job warden-verify-6f6bff1c2b4ef734ff FAIL due to NaN warmup incorrectly represented as non-null; corrected to a causal null mask, no parameter or screening attempt changed.
- Revised native Warden causality job warden-verify-fa258d74e54ce1816c PASS on 55812 real market rows, prefix_invariance true.
- Native Stage1 durable job warden-screen-a57543ca3942557877; immutable attempt **ATT-9089b6a0201cffa3-001**, stage STAGE_1_SIGNAL, outcome REJECT.
- Net PF **1.0714091034062578**, bootstrap p **0.0** (finite bootstrap empirical estimate, not proof of zero probability), time in market **0.45575281203415097**, average hold **16.857142857142858 one-hour bars**, conditional_edge **0.00005640433859808132**, trade_edge **0.0009508159935105137**.
- FAIL: PF<1.15, despite bootstrap significance and adequate time in market.
- Additional unresolved fidelity: original source zero-fee backtest, intrabar stop, IG dealer spread, weekend financing and vendor session conventions; no qualification/paper/live.
- No Stage3, no protected holdout reads, no live orders. Source and frozen adaptation must not be silently re-screened.
