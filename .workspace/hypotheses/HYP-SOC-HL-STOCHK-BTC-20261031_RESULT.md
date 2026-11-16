# H018 BTC Stoch-K Binance transfer — native Stage 1 terminal evidence

- Hypothesis: `HYP-SOC-HL-STOCHK-BTC-20261031`
- Contract: `AUTO_CRYPTO_PERPS_TOP100_1H_V1_H24_E24_F45_S20_V1`
- Original Bybit 1h research and negative OOS prior: `https://github.com/SeifBoukerdenna/hl/blob/88151f9f9845f20636b43fd3f70d133243d60382/docs/hypotheses/H018.md`, Git blob `622bc86b73f5e97ec6c4db018a867ab0e51242da`.
- Adaptation: Bybit to Binance BTCUSDT USD-M 1h, same K14, cross up 20, exit K>80 or 24-bar timeout, long-only 50%-equity target, native next-bar fills, taker+slippage 6.5bps/side; no intrabar stop.
- Program source-side selection: original source author had 45-path CPCV with mean OOS Sharpe -0.12 on Bybit, 24/45 negative paths. This validation period overlaps Warden's and is NOT fresh independent OOS.
- Registration: successful guarded `warden register-hypothesis` (native ledger).
- Native prefix/causal verify: `PASS`, 21,888 rows, `verified_market`, `prefix_invariance=true`; durable unit `warden-verify-f506fae96cfb901b9c`.
- Native Warden Stage 1: durable unit `warden-screen-8dd5ab878f62536618`, native ledger selection attempt **`ATT-5d71f73e9c96ca2f-001`**, stage `STAGE_1_SIGNAL`, terminal scientific verdict **`REJECT`**. Warden returns exit code 1 for scientific REJECT, not infrastructure failure.

## Frozen native metrics

| Warden metric | Observed |
| --- | ---: |
| profit_factor | 0.9875092804646355 |
| bootstrap_p_value | 0.135 |
| time_in_market | 0.3860730593607306 |
| conditional_edge | 0.00004089098276148867 |
| trade_edge | 0.0010867843119792115 |
| avg_holding_bars (Warden turnover-derived) | 26.577603143418468 |
| passed | false |

PASS requires PF>=1.15, p<0.05, TiM>=0.05 and trade-edge greater than 1.5x one-way cost. This fails PF and bootstrap; no Stage3 / holdout access.

**Holding caveat:** Warden's Stage1 `avg_holding_bars` is computed as `2 * n_active / sum(abs(diff(positions)))`. For a fixed position weight of 0.5, that estimator is scale-sensitive and can report more than the hard 24-bar signal timeout. It is not a measured completed trade duration. The source-derived state machine has a 24-bar signal cap; do NOT interpret 26.58 as proof the source hold was changed, and do not change the protected Warden evaluator to hide the metric. No unregistered return diagnostics were run.

## Tests and permanence

- `uv run pytest -q tests/test_soc_hl_stochk_btc_1h_20261031.py`: 2 passed.
- `uv run ruff check` for candidate and tests: pass.
- `uv run warden check-worktree`: pass; protected files unchanged.
- Hypothesis, strategy, spec and this report are VPS-local under `/home/quant/dev/quant-lab`.
- **No retry, inversion, tuning or rescreen**. Continue from another source mechanism, Warden selection history counts this attempt.
