"""Feature-cache reuse is conditional on matching causal and data evidence."""

from __future__ import annotations

import polars as pl
import pytest

from warden.leakage import compute_and_cache_features
from warden_evidence.feature_cache import UnverifiedFeatureCacheError


class DelayedFeatures:
    def compute(self, frame: pl.DataFrame, contract) -> pl.DataFrame:
        return frame.sort("asset", "timestamp").with_columns(
            pl.col("close").shift(1).over("asset").alias("previous_close")
        )


def _exercise(fixture, cache_dir):
    return compute_and_cache_features(DelayedFeatures(), fixture.panel,
                                      fixture.contract, cache_dir, fixture.registry)


def test_missing_registry_proof_is_revalidated_before_reuse(tiny_repo, tmp_path, monkeypatch):
    fixture = tiny_repo()
    cache = tmp_path / "cache"
    first = _exercise(fixture, cache)
    with fixture.registry._connect() as conn:
        conn.execute("DELETE FROM feature_sets")
    from warden import leakage
    real = leakage.verify_prefix_invariance
    calls = []

    def observe(*args, **kwargs):
        calls.append(True)
        return real(*args, **kwargs)

    monkeypatch.setattr(leakage, "verify_prefix_invariance", observe)
    second = _exercise(fixture, cache)
    assert first.equals(second)
    assert len(calls) == 1
    with fixture.registry._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM feature_sets").fetchone()[0] == 1


def test_data_revision_changes_cache_identity(tiny_repo, tmp_path):
    fixture = tiny_repo()
    cache = tmp_path / "cache"
    _exercise(fixture, cache)
    changed = fixture.panel.with_columns((pl.col("close") + 0.01).alias("close"))
    compute_and_cache_features(DelayedFeatures(), changed, fixture.contract, cache, fixture.registry)
    assert len(list(cache.glob("*.parquet"))) == 2
    assert len(list(cache.glob("*.proof.json"))) == 2


def test_corrupt_artifact_fails_closed_without_overwriting(tiny_repo, tmp_path):
    fixture = tiny_repo()
    cache = tmp_path / "cache"
    _exercise(fixture, cache)
    path = next(cache.glob("*.parquet"))
    path.write_bytes(b"corrupt, not the verified feature artifact")
    with pytest.raises(UnverifiedFeatureCacheError, match="operator review"):
        _exercise(fixture, cache)
    assert path.read_bytes() == b"corrupt, not the verified feature artifact"


def test_crash_after_data_publication_recovers_with_one_proof(tiny_repo, tmp_path, monkeypatch):
    fixture = tiny_repo()
    cache = tmp_path / "cache"
    import warden_evidence.feature_cache as proof
    real_replace = proof.os.replace
    calls = []

    def crash_once(*args, **kwargs):
        if not calls:
            calls.append(True)
            raise OSError("injected power loss before proof commit")
        return real_replace(*args, **kwargs)

    monkeypatch.setattr(proof.os, "replace", crash_once)
    with pytest.raises(OSError, match="injected power loss"):
        _exercise(fixture, cache)
    assert len(list(cache.glob("*.parquet"))) == 1
    assert len(list(cache.glob("*.proof.json"))) == 0
    _exercise(fixture, cache)
    assert len(list(cache.glob("*.parquet"))) == 1
    assert len(list(cache.glob("*.proof.json"))) == 1
    with fixture.registry._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM feature_sets").fetchone()[0] == 1
