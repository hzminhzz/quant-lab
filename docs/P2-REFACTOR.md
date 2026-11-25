# What changed in the P2 research refactor

P2 changes how Warden validates research and records evidence. It does not deploy a strategy, activate a market dataset, or authorize a production holdout read.

| Area | What changes for researchers and operators |
| --- | --- |
| Data and causality | [The market-data gateway](../src/warden/market_data.py) owns panel reads. [The evaluation kernel](../src/warden/evaluation.py) shares the evaluator path across verification and execution. A warm feature cache is not causality approval: the proof must match the run's strategy, contract and inputs. |
| Strategy configuration | [Typed parameter models](../src/warden/params_model.py) reject undeclared parameters. Eligible numeric parameters can be used to generate neighbor stress cases; an empty grid is not proof of stability. Keep execution policy out of experiment YAML. |
| Holdout and audit | Qualification separates warmup from scored bars and applies the contract's refit policy. Check 1 distinguishes decision cadence from completed holding periods; open positions at the end are censored. Active carry strategies must supply their own verified carry series before any trial is reserved. |
| Selection evidence | [Return artifacts](../src/warden_evidence/return_artifacts.py) record timestamps; [alignment](../src/warden_evidence/selection_alignment.py) checks the grid before run reservation. Related contracts can share selection-group DSR history rather than starting with a fresh floor. |
| Registry and reports | [Read-only registry access](../src/warden_ops/readonly.py) backs reporting and preflight. Historical contract identity and trials remain readable; [lifecycle restrictions](../contracts/lifecycle/contract_lifecycle.json) apply to new hypotheses. |
| Dataset tooling | [Spec-driven builds](../src/warden_evidence/dataset_builder/) support point-in-time crypto inputs, forward funding, New York-close FX and SHA-pinned composites. `warden data fence` checks exploratory ownership, digest and temporal bounds. v2 market admission also requires verified origin attestation. |

## Research workflow

Follow the [research workflow](WORKFLOW.md) and [agent runbook](../AGENT_RUNBOOK.md). Start with `uv run --frozen warden check-worktree`, then register, verify, screen, backtest and audit. Inspect the recorded failure reasons rather than rerunning a rejected hypothesis or changing its declared horizon. Production qualification remains separately authorized.

The [#79 reference cases](https://github.com/hzminhzz/quant-lab/issues/79) distinguish a real refusal from a broken test: the Ridge reference is refused on holding cadence, while the drawdown reference and an independent fixed four-bar control qualify without an override on synthetic panels. These are mechanics checks, not evidence of market performance.

## Production handoff

[#106](https://github.com/hzminhzz/quant-lab/issues/106) remains open. The replacement specs and contracts under `tests/fixtures/p2_t20/` are rehearsal inputs, not installed market contracts. Follow the [cutover runbook](T20-CUTOVER-RUNBOOK.md) for real vendor builds, reviewed manifest pins, operator sealing and attestation, and a passing real-store fence. Archive the six legacy scripts only after those checks. Until then, leave the current datasets and scripts in place.
