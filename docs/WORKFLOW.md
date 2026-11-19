# Quant-Lab workflow — workflow, steps, stages

A reader's map of how work actually moves through this repo. It documents **what the code enforces**,
not what we wish it enforced; where prose and code disagree, the disagreement is listed in
§8 rather than smoothed over.

Verified against code on **2026-10-28** (`main` @ `b8deedb`).

Sources, in order of authority:

| File | Role |
|---|---|
| `AGENTS.md` | the invariants. When anything disagrees with it, it wins |
| `AGENT_RUNBOOK.md` | the procedure — 7 steps, and the rules that exist because they were violated once |
| `STAGE3_CAMPAIGN_PROMPT.md` | the copy-paste brief for running a whole family through the funnel |
| `agent_native_quant_research_rfc.md` | the design narrative (RFC Steps 1–6) |
| `src/warden/*.py` | the actual gates. This document quotes them |

---

## 1. Three words, three layers

These get conflated in conversation. They are not the same axis.

| Layer | Vocabulary | Defined by | Granularity |
|---|---|---|---|
| **Workflow** | the falsification funnel | `AGENTS.md` §Falsification funnel | one hypothesis, end to end |
| **Step** | 0–7 | `AGENT_RUNBOOK.md` | one command + one required artifact |
| **Stage** | `STAGE_0`, `STAGE_1_SIGNAL`, `STAGE_2_BACKTEST`, `STAGE_3_STRESS` | what warden writes to `trials.stage_reached` | one statistical gate |

So: the **workflow** is the ordered **steps**, and each step lands you at a **stage** that is stamped
into `registry/registry.db`. "Stage-3 campaign" means batching many hypotheses through steps 5–7.

A fourth, informal usage: in campaign reports "**Step 3**" means "close the Stage-3 evidence ledger"
(`scripts/build_*_stage3_closure.py`), *not* runbook step 3. Same word, different axis — watch for it.

---

## 2. The pipeline at a glance

```
            ┌─────────────────────────────────────────────────────────────┐
 preflight  │ scripts/preflight_thesis.py → state card, next legal command │
            │ exit 0 proceed · 2 human must re-sign manifest · 3 stop      │
            └─────────────────────────────────────────────────────────────┘
                                        │
 0  check-worktree   ── rc≠0 → STOP (only the human re-signs the manifest)
 1  resolve contract ── contracts/<ID>.yaml → pinned data path, sha, span, costs, horizon
 2  source trace     ── the original clipping, not a plan about it; [MISSING] for gaps
 3  pre-register     ── warden register-hypothesis      → STAGE_0 economics at trial time
 4  verify           ── warden verify (prefix invariance) → cache/features/*.parquet
 5  screen           ── warden screen                    → STAGE_1_SIGNAL  (cheap, falsifies early)
 6  backtest         ── warden backtest                  → STAGE_3_STRESS  (2+3 in one record)
    verify-causality ── 3 cut points on the run's own code/config/panel
 7  audit-check      ── 6 checks                         → AUDIT_<RUN_ID>.md
    qualify          ── HUMAN ONLY, one holdout read per lineage
```

Terminal outcomes: a hypothesis ends either **falsified** (a gate said no, that is evidence),
**blocked** (data/contract/engineering gap — *not* a verdict), or **qualified** (holdout passed).

---

## 3. The steps in detail

### Step 0 — Gate check

```bash
uv run warden check-worktree                 # rc 0 required
uv run python scripts/check_loc_budget.py    # headroom in src/warden
uv run python scripts/preflight_thesis.py --contract <CONTRACT_ID>
```

`check-worktree` hashes every file listed in the manifest and runs the LOC budget check
(`src/warden/cli.py:104-119`). Every experiment command calls it too and refuses to run when it
fails (`verify`/`screen`/`audit-check` exit 2; `backtest` prints `WARDEN_RUN_FAILED`).
If it fails: **stop**, print the `generate-manifest --human-approve-manifest` command for the
human, never run it yourself — it records `approver: human-operator`.

### Step 1 — Resolve the contract

Read `contracts/<contract_id>.yaml`. You need, and must be able to state:

- `exploratory_data_path` (and `holdout_data_path` — which you may not read)
- `data_snapshot_sha256` + `data_provenance` (`verified_market` / `synthetic` / `unknown`)
- `train_start < val_start < val_end <= holdout_start < holdout_end`
- `bars_per_year`, `asset_class`, `frequency`, `execution_mode` (`NEXT_BAR` / `QUOTE_SIDE`)
- `taker_fee_bps`, `slippage_bps`, `funding_bps_per_8h` → `total_one_way_cost_decimal()`
- `label_horizon_bars`, `embargo_bars`, `execution_delay_bars` (contract validation requires ≥ 1)
- `n_folds`, `min_expected_move_to_cost_ratio` (default 2.5)

Resolution goes through `resolve_dataset(contract, repo_root)`, which verifies the digest. Never
infer a data file from a contract name: `CRYPTO_PERPS_1H_V1` and `_V2` pin different parquets.

### Step 2 — Source trace

Read the **original clipping**, not a plan or a summary of it. Every rule you implement carries
`file:line`; every rule the source does not define is written `[MISSING]` — never invented.
(A path-signature plan once reduced seven defined signals to four and mislabelled two.)

### Step 3 — Pre-register

```bash
uv run warden register-hypothesis .workspace/hypotheses/HYP-2026-NNN.md --json
```

The markdown front-matter must carry: `hypothesis_id`, `contract_id`, `title`, `strategy_family`
(one of `rules_ts`, `cross_sectional_ml`, `carry_rv`, `meta_overlay`), `economic_mechanism` and
`counterparty_or_friction` (≥ 20 chars each, enforced), `expected_holding_bars`,
`falsification_criteria`, optional `parent_hypothesis_id`, `source_provenance`, `llm_model_id`.
Templates: `.workspace/templates/hypothesis.md`, `.workspace/templates/experiment.yaml`.

Registration is immutable once a trial exists. Re-registering the same ID never resets the budget;
changing the spec after the fact is refused.

### Step 4 — Pipeline and causality

```bash
uv run warden verify experiments/active/<spec>.yaml --json
```

Checks that the module exposes `pipeline` / `FeaturePipeline` / `compute`, runs the
prefix-invariance test (adversarial sampling at 3 cut points, `src/warden/leakage.py:85-108` — the
runbook is explicit that this is *not* a proof), and writes `cache/features/<hash>.parquet`.
Returns `status: PASS` on success.

### Step 5 — Screen (Stage 1)

```bash
uv run warden screen experiments/active/<spec>.yaml --json     # exit 0 = PASS, 1 = REJECT
```

Runs the strategy over the **validation** slice and applies the family gate (§4, Stage 1). It
persists a `selection_attempts` row with `kind=standalone_screen`, `stage=STAGE_1_SIGNAL`,
`outcome=PASS|REJECT` — screening consumes validation evidence even when it rejects.

### Step 6 — Trial (Stage 2 + Stage 3)

```bash
uv run warden backtest experiments/active/<spec>.yaml --json
uv run warden verify-causality --run-id <RUN_ID> --json
```

One call does: 1× simulation, the derived 1.5× stress, the 2× simulation, the +1-bar-delay
simulation, the `param_grid` re-simulations (each recorded as its own attempt), the DSR selection
report, and one `trials` row. Output is JSON followed by `WARDEN_RUN_COMPLETE`; failures print
`WARDEN_RUN_FAILED` and exit 1.

The trial is registered **before** the strategy executes (`src/warden/runner.py:311`), so any
failure after that point consumes one of the three slots and is stored with `verdict=FAILED`.
`verify` / `verify-causality` / `preflight_trial.py` are the cheap ways to catch that first.

### Step 7 — Audit, then qualify

```bash
uv run warden audit-check --run-id <RUN_ID> --json     # writes .workspace/hypotheses/AUDIT_<RUN_ID>.md
uv run warden qualify --run-id <RUN_ID> --unseal-holdout --human-confirm-unseal
```

`audit-check` is agent-runnable and exits 0 only when all six checks pass *and* the run is
market-eligible. `qualify` is **human-only**: it refuses under `WARDEN_NON_INTERACTIVE=1`, requires
the literal token `YES_UNSEAL_HOLDOUT`, and refuses a second read of any lineage. An agent may run
it only with the principal's explicit approval in the current session, naming the run ID (or a
criterion plus the plan that produces the run), and must record the approving instruction, the
pre-read tree hashes and the result.

---

## 4. Stage gates, exactly as coded

### Stage 0 — economics feasibility (`gates.py::evaluate_stage0_feasibility`)

```
hurdle = total_one_way_cost_decimal() * 2 * min_expected_move_to_cost_ratio   # default 2.5
reject if holding_volatility < hurdle
also raises HypothesisBudgetExhaustedError if trials_used >= PER_HYPOTHESIS_CAP
```

`holding_volatility` is supplied by the runner as `0.0085 * sqrt(expected_holding_bars)` — a coarse,
hard-coded proxy, computed at trial time rather than at registration. Recorded on failure as
`selection_attempts.stage='STAGE_0'`, `kind='failed_reservation'`.

### Stage 1 — family-specific signal triage (`gates.py::evaluate_stage1_rules_ts / evaluate_stage1_cross_sectional_ml`)

Never force one family's gate onto another.

| Family | Pass condition |
|---|---|
| `rules_ts` | `trade_edge > 1.5 × one_way_cost` **and** `profit_factor ≥ 1.15` **and** `time_in_market ≥ 5%` **and** block-bootstrap `p < 0.05` |
| `cross_sectional_ml` | `mean IC ≥ 0.015` **and** Newey-West HAC `t ≥ 2.0` (max 5 lags, ≥ 10 IC observations) |

`carry_rv` and `meta_overlay` are specified in the RFC/funnel but **unwired**: the runner branches only
on `rules_ts` and `cross_sectional_ml`, and the dead gate stubs were removed. Re-add a family's gate when
wiring that family.

Definitions worth knowing: `trade_edge = mean(gross | active bars) × avg_holding`, where
`avg_holding = 2 × n_active / total_turnover`; the bootstrap resamples 200 blocks of
`min(24, √n_active)` bars with seed 42.

### Stage 2 — causal simulation

Contract-injected execution: `execution_returns()` + `simulate_positions()` with the contract's
`execution_mode`, `execution_delay_bars` (≥ 1, and a strategy may not pre-lag — declaring
`signal_delay_bars > 0` raises `DoubleLatencyError`), taker fee + slippage per side, and funding
charged on the settlement mask only. Missing data is never substituted — `resolve_dataset` fails
closed and the failure is recorded as a `DATA` attempt.

### Stage 3 — stress and selection bias (`runner.py::run_spec`)

```
net15      = net1 - 0.5 * one_way_cost * turnover        # 1.5× is derived, exact, not a 3rd sim
sr2        = 2× cost simulation Sharpe
srd        = +1-bar-latency simulation Sharpe
stability  = min(grid Sharpe) / max(grid Sharpe)          # 1.0 when param_grid is empty
k_gate     = max(k_dedup, stored_floor) + 0.5 × unvectorized_attempts
DSR        = P(SR* - noise) with noise from effective-rank expected-maximum
```

Promotion conjunct (`runner.py::run_spec`):

```
net_sharpe > 1.0            (1.2 for cross_sectional_ml)
and sr2 > 0.50
and srd > 0.0
and DSR ≥ 0.90
and no non_promotable_reason
and ic_ok        (cross_sectional_ml: HAC t ≥ 2.0)
and rules_ok     (rules_ts: the Stage-1 gate still passes on the trial's own simulation)

→ CANDIDATE_FOR_QUALIFICATION
synthetic data → NON_PROMOTABLE regardless of the numbers
```

`DSR ≥ 0.90` is with respect to the **contract-wide** effective rank, which is inherited across
contracts sharing `asset_class:frequency` — not just the trials of this contract.

### Six-check audit (`cli.py::cmd_audit_check`)

| # | Check | Coded condition |
|---|---|---|
| 1 | target/rebalance alignment | `stage_reached ∈ {STAGE_2_BACKTEST, STAGE_3_STRESS}` and `label_horizon_bars ≥ 1` |
| 2 | outlier sensitivity | mean of returns ≤ their 95th percentile is `> -0.001` and `net_sharpe > 0` and `verdict == CANDIDATE_FOR_QUALIFICATION` |
| 3 | point-in-time causality | causal proof bound to *this* run: for `rules_ts` a `causality_evidence` row matching code sha, config digest and data sha; otherwise a `prefix_invariance_passed` feature-set row for the contract — **and** `execution_delay_bars ≥ 1` |
| 4 | fold-local fitting | (`n_folds ≥ 2` and `embargo_bars ≥ 1`) or family is `rules_ts` |
| 5 | cost + latency survival | `stress_2x_cost_sharpe > 0.50` and `delay_plus1_sharpe > 0.0` |
| 6 | selection-bias accounting | `total_trials_n ≥ 1` and `DSR ≥ 0.80` |

Verdict: `APPROVED` (six pass + eligible), `REVOKED_INELIGIBLE` (six pass, run/data not eligible),
`REJECTED` otherwise. The run must be market-eligible for the six to matter.

### Holdout gate (`src/warden/qualify.py`)

Eligibility to even attempt: verdict is `CANDIDATE_FOR_QUALIFICATION`, or `REVISE` with
`net_sharpe ≥ 1.0` and `stress_2x_cost_sharpe ≥ 0.50`; validation Net Sharpe positive; not
synthetic; not quarantined; the lineage has no prior qualification row. The trial's recorded
`strategy_module` must resolve, and its recomputed `code_closure` must equal the recorded
`strategy_code_sha256` — qualification fails closed on a missing module, an absent binding, or changed
code, and never substitutes a baseline (re-run the trial to re-bind).

Outcome (`qualify.py::qualify_candidate`):

```
promoted  ⟺  holdout_net_sharpe > 0.80  and  holdout / validation ratio ≥ 0.50
```

The hypothesis status is then set to `PROMOTED` or `REJECTED_ON_HOLDOUT`. One window, one read:
the second is refused by the registry, forever, for the whole lineage.

---

## 5. Vocabulary you will see in the registry

`trials.stage_reached`

| Value | Meaning |
|---|---|
| `STAGE_1_SIGNAL` | rejected at Stage 1 inside `backtest` (no simulation recorded) |
| `STAGE_3_STRESS` | reached the full simulation + stress record (this is also how Stage 2 appears) |
| `FAILED` | execution error after the trial slot was registered |
| `STAGE_0`, `DATA` | appear only in `selection_attempts`, never as a trial |

`trials.verdict`

| Value | Meaning | What to do |
|---|---|---|
| `CANDIDATE_FOR_QUALIFICATION` | every Stage-3 conjunct passed | audit, then human qualification |
| `REVISE` | at least one conjunct failed — **the row does not say which** | attribute it from the recorded columns, or say you cannot |
| `REJECTED_EARLY` | Stage 1 said no | evidence; refine the mechanism or drop it |
| `FAILED` | execution failure | fix the code/data; the slot is spent |
| `NON_PROMOTABLE` | synthetic data | never qualify; re-run on market data |

`hypotheses.status`: `ACTIVE` · `REJECTED` · `REJECTED_ON_HOLDOUT` · `PROMOTED`.
Only `ACTIVE` may take a new trial (`registry.py:394`).

`audit_evidence.verdict`: `APPROVED` · `REJECTED` · `REVOKED_INELIGIBLE`.

Blocked is not falsified. A `DATA`/`STAGE_0` failure reservation, a missing panel, an ineligible
contract or a corrupt bar is an **engineering gap**; it says nothing about whether the thesis is
true. Say "blocked", never "failed".

---

## 6. Budgets and how evidence gets consumed

- **3 trials per hypothesis**, hard cap (`PER_HYPOTHESIS_CAP = 3`). Baseline + two targeted
  revisions. A fourth is blocked; a new ID does not reset it.
- **A trial slot is spent at registration-start**, before the strategy runs. Validation errors
  raised earlier (`filter_target_symbols`, `resolve_dataset`) do not spend one.
- **`param_grid` entries are attempts, not trials** — but they are recorded, they raise the
  selection hurdle, and they are the only thing that measures parameter-neighbour stability.
  An empty grid records `stability = 1.0`, which reads as perfect stability. Populate it.
- **Lineage cap is six** trials across a parent/child chain (`STAGE3_CAMPAIGN_PROMPT.md`).
- **DSR floor is monotone and inherited.** `contract_search_state` keeps
  `max_keff_gate` / `max_noise` per contract, and `prior_search_floor()` takes the max over every
  contract with the same `asset_class:frequency`. The gate therefore only ever rises for a family,
  which is the point — and the reason a family can become untestable.
- **Known re-charge:** `k_gate = max(k_dedup, stored_floor) + 0.5 × unvectorized`, and the stored
  floor is that same `k_gate`. The `0.5 × unvectorized` term is therefore re-added on every run
  instead of once (`src/warden/registry.py:90` + `:473-479`). Recorded, not fixed — read the
  `prior_max_keff` / `k_eff_dedup` / `unvectorized_attempts` columns in `diagnostics_json` before
  attributing a DSR failure to the strategy.

After exhaustion: post-mortem in `.workspace/memory/LESSONS.md`. The failure gets written down,
not re-tested under a new name.

---

## 7. What the harness does not do for you

Declare these yourself, or write `[MISSING]`:

| Protocol asks for | Reality |
|---|---|
| 1.5× cost stress | derived exactly from the 1× run, not simulated — fine, but say so |
| ±15% neighbours | only the `param_grid` you supply; empty grid = `stability 1.0` |
| Stage 0 economics | computed inside the runner on a hard-coded proxy, not at registration |
| prefix invariance as proof | 3 cut points; adversarial sampling, documented as not a proof |
| "which gate failed" | `REVISE` does not name it — attribute from the row or admit you can't |

And never: `sudo` · edit `contracts/` · edit `src/warden/` (LOC budget nearly exhausted) · lower or
reinterpret a gate · pre-lag a signal · read `data/holdout/` (not even `stat()` it — a permission
check may only look at the parent directory's mode bits) · run `qualify` without an explicit human
instruction · sign the manifest · delete rows from `registry.db` (append-only triggers) · reset or
stash unrelated dirty work.

---

## 8. Known prose-vs-code gaps

Honest list, so nobody inherits a surprise:

1. **`STAGE_2_BACKTEST` is never written.** It appears in the audit's check 1 allow-list and in the
   prose, but Stage 2 and Stage 3 land in one `backtest` record stamped `STAGE_3_STRESS`. As of the
   snapshot below, 0 rows carry `STAGE_2_BACKTEST`.
2. **Audit check 6 is looser than the trial gate** — `DSR ≥ 0.80` in the audit vs `≥ 0.90` to be
   stamped `CANDIDATE_FOR_QUALIFICATION`. A run can pass the audit with a DSR the runner rejected.
3. **Audit check 2 is unreachable for a `REVISE` run** — it requires
   `verdict == CANDIDATE_FOR_QUALIFICATION`, so a high-Sharpe `REVISE` can be qualified on holdout
   yet can never pass the audit.
4. **Audit check 4 auto-passes for `rules_ts`**, regardless of folds/embargo.
5. **`REVISE` is not a gate name.** It means "at least one conjunct failed". Read
   `net_sharpe`, `stress_2x_cost_sharpe`, `delay_plus1_sharpe`, `deflated_sharpe_prob`,
   `param_neighbor_stability` and `diagnostics_json → selection` to attribute it.
6. **The DSR floor ratchets** (§6) — a family's threshold can only go up, and the same
   unvectorized penalty is charged again each run.
7. **Registration order.** Since a trial slot is spent at `register_trial_start`, a strategy that
   raises after that point costs a trial and is recorded `FAILED`. Prefer `verify` and a scratch
   run first.

### What the manifest actually covers

`.warden_manifest.sha256` is the list `check-worktree` hashes. Today it is: every
`src/warden/*.py`, every `contracts/*.yaml`, every `contracts/datasets/*.json`, `AGENTS.md`,
`pyproject.toml`, `uv.lock`, and four scripts (`check_loc_budget.py`,
`lock_holdout_permissions.sh`, `preflight_trust_boundary.py`, `apply_trust_boundary.sh`).

Consequences worth knowing: editing `AGENTS.md` breaks `check-worktree` (and only the human can
re-sign the manifest), while this `docs/` tree, `AGENT_RUNBOOK.md`, `experiments/`, `strategies/`
and `.workspace/` are outside the manifest and do not. A doc change is cheap; a policy change is
not.

---

## 9. Where things live

| Path | Contents |
|---|---|
| `contracts/<id>.yaml` | frozen evaluation contracts (human-owned) |
| `contracts/datasets/` | dataset pins / manifests |
| `experiments/active/<spec>.yaml` | the spec you run: `contract_id`, `hypothesis_id`, `strategy_module`, `parameters`, `param_grid`, `run_id`, `model_type` |
| `strategies/candidates/<module>.py` | `generate_positions(panel, contract, **params)` |
| `.workspace/hypotheses/HYP-*.md` | pre-registration + logged results table |
| `.workspace/hypotheses/AUDIT_*.md` | six-check audit output |
| `.workspace/templates/` | `hypothesis.md`, `experiment.yaml` |
| `.workspace/memory/LESSONS.md` | post-mortems and durable lessons |
| `.workspace/{af30_campaign,fx_stage3,...}/` | campaign ledgers, including `stage3_closure.md` |
| `registry/registry.db` | append-only: `hypotheses`, `trials`, `selection_attempts`, `qualifications`, `audit_evidence`, `causality_evidence`, `feature_sets`, `contract_search_state`, `trial_quarantine` |
| `registry/returns/<contract>/<run>.parquet` | per-run net return vectors (drive DSR history) |
| `cache/features/*.parquet` | verified feature matrices |
| `data/` | panels; `data/holdout/` is sealed (human-owned) |

---

## 10. Snapshot — 2026-10-28, `main` @ `b8deedb`

A point-in-time picture, not a target. Regenerate with the commands below.

| Measure | Value |
|---|---|
| hypotheses | 125 (115 ACTIVE, 4 REJECTED, 4 REJECTED_ON_HOLDOUT, 2 PROMOTED) |
| trials | 67 — 56 `STAGE_3_STRESS`, 9 `STAGE_1_SIGNAL`, 2 `FAILED` |
| trial verdicts | 45 `REVISE`, 11 `CANDIDATE_FOR_QUALIFICATION`, 9 `REJECTED_EARLY`, 2 `FAILED` |
| selection attempts | 158 — 68 `param_grid`, 47 `standalone_screen`, 42 `STAGE_0` failures, 1 `DATA` |
| audits | 33 rows — 29 `REJECTED`, 3 `APPROVED`, 1 `REVOKED_INELIGIBLE` |
| qualifications | 6 (one holdout window, read three times across a lineage) |
| contract YAMLs / contracts in DB | 31 / 13 |
| contracts with trials | `INDICES_1H_V2` 12, `MULTI_ASSET_1D_V1` 10, `CRYPTO_MAJORS_1H_V1` 9, `CRYPTO_PERPS_TOP100_1H_V1` 9, `INDICES_1D_V1` 9, `CRYPTO_PERPS_1H_V1` 7, `FX_MAJORS_1D_V2` 5, `INDICES_1H_V1` 4, `MULTI_ASSET_1H_V1` 2 |

Selection floors currently stored (`contract_search_state` → `max_keff_gate` / `max_noise`):
`INDICES_1H_V2` 51.25 / 1.88 · `MULTI_ASSET_1D_V1` 45.61 / 1.39 · `MULTI_ASSET_1H_V1` 22.50 / 1.22 ·
`CRYPTO_PERPS_TOP100_1H_V1` 7.80 / 1.45 · `CRYPTO_MAJORS_1H_V1` 4.80 / 0.83 · `FX_MAJORS_1D_V2` 1.62 / 0.19 ·
`INDICES_1D_V1` 1.13 / 0.00

Read the floors before choosing a contract: the DSR a new trial must clear is a property of the
family, not of the strategy.

Regenerate this section:

```bash
python3 - <<'EOF'
import sqlite3
c = sqlite3.connect('file:registry/registry.db?mode=ro', uri=True)
print(c.execute("select stage_reached,count(*) from trials group by 1").fetchall())
print(c.execute("select verdict,count(*) from trials group by 1").fetchall())
print(c.execute("select status,count(*) from hypotheses group by 1").fetchall())
print(c.execute("select stage,kind,count(*) from selection_attempts group by 1,2").fetchall())
print(c.execute("select verdict,count(*) from audit_evidence group by 1").fetchall())
print(c.execute("select contract_id,max_keff_gate,max_noise from contract_search_state").fetchall())
EOF
```

---

## 11. Checklist for a new family

1. Preflight: `check-worktree`, LOC budget, which `<FAMILY>_*` contracts have both a YAML and a
   panel, whether `contract_search_state` already has a row for the family, panel schema and
   whether `funding_rate` is all zero (that decides whether carry exists at all).
2. Report those five before writing any spec.
3. One mechanism per hypothesis: mechanism, counterparty, falsification criteria, expected holding
   bars. Reject anything whose holding-period vol is under 2.5× round-trip cost.
4. Per hypothesis: register → spec with a ≥ 2-point `param_grid` (±15%, one factor at a time) →
   `verify` → `backtest` → `verify-causality` → `audit-check`.
5. Close the campaign by generating the ledger, never by transcribing numbers: copy
   `scripts/build_af30_step3_closure.py`, retarget `STRATEGIES`, keep its `--check` drift test.
6. Done means every strategy has either a Stage-3 row (1× / 1.5× / 2× / +1 bar / DSR / neighbours)
   with an audit verdict, or a recorded Stage-1 `REJECT`. A mechanism falsified at Stage 1 owes no
   Stage-3 trial.
