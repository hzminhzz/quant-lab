# Fix 4 — vectorise `_validate` + `generate_positions` (soc_wbx_downshock_1h_20261031)

Worktree: `/home/quant/.devspace/worktrees/quant-lab-ultraperf-20261119`
Branch: `perf/one-process-session-20261119` (no commit made)
Edited (only tracked file): `strategies/candidates/soc_wbx_downshock_1h_20261031.py`
Snapshot of the pre-edit module: `perf_audit/fix4/soc_wbx_old.py`
(`git show HEAD:strategies/...` — md5 `dd5f55241b245c4f1716c1397cb61db2`, identical to the live repo copy)

All python runs: `uv run --frozen --project /home/quant/dev/quant-lab python <script>` with cwd = worktree.
`warden` resolves to `/home/quant/dev/quant-lab/src/warden` (identical to worktree `src/warden`), read-only.

## What changed in the strategy

1. `_validate`
   - contiguous-hour proof: one `np.diff` over the distinct timeline (was `itertools.pairwise`
     over ~21,888 Python datetimes), compared to `np.timedelta64(1, "h")`.
   - per-asset alignment proof: one boolean-mask selection per asset out of the
     `(n_timestamps, 4)` grid of the sorted frame (was `filter(asset)` + `to_list()` × 4),
     plus an exact row-count test, so duplicate/missing asset rows still raise
     `Missing/misaligned hourly panel for <asset>` for the same asset as before.
   - OHLCV finiteness/bounds: per-asset numpy columns from a single reshaped view
     (was 5 polars filters + `select(...).item()` per asset). Same check order
     (nonfinite per column, then bounds) and same messages.
2. `generate_positions`
   - feature alignment proof read off the sorted `(timestamp, asset)` grid (was 4 ×
     `to_list()` comparisons of 21,888-element Python lists).
   - holding-window fill: `np.flatnonzero(event)` + `searchsorted(start + HOLD_BARS + 1)`
     with slice fills — O(events) instead of the per-bar Python state machine
     (87,552 iterations). Exit bar is still written to 0.0; an event on the exit bar is
     still ignored; the next eligible entry is still `start + 5`.
   - final 1x gross-exposure guard unchanged.

## Proof 1 — bit-identical on the real panel

`perf_audit/fix4/equiv_check.py` → exit 0, 53/53 checks PASS
(`perf_audit/fix4/equiv_out.txt`).

```
data       : data/exploratory/crypto_perps_top100_1h.parquet (worktree data -> live repo, read-only)
filter     : BTCUSDT/ETHUSDT/LTCUSDT/XRPUSDT, sorted timestamp,asset
rows       : 87,552   timestamps: 21,888   shock_events: 3,340
positions  : np.array_equal + .tobytes() equal; shape (21888,4), nonzero bars 5,343, sum 2,628.0
features   : pl.Series.equals on every column (old.compute vs new.compute)
```
Old positions also equal a re-run of the literal HEAD per-bar loop (`reference_weights`).

## Proof 2 — synthetic edge panels (contiguous hourly, 4 assets, valid OHLCV)

Same script; every scenario old == new bitwise and == the HEAD reference loop:

| scenario | observed event layout | result |
|---|---|---|
| no events | 0 events | identical, all-zero positions |
| dense consecutive events | run of 20 consecutive events | identical |
| event exactly on an exit bar (100, 104, 105) | 100 and 104 and 105 all events; 104 ignored | identical |
| event inside the last 4 bars (n-4, n-2, n-1) | all three events; windows truncated | identical |
| fixed patterns ({90,95}, {90,94}, {90,96}, {80,85,90,95,100}, {100..106}, {70,120,200,239}) | 2–7 events | identical |
| 6 randomised shock patterns, 520 bars | 11–40 events | identical |
| fill algorithm: exhaustive n=6,7,8,10 (1,632 cases) + 160 random long arrays | — | 0 mismatches |

## Proof 3 — prefix invariance

`perf_audit/fix4/bench_strategy.py` (fresh registry snapshot root under perf_audit/fix4, live registry opened mode=ro):
`warden.leakage.verify_prefix_invariance` → new: **PASS** (0.682 s), old: PASS (0.986 s).

Also `perf_audit/fix4/bench_after.py verify --reps 1` (bench_warden `fresh_root` fixture, scratch registry copy + HYP-BENCH-ULWPERF): rc=0, cold prefix-invariance 0.725 s, cold verify wall 1.561 s, warm 0.762 s, computes 19 cold / 9 warm
(baseline: cold 0.949 s / 2.032 s, warm 0.995 s, same compute counts) — `perf_audit/fix4/after_verify.txt`.

## Proof 4 — screen / backtest outcomes unchanged

| mode | before (live module, `perf_audit/bench_warden.py`) | after (worktree module, `perf_audit/fix4/bench_after.py`) |
|---|---|---|
| screen | `passed=False`, error null, rc=0 | `passed=False`, error null, rc=0 |
| backtest | `REVISE` ×4, error null, rc=0 | `REVISE` ×4, error null, rc=0 |

`perf_audit/fix4/before_screen.txt`, `after_screen.txt`, `before_backtest.txt`, `after_backtest.txt`.

## Proof 5 — fail-closed parity

22 malformed inputs (wrong contract id/frequency/mode/delay/label horizon, missing column,
5th asset, 38 bars, timestamp gap, duplicated block, dropped row, misaligned asset rows,
nonfinite close/volume, close>high, low<=0, high<low, negative volume, 30-min spacing,
subset universe, None universe, unregistered kwarg): identical exception type, message and
raising function in old and new (`fail_closed_parity cases=22 failures=[]`).

## Timing

In-process, real panel, 1 warm-up + 5 measured reps (`perf_audit/fix4/strategy_timing.txt`):

| function | old median | new median | speedup |
|---|---|---|---|
| `generate_positions` (full) | 0.23567 s | 0.10569 s | 2.23× |
| `_validate` | 0.07246 s | 0.02109 s | 3.44× |
| `pipeline.compute` (includes its own `_validate`) | 0.12045 s | 0.07230 s | 1.67× |

Backtest mode, 1 warm-up + 3 measured reps (host loadavg 4.5–6.1, other agents active):
- before: reps `3.168, 3.015, 2.849, 3.094`, median **3.015 s**; child subprocess wall 2.863/2.753/2.552/2.844
- after: reps `2.470, 2.248, 2.586, 2.692`, median **2.586 s**; child subprocess wall 2.244/2.030/2.337/2.440
- delta: −0.43 s median (−14%) on a run whose 2.3–2.9 s is dominated by the child
  interpreter import, which fix 4 does not touch.

## Tests

`uv run --frozen --project /home/quant/dev/quant-lab python -m pytest tests/experiments/test_soc_wbx_downshock_1h_20261031.py -q`
→ `3 passed`, rc=0.
`pytest tests/experiments -q` → `146 passed, 8 skipped`, rc=0.

## Commands + exit codes

```
1  git show HEAD:strategies/... > perf_audit/fix4/soc_wbx_old.py                                  rc=0
2  uv run --frozen --project ... python perf_audit/fix4/equiv_check.py                            rc=0  (53 checks PASS)
3  uv run --frozen --project ... python perf_audit/bench_warden.py backtest --reps 4               rc=0  (REVISE, median 3.015)
4  BENCH_STRAT_SRC=<worktree> uv run ... python perf_audit/fix4/bench_after.py backtest --reps 4    rc=0  (REVISE, median 2.586)
5  uv run --frozen --project ... python perf_audit/bench_warden.py screen --reps 1                  rc=0  (passed=False)
6  BENCH_STRAT_SRC=<worktree> uv run ... python perf_audit/fix4/bench_after.py screen --reps 1       rc=0  (passed=False)
7  BENCH_STRAT_SRC=<worktree> uv run ... python perf_audit/fix4/bench_after.py verify --reps 1       rc=0  (prefix invariance PASS)
8  uv run --frozen --project ... python perf_audit/fix4/bench_strategy.py                          rc=0  (timings, both PASS)
9  uv run --frozen --project ... python -m pytest tests/experiments/test_soc_wbx... -q             rc=0  (3 passed)
10 uv run --frozen --project ... python -m pytest tests/experiments -q                             rc=0  (146 passed, 8 skipped)
```

`perf_audit/bench_warden.py` was not modified (md5 `b7c0c8bd4e13cefc886041eb7f94b5a1`); the adapted
copy is `perf_audit/fix4/bench_after.py`, which only adds `STRAT_SRC = $BENCH_STRAT_SRC` and uses it
for the strategies copy source and the module load path.

## Notes / limits

- `cache/features/.gitkeep` shows as deleted and `scripts/run_spec_session.py` as untracked in
  `git status`; neither was touched by this task (another fix session shares this worktree).
  The only tracked file this task modified is the strategy.
- Backtest wall time is dominated by the strategy child interpreter import (fix 1/3 territory),
  so the wall-time delta understates the fix; the strategy-level numbers above isolate it.
- `np.diff` gap proof is exact for any polars datetime unit; comparing to `np.timedelta64(1, "h")`
  cannot mask a sub-hour offset. Non-hourly or non-datetime timelines still raise the same message.
