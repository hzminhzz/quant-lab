# Architectural untangling — 2026-11-28

## Scope and starting evidence

Principal request: reconstruct today's architecture from first principles; optimize agent autonomy, deep modules, change locality and compute; remove competing orchestration and obsolete mechanisms; preserve scientific invariants; prove behavior/recovery and performance; review, merge and reconcile the appliance. Comment and test cleanup are authorized.

Fixed review base: `d1e9325b553a6895702570a897b9e401786d1853` (fresh origin/main).
Development: isolated `refactor/canonical-research-lifecycle-20261128`.
The original checkout has unrelated dirty/untracked work and a divergent local commit; it is preserved.
Observed appliance: ACTIVE and healthy, same source commit, schema 10, scientific scheduling remains paused.
Baseline fast verification passed and collected 1,477 tests. Full verification remains a release gate.

## Architecture if today's requirements were known on day one

The scientific unit is an admitted operation on a frozen candidate. Its lifecycle is:
resolve the exact subject and approvals → reserve once → compute through ML4T →
commit evidence → inspect or reconcile the original receipt.
A hypothesis, candidate, screen, full trial, robustness scenario, control request and
job are distinct identities because they consume different resources or record different effects.
They must not become independent scientific authorities.

One existing ExperimentRunner owns scientific preparation and progression.
ResearchRegistry owns persistence, state projection, identity binding and recovery.
CLI/MCP render or carry requests. Control preserves plan/request identity. Queued jobs
bound resources and lifetime around those same operations. The appliance verifies and selects
software; its journal cannot approve scientific effects.

Essential complexity: point-in-time inputs, exact-byte provenance and code identity;
family-specific gates and ML4T execution; immutable intent/conformance; candidate/lineage
budgets and selection debt; atomic reservation/finalization; conservative interruption;
distinct metadata-only and artifact-verified reads; one-shot sealed exposure; pinned,
verified appliance recovery without ledger rewind.

Accidental complexity: CLI handlers as internal APIs; capturing/parsing their printed output;
a second session funnel; duplicated screen/trial preparation; repeated screen-persistence
arguments; separate copies of the same spec-state decision tree; control callers reading
private storage to reconstruct frozen operation identity.

## Decision log before implementation

### D1 — One Python research progression, with CLI as adapter
- Hidden link: `cli._advance_one_spec` calls verify/screen/backtest/audit CLI handlers using
  synthetic argparse objects and `_invoke_quiet` stdout/stderr capture.
- Surplus: `scripts/run_spec_session.py` independently schedules verify/screen/backtest,
  parses JSON printed by handlers, duplicates lease/policy/audit handling and has force-all.
- Decision: deepen the existing runner interface; preserve supported CLI output/exit semantics,
  per-spec writer leasing, terminal/review refusal and explicit verification/audit.
  Retire the redundant session funnel after preserving meaningful behavioral coverage.
  Historical reports and Git history remain unchanged.

### D2 — One admission preparation and screen finalization
- Hidden link: screen and trial independently hydrate the hypothesis, validate family/protocol,
  resolve params, require intent/conformance and prepare market data.
- Hidden link: screen branches repeat the same evidence-publication argument set.
- Decision: centralize common preparation and finalize one screen report through the existing
  registry owner. Keep family math, scenario accounting and failure charging unchanged.

### D3 — One state projection, explicit verification tier
- Hidden link: `ResearchRegistry.spec_status` and `read_spec_status` independently reconstruct
  lifecycle precedence, terminal status, pending attempts, trial verdict and screen status.
- Accidental cost: several new SQLite connections for one logical metadata view.
- Decision: share the state decision and metadata snapshot. Metadata-only reads never open
  Parquet; artifact-verified reads retain reconciliation and drift detection.

### D4 — Encapsulate request recovery and recorded subjects
- Needless link: `control_actions` queries research_intents and screen_receipts through
  `registry._connect`; a caller reassembles the reconciliation identity.
- Decision: registry supplies the frozen intent/operation identity and owns request reconciliation.
  Do not add a second ledger, state machine or generic SQL interface.

### Retained / diminishing-return boundary
- Keep runtime/environment byte revalidation, resource leases, exact scientific request IDs,
  explicit historical compatibility, family-specific compute and holdout fencing. Their deletion
  loses real requirements.
- Keep approved legacy read/replay semantics and immutable historical scripts/evidence unless
  usage inspection proves an executable mechanism obsolete.
- Do not reorganize dataset compiler, engine internals, job supervisor or appliance merely
  to shorten files. Do not change contract semantics, statistical gates, schema or dependency pins.
- Optional adapters and historical campaign reports are not a reason to add another abstraction.

## Verification plan

Compare the pinned baseline and candidate on disposable synthetic native-ML4T fixtures:
CLI progression and repeat submission; prepared request replay/conflict; interrupted screen/trial;
immutable failed/rejected evidence; per-spec lease concurrency; metadata-only versus byte-verified
state; real governed admission and negative cases. Preserve output and recorded scientific results.
Measure state-read connection counts and repeated latency on the same synthetic ledger.
Run existing fixed performance witnesses and exact full appliance verification before integration
and again on the exact integrated source before activation. Reconcile through existing appliance
transition with a fresh WAL snapshot, preservation proof and stable request ID.

## Results and review

### Measured simplification

- One progression implementation in the existing runner. CLI handlers render results;
  no internal stdout/stderr capture, JSON parsing or synthetic argparse calls remain.
- Deleted the unused 185-line session scheduler and its 288-line test suite. Its
  `--force-all` alternate funnel has no replacement. Existing `advance`, control and
  queued-operation interfaces remain supported.
- Screen/trial preparation has one owner. Screen evidence publication sites: **7 → 1**.
  Duplicate spec-state decision trees: **2 → 1**. Four private progression switches
  (`status_registry`, `acquire_lease`, `check_outcome_memory`, `pending_mode`) are gone.
- Production Python (changed `src/` and the deleted script): **506 net lines removed**.
  No production module, schema, dependency, control service or persistence layer was added.
  New test/reference and decision-record files are intentionally excluded from this count.

### State-read performance

Same VPS, same isolated synthetic ledger and frozen dependencies; five batches of 100
calls per case plus an initial result check. Every call must reproduce the expected
state; the regression also requires exactly one SQLite connection per call.

| Projection / state | Before median ms | After median ms | Reduction |
|---|---:|---:|---:|
| Metadata / NEW | 2.935 | 1.115 | 62.0% |
| Artifact-aware / NEW | 2.989 | 1.185 | 60.4% |
| Metadata / STAGE1_REJECT | 2.914 | 1.194 | 59.0% |
| Artifact-aware / STAGE1_REJECT | 3.180 | 1.021 | 67.9% |

Connections per read fell **3 → 1** (66.7% fewer). Finalized trials still reconcile
their artifacts and therefore perform the necessary additional work. These are
small-ledger state-read measurements, not an end-to-end research or production
latency claim. Host load varied; connection count is the deterministic improvement.

### Behavioral evidence

The independent reference in `tests/fixtures/research_lifecycle_d1e9325.json` was
captured from the original pinned appliance source/interpreter using disposable
fixtures. The candidate reproduces causal verification, the rejected screen, the
native Stage-3 REVISE trial and the six-check rejection audit. It compares metrics,
selection clocks, return vectors, artifact digests, receipt identities and ledger
counts. Only fixture-root paths and wall-clock timestamps are normalized. The
root-dependent causal proof key is independently bound to its recorded artifact and
proof bytes before normalization. This is a regression witness, not scientific alpha.

Representative safeguards exercised:

| Obligation | Evidence |
|---|---|
| Native computation and family gates | Independent reference; causal-model execution spy forbids both legacy entry points; existing carry/meta/ML regressions |
| No duplicate effects or budget refund | Candidate/lineage accounting, concurrent duplicate requests, idempotent advancement and terminal reruns |
| Interrupted recovery | Real OS-kill test after admission; trial publication fault injection; original request reconciliation after deleting its mutable spec |
| Read authority | Metadata projection succeeds without artifact reads, stays non-authoritative after corruption, and verified advancement refuses the same corrupted artifact |
| Correct mutation owner | Actual selected-ledger contention; per-spec batch lease fairness; runtime context recheck under lease |
| Correct repository context | Direct advancement refuses registration under its own closed lifecycle even when the caller's working directory differs |
| Scientific boundaries | Frozen schema/intent/review, PIT fence, causality binding, selection debt, immutable audit and sandbox refusal regressions |

Initial focused suites passed 61 baseline tests, 66 preparation/recovery tests and
169 of 170 progression tests; the remaining progression test had a missing test
import and passed after correction. The expanded direct/regression batch passed
30 tests. The first broad mutable-environment run passed 1,424 tests and exposed
two obsolete source-string/location assertions plus two environment-integrity
fixture errors. The assertions now inspect their actual owner or execute the real
model behavior. Both appliance fixtures passed with bytecode writes disabled and
a private JIT cache. No scientific, integrity or performance gate was weakened.

Full release acceptance is recorded in the PR and the appliance's immutable
verification receipts: complete collection, the full suite, and the unchanged
serial <5 s Stage-1 / <50 ms cache-hit witnesses. Integration and activation require
the exact source/environment identity. The transition receipt records fresh WAL
backup, preservation, schema compatibility and actual pinned-runtime health.

### Direct review conclusion

Standards review: each changed owner hides its own lifecycle or persistence
decisions; no replacement orchestrator or new state store was introduced.
Spec review: scientific math, thresholds, execution policy, selection clocks,
frozen identities and historical evidence remain with their existing owners.
The obsolete force-all route and implementation-name assertions fail the deletion
test; lease, runtime, artifact and prospective-governance checks do not.

Two deliberate correctness improvements accompany the refactor: mismatched
hypothesis/contract subjects fail before preparation, and direct advancement uses
the selected registry's lease plus the runner repository's lifecycle. These are
fail-closed boundary corrections, not claims of identical diagnostics for malformed
inputs. Review was performed directly under the principal's no-delegation instruction.

### Limits and stopping decision

The golden witness is representative, not exhaustive equivalence for arbitrary
strategy code. The full suite retains its existing conditional skips/expected
failure, with exact reasons in the release receipt. No market study, holdout
qualification or trading permission follows from this work.

The runner retains substantial family-specific mathematics; the registry retains
the distinct receipt/accounting identities. Splitting these to meet a line target
would spread essential decisions. Dataset compilation, job supervision and
appliance integrity are retained because deleting their responsibilities loses
requirements. Runtime environment hashing still has its existing I/O cost.
The concurrent original checkout remains untouched; it is not the supported
operational launcher. This is the 80/20 stopping point for the present scope.
