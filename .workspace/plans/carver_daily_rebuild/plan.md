# Plan: Carver AF30 daily strategy suite rebuild

This decomposes `spec.md` (17 book-faithful Robert Carver AF30 daily directional
strategies) into nine gated issues: shared primitives, five module families, governed
hypothesis registration, a fail-closed screen smoke, and the full 17-strategy screen
campaign with its persisted evidence. No issue runs `warden backtest`, touches
`data/holdout/`, or edits the legacy `strategies/candidates/ts_s*.py` modules.

## Findings that shaped the decomposition

- **`src/warden/` has one line of LOC headroom.** `scripts/check_loc_budget.py` fails at
  `warden_total >= 2400`; the tree is at 2399/2400. Therefore **no issue may edit
  `src/warden/`**; every acceptance criterion must be met by candidate code, specs,
  tests and scripts. This constrains criterion 8 (atomicity) to *proving* existing
  fail-closed behaviour, not changing it.
- **The screen path already fails closed on a raising strategy.** `run_stage1_screen`
  calls `_run_strategy_isolated` (subprocess `generate_positions`) before any
  `record_selection_attempt`; a raise propagates with no row written. `register_hypothesis`
  validates the dataclass and refuses duplicates before insert. Issue 8 pins both with
  regression tests; it must not add a warden retry fallback.
- **Hypothesis IDs 095–111 are free.** `registry.db` holds 95 hypotheses with
  `MAX(hypothesis_id) = HYP-2026-094`; no `HYP-2026-095..111` row exists and
  `selection_attempts` has 0 rows for any of them. Registration consumes no trial
  (`trials_used` defaults to 0) and screening writes only `selection_attempts`.
- **The screen subprocess resolves `strategies.*` through the working directory.** The
  isolated runner runs `python -c` with inherited cwd; `ExperimentRunner` pins
  `repo_root = Path(".")`, so cwd is the repo root and namespace-package imports work.
  Verified for `strategies.baselines.cs_ridge_1h` from `uv run python` and from pytest.
  A shared `carver_common` module is therefore importable in tests and in the sandbox;
  Issue 8 proves it end-to-end before the 17-strategy run.
- **`MULTI_ASSET_1D_V1` is present and pinned.** `data/exploratory/multi_asset_1d.parquet`
  hashes to the contract's `8f0ae063…d1f4`, 53,504 rows, columns
  `timestamp, asset, open, high, low, close, volume, funding_rate`, `bars_per_year: 312`,
  `execution_delay_bars: 1`, `NEXT_BAR`, 17 instruments. `contracts/datasets/MULTI_ASSET_1D_V1.json`
  exists, so `resolve_dataset` needs no change.
- **`warden verify` needs only a spec YAML** (contract, module, parameters), not a
  registered hypothesis; it calls `verify_prefix_invariance` at 3 cut points on the
  module's `pipeline.compute`. Registration is therefore a separate issue and does not
  block leakage proofs.
- **Existing precedent reused, not replaced.** `scripts/run_af30_screens.py` +
  `scripts/generate_campaign_report.py` show the register/verify/screen/aggregate shape;
  Issue 9 adds the daily analogue rather than generalising them. `experiments/active/af30/`
  is the precedent for per-campaign screen-spec subdirectories.

## Decisions carried by this plan

- **Family.** All 17 register as `strategy_family: rules_ts` on `MULTI_ASSET_1D_V1`,
  including S19. The `cross_sectional_ml` gate wants a per-bar IC series and a linear-ML
  baseline; S19 is a deterministic ranking rule, and the operating contract forbids
  forcing rules through cross-sectional IC.
- **Sizing.** Dimensionless continuous weights per instrument, per the spec's
  Position Sizing Convention; a shared `cash_vol_positions` helper implements
  `(forecast / 10) * (vol_target * w_i / vol_i)` with a per-instrument cap and 10%
  buffer, so all 17 differ only in forecast construction.
- **S21 stop removal is explicit.** The tightened Criterion 39 forbids the 3.0x ATR
  trailing stop that the prototype `strategies/candidates/carver_breakout_daily.py`
  carries; S21 gets its own module with close-only extrema, so no ATR column is computed
  at all in the new module.
- **Where the book leaves a number to the reader**, the default lives in the module
  constructor and is overridden in the screen YAML; the module docstring cites the page.
  Nothing is invented silently: the unit tests assert the configured value is honoured.

## Verification strategy

Each family issue ends with `uv run warden verify <spec>` printing
`PASS: Prefix-invariance verified` and `uv run pytest tests/experiments/test_carver_daily_suite.py -q`
green. Formula tests use synthetic series with hand-computable answers (monotone ramps,
staircases, constant ranges) and assert numeric equality to `1e-12`, plus the deadband
and no-stop invariants. The campaign issue ends with the full suite and
`uv run warden check-worktree` at exit 0.

## Open questions

None blocking. The spec defers S10/S11/S13/S15/S16/S20/S26/S27 to future work units;
nothing in this plan reopens them. Any new disarm of criterion 39 would need a spec
revision by the human principal, not a plan edit.

### Milestone: `v1.0-carver-daily - Carver AF30 daily strategy suite (17 modules)`

**Issue 1 - Package scaffold and shared Carver primitives**

Create `strategies/candidates/carver_daily/__init__.py` (empty) and
`strategies/candidates/carver_daily/carver_common.py` holding the pieces all 17 modules
share: `ASSET_CLASSES` (the 17 instruments mapped to `equity_us` / `equity_eu` /
`equity_asia` / `fx` / `metals` / `energy`, matching
`.workspace/af30_campaign/multi_asset_coverage_audit.md`), `ewmac(close, fast, slow)`,
`ann_vol(close, window, bars_per_year)` (raw unlagged statistic, never used for sizing),
`price_vol(close, window, bars_per_year)` (ann_vol lagged one bar, the sizing entry point),
`ewma_price_vol(close, span, bars_per_year)` (Carver's span-35 estimate, lagged),
`forecast_from_trend(trend, price_vol, scalar,
cap=20.0)`, `cash_vol_positions(forecast, vol, weights, vol_target, cap)`,
`apply_buffer(target, held, buffer_fraction, weight)`, `value_matrix(df, col, assets)` and
`observation_mask(df, assets)` (the sparse-session mask used by
`carver_breakout_daily.py`). No strategy module is created here.

Verification: `uv run pytest tests/experiments/test_carver_daily_suite.py -q` passes with
hand-computed cases — EWMAC sign/magnitude on a geometric ramp, `ann_vol` of a constant
1%/day series equals `0.01*sqrt(312)` within `1e-12`, `apply_buffer` holds the previous
position when `|target - held| <= buffer_fraction * weight` and moves it otherwise,
`cash_vol_positions` returns `forecast/10 * vol_target / vol` for `w=1`. Done when the
helpers are importable as `from strategies.candidates.carver_daily.carver_common import …`
from both `uv run python -c` and pytest.

**Issue 2 - S01-S04 buy-and-hold family**

Add `s01_buy_hold_single.py`, `s02_buy_hold_risk_scaled.py`,
`s03_buy_hold_variable_risk.py`, `s04_buy_hold_portfolio_scaling.py` under
`strategies/candidates/carver_daily/`, each exposing `pipeline` (a `compute(bars_df,
contract)` returning `timestamp, asset` plus forecast columns) and `generate_positions`.
Formulas per spec acceptance criterion 2: S01 constant `1/n_active` long weight from the
warmup bar with no rebalance, no vol targeting, no stop; S02 static long-term-historical
vol scaling at `tau = 0.16`; S03 60-day trailing daily volatility scaling at `tau = 0.16`,
re-evaluated every bar; S04 risk-weighted multi-asset book with dynamic cash-vol sizing
and portfolio IDM. No ATR column, no stop logic, no `rebalance_bars` loop in any of the
four.

Add screen specs `experiments/active/carver_daily/01_buy_hold_single_screen.yaml` …
`04_buy_hold_portfolio_scaling_screen.yaml` (`contract_id: MULTI_ASSET_1D_V1`,
`hypothesis_id: HYP-2026-095…098`, `strategy_family: rules_ts`, `model_type: rules`,
`strategy_module`, `parameters` naming every tuned window).

Verification: the suite gains per-module tests — S01 positions are constant after warmup
and identical when the price series is multiplied by 3; S02/S03 positions are inversely
proportional to configured vol; S04 gross exposure sums to the declared risk target within
cap; a 40% single-bar crash with the stop-era prototype would exit but here does not.
`uv run warden verify experiments/active/carver_daily/0N_*_screen.yaml` prints
`PASS: Prefix-invariance verified` for all four. Done when 4 verifies and the suite pass
and `git status --porcelain strategies/candidates/ts_s*.py` is empty.

**Issue 3 - S05-S09 trend family**

Add `s05_slow_trend_long_only.py`, `s06_slow_trend_long_short.py`,
`s07_slow_trend_strength.py`, `s08_fast_trend_strength.py`, `s09_ewmac_multi.py`.
Per spec: S05 EWMAC(64, 256) filter with `max(trend, 0)` long-only constraint; S06
`symsign` of the same filter; S07 EWMAC(64, 256) trend strength divided by daily price
volatility and scalar `1.91`, clipped to `[-20, 20]`; S08 EWMAC(16, 64) strength with
scalar `3.75`, clipped, plus the 10% continuous-weight buffer; S09 the six-speed ensemble
`(2,8), (4,16), (8,32), (16,64), (32,128), (64,256)` with the Table 33 scalars, combined
with `FDM = 1.41`, clipped, buffered. Every module uses dynamic cash-vol sizing and no
stop. Add the five screen specs (`HYP-2026-099…103`).

Verification: suite tests — S05 is never negative; S06 sign matches `sign(EWMA_64 -
EWMA_256)` on a monotone ramp; S07/S08 forecasts equal `trend/price_vol*scalar` clipped
for a constructed series that would exceed the cap; S08 holds the old weight across a
sub-10% forecast change; S09 equals the mean of the six speed forecasts times the Table 33
scalars times 1.41 before clipping; no module emits a zeroed position on an adverse bar
that a stop would have triggered. Five `warden verify` invocations print
`PASS: Prefix-invariance verified`.

**Issue 4 - S12, S14, S17 volatility- and level-normalised trend family**

Add `s12_adjusted_trend.py`, `s14_spot_trend.py`, `s17_normalised_trend.py`. S12 adjusts
EWMAC trend forecasts by rolling daily price-volatility regimes using the Table 45
scalars; S14 builds the spot-trend proxy from front-contract price relative to moving
averages so no back-adjustment roll drag is introduced; S17 constructs the cumulative
normalised price index `p / sigma_t` first and evaluates EWMAC on that index. All three
use dynamic cash-vol sizing and no stop. Add the three screen specs
(`HYP-2026-104…106`).

Verification: suite tests — S17's forecast on a constant-volatility ramp equals the EWMAC
of the raw price divided by `sigma`, i.e. normalisation is a pure scale; S12 switches
regime scalars exactly at the configured vol breakpoints and never consults a future
volatility bucket; S14 positions depend only on `close` and its own trailing moving
averages (a test perturbs `high`/`low` after the cut and asserts no change). Three
`warden verify` invocations print `PASS: Prefix-invariance verified`.

**Issue 5 - S18 asset-class composite and S19 cross-sectional momentum**

Add `s18_trend_asset_classes.py` and `s19_xs_momentum.py`. S18 groups the 17 instruments
by `carver_common.ASSET_CLASSES`, builds an equal-weighted composite normalised price
index per class, evaluates EWMAC on each class index, and allocates the class forecast to
its constituents. S19 ranks instruments inside each class by 252-day return minus the
class mean, holds the long top half and short bottom half, scaled by cash volatility with
the 10% buffer. Both remain `rules_ts`. Add the two screen specs (`HYP-2026-107`, `108`).

Verification: suite tests — S18's class index equals the mean of member normalised prices
and a single distressed member cannot flip the class forecast of a uniformly trending
class; S19 emits zero net exposure to the class mean (equal long and short counts) and
ranks deterministically under symbol permutation (asset-independence). Two `warden
verify` invocations print `PASS: Prefix-invariance verified` — this is the leakage proof
that class-level aggregation is causal.

**Issue 6 - S21 breakout, S22 value, S23 acceleration**

Add `s21_breakout.py`, `s22_value.py`, `s23_acceleration.py`, implementing tightened
Criterion 39 exactly. S21: `roll_max_t = max(p_{t-h+1}, …, p_t)`, `roll_min_t =
min(p_{t-h+1}, …, p_t)` over `h = 80` **daily closing prices only** (never `high`/`low`),
`midpoint_t = (roll_max + roll_min)/2`, `range_t = roll_max - roll_min`, raw forecast
`40 * (p_t - midpoint_t)/range_t`, EWMA smoothing with `span = h/4 = 20`, Table 91 scalar
`0.73`, clipped to `[-20, 20]`, dynamic cash-vol sizing, 10% buffering around held
positions, **zero trailing stop** under `NEXT_BAR` execution. S22: 1,260-day within-class
outperformance, scalar `7.27`, clipped, cash vol, 10% buffer, no stop. S23: rate of change
of EWMAC trend forecasts across speeds 8, 16, 32, 64 with the Table 100 scalars, cash vol,
10% buffer, no stop. No `atr`, `stop_*` or peak/trough arrays may appear in the new
modules. Add the three screen specs (`HYP-2026-109…111`).

Verification: suite tests — (a) S21 rolling extrema over a hand-built 80-close staircase
equal `max`/`min` of those closes, and perturbing `high`/`low` leaves the forecast bitwise
identical (this is the explicit anti-OHLC assertion); (b) an 80-day flat range with a
price at the top yields `+20` after smoothing/scalar/clip, and a price above the range
does not exceed `+20`; (c) S21 holds the prior weight for a forecast move under 10% of the
buffer width and holds through a 40% adverse bar that a 3x ATR stop would have closed;
(d) S22 sign flips when the 1,260-day relative return changes sign; (e) S23 equals the
finite difference of each speed's EWMAC forecast times its Table 100 scalar. Three
`warden verify` invocations print `PASS: Prefix-invariance verified`.

**Issue 7 - Register hypotheses HYP-2026-095 through HYP-2026-111**

Write 17 YAML files `.workspace/hypotheses/registered/HYP-2026-095.yaml` …
`HYP-2026-111.yaml` (one per module from Issues 2-6) with `contract_id:
MULTI_ASSET_1D_V1`, `strategy_family: rules_ts`, a per-strategy `expected_holding_bars`
that reflects the strategy's natural horizon, an `economic_mechanism` naming the
counterparty and friction, `falsification_criteria` mirroring the family gate, and
`source_provenance` naming the chapter and pages. Register each with
`uv run warden register-hypothesis <file> --json` (no batch path exists and none is added).

Verification: every registration exits 0 and prints `Registered hypothesis 'HYP-2026-095'`
-style output; then `sqlite3 registry/registry.db "SELECT COUNT(*) FROM hypotheses WHERE
hypothesis_id BETWEEN 'HYP-2026-095' AND 'HYP-2026-111'"` returns 17, all 17 have
`contract_id='MULTI_ASSET_1D_V1'` and `trials_used=0`, and
`SELECT COUNT(*) FROM trials` is unchanged from before the issue. Re-running a registration
is refused with no duplicate row (idempotent refusal, not silent overwrite).

**Issue 8 - Screen-path smoke and fail-closed atomicity proof**

Run one live screen: `uv run warden screen
experiments/active/carver_daily/01_buy_hold_single_screen.yaml --json` for HYP-2026-095.
This is the only issue that proves the isolated `python -c` subprocess can import
`strategies.candidates.carver_daily.carver_common`, and that `generate_positions` returns
a matrix shaped to the 17-instrument x daily-index panel. Then add regression tests to
`tests/experiments/test_carver_daily_suite.py` using the existing `tests/conftest.py`
`tiny_repo` fixture: a module whose `generate_positions` raises makes `run_stage1_screen`
raise and leaves `SELECT COUNT(*) FROM selection_attempts WHERE hypothesis_id=?` at 0 and
`trials_used` at 0; registering a hypothesis whose module already holds an unrelated
lineage-less registration is refused and inserts no row.

Verification: the screen prints a canonical JSON payload and
`SELECT COUNT(*) FROM selection_attempts WHERE hypothesis_id='HYP-2026-095' AND
kind='standalone_screen'` returns 1; `uv run pytest tests/experiments/test_carver_daily_suite.py -q`
passes including the two new failure-path tests; no `src/warden/` file is modified
(`git status --porcelain src/warden` empty) and `uv run warden check-worktree` exits 0.

**Issue 9 - Full 17-strategy screen campaign, evidence artifacts and final gate**

Add `scripts/run_carver_daily_campaign.py`, following the shape of
`scripts/run_af30_screens.py`: for each of the 17 specs run `uv run warden verify` then
`uv run warden screen --json` from the repo root, skip work already recorded (never
re-screen a recorded hypothesis), collect metrics, and write
`.workspace/af30_campaign/carver_daily_screens.json` (one record per hypothesis with
`hypothesis_id`, `strategy`, `module`, `passed`, `verdict`, `exit_code`, `metrics`,
`notes`, `raw_payload`) plus `.workspace/af30_campaign/carver_daily_screens_report.md`
(a table of all 17 with profit factor, conditional edge, trade edge, time in market,
average holding bars, pass/fail and notes, and a disclosure section stating that no
`warden backtest` ran and no holdout data was read). Never abort on a rejecting screen:
`warden screen` exits 1 on a legitimate REJECT and that verdict must be recorded, not
retried or re-tuned.

Verification: `SELECT COUNT(*) FROM selection_attempts WHERE kind='standalone_screen' AND
hypothesis_id BETWEEN 'HYP-2026-095' AND 'HYP-2026-111'` returns exactly 17; the same
query over `trials` returns 0; all 17 `warden verify` runs printed
`PASS: Prefix-invariance verified`; `carver_daily_screens.json` parses with 17 records
each carrying a non-empty `raw_payload`; the report has 17 data rows. Final gate:
`uv run pytest -q` passes with zero failures, `uv run warden check-worktree` exits 0 with
`src/warden/` still at 2399 LOC, and `git status --porcelain` shows no change to
`strategies/candidates/ts_s*.py`, `contracts/`, or `data/`.
