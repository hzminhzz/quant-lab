# Make audit check 3 mean something for rules strategies

Status: **proposal, not applied.** `src/warden/` untouched.

## Problem

`cli.py:218` decides check 3 from one query:

```python
f_row = conn.execute("SELECT prefix_invariance_passed FROM feature_sets "
                     "WHERE contract_id = ? AND prefix_invariance_passed = 1",
                     (trial["contract_id"],)).fetchone()
has_causal_proof = f_row is not None
```

That is evidence about the *contract*, not about the strategy under audit, and it is written only by
the ML feature path (`leakage.py:178`). Two consequences, both observed:

- Fifteen `rules_ts` runs pass check 3 incidentally, because an unrelated ML run left a row on the
  same contract (`RUN-HYP007-002`, `091-001/002`, `092-001/002/003`, `093-001/002`, `094-001/002`,
  `095-001`, `099-001`, `007-003`).
- `INDICES_1D_V1` has no row, so `RUN-HYP112-002`, `113-002` and `114-002` cannot pass, even though
  the strategies are prefix-invariant — `tests/experiments/test_stage3_causality.py` samples that at
  five cut points per run, three inside the validation window, on each run's recorded config.

The check therefore measures the wrong thing and decides by accident.

## Proposal

Make the proof **strategy-bound and code-bound**, and stop accepting contract-level evidence.

### What the registry can bind today

`trials` stores `strategy_module` (a path), `config_hash` and `data_snapshot_sha256`. It stores **no
module digest**, so no stored trial can prove which bytes of its strategy ran. This is not
hypothetical: `strategies/candidates/carver_breakout_daily.py` is `e0b71a2b…` today and was
`047ddc1d…` when `RUN-HYP094-002` ran, after the trailing-stop fix. Re-running verification against
today's module would certify code that did not produce the trial.

### Changes

1. `trials.strategy_module_sha256` — written at trial time from the module file.
2. `causality_evidence` — one row per verification:

```sql
CREATE TABLE IF NOT EXISTS causality_evidence (
    run_id TEXT PRIMARY KEY, module_sha256 TEXT, config_digest TEXT,
    data_sha256 TEXT, cuts_json TEXT, passed INTEGER, recorded_at REAL);
```

3. `warden verify-causality --run-id ID` recomputes positions on the full panel and on N truncated
   panels, using the trial's recorded `config_json`, and records the module digest, config digest,
   data digest and the exact cut points. Not a trial; no budget consumed; no holdout touched.

4. Check 3 for `rules_ts`:

```python
ev = causality_evidence(conn, trial["run_id"])
bound = ev and ev["passed"] and trial["strategy_module_sha256"] and \
        ev["module_sha256"] == trial["strategy_module_sha256"] and \
        ev["data_sha256"] == trial["data_snapshot_sha256"]
"3_point_in_time_causality": bound and contract.execution_delay_bars >= 1,
```

Fitted families keep the `feature_sets` path, because there the features are the model's inputs.

### Sequencing

The trial-time digest and the 1.5x column must land together: `HYP-2026-112/113/114` have one slot
each, and a re-run for one purpose only would spend it without serving the other. See the sequencing
note in `.workspace/plans/step3_1p5_cost_stress/proposal.md`.

### Consequence for the three candidates, stated plainly

`RUN-HYP112-002`, `113-002`, `114-002` have **no** `strategy_module_sha256`, so `verify-causality`
run today cannot make them pass — the binding would be absent and the check would (correctly) fail.

They reach 6/6 only if:
- each is re-run once with the module digest recorded (one trial slot each, 2/3 used today), and
- `verify-causality` then passes for that new run.

So the honest description is "potentially 6/6 after one more trial each plus a passing verification",
not "6/6 once the check changes".

## The historical reassessment is a separate decision, not a reason to keep the check wrong

Fifteen stored runs passed check 3 through a contract-level row that says nothing about their own
causality — `RUN-HYP007-002`, `091-001/002`, `092-001/002/003`, `093-001/002`, `094-001/002`,
`095-001`, `099-001`, `007-003`. Two of the promoted lineages (`RUN-HYP065-003`, `RUN-HYP069-001`)
have **no audit row at all**, so they were never assessed.

Making the check strategy-bound means those historical passes rest on invalid evidence. What to do
about that — re-audit them, annotate the audit records, or leave consumed lineages alone — is a
governance decision about completed work and needs its own approval. It is **not** a reason to keep
`has_causal_proof` in the rules path: a known false-pass is not made acceptable by the cost of
correcting its history.

Recommended sequencing: adopt the strategy-bound check, run `verify-causality` on the three
`INDICES_1D_V1` candidates, and treat the fifteen historical rows as an explicit open item in the
governance record rather than silently inheriting their status either way.
## Tests

1. A rules run with no `feature_sets` row and no `causality_evidence` still fails check 3.
2. After `verify-causality`, the same run passes.
3. A look-ahead strategy is recorded as `passed = 0` and still fails.
4. A rules run on a contract that *does* have unrelated `feature_sets` rows still fails check 3
   until `verify-causality` is run for that run — this is the false-pass the change removes.
5. Evidence bound to a different module digest than the trial's does not satisfy check 3.
6. Evidence bound to a different data digest than the trial's does not satisfy check 3.
7. A historical trial with no `strategy_module_sha256` cannot satisfy check 3 at all.
5. `verify-causality` refuses a run whose module does not exist or whose panel is absent.
6. No trial budget is consumed: `trials_used` is unchanged across `verify-causality`.

## What this does not do

It does not change any threshold, any DSR figure or any verdict other than check 3. It does not
replace the sampled-evidence caveat: the test is sampled at the cut points it runs, not a proof over
the whole series.
