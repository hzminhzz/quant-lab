# RG-09 integrated adversarial acceptance (2026-11-27)

Issue: [#128](https://github.com/hzminhzz/quant-lab/issues/128), parent [#119](https://github.com/hzminhzz/quant-lab/issues/119).

Scope: engineering verification only. No new strategy research, live registry
migration, production holdout read, signing, deployment or capital allocation.
All scoring witnesses use isolated synthetic fixtures. Historical production
evidence is inspected read-only and migrated only on a private SQLite snapshot.

## Integration baseline and reconciliation

- Feature branch: `feat/119-research-governance`.
- RG-08 starting commit: `2f64ef7e84e1d1070733c5617b1fe8c081868a9e`.
- After `git fetch origin`, the #109 integrated, signed baseline was
  `249436947ed9be808bb5b6f41cfdaeec03e3f5f7` (merge PR #129 at `7860f77`,
  followed by the manifest commit). It is an ancestor of this feature branch.
- The canonical live checkout remains at the divergent foundational commit
  `b1322ff996b0e31dfd6ac8576f59f1dd063e557b`. Its working tree includes
  concurrent research work and was not reset, stashed, rebased or modified.

The foundational commit's generic lake adapter, sandbox boundary and loader
changes are superseded by the integrated #109 implementations (contract
source/archive, resolver/compiler, policy, lake adapter, sandbox seal and
promotion). Use the integrated implementations and their tests, not a
cherry-pick of the older protected files or manifest. The original build
script and two local-only dataset/contract definitions remain on the live
checkout and its commit; they are not silently discarded or imported.

The read-only registry comparison found 20 registered contracts: 18 resolve
on the integrated tree with the exact recorded contract hash, including all
nine contract IDs used by the 75 historical trials. The other two,
`FX_MAJORS_1D_V3` (two registered hypotheses) and `INDICES_1D_V2` (one),
have no historical trials. Their definitions and pins are absent from the
integrated tree. Preserve those rows and local files. Before live cutover,
the principal must decide their lifecycle and resolve their data and policy
through the canonical #109 path; do not invent a replacement identity or
admit an obsolete contract merely to obtain a green gate.

## Behavioral witnesses

- Original two metadata-preflight requests: no strategy execution, economic
  outcome, trial or selection debt; metadata/schema/clock refusal preserved.
- Original eight screened variants: three admitted, five refused before
  scoring; the seventh related-lineage candidate is refused after six,
  including renamed byte-identical code and contract/cadence changes.
- New `tests/test_rg09_integrated_governance.py` exercises the two-preflight
  and eight-screen sequence with real approved intent, independent review,
  novelty, recorded revisions and candidate admission, without the
  pre-RG05 compatibility double. Three passing screens advance one frozen
  renamed spec to a finalized full trial without charging a fourth candidate.
  Exact replay is refused; all denied variants leave counts unchanged.
- The existing RG-02/06/08 controls cover frozen comparator identity,
  aligned net-return tapes, inference/sample inadequacy, benchmark
  inferiority despite an absolute PASS, authorized synthetic qualification,
  alias/time overlap, legacy exposure reconciliation, concurrent claims
  and crash-spent holdout state.
- #109 fixtures verify mandatory selection groups and retained search debt,
  the archived contract identity/recorded lookup, real synthetic lake
  resolution and compilation, signed policy, cash rebalancing and
  promotion without implicit holdout authorization.

### Review defect and repair

The scoped gateway previously accepted a prior verified digest and then
parsed a separately opened file without verifying the exact parsed bytes.
The synthetic wrong-digest regression failed at that boundary on the
RG-08 implementation. The governed holdout branch now checks its canonical
path and verifies the exact byte buffer before parsing it through the
existing gateway reader. A path or digest mismatch raises a scope incident;
qualification's existing incident handling retains the spent claim. It
does not add a new data reader or change the statistical/economic gates.
This closes the tested read/parse identity gap, not all possible filesystem
races or hostile same-UID behavior.

## Scratch migration and historical replay

Using the existing read-only WAL-consistent snapshot utility:

```bash
umask 077
.venv/bin/python scripts/registry_evidence_snapshot.py \
  --source /home/quant/dev/quant-lab/registry/registry.db \
  --destination /tmp/rg09-20261127-2f64ef7/registry.db
.venv/bin/python scripts/rehearse_state_machine_migration.py \
  --source /tmp/rg09-20261127-2f64ef7/registry.db \
  --destination /tmp/rg09-20261127-migration/registry.db
```

The first rehearsal attempt with a destination in the snapshot's own
directory was refused as intended; the independent-directory retry passed.
SQLite integrity check: ok. Schema v2 -> v8; idempotent; every pre-existing
column and row of all 17 tables matched the source, including 153
hypotheses, 75 trials, 244 selection attempts, 119 screen receipts, 75
attempt receipts, 6 qualifications, 6 holdout claims, 7 contract-search
states and 5 run-bound causal proof records. The migration created 6
legacy exposure records, zero batches and zero legacy scope attestations.
No historic scope or approval was fabricated.

Historical replay evidence: synthetic legacy-v1 replay and archived
recorded-lookup tests passed; all nine contract identities used by actual
historical trials resolve to their original hashes. Historical return
artifacts and selection debt were compared, not regenerated by running
market strategies.

## Verification commands

Run from the isolated feature worktree and locked existing virtualenv.

```bash
.venv/bin/python -m pytest -q -n 0 tests/test_rg09_integrated_governance.py --tb=short
.venv/bin/python -m pytest -q -n 4 --dist worksteal \
  tests/experiments/test_preflight_trial.py tests/test_rg0*.py \
  tests/test_holdout_claims.py tests/test_holdout_execution_parity.py \
  tests/test_spec109*.py tests/test_ml4t_lake_adapter.py \
  tests/test_venue_policy.py tests/test_cash_rebalancer.py tests/test_p2_t15.py \
  tests/test_state_machine_registry_backup.py \
  tests/test_ticket03_static_migration_parity.py tests/test_p2_t08.py
.venv/bin/python -m pytest -q -n 0 tests/test_rg09_integrated_governance.py \
  -k scoped_gateway --tb=short
.venv/bin/python -m pytest -q -n 4 --dist worksteal \
  tests/test_rg09_integrated_governance.py tests/test_rg08_holdout_batch.py --tb=short
.venv/bin/python -m pytest -q -n 4 --dist worksteal
.venv/bin/python -m pytest -q -n 0 tests/test_warden_leakage.py::test_content_addressed_feature_cache_hit_speed --tb=short
.venv/bin/python -m pytest -q -n 4 --dist worksteal
.venv/bin/ruff check src/warden/market_data.py tests/test_rg09_integrated_governance.py
git diff --check
```

- Initial integrated witness: 1 passed.
- Targeted integrated #109/#119 and replay/migration suite: 423 passed,
  before the additional digest-witness repair.
- New digest witness: 1 expected RED failure before repair.
- Post-repair focused RG-08/RG-09: 18 passed.
- First final full run: 1,349 passed, 42 skipped, 1 xfailed, 1 failed (123.26 s).
  The sole failure was the strict 50 ms cache-hit timing assertion
  (`tests/test_warden_leakage.py::test_content_addressed_feature_cache_hit_speed`):
  measured 94.2 ms, cold 0.223 s. Feature equality and no-recomputation
  assertions passed. An unchanged isolated rerun passed (1 test, 3.02 s).
  Host contention is plausible (load average 13.04 at subsequent inspection),
  not proven as the cause. No test or threshold was relaxed.
- Final unchanged full-suite retry: **1,350 passed, 42 skipped, 1 xfailed,
  zero unexpected failures** (124.28 s, exit 0).
- AST boundary scan: no violations. Changed-code Ruff and diff checks: clean.

## Production boundary and pre-cutover requirements

Read-only before/after fingerprints cover the live database and WAL,
all 17 table row streams, registry return/prediction files (76), tracked
working-tree files (1,350), and nonignored untracked files (18).
The canonical checkout had 16 dirty/untracked status entries, including
an untracked directory with multiple files. The protected holdout directory
is root:trader mode 0750; the test identity quant is not in the trader
group and cannot list it. Only directory metadata was checked, never
holdout contents or hashes. Fingerprints exclude SQLite shared-memory
coordination state; no claims are made about unrelated external activity.
The final comparison after the successful full-suite retry matched the
pre-test database/WAL, all 17 table contents, all 76 evidence files, all
1,350 tracked and 18 untracked file identities, canonical HEAD/status and
holdout directory metadata. The live schema remains v2.

The integrated branch's protected manifest remains stale and the normal
worktree integrity gate correctly refuses it. No manifest signature is
generated. Before a separate authorized cutover: reconcile the two
local-only registered contracts and concurrent work; verify/approve the
integrated protected inventory and its signature; review the migration
backup/rollback plan; provision a principal-signed exposure policy and
reconcile the six legacy exposure scopes. Authorization of a batch and
authorization of its read remain separate human decisions.

Known limitations: semantic equivalence and completeness of historical
exposure are principal-attested, not automated proofs. An uncertain
scope incident blocks new confirmatory qualification; no automated
incident recovery or refund is implemented. Application controls are not
a sandbox against hostile same-UID access. No independent external coding
agent was used; the standards/spec review was direct. Synthetic test
success and metadata replay establish engineering evidence only, not
market alpha, scientific qualification, deployment or trading authority.
