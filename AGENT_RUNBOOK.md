# Agent runbook — quant-lab thesis intake

One page. `AGENTS.md` states the invariants; this file is the procedure. When the two disagree,
`AGENTS.md` wins and this file is wrong — say so instead of guessing.

Running a whole strategy set through the funnel? The copy-paste brief for a new session is
`STAGE3_CAMPAIGN_PROMPT.md`.

Start every thesis with:

```bash
uv run python scripts/preflight_thesis.py --contract <CONTRACT_ID>   # state card + next legal command
```
Preflight exit codes: `0 proceed · 2 human must re-sign the manifest · 3 stop`.

## The seven steps, with the evidence each step must produce

| # | Do | Command | Done when |
|---|---|---|---|
| 0 | Gate check | `uv run warden check-worktree` | rc=0. If not, **STOP** — only the human re-signs (see Escalation) |
| 1 | Resolve the contract | `contracts/<id>.yaml` → its `exploratory_data_path` | you have the pinned path, sha match, span, assets, cost per side, `label_horizon_bars`, `execution_mode` |
| 2 | Source trace | read the **original** clipping, not a plan or summary | every rule carries `file:line`; every absent rule is written `[MISSING]` |
| 3 | Pre-register | `uv run warden register-hypothesis .workspace/hypotheses/<ID>.md --json` | mechanism, counterparty, falsification and `expected_holding_bars` are all stated |
| 4 | Pipeline | `uv run warden verify experiments/active/<spec>.yaml --json` | `status: PASS`; feature cache written (proves causal protocol: `CausalModelStrategy` or `CausalRulesStrategy`; legacy `generate_positions` is frozen for historical replay only) |
| 5 | Screen | `uv run warden screen <spec> --json` | family gate passes, or the hypothesis is done |
| 6 | Trial | `uv run warden backtest <spec> --json` | `WARDEN_RUN_COMPLETE` and a verdict; max 3 trials, ever |
| 7 | Audit / qualify | `uv run warden audit-check --run-id <ID> --json` | `AUDIT_<ID>.md`; `qualify` is human-only |

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
5. **`verdict=REVISE` does not name the failing gate.** Attribute it from the recorded columns, and
   say which conjuncts you could not attribute.
6. **A clean contract floor is not a reason to choose a contract.** Frequency, horizon and
   contract are the principal's decisions; choosing them to dodge a DSR floor is gate selection.
7. **Tests must not write the live ledger.** `run_spec` appends a `failed_reservation` selection
   attempt even when it is expected to raise, which permanently raises that contract's DSR penalty.
   Any test that calls `run_spec`/`run_stage1_screen` against `repo_root="."` must pass a temporary
   registry copy instead. This was real: `test_hyp_2026_001.py` wrote into `registry.db` on every
   suite run; it now uses a copy, and `selection_attempts` is unchanged by the run.
8. **`ExperimentRunner()` and `ResearchRegistry()` are not read-only.** Their constructors create
   schema and insert quarantine/migration rows (`registry.py:149-198`). For a status read use
   `scripts/preflight_thesis.py` (SQLite `mode=ro`); a temporary copy is for tests only, and a copy
   taken without `registry.db-wal` is stale because the WAL holds recent commits.
9. **A strategy's code identity is its full closure, and qualification binds to it.** `code_closure`
   covers all imports plus any runtime-loaded code a module declares in `CODE_DEPENDENCIES`. A new
   strategy that loads code at runtime or from a subdir must declare `CODE_DEPENDENCIES`, or its edits
   won't flip `strategy_code_sha256` and the binding silently under-covers. `qualify` refuses a trial
   whose module is missing or whose closure no longer matches the recorded digest — re-run the trial to
   re-bind; it never substitutes a baseline. Register contracts only via `register_contract` (it refuses
   a changed contract); never raw-write the `contracts` table.

## What the harness does NOT enforce (declare it yourself, or write `[MISSING]`)

| Required by protocol | Reality in code |
|---|---|
| 1.5× cost stress | not run; only `cost_multiplier=2.0` (`runner.py:359-362`) |
| ±15% parameter neighbours | only the `param_grid` entries you supply; empty grid records `stability=1.0` |
| Stage 0 economics | runs at `runner.py:227,292`, not at registration, on a hard-coded `0.0085·√holding_bars` proxy |
| prefix-invariance as proof | adversarial sampling at 3 cut points (`leakage.py:85-108`), documented as not a proof |

## Never

`sudo` · edit `contracts/` · edit `src/warden/` (budget is nearly exhausted — check
`scripts/check_loc_budget.py`) · lower or
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
| source rule missing | stop, name the gap, do not invent a parameter |
| trial budget exhausted (3) | post-mortem in `.workspace/memory/LESSONS.md`; a new ID does not reset the budget |
| gate fails | record it; the failure is evidence, not permission to adjust |
| contract/data mismatch | stop; contract changes need the human |

## Where things live

`experiments/active/<spec>.yaml` · `strategies/candidates/<module>.py` ·
`.workspace/hypotheses/<ID>.md` · `registry/registry.db` (trials, attempts) ·
`registry/returns/<contract>/<run>.parquet` · `cache/features/` · `.workspace/memory/LESSONS.md` ·
audits in `.workspace/hypotheses/AUDIT_<RUN_ID>.md` · templates in `.workspace/templates/`.
