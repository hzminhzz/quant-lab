# Plan: AF30 daily strategy decisions

This decomposes `spec.md` (per-strategy falsification-funnel decisions for the 17 AF30
daily strategies shipped at `67e0253`) into six gated issues: seal the S05 revision
preregistration and its validator, run the S01 benchmark trial, run S05's first trial,
run S05's gated revisions (which may be zero runs), generate the 17 decision records,
and close with the campaign summary and the final verification gate.

Gating order is strict and load-bearing. Issue 1 must land **before** any trial, because
the spec forbids a revision that was not sealed before S05 T1. Issues 2 and 3 are the
only trial spend and must run in that order (S01 first, per the spec). Issue 4 may not
run a single backtest unless issue 1's validator confirms both the sealed configuration
and a failing predecessor axis. Issue 5 reads rows that issues 2–4 create. Issue 6
verifies the whole work unit.

## Findings that shaped the decomposition

- **The trial path evaluates only the validation slice.** `run_spec` slices
  `val_start`…`val_end` = 2023-01-01 → 2025-07-01, 806 bars. Every trial return vector on
  this contract is 806 bars long and `selection_history` raises `SelectionHistoryError`
  on a length mismatch, so a spec that changes the contract window would refuse rather
  than silently pad. No claim may describe a trial as covering 2015–2025.
- **The DSR floor is already mostly spent.** Current `contract_search_state` for
  `MULTI_ASSET_1D_V1`: raw N = 28 (5 trials + 23 selection attempts), 11 vectorized
  streams, 17 unvectorized Stage-1 screens, `k_eff_gate` 2.834, stored noise floor 0.522.
  `compute_selection_report` charges `+0.50` per *unvectorized* attempt only; a trial and
  its grid points all store returns and therefore add streams, not penalty. The 17 screens
  are the fixed cost. Projections of the resulting hurdle are estimates, not campaign
  evidence, and every document that quotes one must label it so.
- **`param_neighbour_stability` is computed and gated by nothing.** `runner.py:382` stores
  `min(grid SR)/max(grid SR)` on the trial row; nothing in `statistical` or `audit-check`
  reads it. The spec's neighbour axis therefore reads the stored ratio *and* the net Sharpe
  of each grid point, attributed to its trial by time window (grid rows carry `run_id=''`
  and are written just before the trial row). No issue may invent a new gate inside
  `src/warden/`.
- **A `FAILED` trial row would satisfy every axis predicate.** The runner's exception
  handler records `stage_reached='FAILED'`, `verdict='FAILED'` with zeroed stress, latency
  and breakeven metrics; `0.0 <= 0.50` and `0.0 <= 0.0` both read as "axis failed". The
  validator refuses any predecessor that is not an observed Stage-3 `REVISE`, and that
  refusal is pinned by a test.
- **S01 exposes no economic parameter.** `s01_buy_hold_single.generate_positions` accepts
  only `warmup: int = 1`; a ±15% perturbation of an integer 1 is undefined (0.85→0, 1.15→1).
  See "Decisions carried by this plan".
- **`audit-check` cannot return APPROVED for a REVISE trial.** Check 2 requires
  `verdict == 'CANDIDATE_FOR_QUALIFICATION'`, so a REVISE trial always audits `REJECTED`.
  That is the audit status, not a strategy falsification, and the spec's criterion 4 makes
  the distinction explicit.
- **Trial budget is enforced before execution and is unrecoverable.**
  `register_trial_start` increments `trials_used` under `BEGIN IMMEDIATE` and refuses
  duplicates; the HYP-2026-068 post-mortem in `.workspace/memory/LESSONS.md` records a
  typo probe permanently consuming a slot. Every trial must be dry-checked against a
  throwaway registry before it touches `registry/registry.db`.
- **The budget is committed before the history check, so post-budget failures burn a slot.**
  `run_spec` calls `register_trial_start` at `runner.py:310` and only then
  `selection_history` at `runner.py:327`; an unaligned-history `SelectionHistoryError` or a
  strategy subprocess error is caught by the outer handler and recorded as a `FAILED` trial
  that already consumed the slot. Pre-budget refusals (Stage-0 gate, `resolve_dataset`,
  panel read, `filter_target_symbols`, duplicate `run_id`, exhausted budget) are the only
  ones Warden makes atomic. Spec criterion 8 is scoped accordingly, and every trial in this
  plan is preflighted on a throwaway registry — including an assertion that the candidate
  return length equals the 806-bar length of the existing 1D streams — before any live
  invocation. No issue may add a rollback to `src/warden/` to make the stronger claim true.
- **`warden backtest` refuses to run when `check-worktree` fails.** Other agents edit this
  checkout. A failing gate blocks the trial and is reported, never worked around.
- **`registry/registry.db` is append-only by trigger.** `trials` and `selection_attempts`
  reject UPDATE and DELETE; `hypotheses.trials_used` cannot decrease and a REJECTED row
  cannot be reactivated. Nothing in this plan may rewrite an existing row.

## Decisions carried by this plan

- **S01 runs with no `param_grid` — an exemption the spec now records.** S01's only knob is
  the integer `warmup`, so a ±15% grid collapses to three identical points: a fake grid that
  would add three `selection_attempts` rows and three streams to the DSR family for no
  evidence. The spec's Constraints and In-scope sections carry the exemption explicitly;
  `param_neighbor_stability` takes the runner's no-grid default of `1.0`, and S01's
  `DECISION.md` states that no neighbour evidence exists because the module exposes no
  economic parameter. S01 has no revisions, so no axis depends on it.
- **S05's grid perturbs `fast` (54/64/74), not `vol_target`.** `vol_target` scales every
  weight linearly, so all three grid points would be the same book at different leverage
  and the stability ratio would be trivially 1.0. `fast` is the mechanism's actual knob.
- **`warmup` stays at 1 in every trial.** It is a lookback mask, not a strategy parameter,
  and moving it would change the evaluated window rather than the rule.
- **The 12 Stage-1 rejects keep `status='ACTIVE'`.** The spec excludes setting `REJECTED`
  (the DB trigger would make it permanent and the funnel wants a new preregistered
  hypothesis instead). Their verdict lives in `DECISION.md`.

### Milestone: `v1.1-carver-daily-decisions - AF30 daily strategies through the falsification funnel`

**Issue 1 - Seal the S05 revision preregistration and its admissibility validator**

Files: `.workspace/af30_campaign/revisions/HYP-2026-099.md` (new),
`scripts/check_revision_prereg.py` (new), `tests/experiments/test_revision_prereg.py` (new).

Write the preregistration before any trial exists. It names exactly two S05 revisions —
`RUN-HYP099-002` and `RUN-HYP099-003` — each with a concrete `parameters` mapping, the
single axis from the closed set it addresses, and one sentence on why that change is
expected to move that axis. The closed set and its numeric failure rules are fixed by
spec criterion 7: `2x_cost_survival` (`stress_2x_cost_sharpe <= 0.50`), `latency_survival`
(`delay_plus1_sharpe <= 0.0`), `breakeven_cost` (`breakeven_cost_bps < 1.0`),
`param_neighbour_stability` (`param_neighbor_stability < 0.50` or any grid point with
annualised net Sharpe ≤ 0, read from that grid point's `selection_attempts` diagnostics).

`scripts/check_revision_prereg.py` parses the file and, for a given trial spec plus
`registry/registry.db`, enforces: the spec's `parameters` match a preregistered revision
exactly; that revision's named axis is actually failing on the immediately preceding
trial row under the numeric rule above; and a first trial (`trial_number_in_hyp == 1`)
requires no axis. It exits non-zero with the offending reason. It reads only; it never
writes the registry and never edits `src/warden/`.

Verification: `uv run python scripts/check_revision_prereg.py` exits 0 against the sealed
file. `uv run pytest tests/experiments/test_revision_prereg.py -q` passes, covering
refusal on an unlisted parameter set, refusal when the named axis is not failing on the
predecessor row, and refusal when the preregistration file is absent. `git log --oneline`
shows this commit before any `RUN-HYP099-*` commit.

Done: the file is committed, the validator is green, and the three refusal paths are
pinned by tests.

**Issue 2 - Run the S01 benchmark trial**

Files: `experiments/active/carver_daily/01_buy_hold_single_t1.yaml` (new).

One trial, one configuration. `run_id: RUN-HYP095-001`, `contract_id: MULTI_ASSET_1D_V1`,
`hypothesis_id: HYP-2026-095`, `strategy_family: rules_ts`, `model_type: rules`,
`strategy_module: strategies/candidates/carver_daily/s01_buy_hold_single.py`,
`parameters: {warmup: 1}`, and no `param_grid` (see "Decisions carried by this plan").
Before it touches the live ledger, exercise the same spec against a throwaway
`ResearchRegistry(tmp_path / "registry.db")` to prove the module resolves, the panel loads,
and the produced net-return vector has the same 806-bar length as the existing
`MULTI_ASSET_1D_V1` streams — the length check is what stops a post-budget
`SelectionHistoryError` from burning the slot. A spec that fails the preflight must not be
run against `registry/registry.db`.

Then `uv run warden verify <spec>` (prefix invariance), `uv run warden backtest <spec>
--json` (prints the JSON payload followed by `WARDEN_RUN_COMPLETE`), and
`uv run warden audit-check --run-id RUN-HYP095-001`.

Verification: `SELECT trials_used FROM hypotheses WHERE hypothesis_id='HYP-2026-095'`
returns 1; exactly one new `trials` row exists for the run with
`stage_reached='STAGE_3_STRESS'` and a verdict in
`('CANDIDATE_FOR_QUALIFICATION','REVISE','NON_PROMOTABLE')`; its
`returns_parquet_path` loads and has 806 rows; `.workspace/hypotheses/AUDIT_RUN-HYP095-001.md`
exists. The backtest output ends with `WARDEN_RUN_COMPLETE`; a `WARDEN_RUN_FAILED` means
the run is reported, not retried with different parameters.

Done: one S01 trial is recorded, audited, and reproducible from its committed spec.

**Issue 3 - Run the S05 baseline trial**

Files: `experiments/active/carver_daily/05_slow_trend_long_only_t1.yaml` (new).

`run_id: RUN-HYP099-001`, `hypothesis_id: HYP-2026-099`, module
`strategies/candidates/carver_daily/s05_slow_trend_long_only.py`,
`parameters: {fast: 64, slow: 256, vol_span: 35, vol_target: 0.16, per_instrument_cap: 0.5,
warmup: 1}`, and a 3-point grid over `fast` at 54 / 64 / 74 with every other parameter
held at its registered value.

Depends on issue 1 (preregistration committed) and issue 2 (S01 ran first). Run the same
throwaway-registry dry check, then `warden verify`, `warden backtest --json`, and
`audit-check --run-id RUN-HYP099-001`.

Verification: `trials_used` for `HYP-2026-099` is 1; one new `trials` row with an 806-row
returns parquet; three new `param_grid` `selection_attempts` rows for the hypothesis;
`.workspace/hypotheses/AUDIT_RUN-HYP099-001.md` exists; the output ends with
`WARDEN_RUN_COMPLETE`.

Done: the S05 baseline is recorded with neighbour evidence, and the predecessor row that
issue 4 gates on exists.

**Issue 4 - Run S05 revisions only when an admissible axis fails**

Files: `experiments/active/carver_daily/05_slow_trend_long_only_t2.yaml`,
`experiments/active/carver_daily/05_slow_trend_long_only_t3.yaml` — created only if
admissible.

This issue may legitimately produce **zero** runs. For `RUN-HYP099-002`: run
`uv run python scripts/check_revision_prereg.py` against the T2 spec and the T1 row. It
passes only if T2's parameters are sealed in `.workspace/af30_campaign/revisions/HYP-2026-099.md`
and T1 fails the axis T2 addresses under spec criterion 7's numeric rule. If it exits
non-zero, T2 is not run and the unspent budget is recorded as a terminal state.

T3 is strictly sequential: it may run only after T2 ran and failed a different
preregistered axis. A sealed revision must run as its own trial number, so T3 cannot skip
an unrun T2 — if T2 did not run, T3 does not run and the remaining budget is recorded
unspent. The validator enforces this by requiring `run_id` index == next trial number.

Never re-tune a configuration that is not in the preregistration file, never lower a
threshold to make a revision admissible, and never run a fourth trial (the registry refuses
it anyway).

Verification: for each of T2/T3, either a `trials` row plus `AUDIT_<run_id>.md` exists, or
the decision record for `HYP-2026-099` states which axis was checked and why no run was
made. In every case `trials_used` for `HYP-2026-099` is ≤ 3, the validator exited 0 before
any run that did happen, and `uv run python scripts/check_revision_prereg.py` still exits 0
on the final state.

Done: S05's budget is either spent on admissible revisions or explicitly unspent, and the
reason is recorded in a form that a reader can re-check.

**Issue 5 - Generate the 17 decision records**

Files: `scripts/build_carver_daily_decisions.py` (new),
`tests/experiments/test_carver_daily_decisions.py` (new),
`.workspace/af30_campaign/decision/<HYP_ID>/DECISION.md` ×17 (generated).

The script reads `registry/registry.db` plus `.workspace/hypotheses/AUDIT_*.md` and writes
one record per hypothesis `HYP-2026-095` … `HYP-2026-111`. Each record carries: the
verdict; the binding gate; the evidence pointer (`run_id` + trial fields, or the
`attempt_id` + `outcome` + profit factor + conditional edge + bootstrap p of the existing
Stage-1 row); the audit status where a trial exists; and the disclosure block. The
disclosure states that the registered falsification criteria are Stage-1-only and were
met, that the Stage-3 verdict is `REVISE` / `NOT QUALIFIED AT STAGE 3`, that the audit
`REJECTED` is an audit status and not a falsification of the mechanism, and the selection
state the run was judged under (unvectorized attempts, `k_eff_gate`, noise floor, implied
NetSR for DSR ≥ 0.90 — labelled an estimate where projected).

The script fails closed: a hypothesis with neither a trial nor a `standalone_screen` row
aborts the run with its ID, and no partial set of files is left behind. Output is
deterministic — rerunning on unchanged registry content produces byte-identical files.

Verification: `uv run python scripts/build_carver_daily_decisions.py` exits 0;
`ls .workspace/af30_campaign/decision/*/DECISION.md | wc -l` returns 17; grep finds no
occurrence of "falsified" in any record; the two trialled records name their audit status
and their selection state. `uv run pytest tests/experiments/test_carver_daily_decisions.py -q`
passes, covering the missing-row abort, the atomic-write behaviour, and byte-identical
regeneration.

Done: 17 records exist, generated rather than hand-written, and their content is pinned by
tests.

**Issue 6 - Campaign summary and final verification gate**

Files: `.workspace/af30_campaign/carver_daily_decision_report.md` (new),
`.workspace/memory/LESSONS.md` (appended only if a budget was exhausted or a protocol
deviation occurred).

The report states: how many of the 17 are qualified (whatever the trials produced — no
outcome is assumed), the trial spend per strategy, the binding gate for each non-qualified
strategy, the total attempts added to `MULTI_ASSET_1D_V1` by this work unit, and the
disclosure that no production holdout was read. If any S05 revision ran, the report names
the axis and the failing predecessor value; if none ran, it says so and says why.

Verification: `uv run pytest -q` passes with zero failures; `uv run warden check-worktree`
exits 0; `git diff --name-only $(git merge-base origin/main HEAD)...HEAD -- src/warden
contracts data` is empty, i.e. this branch adds nothing to the protected paths — the
working tree may carry unrelated uncommitted changes from other agents, which are
reported rather than cleaned; the report contains a qualified count and a per-strategy
gate line for all 17; `SELECT SUM(trials_used) FROM
hypotheses WHERE hypothesis_id BETWEEN 'HYP-2026-095' AND 'HYP-2026-111'` equals the number
of trials the report claims and is ≤ 4.

Done: the campaign is closed with a single readable summary whose numbers match the
registry, and the governance gates are green.
