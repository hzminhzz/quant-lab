# AF30 daily strategy suite — campaign decision report

Derived from `registry/registry.db` by `scripts/build_carver_daily_decisions.py --report`. Every number below is queried, not transcribed; the per-strategy verdicts are the same classifier that writes each `DECISION.md`.

## Outcome

- **Qualified: 0 of 17.** No AF30 daily strategy cleared the Stage-3 gate on `MULTI_ASSET_1D_V1`.
- **Trial spend:** 2 of a possible 51 — HYP-2026-095 1/3, HYP-2026-099 1/3.
- **Selection attempts added by this work unit:** 5 (2 trials + 3 param-grid points). Contract raw N went from 28 to 33.
- **Production holdout:** not read. `data/holdout/` was never opened and no invocation passed `--unseal-holdout`.

## Per-strategy decisions

| Hypothesis | Strategy | Verdict | Binding gate | Trials |
|---|---|---|---|---|
| `HYP-2026-095` | Buy and Hold, Single Contract | NOT QUALIFIED AT STAGE 3 | deflated Sharpe probability 0.6626 < 0.9 | 1/3 |
| `HYP-2026-096` | Buy and Hold with Risk Scaling | STAGE-1 PASS — NOT ADVANCED TO STAGE 3 | no Stage-1 criterion failed; this work unit advanced only S01 and S05 to Stage 3 | 0/3 |
| `HYP-2026-097` | Buy and Hold with Variable Risk Scaling | STAGE-1 PASS — NOT ADVANCED TO STAGE 3 | no Stage-1 criterion failed; this work unit advanced only S01 and S05 to Stage 3 | 0/3 |
| `HYP-2026-098` | Buy and Hold Portfolio with Scaling | STAGE-1 PASS — NOT ADVANCED TO STAGE 3 | no Stage-1 criterion failed; this work unit advanced only S01 and S05 to Stage 3 | 0/3 |
| `HYP-2026-099` | Slow Trend Following, Long Only | NOT QUALIFIED AT STAGE 3 | net Sharpe 0.879 <= 1.0; deflated Sharpe probability 0.3144 < 0.9 | 1/3 |
| `HYP-2026-100` | Slow Trend Following, Long and Short | REJECTED AT STAGE 1 | profit factor 1.03 < 1.15; bootstrap p 0.31 >= 0.05 | 0/3 |
| `HYP-2026-101` | Slow Trend with Trend Strength | REJECTED AT STAGE 1 | profit factor 1.05 < 1.15; bootstrap p 0.23 >= 0.05 | 0/3 |
| `HYP-2026-102` | Fast Trend with Trend Strength | REJECTED AT STAGE 1 | profit factor 1.03 < 1.15; bootstrap p 0.34 >= 0.05 | 0/3 |
| `HYP-2026-103` | Multiple EWMAC Trend Rules | REJECTED AT STAGE 1 | profit factor 1.02 < 1.15; bootstrap p 0.34 >= 0.05 | 0/3 |
| `HYP-2026-104` | Adjusted Trend Forecasts | REJECTED AT STAGE 1 | profit factor 1.03 < 1.15; bootstrap p 0.28 >= 0.05 | 0/3 |
| `HYP-2026-105` | Spot Trend | REJECTED AT STAGE 1 | profit factor 1.05 < 1.15; bootstrap p 0.23 >= 0.05 | 0/3 |
| `HYP-2026-106` | Normalised Trend | REJECTED AT STAGE 1 | profit factor 1.14 < 1.15; bootstrap p 0.07 >= 0.05 | 0/3 |
| `HYP-2026-107` | Trend Following Asset Classes | REJECTED AT STAGE 1 | profit factor 1.02 < 1.15; bootstrap p 0.35 >= 0.05 | 0/3 |
| `HYP-2026-108` | Cross-Sectional Momentum | REJECTED AT STAGE 1 | profit factor 1.01 < 1.15; bootstrap p 0.35 >= 0.05 | 0/3 |
| `HYP-2026-109` | Breakout 80 | REJECTED AT STAGE 1 | profit factor 1.09 < 1.15; bootstrap p 0.12 >= 0.05 | 0/3 |
| `HYP-2026-110` | Value | REJECTED AT STAGE 1 | profit factor 0.93 < 1.15; trade edge -50.35 bps <= 1.5 x one-way cost (1.50 bps); bootstrap p 0.76 >= 0.05 | 0/3 |
| `HYP-2026-111` | Acceleration | REJECTED AT STAGE 1 | profit factor 1.03 < 1.15; bootstrap p 0.34 >= 0.05 | 0/3 |

## Why nothing qualified

The two trialled strategies failed the deflated-Sharpe gate, not the cost or latency gates: S01 recorded net Sharpe 1.317 with DSR 0.663, S05 net Sharpe 0.879 with DSR 0.314, against a 0.9 hurdle. That hurdle is set by the contract-wide selection family, which already held 17 unvectorized Stage-1 screens before this work unit ran.

S05 could not be revised: all four admissible revision axes passed on its baseline (2x-cost 0.859 vs 0.5, latency 0.867 vs 0.0, breakeven 45.4 bps vs 1.0, neighbour stability 0.917 with 3 of 3 grid points positive), and its actual failure — net Sharpe and DSR — is outside the closed axis set. Both sealed revisions were refused before any spec was authored; see `.workspace/af30_campaign/revisions/HYP-2026-099.refusals.md`.

## Disclosures

- **The selection floor ratchets.** `compute_selection_report` computes `max(k_dedup, prior_max_keff) + 0.50 * unvectorized`, and `prior_max_keff` is the previous run's already-penalised gate, so the 0.50 x 17 penalty is re-applied on every trial. Measured gates: 2.8345 before this work unit, 11.6097 after S01, 20.1097 after S05, and the next trial's gate is estimated at no less than 28.6097 — a lower bound that assumes the new candidate's effective rank does not exceed the stored floor. This is recorded, not fixed — `src/warden/` is out of scope and changing the formula is a contract decision.
- **A `REVISE` verdict is not a falsification.** The registered falsification criteria for all 17 hypotheses are Stage-1-only. S01 and S05 met theirs; they are NOT QUALIFIED at Stage 3.
- **The audit status is separate.** `warden audit-check` returns `REJECTED` for a `REVISE` trial because check 2 requires a `CANDIDATE_FOR_QUALIFICATION` verdict. That is an audit status, not evidence that the mechanism has no edge.
- **Worktree scope.** This branch changes no file under `src/warden/`, `contracts/` or `data/`; the working tree also carries uncommitted changes from other agents, which this work unit neither created nor relies on.
