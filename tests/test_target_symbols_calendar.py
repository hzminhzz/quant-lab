"""Tests for target_symbols calendar filtering, validation, and cache identity."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from warden.contracts import EvaluationContract
from warden.leakage import compute_and_cache_features
from warden.runner import ExperimentRunner, filter_target_symbols


class SimplePipeline:
    def compute(
        self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract
    ) -> pl.DataFrame:
        _ = contract
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        return df.with_columns(feat=pl.col("close") * 2.0)


def test_filter_target_symbols_valid() -> None:
    df = pl.DataFrame({"asset": ["ES", "NQ", "DAX", "ES"], "close": [100.0, 200.0, 300.0, 101.0]})
    filtered = filter_target_symbols(df, {"target_symbols": ["ES"]})
    assert filtered["asset"].to_list() == ["ES", "ES"]

    multi = filter_target_symbols(df, {"target_symbols": ["ES", "NQ"]})
    assert set(multi["asset"].unique().to_list()) == {"ES", "NQ"}


def test_filter_target_symbols_symbol_column() -> None:
    df = pl.DataFrame({"symbol": ["ES", "NQ"], "close": [1.0, 2.0]})
    out = filter_target_symbols(df, {"target_symbols": ["ES"]})
    assert out["symbol"].to_list() == ["ES"]


def test_filter_target_symbols_rejects_empty() -> None:
    df = pl.DataFrame({"asset": ["ES", "NQ"], "close": [100.0, 200.0]})
    with pytest.raises(ValueError, match="non-empty"):
        filter_target_symbols(df, {"target_symbols": []})


def test_filter_target_symbols_rejects_bare_string() -> None:
    df = pl.DataFrame({"asset": ["ES", "NQ"], "close": [100.0, 200.0]})
    with pytest.raises(ValueError, match="non-empty"):
        filter_target_symbols(df, {"target_symbols": "ES"})


def test_filter_target_symbols_rejects_unknown() -> None:
    df = pl.DataFrame({"asset": ["ES", "NQ"], "close": [100.0, 200.0]})
    with pytest.raises(ValueError, match=r"unknown symbols.*NONEXISTENT"):
        filter_target_symbols(df, {"target_symbols": ["ES", "NONEXISTENT"]})


def test_filter_is_noop_without_key() -> None:
    df = pl.DataFrame({"asset": ["ES", "NQ"], "close": [100.0, 200.0]})
    assert filter_target_symbols(df, {}).height == 2


@pytest.fixture
def runner(tmp_path: Path) -> ExperimentRunner:
    """Runner bound to a throwaway registry so tests never touch registry/registry.db."""
    from warden.registry import ResearchRegistry

    return ExperimentRunner(
        registry=ResearchRegistry(tmp_path / "registry.db"), repo_root=Path(".")
    )


@pytest.fixture(scope="module")
def panel() -> pl.DataFrame:
    return pl.read_parquet("data/exploratory/indices_1h.parquet").sort("timestamp", "asset")


def test_cache_separates_equal_length_date_ranges(
    tmp_path: Path, panel: pl.DataFrame, runner: ExperimentRunner
) -> None:
    """Same assets, same row count, different dates must not share a cache entry."""
    contract = runner.resolve_contract("INDICES_1H_V2")
    es = panel.filter(pl.col("asset") == "ES")
    early, late = es.head(500), es.tail(500)
    assert len(early) == len(late)
    assert early["timestamp"].min() != late["timestamp"].min()

    p = SimplePipeline()
    compute_and_cache_features(p, early, contract, cache_dir=tmp_path)
    compute_and_cache_features(p, late, contract, cache_dir=tmp_path)

    assert len(list(tmp_path.glob("*.parquet"))) == 2


def test_cache_separates_equal_length_symbols(tmp_path: Path, panel: pl.DataFrame) -> None:
    """Two different single-symbol subsets of equal length must not share a cache entry."""
    contract = ExperimentRunner().resolve_contract("INDICES_1H_V2")
    es = filter_target_symbols(panel, {"target_symbols": ["ES"]}).head(500)
    nq = filter_target_symbols(panel, {"target_symbols": ["NQ"]}).head(500)
    assert len(es) == len(nq)

    p = SimplePipeline()
    compute_and_cache_features(p, es, contract, cache_dir=tmp_path)
    compute_and_cache_features(p, nq, contract, cache_dir=tmp_path)

    assert len(list(tmp_path.glob("*.parquet"))) == 2
