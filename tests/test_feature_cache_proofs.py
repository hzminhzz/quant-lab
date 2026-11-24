"""Feature-cache reuse is conditional on matching causal and data evidence."""

from __future__ import annotations

import shutil
import sys

import polars as pl
import pytest

from warden.leakage import compute_and_cache_features
from warden.strategy_loader import load_module_from_path
from warden_evidence.feature_cache import UnverifiedFeatureCacheError, feature_identity


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


def test_helper_only_edit_changes_identity_and_forces_recomputation(tiny_repo, tmp_path) -> None:
    """Acceptance #4: editing an imported helper invalidates identity and the cache."""
    fixture = tiny_repo()
    strategies = fixture.repo / "strategies"
    helper = strategies / "shared_scale.py"
    pipeline_file = strategies / "helper_pipe.py"
    helper.write_text("SCALE = 1.0\n", encoding="utf-8")
    pipeline_file.write_text(
        "import polars as pl\n"
        "from shared_scale import SCALE\n\n"
        "class HelperPipeline:\n"
        "    def compute(self, frame, contract):\n"
        "        return frame.sort('asset', 'timestamp').with_columns(\n"
        "            scaled_close=pl.col('close') * pl.lit(SCALE)\n"
        "        )\n",
        encoding="utf-8",
    )
    pipeline_bytes = pipeline_file.read_bytes()

    first_module = load_module_from_path(pipeline_file)
    first_pipe = first_module.HelperPipeline()
    identity_before, config_before = feature_identity(first_pipe, fixture.panel, fixture.contract)
    assert config_before["code_closure"]
    cache = tmp_path / "cache"
    first = compute_and_cache_features(first_pipe, fixture.panel, fixture.contract, cache, fixture.registry)

    helper.write_text("SCALE = 2.0\n", encoding="utf-8")
    assert pipeline_file.read_bytes() == pipeline_bytes  # only the helper changed
    sys.modules.pop("shared_scale", None)  # drop the stale helper module so the edit is observable
    shutil.rmtree(strategies / "__pycache__", ignore_errors=True)  # and its bytecode cache
    second_module = load_module_from_path(pipeline_file)
    second_pipe = second_module.HelperPipeline()
    identity_after, config_after = feature_identity(second_pipe, fixture.panel, fixture.contract)
    assert config_after["code_closure"] != config_before["code_closure"]
    assert identity_after != identity_before

    second = compute_and_cache_features(second_pipe, fixture.panel, fixture.contract, cache, fixture.registry)
    assert len(list(cache.glob("*.parquet"))) == 2
    assert len(list(cache.glob("*.proof.json"))) == 2
    assert first["scaled_close"].to_list() != second["scaled_close"].to_list()
    assert second["scaled_close"].to_list() == (fixture.panel["close"] * 2.0).to_list()
    with fixture.registry._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM feature_sets").fetchone()[0] == 2
