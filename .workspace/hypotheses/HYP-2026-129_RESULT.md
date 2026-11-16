# HYP-2026-129 — First ChatGPT-only AutoResearch test (2026-10-29)

Outcome: **STAGE_1_REJECT / FALSIFIED**, not a Warden backtest trial.

- Contract: `FX_MAJORS_1D_V2` (eight FX majors, verified market-data panel, 2014-01-01..2025-06-30; 5-bar contract horizon, NEXT_BAR delay 1, 1.4 bps round-trip modeled cost).
- Original pre-registered mechanism: five-session volatility-normalized price shock may reverse over the following five sessions.
- Frozen parameters: formation=5, volatility window=20, absolute z entry threshold=1.25, rebalance every 5 bars, each pair exposure=0.125.
- Source: original ChatGPT-generated conjecture; not presented as published research.
- `uv run warden check-worktree`: integrity verified.
- `uv run python scripts/preflight_thesis.py --contract FX_MAJORS_1D_V2 --hypothesis HYP-2026-129`: contract/digest/execution validated; new hypothesis.
- `uv run warden register-hypothesis .workspace/hypotheses/HYP-2026-129.md --json`: registered.
- `uv run warden verify experiments/active/hyp_2026_129_fx5d_reversal.yaml --json`: `PASS`; 28,755 rows; sampled prefix-invariance passed, verified market data.
- `uv run warden screen experiments/active/hyp_2026_129_fx5d_reversal.yaml --json`: exit 1, `passed=false`, rules_ts; PF=0.9747754443717062; p=0.59; time_in_market=0.6133162612035852; conditional_edge=-3.6402528159566807e-06; trade_edge=-7.063012045947342e-05; reported average holding=19.40253164556962 bars.
- **Fail conditions:** PF <1.15; bootstrap p>=0.05; trade edge <=1.5x one-way costs. Time-in-market passes. The effective holding statistic is greater than the 5-bar decision interval because positions persist when successive targets agree; audit duration interpretation before any future comparable trials.
- The screen appends a validation selection attempt. The read-only post-screen preflight showed contract selection_attempts=15, hypothesis trials_used=0/3. Do not represent 0 trials as 0 statistical searches.
- No `warden backtest`, `warden qualify`, holdout access, or live order took place.
- **Research decision:** Stop this configuration. Do not invert/tune the signal based on the failed validation without declaring a new distinct hypothesis and accounting for this screen in selection history.
- Existing `HYP-2026-128` contaminated validation family remains quarantined and untouched.
