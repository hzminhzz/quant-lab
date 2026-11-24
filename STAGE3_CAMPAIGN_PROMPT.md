# Stage-3 campaign prompt

A reusable brief for driving a set of strategies through the falsification funnel on one contract
family, in a fresh session. `AGENT_RUNBOOK.md` is the procedure; this file is the brief you paste.

Works in any warden workspace: nothing here is specific to one asset class.

## How to use

1. Copy the block below into a new session.
2. Fill every `{{...}}` **from preflight output**, not from memory. The family gate, the usable
   frequencies and the panel schema are the three things that change between families, and they
   change while you work.
3. Keep the hard limits — they are not negotiable per campaign.

---

Do Stage 3 for the **{{FAMILY}}** strategy set, following `AGENT_RUNBOOK.md`.

## Preflight first, before registering anything

1. `uv run warden check-worktree` — must exit 0 (checks `.warden_manifest.sha256` and AST import boundaries), or every `backtest` refuses.
2. Which `{{CONTRACT_PREFIX}}_*` contracts have both a contract YAML and an
   `data/exploratory/{{...}}` panel. List the usable frequencies.
3. `contract_search_state` — a clean family has **no row**. {{FAMILY_GATE_NOTE}}
4. Panel schema per candidate frequency: asset count, date range, columns, and whether
   `funding_rate` is all zero (which decides whether carry exists at all).

Report these four before writing any spec.

## Scope

Only mechanisms that have **not** already failed their own Stage-1 bars on another contract.
{{CANDIDATE_SET}}

If you propose mechanisms native to this family instead, register them as new hypotheses with their
own economic mechanism, counterparty and falsification bars. Do not repurpose one that was
falsified elsewhere, and do not re-register an existing set under a new id to get a clean-looking
gate — `prior_search_floor` matches on `asset_class:frequency`, so the gate follows the family.

## Frequency

Choose one from {{FREQUENCY_OPTIONS}} and justify it against **effective** sample size, not row
count — intraday returns are autocorrelated, and the contract dictates the execution mode
({{EXECUTION_MODE}}, which is not always `NEXT_BAR`). State the validation window in years and the
Sharpe standard error it implies.

## Per hypothesis

Register → one spec with a **≥2-point `param_grid`** (±15%, one factor at a time) → preflight →
`backtest` → `verify-causality` → `audit-check`, in that order. A grid is strongly recommended:
without one `evaluate_neighbor_stability` records `neighbor_tested=False` and `stability=None`.

Then generate the ledger rather than transcribing numbers: copy
`scripts/build_af30_step3_closure.py` to `scripts/build_{{FAMILY_SLUG}}_stage3_closure.py`, retarget
`STRATEGIES`, and keep the `--check` drift test so the record cannot disagree with the registry.

## Done

Every strategy in the set has either a Stage-3 row carrying 1x / 1.5x / 2x / +1 bar / DSR /
neighbours **and** an audit verdict, or a recorded Stage-1 `REJECT`. A mechanism falsified at
Stage 1 owes no Stage-3 trial.

## Hard limits

- Do not read the holdout. Sealing and pinning it are the principal's to approve.
- Do not apply evaluator or contract changes. Prepare the patch, the exact commands and the tests,
  and report what is blocked.
- Do not re-run a hypothesis to fill a single column when another pending change also needs a trial
  slot — three trials per hypothesis, and the lineage cap is six.

---

## Placeholder reference

| Placeholder | Where it comes from |
|---|---|
| `{{FAMILY}}` | `asset_class` of the contracts, e.g. `FX_MAJORS`, `INDICES`, `CRYPTO_PERPS` |
| `{{CONTRACT_PREFIX}}` | contract id prefix, e.g. `FX_MAJORS_1D_V1` → `FX_MAJORS` |
| `{{FAMILY_GATE_NOTE}}` | preflight: "confirm no row" when clean, otherwise the inherited `max_keff_gate` and which contracts share the `asset_class:frequency` family |
| `{{CANDIDATE_SET}}` | the named strategies with the reason each is eligible; never "all of them" |
| `{{FREQUENCY_OPTIONS}}` | contracts with data, e.g. `_1D`, `_4H`, `_1H` |
| `{{EXECUTION_MODE}}` | `execution_mode` in the chosen contract YAML |
| `{{FAMILY_SLUG}}` | lowercase slug for the ledger script, e.g. `fx` |

## Traps, each paid for once already

1. **1.5x cost stress is analytical, 2.0x is full Engine re-simulation**: `stress_1p5_cost_sharpe`
   is computed analytically from the 1x `ml4t.backtest.Engine` result (`analytical_1p5x_sharpe`),
   while `cost_2x` runs a full `cost_multiplier=2.0` Engine simulation.
2. **A module digest must cover the import closure.** Hashing one file misses shared helpers;
   declare `CODE_DEPENDENCIES` for runtime-loaded or subdir helpers so `code_closure` covers them.
3. **Sequencing beats slot count.** Land every evaluator change before the re-runs, or the last slot
   buys half the evidence.
4. **A holdout needs the file sealed *and* its digest pinned.** With no `holdout_sha256` in the
   contract record, `resolve_dataset` accepts any bytes as verified market data.
5. **`a | tail && b` tests `tail`'s status.** Use `set -o pipefail`, or no pipe.
6. **The registry is append-only.** Regenerate the ledger; never edit a trial row.
7. **`preflight_trial.py` may be absent from a worktree.** It spends nothing and catches stream
   misalignment before a slot is burned — restore it from git history if missing.
