"""Engineering tests for the preregistered BTC/ETH cost-filter research pair."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from scripts.prepare_research_snapshot import (
    _checked_exploratory_relpath,
    install_verified_data,
    snapshot_registry,
)
from strategies.candidates.arxiv_cost_aware_ridge_1h import (
    CostAwareRidgePipeline,
    generate_positions,
    position_decision,
)
from warden.contracts import load_contract
from warden.leakage import verify_prefix_invariance


def _bars(n: int = 800) -> pl.DataFrame:
    start = datetime(2020, 1, 1, tzinfo=UTC)
    rng = np.random.default_rng(83)
    records = []
    for asset in ("BTCUSDT", "ETHUSDT"):
        prices = 100.0 * np.exp(np.cumsum(rng.normal(0.00002, 0.002, n)))
        for i, price in enumerate(prices):
            records.append({
                "timestamp": start + timedelta(hours=i),
                "asset": asset,
                "open": float(price),
                "high": float(price * 1.003),
                "low": float(price * 0.997),
                "close": float(price * 1.0001),
                "volume": float(500.0 + i % 70),
                "funding_rate": 0.00001,
            })
    return pl.DataFrame(records).sort("timestamp", "asset")


def _contract():
    start = datetime(2020, 1, 1, tzinfo=UTC)
    old = load_contract("contracts/crypto_majors_1h_v1.yaml")
    return replace(
        old,
        train_start=start,
        val_start=start + timedelta(hours=400),
        val_end=start + timedelta(hours=700),
        holdout_start=start + timedelta(hours=700),
        holdout_end=start + timedelta(hours=799),
        n_folds=3,
        data_provenance="unknown",
        data_snapshot_sha256="",
    )


def test_policy_filters_changes_not_existing_positions() -> None:
    fee = 0.0006
    assert position_decision(0.0002, 0, fee, True, 2.0) == 0
    assert position_decision(0.0013, 0, fee, True, 2.0) == 1
    assert position_decision(-0.0001, 1, fee, True, 2.0) == 1
    assert position_decision(-0.0013, 1, fee, True, 2.0) == 0
    assert position_decision(-0.0001, 1, fee, False, 2.0) == 0
    assert position_decision(float("nan"), 1, fee, True, 2.0) == 1


def test_causal_features_prefix_and_perturbation() -> None:
    verify_prefix_invariance(CostAwareRidgePipeline(), _bars(400), _contract())


def test_ridge_outputs_no_train_exposure_and_no_double_lag() -> None:
    panel, contract = _bars(), _contract()
    immediate = generate_positions(panel, contract, cost_aware=False)["positions"]
    filtered = generate_positions(panel, contract, cost_aware=True)["positions"]
    assert immediate.shape == filtered.shape == (800, 2)
    assert np.all(immediate[:400] == 0)
    assert np.all(filtered[:400] == 0)
    assert np.all(immediate[700:] == 0)
    assert np.all(filtered[700:] == 0)
    assert set(np.unique(immediate)).issubset({0.0, 0.5})
    assert set(np.unique(filtered)).issubset({0.0, 0.5})
    assert np.isfinite(immediate).all() and np.isfinite(filtered).all()


def test_materialization_requires_exact_digest_and_exploratory_path(tmp_path) -> None:
    source = tmp_path / "source" / "data" / "exploratory" / "prices.parquet"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"immutable fixture")
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    target = tmp_path / "target" / "data" / "exploratory" / "prices.parquet"
    assert install_verified_data(source, target, sha) == "installed_verified"
    assert install_verified_data(source, target, sha) == "existing_verified"
    assert target.read_bytes() == b"immutable fixture"
    with pytest.raises(ValueError, match="SHA mismatch"):
        install_verified_data(source, target, "0" * 64)
    for unsafe in ("data/holdout/x.parquet", "../data/exploratory/x.parquet", "/tmp/x"):
        with pytest.raises(ValueError):
            _checked_exploratory_relpath(unsafe)


def test_registry_snapshot_copies_committed_wal_without_source_mutation(tmp_path) -> None:
    source = tmp_path / "origin.db"
    with sqlite3.connect(source) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        for name in ("hypotheses", "trials", "selection_attempts"):
            conn.execute(f"CREATE TABLE {name} (id INTEGER)")
            conn.execute(f"INSERT INTO {name} VALUES (1)")
    target = tmp_path / "research" / "registry.db"
    result = snapshot_registry(source, target)
    assert result["status"] == "snapshot_created"
    assert result["counts"] == {"hypotheses": 1, "trials": 1, "attempts": 1}
    assert snapshot_registry(source, target)["status"] == "existing_preserved"
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0] == 1
