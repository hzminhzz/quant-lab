"""Forecast primitives for future evaluator review; not an admission or backtest path."""
from __future__ import annotations

import numpy as np
from arch.bootstrap import CircularBlockBootstrap
from sklearn.linear_model import Ridge

from case_studies.phase2.volatility import VARIANCE_FLOOR


def ridge_predict(x_train, y_train, x_validation, *, variance: bool) -> np.ndarray:
    """Fit on caller-provided, fold-locally preprocessed training data only."""
    estimator = Ridge(alpha=10.0)
    y = np.asarray(y_train, dtype=float)
    if variance:
        if np.any(y < 0):
            raise ValueError("negative realized variance")
        transformed = np.log(np.maximum(y, VARIANCE_FLOOR))
        estimator.fit(x_train, transformed)
        # Duan-style smearing: estimate the arithmetic-mean correction
        # using training residuals exclusively, never validation labels.
        residual = transformed - estimator.predict(x_train)
        correction = np.exp(residual).mean()
        return np.maximum(
            np.exp(estimator.predict(x_validation)) * correction, VARIANCE_FLOOR
        )
    estimator.fit(x_train, y * 1e4)
    return estimator.predict(x_validation) / 1e4


def qlike(realized, predicted) -> np.ndarray:
    y = np.asarray(realized, dtype=float)
    v = np.asarray(predicted, dtype=float)
    if y.shape != v.shape or not np.isfinite(y).all() or not np.isfinite(v).all():
        raise ValueError("unaligned or nonfinite forecasts")
    if np.any(y < 0) or np.any(v <= 0):
        raise ValueError("variance must be nonnegative, forecast strictly positive")
    return np.log(v) + y / v


def paired_loss_summary(benchmark_loss, candidate_loss, *, block: int) -> dict:
    """Descriptive paired circular-block interval; not a qualification gate.

    Caller must establish a complete chronological grid or an ordered session
    tape before using this primitive. No missing-row intersection or tuning.
    """
    a, b = np.asarray(benchmark_loss, dtype=float), np.asarray(candidate_loss, dtype=float)
    if a.ndim != 1 or a.shape != b.shape or not np.isfinite(a - b).all():
        raise ValueError("losses must be paired finite vectors")
    if block < 1 or len(a) < 5 * block:
        raise ValueError("insufficient complete blocks for demonstration")
    improvement = a - b
    bs = CircularBlockBootstrap(block, improvement, seed=42)
    interval = bs.conf_int(np.mean, reps=499, method="percentile")
    return {
        "mean_loss_improvement": float(improvement.mean()),
        "ci_low": float(interval[0, 0]),
        "ci_high": float(interval[1, 0]),
        "observations": len(improvement),
        "block_observations": block,
        "interpretation": "DESCRIPTIVE_SYNTHETIC_ONLY",
    }
