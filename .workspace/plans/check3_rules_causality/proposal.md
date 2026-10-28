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

Add a strategy-level causality record and accept it as an alternative proof.

1. Table (created by `_ensure_columns`-style bootstrap in `registry.py`):

```sql
CREATE TABLE IF NOT EXISTS causality_evidence (
    run_id TEXT PRIMARY KEY, method TEXT, passed INTEGER,
    cuts_checked INTEGER, artifact_sha256 TEXT, recorded_at REAL);
```

2. New command `warden verify-causality --run-id ID`: loads the trial's recorded `config_json` and
   `strategy_module`, recomputes positions on the full panel and on N truncated panels, and records
   `passed` plus the digest of the config it verified. This runs inside the harness — it is not a
   trial, does not consume budget and does not touch the holdout.

3. Check 3 becomes:

```python
"3_point_in_time_causality": (has_causal_proof or causality_verified(
        conn, trial["run_id"])) and contract.execution_delay_bars >= 1,
```

Additive by design: runs that already pass keep passing, so nothing is retroactively revoked.
`RUN-HYP112/113/114` become 6/6 only after `verify-causality` is run on each.

## The alternative, and why it is not recommended

Gating check 3 on family — `feature_sets` for fitted families, `causality_evidence` for `rules_ts` —
closes the incidental-pass hole. But it would retroactively revoke check 3 for fifteen stored runs,
including `RUN-HYP065-003` and `RUN-HYP069-001`, whose lineages are already promoted. Reopening that
is a governance decision about completed work, not a code change.

## Tests

1. A rules run with no `feature_sets` row and no `causality_evidence` still fails check 3.
2. After `verify-causality`, the same run passes.
3. A look-ahead strategy is recorded as `passed = 0` and still fails.
4. Runs that passed via `feature_sets` keep their verdict (no retroactive revocation).
5. `verify-causality` refuses a run whose module does not exist or whose panel is absent.
6. No trial budget is consumed: `trials_used` is unchanged across `verify-causality`.

## What this does not do

It does not change any threshold, any DSR figure or any verdict other than check 3. It does not
replace the sampled-evidence caveat: the test is sampled at the cut points it runs, not a proof over
the whole series.
