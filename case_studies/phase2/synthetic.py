"""Bounded synthetic demonstrations using the installed ML4T dataset/CV APIs.

No file loaders, market-data arguments, Warden scoring commands or registry writes.
The returned forecasts are fabricated-data engineering witnesses, not evidence.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
from ml4t.diagnostic.splitters import WalkForwardCV
from ml4t.engineer import create_dataset_builder

from case_studies.phase2.diagnostics import paired_loss_summary, qlike, ridge_predict
from case_studies.phase2.volatility import (
    BASE_FEATURES,
    CANDIDATE_FEATURES,
    VARIANCE_FLOOR,
    variance_labels,
    volatility_features,
)

SYNTHETIC = "SYNTHETIC ENGINEERING DEMONSTRATION — NOT MARKET EVIDENCE"


def synthetic_minute_bars() -> pl.DataFrame:
    """Fixed fabricated path; known periodic variance injected intentionally."""
    n = 20 * 1440
    start = datetime(2000, 1, 1, tzinfo=UTC)
    timestamp = pl.datetime_range(
        start, start + timedelta(minutes=n - 1), interval="1m",
        time_zone="UTC", eager=True,
    )
    rng = np.random.default_rng(42)
    minute = np.arange(n)
    sigma = 0.00045 * (
        1 + 0.35 * np.sin(2 * np.pi * minute / 1440) ** 2
        + 1.8 * (minute % 15 == 0)
    )
    returns = rng.normal(0, sigma)
    opens = 100 * np.exp(np.r_[0.0, np.cumsum(returns[:-1])])
    closes = opens * np.exp(returns)
    return pl.DataFrame(
        {"timestamp": timestamp, "open": opens, "close": closes}
    ).with_columns((pl.col("timestamp") + pl.duration(minutes=1)).alias("available_at"))


def _synthetic_folds(
    frame: pl.DataFrame, base: tuple[str, ...], candidate: tuple[str, ...],
    target: str, *, variance: bool, embargo: timedelta,
    label_horizon: int, block: int,
) -> dict:
    """Shared fit witness on generated fixtures only.

    ML4T owns splits and fold preprocessing. The timestamp assertions are
    additional engineering checks, not an alternative scientific evaluator.
    """
    frame = frame.sort("decision_timestamp")
    if frame["decision_timestamp"].n_unique() != frame.height:
        raise ValueError("duplicate decisions")
    builder = create_dataset_builder(
        frame.select(candidate), frame[target],
        dates=frame["decision_timestamp"], scaler="standard",
    )
    cv = WalkForwardCV(
        n_splits=4, test_size=0.15, label_horizon=label_horizon,
        embargo_size=1, expanding=True, consecutive=True, calendar=None,
    )
    chunks, schedule = [], []
    for fold in builder.split(cv):
        train, test = frame[fold.train_indices], frame[fold.test_indices]
        latest = train["label_available_at"].max()
        earliest = test["decision_timestamp"].min()
        if latest >= earliest - embargo:
            raise RuntimeError("training label availability crosses purge/embargo")
        baseline = ridge_predict(
            fold.X_train.select(base).to_numpy(), fold.y_train.to_numpy(),
            fold.X_test.select(base).to_numpy(), variance=variance,
        )
        extended = ridge_predict(
            fold.X_train.select(candidate).to_numpy(), fold.y_train.to_numpy(),
            fold.X_test.select(candidate).to_numpy(), variance=variance,
        )
        reference = (
            np.maximum(test["rv5"].to_numpy(), VARIANCE_FLOOR)
            if variance else np.sign(test["rod"].to_numpy())
        )
        chunks.append(test.select("decision_timestamp", target).rename(
            {target: "realized"}
        ).with_columns(
            pl.lit(fold.fold_number).alias("fold"),
            pl.Series("reference", reference),
            pl.Series("benchmark", baseline),
            pl.Series("candidate", extended),
        ))
        schedule.append({
            "fold": fold.fold_number, "training_rows": train.height,
            "validation_rows": test.height, "train_labels_known": latest,
            "first_validation": earliest,
        })
    if not chunks:
        raise RuntimeError("native splitter produced no validation observations")
    tape = pl.concat(chunks).sort("decision_timestamp")
    if tape["decision_timestamp"].n_unique() != tape.height:
        raise RuntimeError("overlapping validation folds")
    if variance and not tape.select(
        (pl.col("decision_timestamp").diff().drop_nulls() == pl.duration(minutes=5)).all()
    ).item():
        raise RuntimeError("non-contiguous OOS grid; block comparison not admissible")
    y = tape["realized"].to_numpy()
    rows = []
    for name in ("reference", "benchmark", "candidate"):
        pred = tape[name].to_numpy()
        if variance:
            rows.append({
                "model": name, "mean_qlike": float(qlike(y, pred).mean()),
                "forecast_to_realized_variance": float(pred.sum() / y.sum()),
            })
        else:
            rows.append({
                "model": name,
                "mean_squared_error": None if name == "reference" else float(
                    np.mean((y - pred) ** 2)
                ),
                "direction_accuracy": float(np.mean(np.sign(y) == np.sign(pred))),
            })
    loss = (
        (qlike(y, tape["benchmark"]), qlike(y, tape["candidate"]))
        if variance else (
            (y - tape["benchmark"].to_numpy()) ** 2,
            (y - tape["candidate"].to_numpy()) ** 2,
        )
    )
    return {
        "scope": SYNTHETIC, "forecast_tape": tape, "schedule": pl.DataFrame(schedule),
        "metrics": pl.DataFrame(rows), "paired": paired_loss_summary(*loss, block=block),
    }


def run_d01() -> dict:
    bars = synthetic_minute_bars()
    features = volatility_features(bars)
    labels = variance_labels(bars)
    frame = features.join(
        labels, on=["decision_timestamp", "entry_timestamp", "exit_timestamp"],
        how="inner", validate="1:1",
    )
    result = _synthetic_folds(
        frame, BASE_FEATURES, CANDIDATE_FEATURES, "target_variance",
        variance=True, embargo=timedelta(minutes=5), label_horizon=2, block=288,
    )
    result["features"] = features
    result["eligible_rows"] = frame.height
    return result


def synthetic_index_bars() -> tuple[pl.DataFrame, pl.DataFrame]:
    """Fabricated sessions on an actual calendar, including half-days and DST."""
    from case_studies.phase2.closing_momentum import cash_sessions

    sessions = cash_sessions("2024-01-02", "2024-12-31")
    rng = np.random.default_rng(43)
    chunks = []
    price = 100.0
    for start, close in sessions.iter_rows():
        n = int((close - start).total_seconds() // 60) + 1
        price *= np.exp(rng.normal(0, 0.002))
        sigma = rng.uniform(0.0001, 0.0004)
        returns = rng.normal(0, sigma, size=n)
        cutoff = n - 31
        # Inject a conditional response into an artificial path; not a market claim.
        returns[cutoff + 1:] += returns[:cutoff].sum() * (sigma / 0.00025) / 90
        opens = price * np.exp(np.r_[0.0, np.cumsum(returns[:-1])])
        closes = opens * np.exp(returns)
        timestamp = pl.datetime_range(start, close, interval="1m", eager=True, time_zone="UTC")
        chunks.append(pl.DataFrame(
            {"timestamp": timestamp, "open": opens, "close": closes}
        ).with_columns((pl.col("timestamp") + pl.duration(minutes=1)).alias("available_at")))
        price = closes[-2]
    return pl.concat(chunks), sessions


def run_a01() -> dict:
    from case_studies.phase2.closing_momentum import (
        BASE_FEATURES as A_BASE,
    )
    from case_studies.phase2.closing_momentum import (
        CANDIDATE_FEATURES as A_CANDIDATE,
    )
    from case_studies.phase2.closing_momentum import (
        closing_features,
        closing_labels,
    )

    bars, sessions = synthetic_index_bars()
    features = closing_features(bars, sessions)
    labels = closing_labels(bars, sessions)
    frame = features.join(
        labels, on=["decision_timestamp", "entry_timestamp", "exit_timestamp"],
        how="inner", validate="1:1",
    )
    result = _synthetic_folds(
        frame, A_BASE, A_CANDIDATE, "target_return",
        variance=False, embargo=timedelta(hours=24), label_horizon=1, block=5,
    )
    result["features"] = features
    result["eligible_rows"] = frame.height
    return result
