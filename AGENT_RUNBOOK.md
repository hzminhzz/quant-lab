# Agent runbook — quant-lab thesis intake

One page. `AGENTS.md` states the invariants; this file is the procedure. When the two disagree,
`AGENTS.md` wins and this file is wrong — say so instead of guessing.

Running a whole strategy set through the funnel? The copy-paste brief for a new session is
`STAGE3_CAMPAIGN_PROMPT.md`.

## Intake and admission (#109 / #119)

Use [.workspace/templates/hypothesis.md](.workspace/templates/hypothesis.md) for
each new thesis, and the applicable ML4T methodology. The approved intent and
actual Warden admission, not a completed Markdown checklist, authorize evaluation.

| # | Do | Command / artifact | Done when |
|---|---|---|---|
| 0 | Gate check | `uv run warden check-worktree` | rc=0; otherwise stop for human manifest approval |
| 1 | Term sheet | original source, prior-art/lessons search, draft hypothesis, semantic DataRequirement | every material rule is sourced or an explicit proposed choice; novelty, timing, capacity, purpose, evaluation and bounded search are resolved |
| 2 | Resolve | `uv run warden resolve-data --requirement requirement.yaml --json`; see [DataRequirement](docs/DATA-REQUIREMENT.md) | reusable signed contract or properly compiled sandbox, matching dataset identity, declared universe and clock; run `uv run python scripts/preflight_thesis.py --contract <CONTRACT_ID>` for the read-only state card (0 proceed, 2 human re-sign, 3 stop) |
| 3 | Register and approve intent | `uv run warden register-hypothesis .workspace/hypotheses/<ID>.md --json`; RG-05 principal approval below | approved digest binds the complete term sheet, registered hypothesis, data, evaluation, allowed revisions and benchmark when required, before candidate code or outcomes |
| 4 | Implement and review | native causal protocol, experiment YAML, `warden describe-candidate`, independent conformance and principal recording | exact candidate binding reviewed and recorded as PASS; `warden verify` passes sampled causality checks |
| 5 | Screen | `uv run warden screen <spec> --json` | family gate passes or failure is recorded; frozen candidate admitted before scoring |
| 6 | Trial | `uv run warden backtest <spec> --json` | same frozen candidate advances once; durable verdict and robustness/comparative evidence; stop on decisive failure or exhausted budget |
| 7 | Audit / qualify | `uv run warden audit-check --run-id <ID> --json` | audit recorded; qualification separately human-authorized, subject to all holdout gates |

### Freeze and review

Use the existing plan and review schemas and exact commands in
[Research Governance, RG-05/RG-06](docs/RESEARCH-GOVERNANCE.md#rg-05-frozen-research-intent-and-independent-conformance-124).
The Markdown registration parser consumes only front matter: place the entire
finalized term sheet text in the plan's `thesis` string (not a filename or
summary). Embed the semantic `data_requirement` used for resolution in the plan;
use `purpose: absolute` or `purpose: incremental` and named `allowed_revisions`.
Incremental mode requires a frozen executable benchmark spec and the approved
RG-06 comparative policy. Resolve missing benchmark prerequisites before
approving intent; never choose a comparator after seeing candidate outcomes.

The principal approves the plan; the author writes candidate code only after
approval and copies the returned `research_intent_sha256` to the experiment
spec. An independent reviewer assesses the exact described binding against the
approved thesis. Include the RG-07 novelty disposition against the `prior_memory` snapshot returned by
`describe-candidate`, as specified in [Research Governance](docs/RESEARCH-GOVERNANCE.md).
The principal records that review. A missing, refused, stale or unavailable review is not a PASS. Material revisions require the approved
revision key, predecessor and a new binding/review; a changed plan requires a
related hypothesis, not an overwritten approval.

Use `scripts/preflight_trial.py` for metadata-only checks and
`warden spec-status <spec> --json` before scheduling. Neither grants scoring
permission. `warden advance` may resume eligible frozen work up to audit;
it cannot substitute for intent approval or conformance. Identical screen and
full evaluation share one candidate slot; fixed robustness scenarios are
recorded separately. Budget is a ceiling, not a target. Preserve failed and
interrupted admissions. Record terminal dispositions through RG-07's principal-authorized
`close-hypothesis` path where applicable. SQLite is authoritative. See RG-03/RG-04 for the
candidate/lineage limits and accounting policy.

## Promoting a compiled sandbox contract (human only, #118)

When an exploratory artifact compiled by `warden data compile` should become a production
**candidate**, the human — never an agent — runs:

```bash
uv run warden promote-contract --contract-id SANDBOX_<CLASS>_<FREQ>_<DIGEST> \
  --human-confirm-promote YES_PROMOTE --json
uv run warden generate-manifest --human-approve-manifest   # you must re-sign after it
uv run warden check-worktree                              # expect rc=0
```

It re-validates the compiled artifact in the order `warden.compile.ARTIFACT_CONTRACT`
states (paths → bytes → raw Merkle root → spec/contract identity → policy → fence → lake
provenance → namespace), refuses atomically with a named reason (`not_authorized`,
`tampered_parquet`, `mismatched_merkle`, `stale_policy`, `already_promoted`, …), copies the
contract and dataset manifest with identical bytes, and writes
`contracts/promotions/<CONTRACT_ID>.json`. It **never** signs, never generates the manifest,
never writes an admission attestation and never opens a holdout — so a promoted contract is
still `STAGED` and not market-eligible until you sign the inventory and attest admission.
Refused under `WARDEN_NON_INTERACTIVE=1`. Full procedure, refusal matrix and exit codes:
`docs/SANDBOX-PROMOTION.md`.

Paste the verbatim command output into the handoff. A summarized number is not evidence.

## Rules that exist because they were already violated once

1. **The source is the clipping, never a document about it.** A plan summary can be wrong: the
   path-signature plan reduced seven defined signals to four and mislabelled two. Cite
   `raw/clippings/...:line`.
2. **Resolve files through the contract's own key.** `CRYPTO_PERPS_1H_V1` and `_V2` pin different
   Parquets (`crypto_perps_1h.parquet` vs `crypto_perps_1h_v2.parquet`). Never infer a file from a
   contract name or read a neighbouring one. `resolve_dataset()` verifies the sha for you.
3. **Do not recompute a gate threshold by hand.** Read the conjunct from `src/warden/runner.py` and
   `qualify.py`, or measure with the repo's own function. DSR lives in per-bar units inside
   `compute_selection_report`; hand arithmetic in annualised units overstates or understates it.
4. **Absence needs a search, not an impression.** Write "not found by <query over which fields>";
   never "there is none".
5. **`verdict=REVISE` records `failed_gates` in `diagnostics_json`.** Inspect
   `diagnostics_json["failed_gates"]` (e.g., `net_sharpe`, `dsr`, `cost_2x`, `latency_plus1`,
   `neighbor_stability`, `trimmed_sharpe`, `stage1_rules`, `stage1_carry_rv`, `stage1_meta_overlay`,
   `ic_hac`) directly instead of guessing from recorded columns.
6. **A clean contract floor is not a reason to choose a contract.** Frequency, horizon and
   contract are the principal's decisions; choosing them to dodge a DSR floor is gate selection.
7. **Tests must not write the live ledger.** `run_spec` appends a `failed_reservation` selection
   attempt even when it is expected to raise, which permanently raises that contract's DSR penalty.
   Any test that calls `run_spec`/`run_stage1_screen` against `repo_root="."` must pass a temporary
   registry copy instead. This was real: `test_hyp_2026_001.py` wrote into `registry.db` on every
   suite run; it now uses a copy, and `selection_attempts` is unchanged by the run.
8. **`ExperimentRunner()` and `ResearchRegistry()` are not read-only.** Their constructors create
   schema and insert quarantine/migration rows (`src/warden/registry.py`). For a status read use
   `scripts/preflight_thesis.py` (SQLite `mode=ro`); a temporary copy is for tests only, and a copy
   taken without `registry.db-wal` is stale because the WAL holds recent commits.
9. **A strategy's code identity is its full closure, and qualification binds to it.** `code_closure`
   covers all imports plus any runtime-loaded code a module declares in `CODE_DEPENDENCIES`. A new
   strategy that loads code at runtime or from a subdir must declare `CODE_DEPENDENCIES`, or its edits
   won't flip `strategy_code_sha256` and the binding silently under-covers. `qualify` refuses a trial
   whose module is missing or whose closure no longer matches the recorded digest — resolve through
   the governed candidate/revision path; never replay spent evidence to re-bind. Register contracts only via `register_contract` (it refuses
   a changed contract); never raw-write the `contracts` table.

## What the harness does NOT enforce (declare it yourself, or write `[MISSING]`)

| Required by protocol | Reality in code |
|---|---|
| 1.5× cost stress | computed analytically from the `ml4t.backtest.Engine` result (`analytical_1p5x_sharpe`, stored as `stress_1p5_cost_sharpe`); `2.0×` cost stress (`cost_multiplier=2.0`) is a full Engine re-simulation and hard gate (`cost_2x`) |
| ±15% parameter neighbours | only the `param_grid` entries you supply; a strategy whose `params_model` declares numeric fields (or that supplies numeric values without a model) needs **≥2 distinct** entries that each change a number from the effective baseline, else Stage 3 records `neighbor_stability` and `REVISE`; an empty grid records `neighbor_tested=False`, and a zero-numeric-field model is the only `neighbor_exemption="parameter_free"` pass |
| Stage 0 economics | runs in `_prepare_and_check_stage0` (`src/warden/runner.py`) at screen/backtest time, not at registration, using empirical holding-period volatility from the panel (`gates.empirical_holding_volatility`) |
| prefix-invariance as proof | adversarial sampling at 3 cut points (`src/warden/leakage.py::verify_prefix_invariance`, 1/4, 1/2, 3/4), documented as not a proof |

## Never

`sudo` · edit `contracts/` · edit `src/warden/` (protected by `.warden_manifest.sha256` and AST import
boundary checks in `warden check-worktree`; `scripts/check_loc_budget.py` is retired) · lower or
reinterpret a gate · pre-lag a signal (`signal_delay_bars>0` raises `DoubleLatencyError`) · read
`data/holdout/` — and never `open()`, `read()` or `stat()` it to "test permissions"; a permission
check may only look at the parent directory's mode bits · run `qualify` without an explicit human
instruction · sign the manifest · delete rows from `registry.db` (append-only triggers) ·
reset/stash/clean unrelated dirty work.

Security note: `data/holdout` carries an ACL entry `user:quant:--x`, so this account can *traverse*
the directory even though it cannot list it. Isolation therefore rests on the file modes, not the
directory. Do not test that, and do not cite it as evidence that the data is or is not sealed.

## Escalation

| Situation | Action |
|---|---|
| `check-worktree` rc≠0 | output `uv run warden generate-manifest --human-approve-manifest` for the human; never run it yourself — it records `approver: human-operator` |
| source rule missing | mark `[MISSING]`; preregister an explicitly justified adaptation or stop if unresolved |
| candidate or lineage budget exhausted | post-mortem in `.workspace/memory/LESSONS.md`; a new ID does not reset the budget |
| gate fails | record it; the failure is evidence, not permission to adjust |
| contract/data mismatch | resolve through #109; stop for any required human policy/promotion decision |

## Where things live

`experiments/active/<spec>.yaml` · `strategies/candidates/<module>.py` ·
`.workspace/hypotheses/<ID>.md` · `registry/registry.db` (trials, attempts) ·
`registry/returns/<contract>/<run>.parquet` · `registry/backtest_results/<simulation_epoch>/<contract>/<run_id>` ·
`cache/features/` · `.workspace/memory/LESSONS.md` ·
audits in `.workspace/hypotheses/AUDIT_<RUN_ID>.md` · templates in `.workspace/templates/` ·
cron entrypoint `scripts/run_hourly_pipeline.sh` (`warden advance --pending`).
