# Decision — HYP-2026-106 · AF30 S17 Normalised Trend

- **Contract**: `MULTI_ASSET_1D_V1`
- **Verdict**: REJECTED AT STAGE 1 — no Stage-3 trial was run
- **Binding gate**: profit factor 1.14 < 1.15; bootstrap p 0.07 >= 0.05
- **Trial spend**: 0 of 3 — no trial was run; the decision rests on the Stage-1 screen
- **Registry status**: `ACTIVE` (the status column is not the verdict)

## Evidence

Source: `registry/registry.db`, `selection_attempts` row `ATT-6ce8aa9950ec776a-001` (`standalone_screen`, outcome `REJECT`). No new computation was performed for this record.

| field | value |
|---|---|
| `profit_factor` | 1.1353 |
| `conditional_edge` | 0.000054 |
| `trade_edge` (bps) | 9.86 |
| `time_in_market` | 1.000 |
| `bootstrap_p_value` | 0.075 |
| `avg_holding_bars` | 18.3 |

## Disclosure

- Registered falsification criteria (Stage 1): Stage-1 on MULTI_ASSET_1D_V1 rejects the mechanism when profit factor < 1.15, time in market < 5%, trade edge <= 1.5x the one-way cost, or the block-bootstrap p-value >= 0.05.
- Those criteria were **not** met, so the mechanism is falsified at Stage 1 under its own registered rule. Revisiting it requires a **new** preregistered hypothesis with a different mechanism, never a re-run of this ID.
- A trial started now would compute at least `max(k_dedup, 20.1097) + 0.50 x 17 = 28.6097` as its gate, because the unvectorized penalty is added on top of the stored floor rather than replacing it. That is a lower bound, not the stored value: a new candidate with a higher effective rank would raise it further.
- Selection state last stored on the contract on `MULTI_ASSET_1D_V1`: `k_eff_gate` 20.1097, expected-max-Sharpe noise floor 1.1841 annualised, 17 unvectorized attempts. The unvectorized penalty is re-applied on top of the previous floor on every trial, so the gate ratchets.
- Implied net Sharpe for DSR >= 0.9 under that floor: about 1.98 annualised (estimate, variance ~1, 806 bars).
- Evidence window: the trial path evaluates `val_start`..`val_end` only (2023-01-01..2025-07-01, 806 bars). No production holdout was read.
