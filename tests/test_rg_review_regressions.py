"""Independent review witnesses; every input and holdout is a disposable fixture."""

from datetime import timedelta

import polars as pl
import pytest

from tests.conftest import tiny_panel
from tests.test_rg05_research_intent import approve, review
from tests.test_rg06_incremental import _control_panel, _long_module
from tests.test_rg08_holdout_batch import candidate, freeze
from tests.test_rg09_integrated_governance import counts
from warden import exposure
from warden.contracts import ContractValidationError, utc
from warden.market_data import MarketDataGateway
from warden.provenance import DatasetProvenanceError, resolve_dataset
from warden.qualify import qualify_candidate


def test_rejected_screen_cannot_advance_via_direct_runner(tiny_repo):
    end = utc("2025-06-30T00:00:00Z")
    f = tiny_repo(module_source=_long_module("NONE"), panel_override=_control_panel(),
                  contract_overrides={"frequency": "1d", "bars_per_year": 365,
                                      "val_start": utc("2025-01-21T00:00:00Z"),
                                      "val_end": end, "holdout_start": end,
                                      "holdout_end": utc("2025-12-01T00:00:00Z")})
    runner, _, _ = approve(f)
    review(runner, f)
    assert not runner.run_stage1_screen(f.spec).passed
    before = counts(f)
    with pytest.raises(ValueError, match="screen.*REJECT"):
        runner.run_spec(f.spec)
    assert counts(f) == before


def test_exploratory_reader_rejects_bytes_changed_after_resolution(tiny_repo):
    f = tiny_repo()
    resolution = resolve_dataset(f.contract, f.repo)
    path = f.repo / resolution.path
    f.panel.with_columns((pl.col("volume") * 2).alias("volume")).write_parquet(path)
    with pytest.raises(DatasetProvenanceError, match="digest"):
        MarketDataGateway(f.repo).load(f.contract, resolution=resolution)


def test_exploratory_fence_covers_excluded_symbols(tiny_repo):
    panel = tiny_panel()
    outside = panel.filter(pl.col("asset") == "BBB").tail(1).with_columns(
        (pl.col("timestamp") + timedelta(hours=1)).alias("timestamp"))
    f = tiny_repo(panel_override=pl.concat([panel, outside]))
    with pytest.raises(ContractValidationError, match="holdout"):
        MarketDataGateway(f.repo).load(f.contract, symbols=["AAA"])


def test_frozen_pin_drift_records_scope_incident(tiny_repo, monkeypatch):
    f, runner, run_id = candidate(tiny_repo, monkeypatch=monkeypatch)
    batch = freeze(runner, run_id)
    path = f.repo / f.contract.holdout_data_path
    path.write_bytes(b"unattested fixture bytes")
    with pytest.raises((ValueError, OSError)):
        qualify_candidate(run_id, "YES_UNSEAL_HOLDOUT", runner, batch_id=batch["batch_id"])
    state = exposure.status(f.registry, batch["batch_id"])
    assert state["spent"] and state["state"] == "SCOPE_UNCERTAIN"
    with pytest.raises(ValueError, match="Unresolved holdout scope incident"):
        freeze(runner, run_id)


def test_warmup_pin_drift_refused_before_claim(tiny_repo, monkeypatch):
    f, runner, run_id = candidate(tiny_repo, monkeypatch=monkeypatch)
    batch = freeze(runner, run_id)
    path = f.repo / f.contract.exploratory_data_path
    f.panel.with_columns((pl.col("volume") * 2).alias("volume")).write_parquet(path)
    with pytest.raises(ValueError, match="dataset|digest"):
        qualify_candidate(run_id, "YES_UNSEAL_HOLDOUT", runner, batch_id=batch["batch_id"])
    assert exposure.status(f.registry, batch["batch_id"])["state"] == "AUTHORIZED"
