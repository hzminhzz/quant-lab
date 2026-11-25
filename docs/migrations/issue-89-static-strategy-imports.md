# Migration note — issue #89: static strategy imports (zero AST exemptions)

Status: worker branch `feat/t03-worktree-trust-and-ast`. Not signed, not published, no trust
cutover. Principal approval for a **versioned migration** of the two frozen strategy sources is
recorded in the t03-strict handoff; this note is the durable record of that migration.

## Why this migration exists

`warden_ops.integrity.check_ast_boundaries` bans, inside `strategies/`, the import roots
`sqlite3, aiosqlite, subprocess, httpx, requests, urllib, urllib3, socket, aiohttp, duckdb,
importlib` and the calls `__import__()`, `eval()`, `exec()` (including the `builtins.<name>(...)`
form). The ban exists because a strategy that loads code at import time can execute untracked
bytes that the trial's recorded code digest does not cover.

Two committed strategies violated the ban. Rather than keep a per-file checksum exemption — which
is a waiver by another name and also silently disabled the `eval`/`exec` bans for those files —
both sources were migrated. The exemption machinery (`_LEGACY_DYNAMIC_PRIMITIVE_EXEMPT`,
`_LEGACY_EXEMPT_STRATEGY_ROOTS`, `_STRATEGY_ROOTS_ACTIVE`,
`_is_legacy_dynamic_primitive_module`) is deleted. The scan now has zero exemptions.

## Historical originals (preserved in Git, not in the working tree)

Both files are unchanged on `main` at `cd7441abc8e9004bb6a1a2ca0db10411d6b4e3e6`; the originals
are recoverable byte-for-byte with `git show cd7441a:<path>`.

| Path | Original sha256 | Original closure digest |
|---|---|---|
| `strategies/candidates/s183_reference.py` | `ef0dd455567df35908ba918d959e44daeb8db097b49dd10930081db535a63f0d` | `e8997c9e96ce0c9bdca32cf81a430a55afefffbc1b4f1e51ef9bc144eac81582` |
| `strategies/candidates/social_stoic_xsmom_10d21d.py` | `538d27cc4437a7c8d5c6be81830d0241c7c4e1d5bcaef666280ce032ce0dd6df` | `1aeaeff53648a98d10a7145cf5a4e78fa35ba0456070a68aa79e7b3d87cd5815` |

The `s183_reference.py` closure digest is the value the registry recorded as
`strategy_code_sha256` on `RUN-RU-001` and `RUN-RU-002` (HYP-2026-127). The social-stoic file has
no recorded trial code hash: its `HYP-SOC-STOIC-XSMOM19-20261030` row carries `strategy_module=''`
and `trials_used=0`, and the only reference is the append-only `selection_attempts` row
`ATT-13ebd05b1001965b-001` (`standalone_screen`, `STAGE_1_SIGNAL`, `REJECT`).

## What changed

### `strategies/candidates/social_stoic_xsmom_10d21d.py`

```diff
-from __future__ import annotations
+from __future__ import annotations
+
+from datetime import timedelta
...
-                    next_rebalance = ts + __import__("datetime").timedelta(hours=REBALANCE_HOURS)
+                    next_rebalance = ts + timedelta(hours=REBALANCE_HOURS)
```

`__import__("datetime").timedelta` and `datetime.timedelta` are the same class, so the 21-day
rebalance step (`REBALANCE_HOURS = 21 * 24`), the universe, the sizing and the fill timing are
unchanged. This is a binding change only: no parameter, threshold or lookback moved.

One style-only edit rides along in the same loop, visible to reviewers: the pre-existing
`ruff check` SIM102 (nested `if ts.hour == 0 and index > LOOKBACK_HOURS:` / `if next_rebalance is
None or ts >= next_rebalance:`) was merged into one `and`-chained condition. Short-circuit order
is identical, so the branch is taken exactly when it was before; it is not a behavioural or
scientific change.

### `strategies/candidates/s183_reference.py`

The path load is replaced by a static import of a vendored subset:

```diff
-import importlib.util
-from pathlib import Path
+import trends_trading_book as tb
...
-TB_PATH = Path("data/external/trends-research/Live/2_Engine/trading_book.py")
+CODE_DEPENDENCIES = ("strategies/candidates/trends_trading_book.py",)
...
-def _tb() -> Any:
-    spec = importlib.util.spec_from_file_location("trends_trading_book", TB_PATH)
-    ...
+def _tb() -> Any:
+    return tb
```

`_tb()` is kept because `.workspace/investigations/trends_research_20261029/` scripts import it;
it now returns the vendored module instead of executing a file by path. The loader
(`warden.strategy_loader.load_module_from_path`) already puts a strategy's own directory on
`sys.path`, so no `sys.path` mutation is added to the strategy. `CODE_DEPENDENCIES` makes
`warden.codeid.code_closure` hash the vendored file explicitly (the sibling import resolves there
anyway, the declaration is the auditable statement of intent). Internal helper parameters were
renamed `tb` → `book` because `tb` is now a module-level name; call sites are identical.

### `strategies/candidates/trends_trading_book.py` (new, vendored)

Upstream `data/external/trends-research/Live/2_Engine/trading_book.py`, commit
`0426baca40b148b9fed760740a7b7ac2e4cb3a70`, upstream file sha256
`f7bfac2916b53eb436fb360fe2a183629cd15d2fe14647df4aeba6f5aff7f0a5`, MIT, copyright (c) 2026
Lucas Joly. Licence text: `strategies/candidates/trends_trading_book.LICENSE.txt`.

Only the pure, I/O-free functions the strategy calls are copied, body-for-body from the upstream
commit: `ewmac`, `_blended_std`, `daily_vol_abs`, `s_g_vol`, `skew`, `vov_inner`, `vov_smooth`,
`vov_mean_ann`, `vov_signal`, `normalise_signal`, `trend_sign`, `_recursive_sum`, `ewm_corr_4`,
`xs_return`, `cross_sectional_z`, plus the constants they read. Deliberately **not** vendored and
never executed by this repository: upstream's Norgate/FX/IRX data loading, `argparse` CLI,
worksheet caching, `hashlib` fingerprinting, `importlib` module loading, and `portfolio.py`.

## Scientific record: what this does and does not change

- No retune, no parameter change, no re-derivation. The signal, sizing and declared deviations in
  `s183_reference.py` are untouched; the vendored algebra is verified value-identical (below).
- `RUN-RU-001`/`RUN-RU-002` remain historical rows with their recorded digest. Because
  `warden.qualify` refuses a holdout qualification whose `code_closure` differs from the recorded
  `strategy_code_sha256`, those two trials are now **no longer qualification-bound**: new trials
  must bind the new bytes. This is the accepted cost of removing runtime-loaded, untracked code.
- No trial, budget counter, selection attempt, hypothesis row, holdout claim or ledger entry was
  created, deleted, reset or replayed. Nothing was re-executed to produce this change.
- The append-only `selection_attempts` record for the social-stoic screen is unchanged.

## Verification (performed on this branch)

1. Strict scan: `warden_ops.integrity.check_ast_boundaries(REPO_ROOT) == []` with the exemption
   machinery deleted, and the two pre-migration byte patterns fail closed at their old paths
   (`tests/test_ticket02_ast_boundaries.py::test_pre_migration_strategy_bytes_now_fail_closed`).
2. Vendored-vs-upstream value parity on identical synthetic inputs: every vendored function
   returns an identical series/tensor (null-for-null) to the upstream module loaded read-only from
   the primary checkout; constants match.
3. Old-vs-new strategy parity: the historical `HEAD` bytes of `s183_reference.py` and the migrated
   file produce an identical `build_signal_frame` (6400 rows) and identical `generate_positions`
   output (1600x4, `max|w| = 0.8756`) on the same synthetic panel.
4. Closure: `code_closure(REPO_ROOT, "strategies/candidates/s183_reference.py")` now returns
   `3675e6f25b04bec13ede7f12639375d149b14d4e33b1149028d5a9e168b059f4` over
   `[strategies/candidates/s183_reference.py, strategies/candidates/trends_trading_book.py]`.
5. `tests/test_ticket03_static_migration_parity.py` pins the vendored digest, the upstream
   attribution, the golden values, the closure membership and the social-stoic rebalance step.

## Post-migration digests (this branch)

| Path | sha256 |
|---|---|
| `strategies/candidates/s183_reference.py` | `8e4d477519c5b673868a68517434d6396922552cebe36ef62a66c8b1040db660` |
| `strategies/candidates/social_stoic_xsmom_10d21d.py` | `a4be8f79fb91db4581d3247f5fa1004a1900fce70ed5df42c75e311c000b50b8` |
| `strategies/candidates/trends_trading_book.py` (vendored) | `edeee90da4b0a1d49d8c89851defb442170167d8e523e2cfa63e122ed4bcb70c` |
| `s183_reference.py` code closure | `3675e6f25b04bec13ede7f12639375d149b14d4e33b1149028d5a9e168b059f4` |

## Rollback

Revert this commit. The originals return from `cd7441a`; the vendored module and its licence file
become unused. Reverting also restores the exemption machinery, which is the state #89 rejects, so
a rollback needs a principal decision, not a local edit.
