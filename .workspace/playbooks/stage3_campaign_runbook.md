# Runbook: take a strategy set through Stage 3 on one contract family

Derived from the AF30 campaign (`HYP-2026-094..114`, closed 2026-10-28). Use this to repeat the
process on a different family (e.g. FX) without rediscovering the failure modes.

## Definition of done

Every strategy in the set has a **final disposition backed by registry evidence**:

- a Stage-3 trial row with `net_sharpe`, `stress_1p5_cost_sharpe`, `stress_2x_cost_sharpe`,
  `delay_plus1_sharpe`, `deflated_sharpe_prob`, `param_neighbor_stability`, turnover and breakeven;
- a six-check audit verdict in `audit_evidence`;
- or a recorded Stage-1 `REJECT` screen row, which is a terminal result for that mechanism.

A strategy falsified at Stage 1 owes **no** Stage-3 trial. Re-testing it on a fresh family is
selection shopping, not coverage.

## 1. Preflight (read-only, minutes)

```bash
uv run warden check-worktree            # must exit 0, or every backtest refuses
uv run python scripts/check_loc_budget.py | head -2   # headroom in src/warden
ls contracts/<family>_*.yaml            # contract definitions
ls data/exploratory/<family>_*          # market panels must exist
uv run python -c "import sqlite3;c=sqlite3.connect('registry/registry.db');print(list(c.execute(\"select contract_id,round(max_keff_gate,2) from contract_search_state\")))"
```

`contract_search_state` is the inherited family gate. **Clean = no row.** A row means prior attempts
on that `asset_class:frequency` and every new trial pays for them. Prefer a frequency with no row.

Contracts register lazily: the first `register-hypothesis` on a contract registers it.

## 2. Register the hypotheses

One hypothesis per strategy. `falsification_criteria` must state the Stage-1 bars (profit factor,
bootstrap p, time in market, edge vs cost) and the Stage-3 bars (net Sharpe, 2x stress, DSR).

```bash
uv run warden register-hypothesis .workspace/hypotheses/registered/HYP-YYYY-NNN.yaml --json
```

Re-registering the same ID never resets budget; a changed spec is refused as immutable.

## 3. One spec per hypothesis, then preflight

```yaml
contract_id: <CONTRACT>
hypothesis_id: HYP-YYYY-NNN
model_type: rules
strategy_family: rules_ts
strategy_module: strategies/candidates/<module>.py
strategy_name: <label>
run_id: RUN-HYP<NNN>-001
parameters: {<full dict>}
param_grid: [{...}, {...}]   # >=2 points, one factor at a time, +-15%
```

```bash
uv run python scripts/preflight_trial.py experiments/active/<spec>.yaml
```

Preflight spends nothing and catches stream misalignment before a slot is burned. Note:
`scripts/preflight_trial.py` is absent from some worktrees; restore with
`git show 778657a:scripts/preflight_trial.py > /tmp/preflight_trial.py` and run that.

**Always supply a `param_grid`.** Without one, `param_neighbor_stability` is the unmeasured default
`1.000`, which reads as perfect stability.

## 4. Run, verify causality, audit — in that order

```bash
uv run warden backtest experiments/active/<spec>.yaml          # prints WARDEN_RUN_COMPLETE
uv run warden verify-causality --run-id <RUN_ID>               # records bound evidence
uv run warden audit-check --run-id <RUN_ID>
```

`verify-causality` recomputes prefix invariance on the run's recorded config and binds it to the code
closure, config digest and panel digest. Check 3 for `rules_ts` requires that binding, so an audit
without it fails. Budget is not consumed.

## 5. Ledger

Generate the record rather than transcribing numbers:

```bash
uv run python scripts/build_af30_step3_closure.py --check   # copy and retarget for a new family
```

The AF30 generator is family-specific (hard-coded hypothesis range, an `INDICES_COVERAGE` map). Copy
it to `scripts/build_<family>_stage3_closure.py` and change `STRATEGIES`; keep the `--check` drift
test.

## Traps, each one paid for already

1. **1.5x is derivable, not a third simulation**: `net15 = net1 - 0.5*c*turnover`, exact to 1e-18.
2. **Module digests must cover the import closure.** `s02/s03/s04` all import `carver_common.py`.
3. **Do not re-run a hypothesis for one column only** if a later change also needs a slot; the budget
   is 3 per hypothesis and lineage.
4. **Sequencing**: land every evaluator change before the re-runs, or the slots buy only half the
   evidence. `HYP-2026-112/113/114` each had one slot left when two changes were pending.
5. **A holdout read needs the file sealed *and* the digest pinned.** With no `holdout_sha256` in the
   contract record, `resolve_dataset` accepts any bytes as verified market data.
6. **`a | tail && b` tests `tail`'s status, not `a`'s.** Use `set -o pipefail` or no pipe.
7. **The registry is append-only.** Regenerate the ledger; never edit a trial row.

## Governance gates that are not yours

Evaluator changes, contract-record changes and holdout unsealing are human decisions. Prepare the
patch, the exact commands and the tests; do not apply them.
