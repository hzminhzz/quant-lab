# Prompt template: Stage 3 campaign for a contract family

Copy the block, fill every `{{...}}`, paste into a **fresh session**. Pair it with
`stage3_campaign_runbook.md`, which holds the procedure this prompt assumes.

Fill the placeholders from the preflight output, not from memory — the family gate, the
available frequencies and the panel schema are the three things that change between families.

---

Do Stage 3 for the **{{FAMILY}}** strategy set, following
`.workspace/playbooks/stage3_campaign_runbook.md`.

## Preflight first, before registering anything

1. `uv run warden check-worktree` — must exit 0, or every `backtest` refuses.
2. `uv run python scripts/check_loc_budget.py | head -2` — headroom in `src/warden`.
3. Which `{{CONTRACT_PREFIX}}_*` contracts have a contract YAML **and** an
   `data/exploratory/{{...}}` panel. List the usable frequencies.
4. `contract_search_state` — a clean family has **no row**. {{FAMILY_GATE_NOTE}}
5. Panel schema per candidate frequency: asset count, date range, columns, and whether
   `funding_rate` is all zero (which decides whether carry is available at all).

Report these five before writing any spec.

## Scope

Only mechanisms that have **not** already failed their own Stage-1 bars on another contract.
{{CANDIDATE_SET}}

If you propose mechanisms native to this family instead, register them as new hypotheses with
their own economic mechanism, counterparty and falsification bars. Do not repurpose one that
was falsified elsewhere.

## Frequency

Choose one from {{FREQUENCY_OPTIONS}} and justify it against **effective** sample size, not row
count — intraday returns are autocorrelated and the contract dictates the execution mode
({{EXECUTION_MODE}}, not necessarily `NEXT_BAR`). State the validation window length in years
and the Sharpe standard error it implies.

## Per hypothesis

Register → one spec with a **≥2-point `param_grid`** (±15%, one factor at a time) → preflight →
`backtest` → `verify-causality` → `audit-check`, in that order. A grid is not optional: without
one, `param_neighbor_stability` is the unmeasured default `1.000`, which reads as perfect
stability.

Then `scripts/build_{{FAMILY_SLUG}}_stage3_closure.py`, copied from
`scripts/build_af30_step3_closure.py` with `STRATEGIES` retargeted, keeping the `--check` drift
test so the record cannot disagree with the registry.

## Done

Every strategy in the set has either a Stage-3 row with 1x / 1.5x / 2x / +1 bar / DSR /
neighbours **and** an audit verdict, or a recorded Stage-1 `REJECT`. A falsified mechanism owes
no Stage-3 trial.

## Hard limits

- Do not read the holdout. Sealing and pinning it are mine to approve.
- Do not apply evaluator or contract changes. Prepare the patch, the exact commands and the
  tests; report what is blocked.
- Do not re-run a hypothesis to fill a single column when another pending change also needs a
  trial slot — three per hypothesis, and the lineage cap is six.

---

## Placeholder reference

| Placeholder | Where it comes from |
|---|---|
| `{{FAMILY}}` | `asset_class` of the contracts, e.g. `FX_MAJORS`, `INDICES`, `CRYPTO_PERPS` |
| `{{CONTRACT_PREFIX}}` | contract id prefix, e.g. `FX_MAJORS_1D_V1` → `FX_MAJORS` |
| `{{FAMILY_GATE_NOTE}}` | preflight: either "confirm no row" (clean) or the inherited `max_keff_gate` and which contracts share it |
| `{{CANDIDATE_SET}}` | the named strategies, with the reason each is eligible; never "all of them" |
| `{{FREQUENCY_OPTIONS}}` | the contracts with data, e.g. `_1D`, `_4H`, `_1H` |
| `{{EXECUTION_MODE}}` | `execution_mode` in the chosen contract YAML |
| `{{FAMILY_SLUG}}` | lowercase slug for the ledger script, e.g. `fx` |

## Notes that stay constant

- The runbook's traps are not advisory; each one was paid for once already.
- Registry rows live outside git. The generated ledger is the reviewable artifact.
- If the campaign needs an evaluator change, stop and report — do not batch it into the trials.
