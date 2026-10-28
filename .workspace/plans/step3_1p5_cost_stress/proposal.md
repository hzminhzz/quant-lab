# Proposal: implement the 1.5x cost stress that AGENTS.md step 3 requires

Status: **proposal, not applied.** `src/warden/` is untouched. This is an evaluator change, which
AGENTS.md reserves to the human after cutover; the manifest must be re-signed afterwards.

## Gap

`AGENTS.md` funnel step 3: "Apply contract-wide effective-rank DSR, 1.5x/2x costs, +1 bar latency
and ±15% parameter neighbors." The harness simulates 1.0 and 2.0 only:

| Location | Multiplier |
|---|---|
| `runner.py:253`, `runner.py:350` | 1.0 |
| `runner.py:359` | 2.0 |
| `gates.py:63` | 1.5, but the Stage-1 *trade-edge* threshold, not a simulation |
| `qualify.py:113` | 1.0 (holdout) |

So no trial in the registry has a 1.5x Sharpe and the figure cannot be recovered afterwards: 1.5x
net returns cannot be derived from stored net returns alone, because the cost term needs the
per-bar turnover and gross series, which are not persisted. Populating the column requires re-running
the trials.

2x does not stand in for 1.5x. A higher multiplier reduces mean return and volatility together for a
cost series correlated with the return path, so Sharpe is not monotone in the multiplier.

## Patch

Additive only: one defaulted dataclass field, one extra simulation, one auto-migrated column. No
existing signature changes, no verdict changes.

1. `contracts.py`, `ExperimentResult`, in the defaulted tail so positional construction is unaffected:

```python
    stress_1p5_cost_sharpe: float | None = None
```

`None`, not `0.0`: zero is a legitimate measured Sharpe, so a `0.0` default would make every
historical row, every `FAILED` row and every unmeasured trial indistinguishable from a real zero.
`registry.py::_ensure_columns` maps non-`str`/`datetime` fields to `REAL`, so a nullable column needs
either a `TEXT` type override or a `NULL`-permitting `REAL` with an explicit default of `NULL`;
`ALTER TABLE ... ADD COLUMN stress_1p5_cost_sharpe REAL DEFAULT NULL` is the intended migration.
Read paths must treat `None` as "not measured" rather than "measured zero" — specifically
`trial.get("stress_1p5_cost_sharpe")` must not be compared with `>` without a `None` guard.

2. `runner.py`, beside the 2.0 block at :359:

```python
            sim15 = simulate_positions(raw_pos, ret_p["long"], fund_p, contract, v_slice,
                                       cost_multiplier=1.5, funding_mask=fund_mask,
                                       quote_returns=ret_p)
            sr15 = float(np.mean(sim15["net_returns"])
                         / (np.std(sim15["net_returns"], ddof=1) + 1e-12)
                         * math.sqrt(contract.bars_per_year))
```

and pass `stress_1p5_cost_sharpe=sr15` at the `ExperimentResult(...)` construction site (or
`dc_replace` alongside the existing `provenance_kw`).

3. Optional, and a semantics change that needs its own decision: extend audit check 5 to
`stress_1p5_cost_sharpe > 0.50` as well. Recommend **not** doing this in the same change — it would
retroactively invalidate every stored trial, which have no 1.5x figure.

## Tests

1. `test_cost_stress_columns_populated`: a trial on a synthetic zero-cost contract reports
   1.5x == 2x == net Sharpe (multiplier is inert when cost is zero).
2. `test_cost_stress_monotone_in_mean_not_sharpe`: with a real cost, `mean(net@2.0) < mean(net@1.5) <
   mean(net@1.0)`; assert on means, **not** on Sharpe, since Sharpe is not guaranteed monotone.
3. `test_experiment_result_backward_compatible`: constructing `ExperimentResult` with the old
   positional signature still works and yields `stress_1p5_cost_sharpe is None`.
4. `test_registry_migrates_column`: an old registry without the column gains it on open, and existing
   rows read `NULL` — never `0.0`.

## It does not currently fit the LOC budget

`src/warden/` is at **2399 / 2400** LOC (`scripts/check_loc_budget.py`, `WARDEN_MAX_LOC = 2400`).
This patch adds **5** lines, so applying it makes the tree 2404 and `check_loc_budget.py` fails —
which means `check-worktree` fails, which means every `backtest`, `audit-check` and `qualify` refuses.
The patch applies cleanly to the source; it does not pass the governance gate.

Raising `WARDEN_MAX_LOC` is itself a governance control: the comment at `check_loc_budget.py:12-14`
records that the 2400 limit was raised under the issue-4 mandate and that "ticket-10 operator cutover
must ratify it". So this proposal needs two approvals, not one: the cap, and the change.

## Sequencing: do not spend a trial slot on this alone

`HYP-2026-112/113/114` each have **one** trial left (2/3 used). A re-run that only populates
`stress_1p5_cost_sharpe` would consume that slot while the registry still has no
`trials.strategy_module_sha256`, so the resulting row could never bind causal evidence and those
three candidates could never reach 6/6 — the slot would be spent for one column and close the other
door permanently.

Both evaluator changes must be in the same tree before any re-run:

1. this patch (`stress_1p5_cost_sharpe`, nullable), **and**
2. `.workspace/plans/check3_rules_causality/` (`trials.strategy_module_sha256` + `causality_evidence`).

Then one re-run per hypothesis produces the 1.5x column and the module digest together, and
`verify-causality` can bind. Applying only the first patch and stopping there is the failure mode to
avoid.

## Populating the column

Existing trials read `NULL` and cannot be back-filled. To get real numbers, re-run at the same
configuration (which consumes the remaining trial slot):

| Hypothesis | Trials used | Remaining |
|---|---|---|
| HYP-2026-095 | 2/3 | 1 |
| HYP-2026-099 | 1/3 | 2 |
| HYP-2026-094 | 2/3 | 1 |
| HYP-2026-112/113/114 | 2/3 each | 1 each |

Six re-runs finish every AF30 Stage-3 row with 1x/1.5x/2x/+1-bar/neighbour/DSR. 095 is buy-and-hold
and 099 has no open axis, so for those two the re-run buys the missing column only.

## Risk

- The extra simulation adds one `simulate_positions` pass per trial (~seconds on 1D).
- Re-running consumes final trial slots; a re-run that errors would leave a hypothesis at cap with a
  `FAILED` row. Preflight each spec first (`scripts/preflight_trial.py`, restored from 778657a — it is
  currently missing from the working tree).
- Re-signing the manifest afterwards covers any other agent's concurrent evaluator edits. Diff every
  protected file before signing.

## Ready-to-apply patch

`cost_stress.patch` beside this file. What has actually been run:

- `git apply --check cost_stress.patch` — applies cleanly to the current tree;
- applied to a throwaway copy of `src/`, then imported: `ExperimentResult` still constructs with
  the old positional signature and `stress_1p5_cost_sharpe` defaults to `None`;
- the runner contains `cost_multiplier=1.5` and passes the value at the Stage-3 call site only
  (the `cross_sectional_ml` early-return path is untouched);
- `tests/test_cost_stress.py` — three isolated tests, **3 passed** on the patched copy and skipped
  on an unpatched tree: mean net return is strictly ordered across 1.0x/1.5x/2.0x (asserted on
  means, not Sharpe, which is not monotone); the added column is `REAL` and backfills `NULL`, never
  `0.0`; the constructor keeps its positional signature.

**Not yet run, and this patch is not evidence until they are:** a patched `warden backtest` end to
end, a real SQLite registry round-trip through `record_trial`, and any numerical comparison against
a run's stored 2x figure. Those arrive with the six re-runs below.

```bash
cd /home/quant/dev/quant-lab
git apply .workspace/plans/step3_1p5_cost_stress/cost_stress.patch
uv run python -m pytest tests/ -q            # expect 257 + 3 passed
uv run warden generate-manifest --human-approve-manifest --json   # re-baseline, human gate
```

Then the six re-runs populate the column. Historical rows stay `NULL`, which is the point:
unmeasured is not zero.
