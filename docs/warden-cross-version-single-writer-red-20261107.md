# Warden cross-version single-writer investigation — 2026-11-07

## Verdict

**PASSED — SAFE_SINGLE_WRITER for the tested legacy-governance-to-merged-Warden path.**

No genuine cross-version split-brain was reproduced. **Do not open a defect issue merely because the legacy and new lock filenames differ.** The legacy guard invokes the *merged* Warden CLI; while old governance holds `warden-writer.lock`, the invoked Warden also holds its canonical mutation lease for the full scientific computation. A competing direct Warden CLI receives exit code **75 / BUSY**, before a second reservation or validation read.

This is a **RED-phase investigation**, not a release recommendation or proof for untested writers. No production source, services, timers, credentials or holdout files were modified. No scientific qualification is implied.

## Exact revisions and scope

| Item | Observed |
| --- | --- |
| quant-lab merged baseline | `b654352ff2e3d2ed7163a212916a49766457590c` |
| AI-research-loop legacy checkout | `4b31646a19ea5d69fefd8f61f78cf34d4dc2a7f7` |
| Working tree | `test/red-warden-split-brain-20261107` (isolated DevSpace worktree) |
| Live source | `/home/quant/dev/quant-lab/registry/registry.db`, opened `mode=ro` by SQLite online backup |
| Synthetic fixture hypothesis | `HYP-RED-WRITER-20261107` |
| Synthetic fixture contract | `RED_WRITER_1H_V1` |
| Interrupted trial | `RUN-RED-WRITER-20261107-001` |
| Persisted screen attempt | `ATT-8f3cd97f35fdae8c-001` |

The checked-out merged release lacks a `.warden_manifest.sha256.meta.json` sidecar required by the legacy preflight. The test supplies a **test-only, explicitly unapproved** metadata sidecar, matched to the immutable release manifest, inside the disposable sandbox. It does **not** alter the manifest or assert a real approval. The fixture uses a separately initialized Git baseline solely because the legacy preflight compares the manifest against `HEAD`. Its provenance and results are synthetic.

## Entry point/lock analysis

- Installed `quant-social-research.service` currently launches `/usr/bin/flock -E 75 -n /home/quant/dev/quant-lab/registry/warden-writer.lock /home/quant/anaconda3/bin/python -m src.social_campaign ...`; the service was **inactive** when inspected. Its outer lock surrounds the campaign process.
- `governance_guard.run_warden()` opens and nonblocking-locks the **legacy** `registry/warden-writer.lock`, then invokes `uv run warden`. The actual legacy campaign normally calls `uv run warden` through `src.autoresearch._warden_call`, without invoking `governance_guard.run_warden()` inside the service outer flock.
- Merged `warden.cli.main()` wraps registry mutation commands in `mutation_lease(Path("registry/registry.db"))` **before** constructing the registry/runner. Lease identity resolves to a lockfile named `registry/.warden-mutation-<digest>.lock`, derived from the resolved DB path.
- The hourly pipeline additionally has `/tmp/quant_lab_hourly.lock`, which is not the canonical Warden lease and does not cross-coordinate external scientific actors on its own.
- A **separate nested-lock probe** invoking `governance_guard.run_warden()` under an already-held legacy outer `flock` is denied with `GOVERNANCE_BLOCKED`, exit **2**, because POSIX locks are acquired on different open file descriptions. This is a **SELF_LOCK_CONFLICT in that nesting arrangement**; **the installed service's normal invocation does not nest that guard**. Do not confuse this separate negative probe with the successful cross-version single-writer experiment.

## Deterministic experiment

One pytest integration test exercises **real OS processes, real flock, real SQLite transactions and the production entrypoint functions**. It does not mock Warden's lock or admission code.

1. SQLite online backup `src.backup(dst)` produces a WAL-consistent disposable DB. The test verifies distinct source/clone file identities, checks synthetic contract data provenance and prohibits `data/holdout` in the sandbox.
2. The legacy governance CLI, `python -m src.workflow.governance_guard --quant-lab <sandbox> run uv run warden backtest <spec> --json`, enters the actual merged `warden.cli`, durably reserves the synthetic trial, and enters a synthetic strategy process. The strategy atomically publishes an entry marker and blocks at a deterministic file barrier.
3. While the legacy process remains in computation, a second `python -m warden.cli screen <same-spec> --json` runs against the **identical resolved SQLite device/inode**.
4. Test records return code and admission marker; sends **SIGKILL** to the legacy process group; reconciles the consumed trial twice, admits one stage-1 screen after release, discards its CLI response, reconciles state twice, and verifies that a direct repeat is refused.
5. Historical rows and source database digest are verified. The test prints and optionally emits the full JSON evidence object.

Unlike test-only lockfile-name comparisons, the experiment asserts that the second CLI actually returns `BUSY` **before scientific computation**.

### Recorded observations

| Measurement | Actual |
| --- | --- |
| Source database SHA-256 before and after | `7b100ec72c623f18f91c9da341fd5c08ece375ac90ffb45293fc3b3d08192eba` (identical) |
| Disposable DB device / inode | `38 / 9447681` |
| Legacy lock inode | `9447703` |
| Merged mutation lease inode | `9447705` |
| Legacy governance PID | `3491593` |
| Legacy strategy subprocess PID | `3491654` |
| New Warden CLI PID | `3491805` |
| Legacy strategy admission UTC | `2026-11-07T15:14:06.087801+00:00` |
| Second writer BUSY response UTC | `2026-11-07T15:14:07+00:00` |
| Legacy outer lock during computation | held |
| Merged mutation lease during legacy computation | held |
| Second writer exit | **75**, JSON `status=BUSY` |
| Second writer computation marker | **absent** |
| Two scientific computations simultaneously | **false** |
| Interrupted legacy process | `SIGKILL`, exit **-9** |
| After crash, legacy trial receipt | `RESERVED`; after reconciliation `INTERRUPTED_REVIEW` |
| Synthetic trial budget | **1**, unchanged across crash/retries |
| After recovery, stage-1 screen | **one** `FINALIZED` screen receipt, one selection attempt, synthetic PASS |
| Stage-1 duplicate retry | rejected by `ScreenAlreadyAttemptedError`, exit **1**, no computation marker |
| Historical snapshot projection | **unchanged** |
| Protected holdout present in sandbox | **false** |
| Nested legacy lock probe | exit **2**, `GOVERNANCE_BLOCKED` |

The synthetic Stage 1 PASS is only a fixture result, **not market evidence or qualified alpha**. The interrupted backtest has **no finalized trial receipt**, correctly remains `INTERRUPTED_REVIEW` and must not be replayed automatically. The Stage 1 final receipt has a durable `screen_key`, `attempt_id`, and exact dataset digest.

The test did not instrument every possible file syscall; zero protected holdout access is supported by absence of the holdout tree in the sandbox, the explicit fixture contract path, and running only backtest/screen/reconciliation, **not by a comprehensive syscall tracer**.

### Reproducibility

Run on the VPS with access to the old checkout and canonical DB:

```bash
cd /home/quant/.devspace/worktrees/quant-lab-2f31dfa4
WARDEN_RED_EVIDENCE_JSON=.workspace/evidence/warden-cross-version-20261107.json \
PYTHONPATH=src /home/quant/dev/quant-lab/.venv/bin/python -m pytest -q \
  tests/test_warden_cross_version_single_writer_red.py::test_old_and_new_warden_cannot_both_admit_same_research_attempt
/home/quant/dev/quant-lab/.venv/bin/python -m ruff check tests/test_warden_cross_version_single_writer_red.py
```

The test uses a fresh temporary sandbox each invocation. `WARDEN_RED_SOURCE_REGISTRY` and `WARDEN_RED_OLD_REPO` may override source paths, but the former is **always read-only**. The baseline release must be an ancestor of the tested quant-lab revision. Deterministic barriers and bounded subprocess timeouts avoid relying on fixed sleeps for synchronization. Linux flock is required.

Evidence: [`.workspace/evidence/warden-cross-version-20261107.json`](../.workspace/evidence/warden-cross-version-20261107.json).

**Actual focused result:** `1 passed in 23.50s`. Ruff: `All checks passed!`.

## Smallest architecture improvement (not implemented)

**Recommendation: ADOPT a canonical Research Execution Authority (REA) for mutation admission** instead of relying on a filename convention. A single owner, with an idempotent API and persisted admission decisions, mediates every research entrypoint:

1. Schedulers, MCP adapters, CLI, and systemd wrappers request `admit(spec_id, operation_id)` from one authority and **never directly acquire an already-held lease inside its children**.
2. On one VPS, the REA may use a non-reentrant OS mutation lease **plus** a transactional SQLite admission/receipt. The CLI must either be the sole authority or explicitly receive a non-forgeable delegated execution token. Do not stack a parent and child acquisition of the same flock file.
3. Reserve the scientific attempt durably **before validation data is read**. An interrupted or uncertain receipt stays consumed/review-required. Lost-response retries return the old operation status without rerunning computation.
4. The authority must canonicalize the SQLite file identity and reject mismatched database aliases/symlinks; the current digest is path-derived rather than inode-derived. Use a stable configured registry identity for the one-VPS case.
5. For distributed execution, flock is insufficient across machines; use a central atomic admission store and **fencing tokens** checked at every commit boundary, with expiration/recovery policy explicitly preserving consumed scientific debt. Network leases alone are insufficient without fencing.

**Merely renaming locks is not recommended:** it leaves recursive acquisition ambiguity, scheduler restarts, MCP bypass paths, and future distributed writers unresolved.

## Cutover decision

**Do not declare production cutover safe on this test alone.** The tested legacy-governance → merged-Warden route maintained exclusion and crash-safe accounting. Untested paths include older standalone Warden binaries, direct registry API callers that bypass the CLI, a truly independent concurrent scheduler, and cross-host executors. Before cutover, inventory every registry-mutating entrypoint, verify delegation through one authority, and repeat acceptance against the actual installed runtime configuration.

No architectural fix, merge, deployment, service restart, or timer modification occurred. Since the cross-version path passed, no split-brain defect issue is justified by this evidence.
