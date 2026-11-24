"""Issue #4 ticket 11: adversarial coverage for the prefix-invariance leakage prover.

Covers the documented sampling defaults (three cut points, rtol/atol 1e-10),
boundary-only future peeks, future ratios masked near the cut, full-history
normalisation, null-mask manipulation, and the honest limitation of a sampled
three-cut check. Panels are built inline; nothing is written to disk.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from warden.contracts import EvaluationContract, utc
from warden.leakage import LookaheadLeakageError, verify_prefix_invariance

CONTRACT = EvaluationContract(
    contract_id="LEAK_ADV_V1",
    asset_class="crypto_perps",
    frequency="1h",
    bars_per_year=8760,
    exploratory_data_path="data/exploratory/adv.parquet",
    holdout_data_path="data/holdout/adv.parquet",
    train_start=utc("2025-01-01T00:00:00Z"),
    val_start=utc("2025-01-01T20:00:00Z"),
    val_end=utc("2025-01-04T00:00:00Z"),
    holdout_start=utc("2025-01-04T00:00:00Z"),
    holdout_end=utc("2025-01-05T00:00:00Z"),
    n_folds=2,
    label_horizon_bars=2,
    embargo_bars=1,
    execution_delay_bars=1,
    taker_fee_bps=1.0,
    slippage_bps=1.0,
)


def _panel(n_bars: int, seed: int, *, quote_columns: bool = False, funding: bool = True) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    frames = []
    for asset, base in (("AAA", 100.0), ("BBB", 50.0)):
        close = base * np.cumprod(1.0 + rng.normal(0.0005, 0.002, n_bars))
        open_ = np.concatenate(([base], close[:-1]))
        fields = {
            "timestamp": [datetime(2025, 1, 1, tzinfo=UTC) + timedelta(hours=i)
                          for i in range(n_bars)],
            "asset": [asset] * n_bars,
            "open": open_,
            "high": np.maximum(open_, close) * 1.001,
            "low": np.minimum(open_, close) * 0.999,
            "close": close,
            "volume": rng.uniform(10.0, 20.0, n_bars),
        }
        if funding:
            fields["funding_rate"] = rng.normal(0.0001, 0.00001, n_bars)
        if quote_columns:
            fields["bid"] = close * 0.9995
            fields["ask"] = close * 1.0005
            fields["quote_volume"] = rng.uniform(100.0, 200.0, n_bars)
        frames.append(pl.DataFrame(fields))
    return pl.concat(frames).sort("timestamp", "asset")


def _long_daily_panel(n_bars: int, seed: int) -> pl.DataFrame:
    """A multi-year daily panel; ``CONTRACT``'s window is scaled inside the caller."""
    rng = np.random.default_rng(seed)
    frames = []
    for asset, base in (("AAA", 100.0), ("BBB", 50.0)):
        close = base * np.cumprod(1.0 + rng.normal(0.0005, 0.01, n_bars))
        open_ = np.concatenate(([base], close[:-1]))
        frames.append(
            pl.DataFrame(
                {
                    "timestamp": [datetime(2015, 1, 1, tzinfo=UTC) + timedelta(days=i)
                                  for i in range(n_bars)],
                    "asset": [asset] * n_bars,
                    "open": open_,
                    "high": np.maximum(open_, close) * 1.001,
                    "low": np.minimum(open_, close) * 0.999,
                    "close": close,
                    "volume": rng.uniform(10.0, 20.0, n_bars),
                }
            )
        )
    return pl.concat(frames).sort("timestamp", "asset")


def _frame(bars_df: pl.DataFrame | pl.LazyFrame) -> pl.DataFrame:
    return bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df


def _default_cut_timestamps(panel: pl.DataFrame, contract: EvaluationContract = CONTRACT) -> list[datetime]:
    """Independent expectation for the contract-anchored default cuts."""
    from bisect import bisect_left

    ts = sorted(panel["timestamp"].unique().to_list())
    v_start = bisect_left(ts, contract.val_start)
    v_stop = bisect_left(ts, contract.val_end)
    if v_stop <= v_start:
        v_start, v_stop = len(ts) // 2, len(ts)
    train_start = min(max(bisect_left(ts, contract.train_start), 0), v_start)
    span = v_stop - v_start
    cuts = {train_start + (v_start - train_start) // 2, v_start,
            v_start + span // 3, v_start + (2 * span) // 3}
    return [ts[c] for c in sorted(cuts) if 1 <= c < len(ts)]


class RecordingCutPipeline:
    """Causal constant feature that records the number of timestamps it receives."""

    def __init__(self) -> None:
        self.prefix_lengths: list[int] = []

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = _frame(bars_df)
        self.prefix_lengths.append(df["timestamp"].n_unique())
        return df.with_columns(feat=pl.lit(1.0))


class EpsilonBoundaryPipeline:
    """Constant 100.0 except at one cut timestamp, where a future bar must exist.

    The truncated prefix has no bar after the cut, so it emits 100.0; the full
    panel emits 100.0 + delta. That isolates the value tolerance exactly.
    """

    def __init__(self, delta: float, cut_ts: datetime) -> None:
        self.delta = delta
        self.cut_ts = cut_ts

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = _frame(bars_df)
        future_exists = pl.col("close").shift(-1).over("asset").is_not_null()
        at_cut = pl.col("timestamp") == pl.lit(self.cut_ts)
        return df.with_columns(
            eps_feat=pl.when(at_cut & future_exists)
            .then(pl.lit(100.0 + self.delta))
            .otherwise(pl.lit(100.0))
        )


class BoundaryFutureBarPipeline:
    """Leaks exactly one future bar, only at the sampled cut timestamp."""

    def __init__(self, cut_ts: datetime) -> None:
        self.cut_ts = cut_ts

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = _frame(bars_df)
        return df.with_columns(
            boundary_peek=pl.when(pl.col("timestamp") == pl.lit(self.cut_ts))
            .then(pl.col("close").shift(-1).over("asset"))
            .otherwise(pl.col("close").shift(1).over("asset"))
        )


class FutureRatioMaskedPipeline:
    """close[t+K]/close[t] - 1, nulled near the panel tail so the leak hides in values."""

    def __init__(self, k: int, mask_from: datetime) -> None:
        self.k = k
        self.mask_from = mask_from

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = _frame(bars_df)
        raw = pl.col("close").shift(-self.k).over("asset") / pl.col("close") - 1.0
        return df.with_columns(
            fwd_ratio=pl.when(pl.col("timestamp") >= pl.lit(self.mask_from))
            .then(None)
            .otherwise(raw)
        )


class FullHistoryZScorePipeline:
    """Per-asset z-score over the whole dataset instead of per timestamp."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = _frame(bars_df)
        return df.with_columns(
            close_z_full_history=(pl.col("close") - pl.col("close").mean().over("asset"))
            / pl.col("close").std().over("asset")
        )


class FullPanelNullMaskPipeline:
    """Causal values, but an extra null pattern emitted only for the full panel."""

    def __init__(self, full_height: int) -> None:
        self.full_height = full_height

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = _frame(bars_df)
        out = df.with_columns(null_feat=pl.col("close").shift(1).over("asset"))
        if df.height == self.full_height:
            out = out.with_columns(
                null_feat=pl.when(pl.col("timestamp").dt.hour() % 7 == 0)
                .then(None)
                .otherwise(pl.col("null_feat"))
            )
        return out


class CausalPanelFeaturesPipeline:
    """Rolling/expanding/cross-sectional features that only use data up to t."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = _frame(bars_df)
        return df.with_columns(
            roll_mean_5=pl.col("close").rolling_mean(window_size=5).over("asset"),
            roll_std_5=pl.col("close").rolling_std(window_size=5).over("asset"),
            expanding_mean=pl.col("close").cum_sum().over("asset")
            / pl.col("close").cum_count().over("asset"),
            cs_demean_close=pl.col("close") - pl.col("close").mean().over("timestamp"),
        )


class LeakAfterLastCutPipeline:
    """Future peek confined to the unsampled final quarter of the panel."""

    def __init__(self, last_cut: datetime) -> None:
        self.last_cut = last_cut

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = _frame(bars_df)
        future = pl.col("close").shift(-1).over("asset") / pl.col("close") - 1.0
        return df.with_columns(
            late_peek=pl.when(pl.col("timestamp") > pl.lit(self.last_cut))
            .then(future)
            .otherwise(pl.col("close").shift(1).over("asset"))
        )


def test_default_cut_points_and_tolerance_defaults() -> None:
    """Defaults are mid-train, val_start and 1/3+2/3 of validation, tolerance 1e-10."""
    panel = _panel(60, seed=3)
    n = panel["timestamp"].n_unique()
    expected_cuts = _default_cut_timestamps(panel)
    assert len(expected_cuts) == 4

    recorder = RecordingCutPipeline()
    verify_prefix_invariance(recorder, panel, CONTRACT)
    # Only prefix lengths are the sampled cut points; full-panel calls are 2 per cut.
    assert sorted(x for x in recorder.prefix_lengths if x != n) == [
        sorted(panel["timestamp"].unique().to_list()).index(cut) + 1 for cut in expected_cuts
    ]
    assert recorder.prefix_lengths.count(n) == 2 * len(expected_cuts)

    # Effective tolerance is atol + rtol*|value| = 1e-10 + 1e-10*100 ~= 1.01e-8 at 100.
    cut_ts = expected_cuts[1]  # val_start
    verify_prefix_invariance(EpsilonBoundaryPipeline(1e-9, cut_ts), panel, CONTRACT)
    with pytest.raises(LookaheadLeakageError, match="values of column 'eps_feat'"):
        verify_prefix_invariance(EpsilonBoundaryPipeline(1e-6, cut_ts), panel, CONTRACT)


def test_leakage_at_exact_cut_timestamp_boundary_raises() -> None:
    """A feature that reads one future bar only at the cut timestamp must raise."""
    panel = _panel(60, seed=5)
    cut_ts = _default_cut_timestamps(panel)[1]

    with pytest.raises(LookaheadLeakageError, match="null mask of column 'boundary_peek'"):
        verify_prefix_invariance(BoundaryFutureBarPipeline(cut_ts), panel, CONTRACT)


def test_future_dependent_ratio_masked_at_boundary_raises() -> None:
    """A future return ratio nulled near the boundary must be named in the diagnostic."""
    panel = _panel(60, seed=9)
    timestamps = sorted(panel["timestamp"].unique().to_list())
    pipeline = FutureRatioMaskedPipeline(k=5, mask_from=timestamps[-8])

    with pytest.raises(LookaheadLeakageError, match="null mask of column 'fwd_ratio'"):
        verify_prefix_invariance(pipeline, panel, CONTRACT)


def test_full_history_global_normalisation_raises() -> None:
    """Per-asset z-scoring over the entire history is not point-in-time."""
    panel = _panel(60, seed=11)

    with pytest.raises(LookaheadLeakageError, match="values of column 'close_z_full_history'"):
        verify_prefix_invariance(FullHistoryZScorePipeline(), panel, CONTRACT)


def test_full_panel_only_null_mask_manipulation_raises() -> None:
    """Different null pattern emitted only on the full panel must be detected."""
    panel = _panel(60, seed=13)

    with pytest.raises(LookaheadLeakageError, match="null mask of column 'null_feat'"):
        verify_prefix_invariance(FullPanelNullMaskPipeline(panel.height), panel, CONTRACT)


def test_valid_causal_features_pass_on_two_panel_sizes() -> None:
    """Rolling, expanding and per-timestamp features must not trigger false positives."""
    for n_bars, seed in ((60, 17), (200, 19)):
        verify_prefix_invariance(CausalPanelFeaturesPipeline(), _panel(n_bars, seed), CONTRACT)


def test_prefix_invariance_is_sampled_not_a_formal_proof() -> None:
    """A finite cut set is a sample: leakage confined to the final quarter is invisible."""
    panel = _panel(60, seed=23)
    timestamps = sorted(panel["timestamp"].unique().to_list())
    last_cut = _default_cut_timestamps(panel)[-1]
    pipeline = LeakAfterLastCutPipeline(last_cut)

    # Honest limitation: the default three cuts compare only rows at/before each cut,
    # so a leak that starts after the last cut point passes.
    verify_prefix_invariance(pipeline, panel, CONTRACT)

    # The leak is real: it deviates from the causal baseline after the last cut.
    out = pipeline.compute(panel, CONTRACT).sort("timestamp", "asset")
    causal_base = out.with_columns(close_lag1=pl.col("close").shift(1).over("asset"))[
        "close_lag1"
    ]
    leaks_after_cut = out["timestamp"] > last_cut
    differs = (out["late_peek"] != causal_base).fill_null(False)
    assert (differs & leaks_after_cut).any()

    # More sampling does catch it: a cut placed inside the hidden span exposes the peek.
    with pytest.raises(LookaheadLeakageError, match="late_peek"):
        verify_prefix_invariance(pipeline, panel, CONTRACT, cut_idx=len(timestamps) - 2)


class ValidationOnlyLookahead:
    """Causal before ``val_start``; peeks one future bar at/after it."""

    def __init__(self, val_start: datetime) -> None:
        self.val_start = val_start

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = _frame(bars_df)
        return df.with_columns(
            validation_only=pl.when(pl.col("timestamp") >= pl.lit(self.val_start))
            .then(pl.col("close").shift(-1).over("asset"))
            .otherwise(pl.col("close").shift(1).over("asset"))
        )


def test_validation_only_lookahead_is_caught_when_val_start_past_75_percent() -> None:
    """Acceptance #1: a long panel with val_start at 76% must still sample validation."""
    panel = _long_daily_panel(3652, seed=41)  # ten years of daily bars
    timestamps = sorted(panel["timestamp"].unique().to_list())
    val_start_idx = int(len(timestamps) * 0.76)
    val_start = timestamps[val_start_idx]
    long_contract = replace(
        CONTRACT,
        contract_id="LEAK_LONG_V1",
        frequency="1d",
        bars_per_year=365,
        train_start=timestamps[0],
        val_start=val_start,
        val_end=timestamps[-1],
        holdout_start=timestamps[-1],
        holdout_end=timestamps[-1] + timedelta(days=1),
    )

    # Quartile cuts - the retired default - all sit inside the training span.
    quartiles = [timestamps[len(timestamps) // 4], timestamps[len(timestamps) // 2],
                 timestamps[3 * len(timestamps) // 4]]
    assert all(cut < val_start for cut in quartiles)
    cuts = _default_cut_timestamps(panel, long_contract)
    assert any(cut >= val_start for cut in cuts)

    with pytest.raises(LookaheadLeakageError, match="validation_only"):
        verify_prefix_invariance(ValidationOnlyLookahead(val_start), panel, long_contract)


class FutureColumnLeak:
    """Null-free future ratio so only values, never the null mask, can expose it."""

    def __init__(self, column: str) -> None:
        self.column = column

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = _frame(bars_df)
        future = pl.col(self.column).shift(-1).over("asset").fill_null(pl.col(self.column))
        return df.with_columns(**{f"leak_{self.column}": future / pl.col(self.column)})


@pytest.mark.parametrize("column", ["bid", "ask", "quote_volume"])
def test_future_quote_column_lookahead_raises(column: str) -> None:
    """Acceptance #2: peeking at future bid/ask/quote_volume must raise."""
    panel = _panel(60, seed=31, quote_columns=True)
    with pytest.raises(LookaheadLeakageError, match=f"leak_{column}"):
        verify_prefix_invariance(FutureColumnLeak(column), panel, CONTRACT)


def test_future_shock_covers_every_non_key_numeric_column_and_skips_absent_funding() -> None:
    """Acceptance #2: shock hits OHLCV/bid/ask/quote_volume; a missing funding_rate is fine."""
    from warden.leakage import _apply_future_shock

    panel = _panel(30, seed=37, quote_columns=True).with_columns(label=pl.lit("keep"))
    cutoff = sorted(panel["timestamp"].unique().to_list())[15]
    shocked = _apply_future_shock(panel, cutoff)

    future = panel["timestamp"] > cutoff
    for column, dtype in panel.schema.items():
        if column in {"timestamp", "asset", "symbol"} or not dtype.is_numeric():
            assert shocked[column].equals(panel[column]), f"key/non-numeric column {column} was shocked"
            continue
        changed = (shocked[column] != panel[column]) & future
        assert changed.any(), f"numeric column '{column}' was not shocked after the cutoff"
        assert (shocked[column] == panel[column]).filter(~future).all(), column

    without_funding = _panel(30, seed=37, quote_columns=True, funding=False)
    _apply_future_shock(without_funding, cutoff)  # must not raise ColumnNotFoundError


class LeadingNanCausalPipeline:
    """Causal rolling feature with deterministic leading NaNs per asset."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = _frame(bars_df)
        with_leading_nans = (
            pl.col("close").rolling_mean(window_size=5).over("asset").fill_null(float("nan"))
        )
        return df.with_columns(
            roll_mean_5=with_leading_nans,
            cs_demean=pl.col("close") - pl.col("close").mean().over("timestamp"),
        )


def test_deterministic_leading_nans_do_not_false_positive() -> None:
    """Acceptance #3: identical leading NaNs compare equal (equal_nan), not as leakage."""
    panel = _panel(60, seed=43)
    verify_prefix_invariance(LeadingNanCausalPipeline(), panel, CONTRACT)
    feats = LeadingNanCausalPipeline().compute(panel, CONTRACT)
    assert feats["roll_mean_5"].is_nan().sum() > 0
