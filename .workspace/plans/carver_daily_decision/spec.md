# Spec: AF30 daily strategy suite — per-strategy falsification-funnel decisions

> ALIGN artifact. WHAT/WHY end-state — never HOW. Durable contract; the
> `decompose` step decomposes this into milestones/issues without asking the user
> anything. Every section must be verifiable. No TODO/??? placeholders.

## Objective

Every one of the 17 AF30 daily strategies shipped at `67e0253` (`HYP-2026-095` …
`HYP-2026-111`) has a frozen per-strategy decision record, produced by spending at
most **4** Stage-3 trials on `MULTI_ASSET_1D_V1` — `HYP-2026-095` (S01) exactly 1,
`HYP-2026-099` (S05) at most 3 — and by citing the already-recorded Stage-1 row for
the other 15.

## Why

The suite shipped with 17 registered hypotheses, 17 Stage-1 screen rows and **zero**
trials. In that state the campaign is indistinguishable from unfinished work: nothing
says whether S01–S05 are dead, alive, or untested past Stage 1, and `contract_search_state`
keeps accumulating a multiple-testing floor that any future run must pay. The decision
has to be recorded now, against the evidence that already exists, while the return
streams and screen rows are still the same objects they were when the screens ran.

## Acceptance criteria

1. **Trial spend.** `SELECT hypothesis_id, trials_used FROM hypotheses WHERE hypothesis_id IN
   ('HYP-2026-095','HYP-2026-099')` returns exactly `1` for HYP-2026-095 and `1..3` for
   HYP-2026-099. The number of `trials` rows added on `MULTI_ASSET_1D_V1` by this work unit is
   ≤ 4 and equals the sum of the two `trials_used` values.
2. **No unplanned trials.** `SELECT SUM(trials_used) FROM hypotheses WHERE hypothesis_id BETWEEN
   'HYP-2026-096' AND 'HYP-2026-098' OR hypothesis_id BETWEEN 'HYP-2026-100' AND 'HYP-2026-111'`
   is `0`. Their 15 `standalone_screen` rows are unchanged in count and content.
3. **Every trial row is complete.** For each new run: `trials.stage_reached='STAGE_3_STRESS'`,
   `verdict IN ('CANDIDATE_FOR_QUALIFICATION','REVISE','NON_PROMOTABLE')`,
   `returns_parquet_path` names a loadable parquet of the same length as the prior 1D streams
   (806 bars), and `trial_number_in_hyp` is 1,2,3 in order.
4. **Audit evidence exists.** `warden audit-check --run-id <RUN_ID>` was executed for every new
   run and `.workspace/hypotheses/AUDIT_<RUN_ID>.md` exists for each. Its `verdict` field is
   copied verbatim into that strategy's `DECISION.md` **as the audit status**. The audit verdict
   is never presented as the strategy verdict: the strategy verdict stays `REVISE` /
   `NOT QUALIFIED AT STAGE 3`, and an audit `REJECTED` is not a falsification of the mechanism.
5. **Seventeen decision records.** `.workspace/af30_campaign/decision/<HYP_ID>/DECISION.md`
   exists for all of `HYP-2026-095` … `HYP-2026-111` (17 files). Each states: the verdict, the
   binding gate, and the evidence pointer. The 15 non-trialled records cite their existing
   `selection_attempts` row (`attempt_id`, outcome, PF, conditional edge, bootstrap p) and state
   that no new computation was performed for them.
6. **Preregistration predates the run.** `.workspace/af30_campaign/revisions/HYP-2026-099.md`
   exists, names both candidate S05 revisions and the failure axis each addresses, and the commit
   that introduced it is an ancestor of the commit state in which S05's first trial ran
   (`git log --oneline` ordering is sufficient proof).
7. **Revisions are gated, not tuned.** Every S05 trial after the first has, in that strategy's
   `DECISION.md`, a named failure axis from the closed set below, with the failing field value
   from the immediately preceding trial row. A trial whose predecessor failed on none of these
   fields must not exist. A trial whose configuration is not in the preregistration file must
   not exist. The axes and their objective failure rules:
   - `2x_cost_survival` — `stress_2x_cost_sharpe <= 0.50`.
   - `latency_survival` — `delay_plus1_sharpe <= 0.0`.
   - `breakeven_cost` — `breakeven_cost_bps < 1.0` (the contract's realised one-way total:
     0.5 bps taker + 0.5 bps slippage).
   - `param_neighbour_stability` — `param_neighbor_stability < 0.50`, or any `param_grid`
     point with annualised net Sharpe ≤ 0 (read from that grid point's `selection_attempts`
     diagnostics, not recomputed by hand).
8. **Atomicity in error paths.** Refusals raised **before** `register_trial_start` — unknown
   hypothesis, `strategy_family` mismatch, budget exhausted, duplicate `run_id`, Stage-0
   feasibility, unresolvable dataset, unreadable panel — leave `trials_used` unchanged and
   insert no `trials` row. Failures raised **after** the budget is committed (unaligned
   `selection_history`, strategy subprocess error) do consume the slot and are recorded as a
   `FAILED` trial; Warden does not roll that back and this work unit does not modify Warden.
   Therefore every trial is preflighted against a throwaway registry before any live
   invocation — the preflight proves the module resolves, the panel loads, and the candidate
   return length equals the length of the existing 1D streams (806 bars) — and a failing
   preflight means no live invocation. No half-applied state: `trials_used` is incremented
   exactly once per `run_id`, under `BEGIN IMMEDIATE`.
9. **Governance intact.** `uv run warden check-worktree` exits 0; the branch itself adds no
   change to `src/warden/`, `contracts/`, or `data/` —
   `git diff --name-only $(git merge-base origin/main HEAD)...HEAD -- src/warden contracts data`
   is empty. The working tree may carry unrelated uncommitted changes from other agents; those
   are neither created nor relied on by this work unit and are reported, not cleaned. No
   invocation passed `--unseal-holdout`; `data/holdout/` is untouched.
10. **No overclaiming.** No `DECISION.md` uses the word "falsified" for a strategy that passed
    Stage 1. Each trialled record states both that its registered falsification criteria are
    Stage-1-only and were met, and that the Stage-3 verdict is `REVISE` / `NOT QUALIFIED AT
    STAGE 3`. Each trialled record also carries the DSR-constraint disclosure (criterion 11).
11. **Hurdle disclosed, not hidden.** Each trialled `DECISION.md` states the selection state it
    was judged under: unvectorized attempts = 17 (the Stage-1 screens, excluded from
    `selection_history`), `k_eff_gate` and noise floor as computed on that run, and the NetSR
    implied for DSR ≥ 0.90. Any projection quoted is labelled an estimate.

## In scope

- Writing and running `experiments/active/carver_daily/` Stage-3 trial specs for S01 (1 run) and
  S05 (1–3 runs), each with a 3-point ±15% `param_grid` — except S01, which is exempt because
  the module exposes no continuous parameter (see Constraints).
- `uv run warden backtest`, `uv run warden audit-check` per run.
- Preregistering S05's two candidate revisions before its first trial.
- 17 `DECISION.md` records under `.workspace/af30_campaign/decision/`.
- A campaign summary in `.workspace/af30_campaign/` stating how many of the 17 are qualified
  and why. No outcome is assumed in advance; the count is whatever the trials produce.
- Disclosures appended to `.workspace/memory/LESSONS.md` if any budget is exhausted.

## Out of scope

- **Production holdout.** `data/holdout/`, `warden qualify --unseal-holdout`, and any unsealing
  are excluded. A `CANDIDATE_FOR_QUALIFICATION` verdict does **not** get promoted here.
- Trials for `HYP-2026-096/097/098` (S02–S04, nested buy-and-hold variants) — 0 trials each.
- Trials for the 12 Stage-1 rejections (`HYP-2026-100`…`HYP-2026-111`). Revisiting any of them
  requires a **new** preregistered hypothesis with a different mechanism, never a re-run of the
  falsified ID.
- Any change under `src/warden/`, `contracts/`, `data/`, or `strategies/candidates/carver_daily/`.
- The 6 carry strategies (S10, S11, S13, S15, S16, S20) and S26/S27.
- Changing any registered hypothesis YAML, or re-registering an ID to reset its budget.
- Setting `hypotheses.status='REJECTED'` for the 12 Stage-1 rejects. They stay `ACTIVE` with
  `trials_used=0`; the decision record carries the verdict, not the status column.

## Constraints

- **Trial ceiling:** 4 Stage-3 trials total. Unspent budget is a legitimate terminal state and
  must be recorded as such rather than spent to "complete" a strategy.
- **Grid scope:** every trial carries a 3-point ±15% `param_grid` except the S01 benchmark.
  `s01_buy_hold_single.generate_positions` accepts only `warmup: int = 1`, so a ±15%
  perturbation collapses to three identical points and would add three `selection_attempts`
  rows and three streams to the DSR family for zero evidence. S01 runs grid-free;
  `param_neighbor_stability` takes the runner's no-grid default `1.0`, and its `DECISION.md`
  states that no neighbour evidence exists. S05's grid perturbs `fast` at 54 / 64 / 74, not
  `vol_target` (a linear scalar, which would make every grid point the same book at different
  leverage).
- **Evidence window:** `MULTI_ASSET_1D_V1` evaluates only `val_start`…`val_end`
  (2023-01-01 → 2025-07-01, 806 bars, 312 bars/year). 2015–2022 is not in the trial return
  vector; no claim may describe a trial as covering 2015–2025.
- **Cost model:** 0.5 bps taker + 0.5 bps slippage one-way, `execution_delay_bars=1`,
  NEXT_BAR. 2x-cost and +1-bar stress are computed by the runner, not by hand.
- **Selection accounting is append-only and monotone.** `contract_search_state` only ever raises
  `max_keff_gate` / `max_noise`; `trials` and `selection_attempts` are immutable by trigger.
  Current state at spec time: raw N = 28 (5 trials + 23 attempts), 11 vectorized streams,
  17 unvectorized screens, `k_eff_gate` 2.834, stored noise floor 0.522.
- **Registry:** `registry/registry.db` is the live append-only ledger and is gitignored.
  Hypotheses YAMLs are committed under `.workspace/hypotheses/registered/` and immutable; a
  changed spec is refused by the registry.
- **No probe may touch the live registry.** Debug/probe runs use a throwaway registry
  (`.workspace/memory/LESSONS.md`, HYP-2026-068 post-mortem).
- **Worktree gate:** `warden backtest` refuses to run when `check-worktree` fails. Other agents
  currently edit this checkout; a trial may not proceed on a failing gate, and the failure is
  reported, not worked around.

## Environment / prior art

- Delivered contract: `.workspace/plans/carver_daily_rebuild/spec.md` (17 modules, Stage-1 only,
  zero trials by design).
- Stage-1 evidence: `.workspace/af30_campaign/carver_daily_screens.json` (17 records) and
  `carver_daily_screens_report.md`.
- Registered hypotheses: `.workspace/hypotheses/registered/HYP-2026-095.yaml` … `111.yaml`.
  All 17 falsification criteria are Stage-1-only (`profit factor < 1.15`, `time in market < 5%`,
  `trade edge <= 1.5x one-way cost`, `bootstrap p >= 0.05`).
- Screen specs: `experiments/active/carver_daily/01_buy_hold_single_screen.yaml`,
  `05_slow_trend_long_only_screen.yaml` — the templates a trial spec extends.
- Prior 1D trial precedent: `experiments/active/hyp_2026_092_t1.yaml`, `hyp_2026_094_t1.yaml`
  (run_id in the YAML, explicit parameters, 3-point `param_grid`).
- Runner gates: `src/warden/runner.py:404` — `net_sharpe > 1.0`, `2xSR > 0.50`, `delay+1 SR > 0`,
  `DSR >= 0.90`, no `non_promotable_reason`, Stage-1 rules re-check on the same simulation.
- Audit gates: `src/warden/cli.py:209` — six checks; check 2 requires
  `verdict == CANDIDATE_FOR_QUALIFICATION`, check 6 requires DSR ≥ 0.80.
- Cost-gate reference: `AGENTS.md` funnel step 0 (holding-period volatility ≥ 2.5× round-trip).
- Budget mechanics: `PER_HYPOTHESIS_CAP = 3` and a lineage cap, enforced in
  `ResearchRegistry.register_trial_start`.

## Existing surface (must not break)

- `registry/registry.db` — all existing `trials` / `selection_attempts` rows stay byte-identical;
  the 17 screen rows and the 5 prior 1D trials are cited, never rewritten.
- `strategies/candidates/carver_daily/*.py` — 17 modules unchanged. A trial that needs different
  behaviour must express it through `parameters`, not by editing a module.
- `experiments/active/carver_daily/*_screen.yaml` — untouched; trial specs are new files.
- `contracts/multi_asset_1d_v1.yaml` and its dataset manifests — unchanged.
- The 236-test suite: `uv run pytest -q` must pass, and any new test must be added only for
  behaviour this work unit introduces.
- `uv run warden check-worktree` exits 0 and `src/warden/` stays within its LOC budget.

## Open questions

- **Deferred — does not block planning.** Whether `param_neighbour_stability` (computed and
  stored, gated by nothing) should become a real gate is a contract decision owned by the human
  principal; this work unit only *reads* the stored value to decide revision admissibility.
- **Deferred — does not block planning.** Whether the 17 Stage-1 screens should be counted as
  unvectorized attempts at +0.50 `k_eff` each (they inflate every future 1D hurdle) is a
  DSR-accounting policy question for the contract owner. This work unit records the effect, it
  does not change it.
