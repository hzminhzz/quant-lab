# Decision — HYP-2026-095 · AF30 S01 Buy and Hold, Single Contract

- **Contract**: `MULTI_ASSET_1D_V1`
- **Verdict**: NOT QUALIFIED AT STAGE 3 — runner verdict `REVISE`
- **Binding gate**: deflated Sharpe probability 0.6626 < 0.9
- **Trial spend**: 1 of 3 — terminal, 2 unspent: no revision was preregistered and no admissible axis can be triggered by this strategy's parameters
- **Registry status**: `ACTIVE` (the status column is not the verdict)

## Evidence

Source: `registry/registry.db`, `trials` row `RUN-HYP095-001` (trial 1 of 3).

| field | value |
|---|---|
| `net_sharpe` | 1.3172 |
| `stress_2x_cost_sharpe` | 1.3168 |
| `delay_plus1_sharpe` | 1.3172 |
| `deflated_sharpe_prob` | 0.6626 |
| `total_trials_n` | 29.0000 |
| `effective_trials_keff` | 11.6097 |
| `breakeven_cost_bps` | 2728.5915 |
| `annualized_turnover` | 0.3871 |
| `param_neighbor_stability` | 1.0000 |
| `stage_reached` | STAGE_3_STRESS |
| `verdict` | REVISE |

## Audit

Status: `REJECTED` — failed `2_outlier_sensitivity`, `6_selection_bias_accounting`
Source: `.workspace/hypotheses/AUDIT_RUN-HYP095-001.md`.

The audit verdict is an audit status, not the strategy verdict. Check 2 requires the runner verdict to be `CANDIDATE_FOR_QUALIFICATION`, so it cannot pass for a `REVISE` trial regardless of the strategy's merit.

## Disclosure

- Registered falsification criteria (Stage 1 only): Stage-1 on MULTI_ASSET_1D_V1 rejects the mechanism when profit factor < 1.15, time in market < 5%, trade edge <= 1.5x the one-way cost, or the block-bootstrap p-value >= 0.05.
- Those criteria were **met**; the mechanism was not falsified at Stage 1. The Stage-3 outcome is `REVISE` / NOT QUALIFIED, which is a gate verdict, not a falsification.
- The audit status is recorded separately and is not evidence that the mechanism has no edge.
- Selection state this trial was judged under on `MULTI_ASSET_1D_V1`: `k_eff_gate` 11.6097, expected-max-Sharpe noise floor 1.0567 annualised, 17 unvectorized attempts. The unvectorized penalty is re-applied on top of the previous floor on every trial, so the gate ratchets.
- Implied net Sharpe for DSR >= 0.9 under that floor: about 1.85 annualised (estimate, variance ~1, 806 bars).
- Evidence window: the trial path evaluates `val_start`..`val_end` only (2023-01-01..2025-07-01, 806 bars). No production holdout was read.
