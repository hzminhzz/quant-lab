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

Make the proof **strategy-bound** and stop accepting contract-level evidence as a substitute.

1. Table (created on registry open, same pattern as the other bootstraps):

```sql
CREATE TABLE IF NOT EXISTS causality_evidence (
    run_id TEXT PRIMARY KEY, method TEXT, passed INTEGER,
    cuts_checked INTEGER, artifact_sha256 TEXT, recorded_at REAL);
```

2. New command `warden verify-causality --run-id ID`: loads the trial's recorded `config_json` and
   `strategy_module`, recomputes positions on the full panel and on N truncated panels, and records
   `passed` plus the digest of the config it verified. Runs inside the harness — not a trial, no
   budget consumed, no holdout touched.

3. Check 3 becomes:

```python
is_rules = trial.get("strategy_family") == "rules_ts"
proof = causality_verified(conn, trial["run_id"]) if is_rules else has_causal_proof
"3_point_in_time_causality": proof and contract.execution_delay_bars >= 1,
```

For `rules_ts` the contract-level `feature_sets` row no longer counts at all. A rules strategy must
earn the check on its own recorded configuration. Fitted families keep the feature-set path, because
there the features *are* the model's inputs.

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
5. `verify-causality` refuses a run whose module does not exist or whose panel is absent.
6. No trial budget is consumed: `trials_used` is unchanged across `verify-causality`.

## What this does not do

It does not change any threshold, any DSR figure or any verdict other than check 3. It does not
replace the sampled-evidence caveat: the test is sampled at the cut points it runs, not a proof over
the whole series.
