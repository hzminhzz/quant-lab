"""Paired diagnostics on already-produced return tapes, not an admission or evaluation path.

The caller must supply identities and a complete eligible clock from trusted evaluation
evidence. Matching identities do not themselves authenticate a research plan or prove
its execution. Binding these inputs to registered intent and governing qualification
remain the responsibilities of later research-governance slices.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from warden_evidence.return_artifacts import ReturnArtifactError, read_return_stream
from warden_evidence.selection_alignment import NS_PER_DAY, compound_by_utc_day


class ComparisonError(ValueError):
    """Missing, invalid or incomparable paired evidence."""


def _digest(value: str, label: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ComparisonError(f"{label} requires a SHA-256 digest")


def _nonempty(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ComparisonError(f"{label} must be nonempty")


@dataclass(frozen=True)
class ComparisonBasis:
    """Shared data, time, information, cost, execution and capital/risk identity."""

    dataset_sha256: str
    calendar_id: str
    information_set_sha256: str
    execution_policy_sha256: str
    cost_policy_sha256: str
    risk_basis_sha256: str
    simulation_epoch: str
    observation_unit: str
    periods_per_year: float

    def __post_init__(self) -> None:
        for key, value in asdict(self).items():
            if key.endswith("sha256"):
                _digest(value, key)
            elif key != "periods_per_year":
                _nonempty(value, key)
        if (isinstance(self.periods_per_year, bool)
                or not isinstance(self.periods_per_year, (int, float))
                or not math.isfinite(self.periods_per_year)
                or self.periods_per_year <= 0):
            raise ComparisonError("periods_per_year must be finite and positive")


@dataclass(frozen=True)
class ReturnTape:
    """Reference to a previously recorded artifact and its declared evidence identity."""

    run_id: str
    path: Path
    artifact_sha256: str
    code_sha256: str
    config_sha256: str
    basis: ComparisonBasis

    def __post_init__(self) -> None:
        _nonempty(self.run_id, "run_id")
        for name in ("artifact_sha256", "code_sha256", "config_sha256"):
            _digest(getattr(self, name), name)
        if not isinstance(self.basis, ComparisonBasis):
            raise ComparisonError("comparison basis is missing")

    def identity(self) -> dict[str, str]:
        return {
            "run_id": self.run_id,
            "artifact_sha256": self.artifact_sha256,
            "code_sha256": self.code_sha256,
            "config_sha256": self.config_sha256,
        }


@dataclass(frozen=True)
class BootstrapSettings:
    """Explicit diagnostic settings; no defaults or acceptance thresholds are implied."""

    seed: int
    block_length: int
    n_resamples: int
    confidence_level: float
    min_observations: int
    min_blocks: int

    def __post_init__(self) -> None:
        for name, lower in (
            ("seed", 0), ("block_length", 2), ("n_resamples", 2),
            ("min_observations", 2), ("min_blocks", 2),
        ):
            value = getattr(self, name)
            if type(value) is not int or value < lower:
                raise ComparisonError(f"{name} must be an integer >= {lower}")
        if (isinstance(self.confidence_level, bool)
                or not isinstance(self.confidence_level, (int, float))
                or not 0 < self.confidence_level < 1):
            raise ComparisonError("confidence_level must be strictly between zero and one")


def _clock(values: Any) -> np.ndarray:
    stamps = np.asarray(values)
    if stamps.ndim != 1 or not stamps.size or stamps.dtype.kind not in "iu":
        raise ComparisonError("expected clock must be nonempty UTC epoch nanoseconds")
    if stamps.dtype.kind == "u" and np.any(stamps > np.iinfo(np.int64).max):
        raise ComparisonError("expected clock is outside the int64 range")
    stamps = stamps.astype(np.int64)
    if np.any(stamps[1:] <= stamps[:-1]):
        raise ComparisonError("expected clock must be strictly increasing")
    return stamps


def _read_tape(tape: ReturnTape, root: Path, clock: np.ndarray) -> np.ndarray:
    try:
        artifact = read_return_stream(tape.path, root, expected_sha256=tape.artifact_sha256)
    except (OSError, ReturnArtifactError, pl.exceptions.PolarsError) as exc:
        raise ComparisonError(f"invalid return artifact for {tape.run_id}: {exc}") from exc
    if artifact.timestamps_ns is None or not np.array_equal(artifact.timestamps_ns, clock):
        raise ComparisonError(f"{tape.run_id}: return tape does not match the complete eligible clock")
    values = artifact.net_returns
    if not np.isfinite(values).all():
        raise ComparisonError(f"{tape.run_id}: return evidence must be finite")
    return values


def _sharpe(values: np.ndarray, periods_per_year: float) -> float | None:
    if values.size < 2:
        return None
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        std = float(np.std(values, ddof=1))
        if not math.isfinite(std) or std <= 0:
            return None
        result = float(np.mean(values)) / std * math.sqrt(periods_per_year)
    return result if math.isfinite(result) else None


def compare_return_artifacts(
    candidate: ReturnTape,
    benchmark: ReturnTape,
    *,
    returns_root: Path,
    expected_timestamps_ns: Any,
    settings: BootstrapSettings,
) -> dict[str, Any]:
    """Compare after-cost Sharpe on an exact, caller-pinned observation grid.

    Circular blocks preserve within-block serial dependence and paired resampling
    preserves candidate/benchmark co-movement. Percentile intervals describe the
    incremental statistic, not a multiplicity-adjusted hypothesis test. Never
    infer missing observations, intersect clocks, drop flat sessions, or select
    a benchmark. This function cannot run strategies or mutate any ledger/artifact.
    """
    if not isinstance(candidate, ReturnTape) or not isinstance(benchmark, ReturnTape):
        raise ComparisonError("candidate and benchmark return evidence are required")
    if candidate.basis != benchmark.basis:
        raise ComparisonError("candidate and benchmark comparison basis differs")
    if not isinstance(settings, BootstrapSettings):
        raise ComparisonError("explicit bootstrap settings are required")
    clock = _clock(expected_timestamps_ns)
    c = _read_tape(candidate, returns_root, clock)
    b = _read_tape(benchmark, returns_root, clock)
    raw_clock_sha256 = sha256(clock.astype("<i8").tobytes()).hexdigest()
    n_bars = len(clock)
    if candidate.basis.observation_unit == "utc_day":
        days, c = compound_by_utc_day(clock, c)
        benchmark_days, b = compound_by_utc_day(clock, b)
        if not np.array_equal(days, benchmark_days):
            raise ComparisonError("daily comparison clocks differ")
        clock = days * NS_PER_DAY
    n = len(clock)
    annualisation = candidate.basis.periods_per_year
    c_sr, b_sr = _sharpe(c, annualisation), _sharpe(b, annualisation)
    with np.errstate(over="ignore", invalid="ignore"):
        mean_delta = float(np.mean(c - b))
    delta = None if c_sr is None or b_sr is None else c_sr - b_sr
    adequate = n >= settings.min_observations and n // settings.block_length >= settings.min_blocks
    report = {
        "status": "INCONCLUSIVE",
        "reason": None,
        "candidate": candidate.identity(),
        "benchmark": benchmark.identity(),
        "basis": asdict(candidate.basis),
        "clock_sha256": sha256(clock.astype("<i8").tobytes()).hexdigest(),
        "raw_clock_sha256": raw_clock_sha256,
        "n_bars": n_bars,
        "n_observations": n,
        "candidate_zero_returns": int(np.count_nonzero(c == 0)),
        "benchmark_zero_returns": int(np.count_nonzero(b == 0)),
        "sample_adequate": adequate,
        "candidate_sharpe": c_sr,
        "benchmark_sharpe": b_sr,
        "delta_sharpe": delta if delta is not None and math.isfinite(delta) else None,
        "mean_net_return_delta": mean_delta if math.isfinite(mean_delta) else None,
        "delta_sharpe_ci": None,
        "bootstrap_method": "paired_circular_block_bootstrap_v1",
        "settings": asdict(settings),
        "bootstrap_samples_completed": 0,
    }
    if not adequate:
        report["reason"] = "insufficient_observations_or_blocks"
        return report
    if report["delta_sharpe"] is None or report["mean_net_return_delta"] is None:
        report["reason"] = "degenerate_or_nonfinite_statistic"
        return report

    rng = np.random.Generator(np.random.PCG64(settings.seed))
    block = settings.block_length
    block_count = (n + block - 1) // block
    offsets = np.arange(block)
    draws = np.empty(settings.n_resamples)
    for i in range(settings.n_resamples):
        starts = rng.integers(0, n, size=block_count)
        idx = ((starts[:, None] + offsets) % n).ravel()[:n]
        c_draw = _sharpe(c[idx], annualisation)
        b_draw = _sharpe(b[idx], annualisation)
        if c_draw is None or b_draw is None or not math.isfinite(c_draw - b_draw):
            # Dropping degenerate draws would condition the interval on an outcome.
            report["reason"] = "degenerate_bootstrap_draw"
            return report
        draws[i] = c_draw - b_draw
        report["bootstrap_samples_completed"] = i + 1
    alpha = (1 - settings.confidence_level) / 2
    report["delta_sharpe_ci"] = np.quantile(draws, [alpha, 1 - alpha], method="linear").tolist()
    report["status"] = "DIAGNOSTIC"
    return report
