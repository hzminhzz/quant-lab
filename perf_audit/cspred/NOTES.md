# cs batched-predict refactor (5 modules) — spike, gates, timings

Worktree: `/home/quant/.devspace/worktrees/quant-lab-cs-batch-20261119`
Branch: `perf/cs-batch-20261119`, HEAD `29ccb57`. **No commit made.**
Edited (all tracked edits): `strategies/baselines/cs_ridge_1h.py`,
`strategies/candidates/{cs_lgbm_1h,cs_amihud_1h,cs_bab_1h,cs_vwap_tracking_error_1h}.py`.
Helpers + evidence: `perf_audit/cspred/` (untracked). Live repo untouched.

Env: cwd = worktree; every run
`uv run --frozen --project /home/quant/dev/quant-lab python …`.
python 3.13.5, numpy 2.5.4 (scipy-openblas, x86_64, X86_V3/AVX2 build),
sklearn 1.9.1, lightgbm 4.7.0, scipy 1.18.1, polars 2.0.0, 8 cpus.
No registry writes (only `mode=ro` backups into scratch roots), no holdout,
no qualify, no manifest.

---

## Part 1 — feasibility spike (result: predict YES, IC NO)

`perf_audit/cspred/spike_bitident.py` → `spike_out.json` (predict),
`perf_audit/cspred/spike_ic.py` → `spike_ic_out.json` (IC).

Panel `data/exploratory/crypto_perps_1h_v2.parquet` (105,024 rows, 13,128 bars,
8 assets), contract `CRYPTO_PERPS_1H_V2`; feature/label loops copied verbatim
from the HEAD bodies, so the only variable is the call shape.

### Batched predict per fold — bit-identical for both models

| model | fold | bars | rows | `np.array_equal` | `.tobytes()` | max abs diff |
|---|---|---|---|---|---|---|
| Ridge (`alpha=10`) | 1–4 | 1,086 each | 8,688 each | True | True | 0.0 |
| LGBM (15 leaves, lr .05, 30 trees, depth 3) | 1–4 | 1,086 each | 8,688 each | True | True | 0.0 |

Same fitted model, same rows, call shape swept over 1/2/4/8/16/32/64 bars:
identical at every width (both models). Ridge is a `dgemv` with k=3 either way;
LGBM prediction is per-row tree traversal, so batch size cannot change a row.

Per-fold OOS prediction cost (4 folds, whole OOS window):

| model | per-bar loop | one batched call | calls removed |
|---|---|---|---|
| Ridge | 0.2947 s | 0.0007 s | 4,344 |
| LGBM | 18.41 s | 0.0207 s | 4,344 |

(`fold_predict_total_cmp` in `spike_out.json` reports "not equal" only because
the 70,272 out-of-fold cells are NaN on both sides and `NaN != NaN`; every
fold's own block is equal, and `tobytes` equality confirms identical NaN bits.)

### IC loop (`np.corrcoef` per bar) — NOT bit-identical, not refactored

Literal HEAD loop vs three vectorised forms (row demean + `sum`, `einsum`,
`matmul` with the `(n-1)` factor kept), on the module's own 4,339 finiteprediction bars:

| variant | array_equal | bytes_equal | mismatching finite bars | max abs diff | max ulp | first mismatch |
|---|---|---|---|---|---|---|
| Ridge, `sum` | False | False | 3,149 / 4,339 (72.6%) | 5.55e-16 | 2,583 | bar 0, 1 ulp |
| Ridge, `einsum` | False | False | 3,173 / 4,339 (73.1%) | 4.44e-16 | 1,574 | bar 0, 1 ulp |
| Ridge, `matmul` | False | False | 3,091 / 4,339 (71.2%) | 5.55e-16 | 1,572 | bar 0, 1 ulp |
| LGBM, `sum` | False | False | 3,130 / 4,339 (72.1%) | 5.55e-16 | 785 | bar 0, 1 ulp |
| LGBM, `einsum` | False | False | 3,156 / 4,339 (72.7%) | 5.55e-16 | 785 | bar 0, 1 ulp |
| LGBM, `matmul` | False | False | 2,992 / 4,339 (69.0%) | 4.44e-16 | 334 | bar 2, 1 ulp |

Synthetic panels confirm it is not an 8-asset artefact: widths 3/4/8/19/100
assets, 4,000 rows each → 65–85% of bars differ, ≤6.1e-16, 18–4,122 ulp.
Cause: `np.corrcoef` subtracts means then forms the 2×2 covariance with a BLAS
`dot`/`dgemm` and divides by `sqrt(c00*c11)`; no element-wise reduction
reproduces that exact rounding chain. **Vectorising the IC loop would change
`ic_series` → science change → left untouched.** The loop costs 0.09–0.11 s per
run (measured), which is now a visible share of the LGBM path but out of scope.

---

## Part 2 — refactor (predict only)

Per fold, in all five modules: build the fold matrix once and predict once.

```python
        x_test = np.column_stack([
            <f1>[test_idx].ravel(),
            <f2>[test_idx].ravel(),
            <f3>[test_idx].ravel(),
        ])
        oos_preds[test_idx] = model.predict(np.nan_to_num(x_test, nan=0.0)).reshape(
            len(test_idx), n_assets
        )
```

`nan_to_num` is kept where HEAD applied it (amihud/bab/vwap); `cs_ridge_1h` and
`cs_lgbm_1h` never called it and still don't. `if len(train_idx) < 10: continue`
guards and the IC/position code are unchanged. `x[f[test_idx]]` is C-order
bar-major, which matches `column_stack([f1[t], f2[t], f3[t]])` row order, so the
row→(bar, asset) mapping is preserved by `reshape`.

Old modules for every gate: `perf_audit/cspred/old/` (`git show HEAD:<path>`),
md5 `c5174ab4…`, `24f3c338…`, `89fbb6cc…`, `86965ffb…`, `568668650…`.

## Gate 1 — positions and ic_series bit-identical on the real panel

`perf_audit/cspred/equiv_check.py` → `equiv_out.json` (rc 0). Compares
`np.array_equal`, `.tobytes()`, dtype and NaN masks.

| module | contract | assets | positions | ic_series | max abs diff | ic len |
|---|---|---|---|---|---|---|
| cs_ridge_1h | CRYPTO_PERPS_1H_V2 | 8 | identical | identical | 0.0 | 4,340 |
| cs_ridge_1h | CRYPTO_PERPS_TOP100_1H_V1 | 100 | identical | identical | 0.0 | 8,756 |
| cs_lgbm_1h | CRYPTO_PERPS_1H_V2 | 8 | identical | identical | 0.0 | 4,340 |
| cs_lgbm_1h | CRYPTO_PERPS_1H_V1 | 19 | identical | identical | 0.0 | 1,396 |
| cs_amihud_1h | CRYPTO_PERPS_1H_V2 | 8 | identical | identical | 0.0 | 4,339 |
| cs_amihud_1h | CRYPTO_PERPS_1H_V1 | 19 | identical | identical | 0.0 | 1,395 |
| cs_bab_1h | CRYPTO_PERPS_1H_V2 | 8 | identical | identical | 0.0 | 4,339 |
| cs_bab_1h | CRYPTO_PERPS_1H_V1 | 19 | identical | identical | 0.0 | 1,395 |
| cs_vwap_tracking_error_1h | CRYPTO_PERPS_1H_V2 | 8 | identical | identical | 0.0 | 4,339 |
| cs_vwap_tracking_error_1h | CRYPTO_PERPS_TOP100_1H_V1 | 100 | identical | identical | 0.0 | 8,755 |

(`array_equal` alone is False for the two ml baselines because 1 IC bar is NaN
in both — zero-variance predictions; `nan_mask_equal` + `tobytes` True, i.e. the
same NaN bit pattern.)

## Gate 2 — fail-closed parity

12 malformed inputs per module (empty panel, missing funding_rate/open/close,
1 and 20 timestamps, duplicated rows, all-NaN funding, single asset, NaN close
block, negative volume, reverse-sorted): **0 normalised mismatches** for all 5
modules (`mismatches_normalised: []`).

`duplicated_rows` differs in raw text only: both sides raise
`polars.exceptions.ComputeError: aggregation 'item' expected no or a single
value, got 2 values` and the *asset name* inside the message varies run to run —
the unmodified HEAD module printed `DOGEUSDT`, then `AVAXUSDT`, then `ADAUSDT` on
three consecutive runs of the identical input (polars parallel error ordering).
Message is normalised (`==v "<asset>"`) before comparison; exception type,
expression text and all other cases match exactly.

## Gate 3 — prefix invariance

`warden.leakage.verify_prefix_invariance` run directly on each module's
`pipeline` over the same panel: **PASS** for old and new in all 10 module/panel
pairs (`bench_strategy_out.json`), 0.17–3.33 s each.

## Gate 4 — screen / backtest verdicts on the fixture (specs that exist)

`perf_audit/cspred/bench_cs.py` (same governance as `bench_warden.py`: live
registry only `mode=ro` → sqlite backup into a scratch root, fixture hypothesis
`HYP-BENCH-ULWPERF` with fresh budget, `data/`+`contracts/` symlinks, roots
removed). Specs: `hyp_2026_002_ridge`, `hyp_2026_002_lgbm`, `hyp_2026_004_ridge`
(amihud), `hyp_2026_005_ridge` (bab), `failed/hyp_2026_063_t1` (vwap).

| spec | mode | before (live) | after (worktree) |
|---|---|---|---|
| 002_ridge | screen | `RuntimeError` ← `ValueError: Input y contains NaN` (fit) | identical error |
| 002_ridge | backtest | identical error | identical error |
| 002_lgbm | screen | `passed=False` | `passed=False` |
| 002_lgbm | backtest | `LinearBaselineRequiredError` (needs a ridge trial first) | identical error |
| 004_ridge | screen | `passed=False` | `passed=False` |
| 004_ridge | backtest | `REJECTED_EARLY`, mean_ic −0.0025872162665864723 | `REJECTED_EARLY`, same mean_ic |
| 005_ridge | screen | `passed=False` | `passed=False` |
| 005_ridge | backtest | `REJECTED_EARLY`, mean_ic −0.05526818317230871 | same |
| 063_t1 | screen | `passed=False` | `passed=False` |
| 063_t1 | backtest | `REJECTED_EARLY`, mean_ic −0.017884195618717028 | same |

Logs: `perf_audit/cspred/verdicts/*.txt`. `mean_ic` reproduces to the last digit
through the whole runner (`reproduce_returns_json` path), independent evidence
that the OOS prediction stream is unchanged.

Pre-existing, not caused here: `cs_ridge_1h` cannot run on `CRYPTO_PERPS_1H_V1`
at all — the 2020-era assets whose `open` is zero-filled give NaN forward labels
in the training block and it fits without a finite mask (`Input y contains NaN`
in both HEAD and refactored module). Its spec therefore errors on both sides.

## Gate 5 — timings (`generate_positions`, 1 warm-up + median of 5)

`perf_audit/cspred/bench_strategy.py` → `bench_strategy_out.json`.

| module | contract | old median | new median | speedup |
|---|---|---|---|---|
| cs_ridge_1h | V2 | 1.0291 s | 0.8063 s | 1.28× |
| cs_ridge_1h | TOP100 | 2.5283 s | 2.2045 s | 1.15× |
| cs_lgbm_1h | V2 | 12.9771 s | 0.8901 s | 14.58× |
| cs_lgbm_1h | V1 | 4.3896 s | 3.3091 s | 1.33× |
| cs_amihud_1h | V2 | 0.5787 s | 0.2936 s | 1.97× |
| cs_amihud_1h | V1 | 0.9091 s | 0.7757 s | 1.17× |
| cs_bab_1h | V2 | 0.6075 s | 0.2812 s | 2.16× |
| cs_bab_1h | V1 | 0.8538 s | 0.8189 s | 1.04× |
| cs_vwap_tracking_error_1h | V2 | 0.6369 s | 0.3724 s | 1.71× |
| cs_vwap_tracking_error_1h | TOP100 | 2.8151 s | 1.6221 s | 1.74× |

Host was shared (loadavg 1.7–7.2); the LGBM V2 old reps spread 8.9–38.6 s, so
that row is the noisiest. V1 rows are small (1,396 IC bars, 19 assets) and are
dominated by the untouched per-bar feature loops and IC loop.

## Tests

`pytest tests/experiments/test_hyp_2026_002.py tests/test_target_basis_alignment.py
tests/test_gates_and_runner.py tests/acceptance/test_rfc_acceptance.py -q`
→ **17 passed, 2 failed**.
The 2 failures (`test_hyp_2026_002_both_trials_recorded_with_ic_metrics`,
`test_early_rejection_does_not_contaminate_return_matrix`) read
`registry/registry.db` and assert pre-recorded `RUN-ML-00x` trials; they fail
identically at HEAD — proved by `git stash push -- <5 files>`, re-running
(2 failed), `git stash pop`, `md5sum -c` (all 5 files restored byte-identical).

## Commands

```
uv run --frozen --project /home/quant/dev/quant-lab python perf_audit/cspred/spike_bitident.py
uv run --frozen --project /home/quant/dev/quant-lab python perf_audit/cspred/spike_ic.py
uv run --frozen --project /home/quant/dev/quant-lab python perf_audit/cspred/equiv_check.py
uv run --frozen --project /home/quant/dev/quant-lab python perf_audit/cspred/bench_strategy.py
uv run --frozen --project /home/quant/dev/quant-lab python perf_audit/cspred/bench_cs.py {screen,backtest} \
    --spec <rel> --strat-src {/home/quant/dev/quant-lab,$PWD}
```

## Notes / limits

- 52 further `cs_*` candidates share the identical loop and would take the same
  edit, but only these 5 were refactored (task cap) and gated.
- IC vectorisation (`np.corrcoef` → batched reduction) is **rejected**: not
  bit-identical on this BLAS at any panel width; it remains a science change
  needing re-registration if ever wanted.
- Ridge gains are small because the per-bar feature standardisation loop, not
  predict, dominates; LGBM is 14.6× on V2 because 4,344 single-call predicts had
  ~4 ms interpreter/C-API overhead each.
