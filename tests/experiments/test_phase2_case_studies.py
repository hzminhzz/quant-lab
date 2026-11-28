"""Engineering witnesses on generated fixtures only; never read market data."""
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest
from polars.testing import assert_frame_equal

from case_studies.phase2 import evidence, synthetic
from case_studies.phase2.closing_momentum import (
    cash_sessions,
    closing_features,
    closing_labels,
)
from case_studies.phase2.diagnostics import qlike, ridge_predict
from case_studies.phase2.volatility import (
    CANDIDATE_FEATURES,
    VARIANCE_FLOOR,
    validate_minute_bars,
    variance_labels,
    volatility_features,
)


@pytest.fixture(scope="module")
def minute_bars():
    return synthetic.synthetic_minute_bars()


@pytest.fixture(scope="module")
def index_fixture():
    return synthetic.synthetic_index_bars()


def test_d01_prefix_invariance_and_completed_bar_availability(minute_bars):
    decision = datetime(2000, 1, 3, 12, 5, tzinfo=UTC)
    full = volatility_features(minute_bars)
    prefix = minute_bars.filter(pl.col("timestamp") < decision)
    assert_frame_equal(
        volatility_features(prefix),
        full.filter(pl.col("decision_timestamp") <= decision),
    )
    assert full.filter(pl.col("feature_available_at") > pl.col("decision_timestamp")).is_empty()
    delayed = minute_bars.with_columns(
        pl.when(pl.col("timestamp") == decision - timedelta(minutes=1))
        .then(pl.col("available_at") + timedelta(seconds=1))
        .otherwise(pl.col("available_at")).alias("available_at")
    )
    assert volatility_features(delayed).filter(
        pl.col("decision_timestamp") == decision
    ).is_empty()


def test_d01_grid_boundary_and_exact_execution_label(minute_bars):
    features = volatility_features(minute_bars)
    assert features["boundary_count"].unique().sort().to_list() == [0.0, 1.0]
    for phase, expected in ((0, 0), (5, 0), (10, 1)):
        decision = datetime(2000, 1, 3, 12, phase, tzinfo=UTC)
        row = features.filter(pl.col("decision_timestamp") == decision).row(0, named=True)
        assert row["boundary_count"] == expected
        assert row["entry_timestamp"] == decision + timedelta(minutes=1)
        assert row["exit_timestamp"] == decision + timedelta(minutes=6)
    row = variance_labels(minute_bars).filter(
        pl.col("decision_timestamp") == decision
    ).row(0, named=True)
    opens = minute_bars.filter(
        pl.col("timestamp").is_between(row["entry_timestamp"], row["exit_timestamp"])
    )["open"].to_numpy()
    assert len(opens) == 6
    assert row["target_variance"] == pytest.approx(np.square(np.diff(np.log(opens))).sum())
    assert row["label_available_at"] == decision + timedelta(minutes=7)


def test_missing_minutes_are_not_filled(minute_bars):
    decision = datetime(2000, 1, 3, 12, 5, tzinfo=UTC)
    past_gap = minute_bars.filter(pl.col("timestamp") != decision - timedelta(minutes=10))
    future_gap = minute_bars.filter(pl.col("timestamp") != decision + timedelta(minutes=3))
    assert volatility_features(past_gap).filter(
        pl.col("decision_timestamp") == decision
    ).is_empty()
    assert variance_labels(future_gap).filter(
        pl.col("decision_timestamp") == decision
    ).is_empty()


def test_invalid_input_refused(minute_bars):
    first = minute_bars.head(10)
    for invalid in (
        pl.concat([first, first.head(1)]),
        first.with_columns(pl.col("timestamp") + timedelta(seconds=1)),
        first.with_columns(pl.col("timestamp").alias("available_at")),
        first.with_columns(pl.lit(-1.0).alias("open")),
    ):
        with pytest.raises(ValueError):
            validate_minute_bars(invalid)


def test_a01_real_calendar_dst_and_early_close():
    dates = cash_sessions("2024-03-08", "2024-03-11")
    assert dates["session_close"].dt.hour().to_list() == [21, 20]
    thanksgiving = cash_sessions("2024-11-27", "2024-11-29")
    assert thanksgiving["session_close"].dt.hour().to_list() == [21, 18]
    assert thanksgiving.height == 2  # Thanksgiving itself is closed.


def test_a01_prefix_missing_prior_and_execution_label(index_fixture):
    bars, sessions = index_fixture
    features = closing_features(bars, sessions)
    labels = closing_labels(bars, sessions)
    decision = datetime(2024, 11, 29, 17, 30, tzinfo=UTC)
    row = features.filter(pl.col("decision_timestamp") == decision).row(0, named=True)
    assert row["exit_timestamp"] == datetime(2024, 11, 29, 18, 0, tzinfo=UTC)
    assert row["entry_timestamp"] == decision + timedelta(minutes=1)
    assert row["feature_available_at"] <= decision
    assert_frame_equal(
        closing_features(bars.filter(pl.col("timestamp") < decision), sessions),
        features.filter(pl.col("decision_timestamp") <= decision),
    )
    label = labels.filter(pl.col("decision_timestamp") == decision).row(0, named=True)
    open_entry = bars.filter(pl.col("timestamp") == label["entry_timestamp"])["open"].item()
    open_exit = bars.filter(pl.col("timestamp") == label["exit_timestamp"])["open"].item()
    assert label["target_return"] == pytest.approx(np.log(open_exit / open_entry))
    assert label["label_available_at"] == label["exit_timestamp"] + timedelta(minutes=1)
    for missing in (decision - timedelta(minutes=2), datetime(2024, 11, 27, 20, 59, tzinfo=UTC)):
        incomplete = bars.filter(pl.col("timestamp") != missing)
        assert closing_features(incomplete, sessions).filter(
            pl.col("decision_timestamp") == decision
        ).is_empty()
    incomplete = bars.filter(pl.col("timestamp") != label["exit_timestamp"])
    assert closing_labels(incomplete, sessions).filter(
        pl.col("decision_timestamp") == decision
    ).is_empty()


@pytest.mark.parametrize("run,variance", [(synthetic.run_d01, True), (synthetic.run_a01, False)])
def test_deterministic_synthetic_only_and_chronological_folds(run, variance):
    result = run()
    repeated = run()
    assert result["scope"] == synthetic.SYNTHETIC
    assert_frame_equal(result["forecast_tape"], repeated["forecast_tape"])
    assert result["paired"] == repeated["paired"]
    tape = result["forecast_tape"]
    assert tape["decision_timestamp"].n_unique() == tape.height
    assert result["schedule"].height == 4
    assert result["schedule"].filter(
        pl.col("train_labels_known") >= pl.col("first_validation")
    ).is_empty()
    assert tape.select(pl.col("benchmark", "candidate").is_finite().all()).row(0) == (True, True)
    if variance:
        assert tape.select(pl.col("reference", "benchmark", "candidate").min()).row(0)[0] > 0
        assert tape["benchmark"].min() >= VARIANCE_FLOOR
        assert tape["candidate"].min() >= VARIANCE_FLOOR


def test_native_fold_preprocessing_and_same_samples(monkeypatch):
    calls = []
    original = synthetic.ridge_predict

    def record(x_train, y_train, x_validation, *, variance):
        assert np.max(np.abs(x_train.mean(axis=0))) < 1e-8
        calls.append((x_train.copy(), y_train.copy(), x_validation.copy()))
        return original(x_train, y_train, x_validation, variance=variance)

    monkeypatch.setattr(synthetic, "ridge_predict", record)
    synthetic.run_d01()
    assert len(calls) == 8
    for base, candidate in zip(calls[::2], calls[1::2], strict=True):
        np.testing.assert_array_equal(base[1], candidate[1])
        np.testing.assert_array_equal(base[0], candidate[0][:, :len(CANDIDATE_FEATURES) - 1])
        np.testing.assert_array_equal(base[2], candidate[2][:, :len(CANDIDATE_FEATURES) - 1])


def test_positive_variance_and_loss_validation():
    x = np.arange(20).reshape(-1, 1)
    predictions = ridge_predict(x, np.geomspace(1e-10, 1e-5, 20), x, variance=True)
    assert np.isfinite(predictions).all() and (predictions >= VARIANCE_FLOOR).all()
    with pytest.raises(ValueError):
        qlike([0.1], [0])
    with pytest.raises(ValueError):
        qlike([0.1], [1, 2])


def test_evidence_view_observes_only_and_fails_closed(tmp_path, monkeypatch):
    launcher = tmp_path / "warden"
    launcher.touch()
    monkeypatch.setattr(evidence, "LAUNCHER", launcher)
    payload = {
        "ok": True, "value": {
            "scientific_writes": 0, "evaluator_calls": 0, "sealed_access": False,
            "ledger": {"schema_version": 10, "counts": {"trials": 75}, "hypotheses": []},
            "runtime": {"status": "ACTIVE", "scientific_authority": False},
        }
    }

    def observe(args, **kwargs):
        assert args[:3] == [str(launcher), "control", "--request"]
        assert json.loads(args[3]) == {
            "schema_version": 1, "operation": "observe", "limit": 1,
            "hypothesis_id": "HYP-FIXTURE",
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload))

    monkeypatch.setattr(evidence.subprocess, "run", observe)
    assert evidence.governed_view("HYP-FIXTURE")["scientific_writes"] == 0
    with pytest.raises(ValueError):
        evidence.governed_view("../../holdout")
    payload["value"]["scientific_writes"] = 1
    with pytest.raises(RuntimeError):
        evidence.governed_view("HYP-FIXTURE")
    payload["value"]["scientific_writes"] = 0
    payload["value"]["sealed_access"] = True
    with pytest.raises(RuntimeError):
        evidence.governed_view("HYP-FIXTURE")
    assert launcher.read_bytes() == b""
