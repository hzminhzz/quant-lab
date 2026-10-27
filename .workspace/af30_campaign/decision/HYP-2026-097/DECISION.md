# Decision — HYP-2026-097 · AF30 S03 Buy and Hold with Variable Risk Scaling

- **Contract**: `MULTI_ASSET_1D_V1`
- **Verdict**: STAGE-1 PASS — NOT ADVANCED TO STAGE 3
- **Binding gate**: no Stage-1 criterion failed; this work unit advanced only S01 and S05 to Stage 3
- **Trial spend**: 0 of 3 — not advanced to Stage 3 in this work unit
- **Registry status**: `ACTIVE` (the status column is not the verdict)

## Evidence

Source: `registry/registry.db`, `selection_attempts` row `ATT-35b38f0df14536ce-001` (`standalone_screen`, outcome `PASS`). No new computation was performed for this record.

| field | value |
|---|---|
| `profit_factor` | 1.2129 |
| `conditional_edge` | 0.000285 |
| `trade_edge` (bps) | 311.90 |
| `time_in_market` | 1.000 |
| `bootstrap_p_value` | 0.010 |
| `avg_holding_bars` | 109.6 |

## Disclosure

- Registered falsification criteria (Stage 1): Stage-1 on MULTI_ASSET_1D_V1 rejects the mechanism when profit factor < 1.15, time in market < 5%, trade edge <= 1.5x the one-way cost, or the block-bootstrap p-value >= 0.05.
- Those criteria were **met**. No Stage-3 trial was run because this work unit advanced only S01 and S05, so no Stage-3 verdict exists for this strategy and none is claimed here.
- A trial started now would compute `max(k_dedup, 20.1097) + 0.50 x 17 = 28.6097` as its gate, because the unvectorized penalty is added on top of the stored floor rather than replacing it. That is an estimate of the next run's gate, not the stored value.
- Selection state last stored on the contract on `MULTI_ASSET_1D_V1`: `k_eff_gate` 20.1097, expected-max-Sharpe noise floor 1.1841 annualised, 17 unvectorized attempts. The unvectorized penalty is re-applied on top of the previous floor on every trial, so the gate ratchets.
- Implied net Sharpe for DSR >= 0.9 under that floor: about 1.98 annualised (estimate, variance ~1, 806 bars).
- Evidence window: the trial path evaluates `val_start`..`val_end` only (2023-01-01..2025-07-01, 806 bars). No production holdout was read.
