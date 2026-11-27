"""RG-02: comparisons of existing, synthetic, timestamped return evidence only."""

from dataclasses import replace
from hashlib import sha256

import numpy as np
import pytest

from warden_evidence.benchmark_comparison import (
    BootstrapSettings,
    ComparisonBasis,
    ComparisonError,
    ReturnTape,
    compare_return_artifacts,
)
from warden_evidence.return_artifacts import return_frame


@pytest.fixture
def pair(tmp_path):
    root = tmp_path / "returns"
    root.mkdir()
    clock = 1_700_000_000_000_000_000 + np.arange(120, dtype=np.int64) * 86_400_000_000_000
    benchmark = 0.0002 + 0.005 * np.sin(np.arange(clock.size) * 0.4)
    candidate = benchmark + 0.002
    # Zero-exposure sessions remain part of BOTH tapes and the expected clock.
    candidate[::5] = benchmark[::5] = 0.0
    basis = ComparisonBasis(
        dataset_sha256="a" * 64,
        calendar_id="synthetic-session-calendar",
        information_set_sha256="b" * 64,
        execution_policy_sha256="c" * 64,
        cost_policy_sha256="d" * 64,
        risk_basis_sha256="e" * 64,
        simulation_epoch="fixture-v1",
        observation_unit="session",
        periods_per_year=252,
    )

    def tape(name, values, timestamps=clock, comparison_basis=basis):
        path = root / f"{name}.parquet"
        return_frame(values, timestamps).write_parquet(path)
        return ReturnTape(
            run_id=name, path=path, artifact_sha256=sha256(path.read_bytes()).hexdigest(),
            code_sha256="f" * 64, config_sha256="1" * 64, basis=comparison_basis,
        )

    settings = BootstrapSettings(
        seed=731, block_length=8, n_resamples=300, confidence_level=0.95,
        min_observations=60, min_blocks=4,
    )
    return root, clock, candidate, benchmark, tape, settings


def compare(pair, candidate, benchmark, **kwargs):
    root, clock, *_, settings = pair
    options = {"returns_root": root, "expected_timestamps_ns": clock, "settings": settings}
    options.update(kwargs)
    return compare_return_artifacts(candidate, benchmark, **options)


def test_paired_superiority_is_reproducible_and_retains_flat_sessions(pair):
    _, clock, c, b, tape, _ = pair
    candidate, benchmark = tape("candidate", c), tape("benchmark", b)
    result = compare(pair, candidate, benchmark)
    assert result == compare(pair, candidate, benchmark)
    assert result["status"] == "DIAGNOSTIC"
    assert result["n_observations"] == clock.size
    assert result["candidate_zero_returns"] == result["benchmark_zero_returns"] == 24
    expected = np.sqrt(252) * (c.mean() / c.std(ddof=1) - b.mean() / b.std(ddof=1))
    assert result["delta_sharpe"] == pytest.approx(expected)
    assert result["mean_net_return_delta"] == pytest.approx((c - b).mean())
    assert result["delta_sharpe_ci"][0] > 0
    assert result["bootstrap_method"] == "paired_circular_block_bootstrap_v1"
    assert result["settings"]["seed"] == 731
    assert result["basis"]["observation_unit"] == "session"
    assert result["candidate"]["artifact_sha256"] == candidate.artifact_sha256
    assert "qualified" not in result
    assert compare(pair, benchmark, candidate)["delta_sharpe_ci"][1] < 0
    assert sha256(candidate.path.read_bytes()).hexdigest() == candidate.artifact_sha256


def test_identical_variable_tapes_have_exact_zero_increment(pair):
    _, _, c, _, tape, _ = pair
    result = compare(pair, tape("one", c), tape("two", c))
    assert result["status"] == "DIAGNOSTIC"
    assert result["delta_sharpe"] == 0
    assert result["delta_sharpe_ci"] == [0.0, 0.0]


def test_sample_adequacy_and_degenerate_returns_are_inconclusive(pair):
    _, _, c, b, tape, settings = pair
    candidate, benchmark = tape("one", c), tape("two", b)
    result = compare(pair, candidate, benchmark, settings=replace(settings, min_observations=121))
    assert result["status"] == "INCONCLUSIVE"
    assert result["sample_adequate"] is False
    assert result["delta_sharpe_ci"] is None
    result = compare(pair, candidate, tape("flat", np.zeros_like(b)))
    assert result["status"] == "INCONCLUSIVE"
    assert result["delta_sharpe"] is None


@pytest.mark.parametrize("field", [
    "dataset_sha256", "information_set_sha256", "execution_policy_sha256",
    "cost_policy_sha256", "risk_basis_sha256", "calendar_id",
    "simulation_epoch", "observation_unit", "periods_per_year",
])
def test_incomparable_basis_is_refused(pair, field):
    _, _, c, b, tape, _ = pair
    candidate = tape("one", c)
    value = "2" * 64 if field.endswith("sha256") else (365 if field == "periods_per_year" else "different")
    benchmark = tape("two", b, comparison_basis=replace(candidate.basis, **{field: value}))
    with pytest.raises(ComparisonError, match="basis"):
        compare(pair, candidate, benchmark)


def test_missing_or_shifted_sessions_are_refused_even_if_both_agree(pair):
    _, clock, c, b, tape, _ = pair
    with pytest.raises(ComparisonError, match="clock"):
        compare(pair, tape("one", c), tape("two", b, clock + 1_000))
    with pytest.raises(ComparisonError, match="clock"):
        compare(pair, tape("three", c[1:], clock[1:]), tape("four", b[1:], clock[1:]))
    with pytest.raises(ComparisonError, match="clock"):
        compare(pair, tape("five", c, None), tape("six", b))


def test_tape_digest_missing_and_nonfinite_evidence_fail_closed(pair):
    _, _, c, b, tape, _ = pair
    candidate, benchmark = tape("one", c), tape("two", b)
    with pytest.raises(ComparisonError, match="digest"):
        compare(pair, replace(candidate, artifact_sha256="0" * 64), benchmark)
    with pytest.raises(ComparisonError, match="artifact"):
        compare(pair, replace(candidate, path=candidate.path.with_name("absent.parquet")), benchmark)
    invalid = c.copy()
    invalid[3] = np.nan
    with pytest.raises(ComparisonError, match="finite"):
        compare(pair, tape("invalid", invalid), benchmark)


def test_missing_comparator_and_invalid_settings_are_refused(pair):
    _, _, c, _, tape, settings = pair
    with pytest.raises(ComparisonError, match="benchmark"):
        compare(pair, tape("one", c), None)
    with pytest.raises(ComparisonError, match="block_length"):
        replace(settings, block_length=1)
    with pytest.raises(ComparisonError, match="seed"):
        replace(settings, seed=-1)


def test_tape_cannot_escape_returns_tree(pair, tmp_path):
    _, clock, c, b, tape, _ = pair
    candidate, benchmark = tape("one", c), tape("two", b)
    outside = tmp_path / "outside.parquet"
    return_frame(c, clock).write_parquet(outside)
    with pytest.raises(ComparisonError, match="under the registry returns tree"):
        compare(pair, replace(candidate, path=outside), benchmark)


def test_sample_adequacy_requires_enough_blocks(pair):
    _, _, c, b, tape, settings = pair
    result = compare(
        pair, tape("one", c), tape("two", b),
        settings=replace(settings, block_length=40, min_blocks=4),
    )
    assert result["status"] == "INCONCLUSIVE"
    assert result["sample_adequate"] is False
    assert result["bootstrap_samples_completed"] == 0


def test_utc_daily_compounding_uses_complete_raw_grid_and_retains_flat_days(pair):
    _, clock, c, b, tape, _ = pair
    day_ns = 86_400_000_000_000
    daily_clock = (clock // day_ns) * day_ns
    raw_clock = (daily_clock[:, None] + np.array([0, day_ns // 2])).ravel()
    c_bars = np.column_stack((c, c / 2))
    b_bars = np.column_stack((b, b / 2))
    basis = replace(tape("basis", c).basis, observation_unit="utc_day")
    candidate = tape("candidate-raw", c_bars.ravel(), raw_clock, basis)
    benchmark = tape("benchmark-raw", b_bars.ravel(), raw_clock, basis)
    result = compare(pair, candidate, benchmark, expected_timestamps_ns=raw_clock)
    c_daily = np.prod(1 + c_bars, axis=1) - 1
    b_daily = np.prod(1 + b_bars, axis=1) - 1
    expected = np.sqrt(252) * (
        c_daily.mean() / c_daily.std(ddof=1) - b_daily.mean() / b_daily.std(ddof=1))
    assert result["delta_sharpe"] == pytest.approx(expected)
    assert result["n_bars"] == 240
    assert result["n_observations"] == 120
    assert result["candidate_zero_returns"] == result["benchmark_zero_returns"] == 24
    missing = tape("missing-bar", b_bars.ravel()[1:], raw_clock[1:], basis)
    with pytest.raises(ComparisonError, match="complete eligible clock"):
        compare(pair, candidate, missing, expected_timestamps_ns=raw_clock)
