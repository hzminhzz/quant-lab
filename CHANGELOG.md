# Changelog

Notable changes to quant-lab. Commit refs are local (not pushed unless noted). Whenever protected
files change, the governance manifest (`.warden_manifest.sha256`) is re-signed by the human principal
— or by an agent under explicit in-session delegation, recorded in an approval trail.

## 2026-11-19

### Documentation contract (docs merge `2a8d0fc`; AGENTS.md region principal-approved)
- `AGENTS.md` gains a marker-delimited agent-maintained region (`omh:agent-instructions`): shared
  strategy scaffolds (`cs_common`/`ts_common`), the `CODE_DEPENDENCIES` declaration rule, evidence
  binding (fail-closed qualify, immutable contracts), unwired-family status, verified commands, the
  manifest generated-file map, and costed pitfalls. Hand-written sections are byte-for-byte unchanged.
  The write required the repo protection guard's explicit approval (granted by the principal); the
  manifest was re-signed under standing delegation (`approver=human-operator`).
- `CHANGELOG.md` added; `AGENT_RUNBOOK.md` rule 9 (full code closure + qualify binding + contract API);
  `docs/WORKFLOW.md` Stage-1 unwired families, holdout code binding, and symbol refs replacing stale
  line numbers.

### Strategy dedup backlog (commit on `refactor/dedup-backlog-20261119`)
- Trailing-ATR-stop overlays: the verbatim-identical stop loops across 20 `rules_ts` modules now call
  three shared `ts_common` helpers (`trailing_atr_stop_symmetric` / `_long_only` / `_symmetric_obs`),
  selected by exact-body clustering. Bit-identical on synthetic + real pinned panels.
- Legacy asset-column normalization: 38 top-level candidate modules call
  `ts_common.normalize_asset_column` instead of an inline `symbol`→`asset` guard. Bit-identical on
  asset- and symbol-column panels.
- Deferred (grounded): the cross-directory `symbol`→`asset` guards in `carver_daily/`, `books/`,
  `baselines/` stay inline — centralizing them would require reintroducing the per-module `sys.path`
  bootstraps the untangle removed. (`recovery/cs_*_v2` were deduped in the follow-up pass below.)
- Verify: `uv run --frozen pytest -q` (403 passed, 8 skipped); `uv run --frozen warden check-worktree`
  (rc=0; strategies are unprotected, so the manifest signature is unchanged).

### Recovery scaffold dedup (final pre-production pass)
- The four `recovery/cs_*_v2` historical variants now call `recovery/recovery_common.py::recovery_positions`,
  which owns their older walk-forward scaffold once (`contract.val_start` split, per-factor warmup,
  purged folds, top-k/bottom-k dollar-neutral book). The genuine structural differences between the
  copies are explicit named axes — `k_floor_one`, `ic_std_guard` (1e-8), `fill_missing_feature_assets` —
  never silent forks; each module keeps its own feature pipeline and public signature.
- Behavior bit-identical (`np.array_equal` on positions/ic_series/mean_ic) on synthetic + real pinned
  panels for all four. These are unwired historical artifacts: behavior is preserved, but their code
  identity (`strategy_code_sha256`) changes — re-run to re-bind before any future qualification.
- Verify: `uv run --frozen pytest -q` (403 passed, 8 skipped); `uv run --frozen warden check-worktree`
  (rc=0; strategies unprotected, manifest signature unchanged).

### Evidence integrity (merge `35335de`, commit `c081fba`)
- Code identity is now complete: `warden.codeid.code_closure` walks all imports (module-level and
  function-local) and unions a module's `CODE_DEPENDENCIES` declaration of runtime-loaded paths. The
  AF30 book declares its constituents; `s183_composite` declares its subdir helper. Editing any covered
  file now flips the recorded `strategy_code_sha256`.
- `warden qualify` no longer substitutes a baseline strategy: it fails closed on a missing/absent trial
  module and binds qualification to the trial's recorded `strategy_code_sha256` (the recomputed closure
  must match). Legacy trials without a code binding must be re-run to bind.
- `scripts/salvage_crypto.py` registers contracts through `ResearchRegistry.register_contract`
  (idempotent for an identical contract, refuses a changed one) instead of raw `INSERT OR REPLACE`,
  restoring contract immutability.
- New `tests/test_evidence_integrity.py` pins closure coverage, the `CODE_DEPENDENCIES` enforcement,
  contract immutability, and fail-closed qualification.
- Semantics change approved by the principal; design + authorization trail in `EVIDENCE_INTEGRITY.md`.
- Verify: `uv run --frozen pytest -q` · `uv run --frozen warden check-worktree` ·
  `uv run --frozen python scripts/check_loc_budget.py`.

### Whole-pass untangle (merge `4bb411c`, commit `c228019`)
- Behavior-preserving refactor: cross-sectional Ridge modules share
  `strategies/candidates/cs_common.py::cs_positions`; rules_ts sizing shares
  `strategies/candidates/ts_common.py::capped_waterfall_weights` (standard / `_clamp` / `_feasibility`
  variants). Every rewrite was proven bit-identical to HEAD on a parity harness before acceptance.
- Warden core: gate vocabulary + trial-budget cap centralized in `gates.py`; registry gained a public
  read surface; the lineage parent-walk cycle guard was fixed; file-sha256 / provenance-token /
  `HypothesisSpec` construction deduplicated; dead gates, methods and `Strategy` subclasses removed.
- Removed dead files (`run_vertical_slice.py`, `create_pr.sh`, one-shot audit/migration scripts,
  duplicate air templates); removed restating comments; tests cleaned (library tautologies removed,
  weakened asserts fixed, fresh-checkout bootstrap fix).
- Authorization + full triage: `UNTANGLE_APPROVAL.md`, `UNTANGLE_DECISION_LOG.md`.
- Verify: same commands as above.