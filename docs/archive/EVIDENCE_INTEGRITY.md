# Evidence-integrity fixes — design + verification record

Principal instruction (2026-11-19, Hermes TUI): fix the three HIGH evidence-integrity flags
"think of the best architecture at the abstraction standpoint, then execute" — explicitly
lifting the preserve-behavior mandate for these three.

## The one principle
Warden's promise is **evidence binding**: every attestation binds to the exact, complete,
immutable artifact identity (code + config + data) that produced it, and **fails closed** when
the binding cannot be established. The three flags were three breaks of that one property:
- F18 qualify fallback = **substitution** (attest to a baseline instead of the trial's code).
- F32 salvage raw SQL = **mutation** (overwrite an immutable contract).
- F29/F30 codeid gaps = **under-coverage** (digest misses runtime-loaded / subdir / function-local code).

## Architecture decision
Complete code identity via **one authority** (`warden.codeid.code_closure`): union of all imports
(module-level AND function-local, `ast.walk`) plus a named module-level `CODE_DEPENDENCIES`
declaration of runtime-loaded repo-relative paths. The book declares `CODE_DEPENDENCIES =
STRATEGY_MODULES`; s183_composite declares its subdir `carver_common`. An enforcement test makes
the declaration reliable repo-wide. Chosen over runtime-capture-in-the-loader because the latter
crosses the isolated-child execution seam, changes digest timing + the screen cache key, and needs
~80-120 core lines — breaching the 2700 LOC cap (2658 used). Runtime capture is the future deepening
if the cap is raised. `register_contract` was already idempotent-identical / immutable-different, so
F32 is just routing salvage through it.

## Changes
- `codeid.py`: `ast.walk` all imports; `_declared_dependencies` reads `CODE_DEPENDENCIES` (literal
  list or a name bound to one); closure unions declared runtime deps recursively.
- `books/af30_handcrafted_book_1h.py`: `CODE_DEPENDENCIES = STRATEGY_MODULES` (20 constituents).
- `candidates/s183_composite.py`: `CODE_DEPENDENCIES = ("strategies/candidates/carver_daily/carver_common.py",)`.
- `qualify.py`: removed the baseline fallback; fail closed on missing/absent strategy_module; added
  the code binding — recompute `code_closure` and require equality with the trial's recorded
  `strategy_code_sha256` (fail closed on absent binding or mismatch).
- `scripts/salvage_crypto.py`: replaced raw `INSERT OR REPLACE INTO contracts` with
  `registry.register_contract` (identical re-salvage no-ops; changed contract fails closed).
- `tests/test_evidence_integrity.py` (new, 8 tests): closure covers book constituents + ts_common +
  s183 subdir helper; runtime-loaders must declare CODE_DEPENDENCIES; register_contract
  idempotent/immutable; qualify fail-closed on missing module / absent file / absent binding / changed code.
- Legacy qualify fixtures (test_rfc_acceptance RUN-QUAL-001, test_redteam RUN-ZERO/RT-01/GD-01) updated
  to carry real code bindings.

## Verification
- pytest **403 passed / 8 skipped** (395 baseline + 8 new integrity tests).
- src/warden **2695 / 2700 LOC**; `warden check-worktree` rc=0; ruff clean on all touched files.
- Closure proof: book -> 22 files (book + 20 constituents + ts_common); s183 -> 2 files (s183 + carver_common).

## Semantics change (principal-approved)
- Code digests are now COMPLETE: editing any runtime-loaded constituent / subdir helper / function-local
  import flips the digest, so causality audit check 3 and qualification correctly fail closed.
- Qualification no longer substitutes code and requires a recorded code binding; legacy trials without a
  binding must be re-run to bind (fail closed). Re-verification of old trials whose code changed in a
  previously-missed file now correctly fails closed (the old binding was under-covered).
- No gate threshold changed; no contract edited; no registry row deleted; data/holdout never accessed.

## Manifest re-sign authorization (principal-delegated)
- Fix instruction (verbatim, chat): "I want it got fix also, think of the best architecture at the
  abstraction standpoint, then excecute" — lifts preserve-behavior for these three flags.
- Landing authorization (verbatim, clarify response): "Merge to main + re-sign under your standing
  delegation — commit, merge --no-ff, re-sign as human-operator, record an approval trail".
- Standing signing delegation (verbatim, earlier this session): "I approve you sign on my behave".
  The agent executes as the principal's instrument, not self-approval (same pattern as
  UNTANGLE_APPROVAL.md and perf_audit/core/APPROVAL.md).
- Pre-read: stale meta attested the untangle manifest (approved_at 2026-11-19T09:27:03Z,
  manifest_sha256 3e0f4132…); regenerated unsigned manifest bf4d9c36…; WARDEN_NON_INTERACTIVE and
  WARDEN_HUMAN_SIGNING_KEY unset.
- Command: uv run --frozen python -c "from warden.cli import main; main(['generate-manifest','--human-approve-manifest'])"
  (plain direct dispatch; the env -u prefix silently no-ops in this terminal — see UNTANGLE_APPROVAL.md).
- Result: rc 0; mode=interactive, approver=human-operator, approved_at=2026-11-19T10:19:53+00:00,
  meta.manifest_sha256 == sha256sum(.warden_manifest.sha256) = bf4d9c36…; audit entry recorded in
  .workspace/audit/toolcalls.jsonl; check-worktree rc=0.