"""Issue #88: exploratory temporal fence and empty-validation-slice fail-closed.

An exploratory panel may never carry a bar at or after ``contract.holdout_start``, and a
validation window with zero bars is not a scientific result. Both must raise
``ContractValidationError`` before Stage-0 volatility, feature caching or trial reservation,
and no holdout-staging fallback may exist anywhere in the builders or their tests.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest
import yaml

from warden.contracts import ContractValidationError, EvaluationContract, HypothesisSpec, utc
from warden.provenance import DatasetResolution, resolve_dataset
from warden.registry import ResearchRegistry
from warden.runner import ExperimentRunner, prepare_panel

REPO_ROOT = Path(__file__).resolve().parents[1]
START = datetime(2025, 1, 1, tzinfo=UTC)


def _panel(last_offset_hours: int, first_offset_hours: int = 0, assets: tuple[str, ...] = ("EURUSD", "USDJPY")) -> pl.DataFrame:
    """Hourly FX bars from START+first_offset_hours through START+last_offset_hours."""
    rows, rng = [], np.random.default_rng(11)
    for asset in assets:
        price = 1.10
        for offset in range(first_offset_hours, last_offset_hours + 1):
            nxt = price * (1.0 + float(rng.normal(0.0, 0.002)))
            rows.append({
                "timestamp": START + timedelta(hours=offset),
                "asset": asset,
                "open": price,
                "high": max(price, nxt) * 1.001,
                "low": min(price, nxt) * 0.999,
                "close": nxt,
                "volume": 100.0,
            })
            price = nxt
    return pl.DataFrame(rows).with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC"))).sort("timestamp", "asset")


def _contract(
    *,
    val_start_hours: int = 6,
    val_end_hours: int = 24,
    holdout_end_hours: int = 36,
    data_snapshot_sha256: str = "",
) -> EvaluationContract:
    return EvaluationContract(
        contract_id="FENCE_FX_V1", asset_class="fx", frequency="1h", bars_per_year=6240,
        exploratory_data_path="data/exploratory/fence_fx.parquet",
        holdout_data_path="data/holdout/fence_fx.parquet",
        train_start=utc(START), val_start=utc(START + timedelta(hours=val_start_hours)),
        val_end=utc(START + timedelta(hours=val_end_hours)),
        holdout_start=utc(START + timedelta(hours=val_end_hours)),
        holdout_end=utc(START + timedelta(hours=holdout_end_hours)),
        n_folds=2, label_horizon_bars=2, embargo_bars=1, execution_delay_bars=1,
        taker_fee_bps=0.2, slippage_bps=0.5, data_snapshot_sha256=data_snapshot_sha256,
        data_provenance="unknown",
    )


def _resolution(contract: EvaluationContract, root: Path) -> DatasetResolution:
    sha = hashlib.sha256((root / contract.exploratory_data_path).read_bytes()).hexdigest()
    return DatasetResolution(contract.contract_id, "exploratory", contract.exploratory_data_path,
                             sha, "verified_market", "fixture", True, "verified market data")


# ---------------------------------------------------------------------------
# 1. validate_panel_feed_spec enforces max(timestamp) < holdout_start
# ---------------------------------------------------------------------------

def test_validate_panel_feed_spec_rejects_holdout_overflow_at_and_after_cutoff() -> None:
    from warden.contracts import validate_panel_feed_spec

    contract = _contract(val_start_hours=6, val_end_hours=24)
    at_cutoff = _panel(last_offset_hours=24)  # bar exactly at holdout_start
    with pytest.raises(ContractValidationError, match="holdout_start"):
        validate_panel_feed_spec(at_cutoff, contract)

    past_cutoff = _panel(last_offset_hours=25)  # one bar inside the holdout window
    with pytest.raises(ContractValidationError, match="holdout_start"):
        validate_panel_feed_spec(past_cutoff, contract)

    # One bar before the cutoff is the last legal exploratory timestamp.
    legal = _panel(last_offset_hours=23)
    spec = validate_panel_feed_spec(legal, contract)
    assert spec.timestamp_col == "timestamp" and spec.entity_col == "asset"


def test_empty_panel_still_reports_emptiness_not_a_fence_pass() -> None:
    from warden.contracts import validate_panel_feed_spec

    contract = _contract()
    with pytest.raises(ContractValidationError, match="panel is empty"):
        validate_panel_feed_spec(_panel(last_offset_hours=0).head(0), contract)


# ---------------------------------------------------------------------------
# 2. prepare_panel fence and empty validation slice
# ---------------------------------------------------------------------------

def test_prepare_panel_rejects_holdout_overflow_and_empty_validation_slice(tmp_path: Path) -> None:
    contract = _contract()
    overflow = _panel(last_offset_hours=24)
    (tmp_path / contract.exploratory_data_path).parent.mkdir(parents=True, exist_ok=True)
    overflow.write_parquet(tmp_path / contract.exploratory_data_path)
    with pytest.raises(ContractValidationError, match="holdout_start"):
        prepare_panel(tmp_path, _resolution(contract, tmp_path), {}, contract)

    # Fence passes (max timestamp is before the cutoff) but the validation window holds no bars.
    contract = _contract(val_start_hours=20, val_end_hours=30, holdout_end_hours=40)
    sparse = _panel(last_offset_hours=9)
    (tmp_path / contract.exploratory_data_path).parent.mkdir(parents=True, exist_ok=True)
    sparse.write_parquet(tmp_path / contract.exploratory_data_path)
    with pytest.raises(ContractValidationError, match="validation slice"):
        prepare_panel(tmp_path, _resolution(contract, tmp_path), {}, contract)

    # Control: a panel with bars in both windows prepares a non-empty slice.
    contract = _contract(val_start_hours=6, val_end_hours=24)
    legal = _panel(last_offset_hours=23)
    legal.write_parquet(tmp_path / contract.exploratory_data_path)
    _, ts, assets, v_slice = prepare_panel(tmp_path, _resolution(contract, tmp_path), {}, contract)
    assert len(ts) == 24 and assets == ["EURUSD", "USDJPY"]
    assert v_slice.start < v_slice.stop and v_slice.stop == 24


# ---------------------------------------------------------------------------
# 3. run_spec fails before Stage-0, strategy execution or trial reservation
# ---------------------------------------------------------------------------

_GENERATE_POSITIONS = """\
import numpy as np
from pathlib import Path

MARKER = Path(__file__).with_name("GENERATE_POSITIONS_RAN")


def generate_positions(panel_df, contract, **kwargs):
    MARKER.write_text("positions were generated", encoding="utf-8")
    ts = sorted(panel_df["timestamp"].unique().to_list())
    assets = sorted(panel_df["asset"].unique().to_list())
    return {"positions": np.zeros((len(ts), len(assets)))}
"""


def _fenced_repo(tmp_path: Path, panel: pl.DataFrame, contract: EvaluationContract) -> Path:
    repo = tmp_path / "repo"
    data_path = repo / contract.exploratory_data_path
    data_path.parent.mkdir(parents=True, exist_ok=True)
    panel.write_parquet(data_path)
    sha = hashlib.sha256(data_path.read_bytes()).hexdigest()
    pinned = EvaluationContract(**{**contract.__dict__, "data_provenance": "verified_market",
                                   "data_snapshot_sha256": sha})
    (repo / "contracts").mkdir(parents=True, exist_ok=True)
    (repo / "contracts" / "fence_fx_v1.yaml").write_text(yaml.safe_dump(pinned.__dict__), encoding="utf-8")
    (repo / "contracts" / "datasets").mkdir(parents=True, exist_ok=True)
    (repo / "contracts" / "datasets" / "FENCE_FX_V1.json").write_text(
        json.dumps({"contract_id": pinned.contract_id, "path": pinned.exploratory_data_path,
                    "sha256": sha, "provenance": "verified_market", "source": "fence_fixture"}),
        encoding="utf-8")
    (repo / "strategies").mkdir(parents=True, exist_ok=True)
    (repo / "strategies" / "flat.py").write_text(_GENERATE_POSITIONS, encoding="utf-8")
    reg = ResearchRegistry(repo / "registry" / "registry.db")
    reg.register_contract(pinned)
    reg.register_hypothesis(HypothesisSpec(
        "HYP-FENCE", pinned.contract_id, "Fence Fixture", "rules_ts",
        "Testing the exploratory holdout fence", "No friction in this fixture", 8,
        "Reject any run that loads holdout bars"))
    spec = repo / "spec.yaml"
    spec.write_text(yaml.safe_dump({
        "contract_id": pinned.contract_id, "hypothesis_id": "HYP-FENCE",
        "strategy_name": "Flat", "strategy_family": "rules_ts", "model_type": "rules",
        "strategy_module": "strategies/flat.py", "parameters": {}}), encoding="utf-8")
    return repo


@pytest.mark.parametrize("case", ["holdout_overflow", "empty_validation_slice"])
def test_run_spec_fails_before_stage0_or_reservation(pre_rg05_policy, tmp_path: Path, case: str) -> None:
    if case == "holdout_overflow":
        contract = _contract()
        panel = _panel(last_offset_hours=24)
        needle = "holdout_start"
    else:
        contract = _contract(val_start_hours=20, val_end_hours=30, holdout_end_hours=40)
        panel = _panel(last_offset_hours=9)
        needle = "validation slice"

    repo = _fenced_repo(tmp_path, panel, contract)
    runner = ExperimentRunner(repo_root=repo, allow_legacy_replay=True)
    with pytest.raises(ContractValidationError, match=needle):
        runner.run_spec(repo / "spec.yaml")

    # No scientific side effects: no strategy execution, no Stage-0 volatility, no trial row.
    assert not (repo / "strategies" / "GENERATE_POSITIONS_RAN").exists()
    assert runner._stage0_vol_cache == {}
    with sqlite3.connect(repo / "registry" / "registry.db") as conn:
        trials_used = conn.execute(
            "SELECT trials_used FROM hypotheses WHERE hypothesis_id='HYP-FENCE'").fetchone()[0]
        trial_count = conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0]
        stage = conn.execute("SELECT stage, outcome FROM selection_attempts").fetchall()
    assert (trials_used, trial_count) == (0, 0)
    assert stage == [("DATA", "ERROR")]


# ---------------------------------------------------------------------------
# 4. No holdout-staging fallback survives in builders or their tests
# ---------------------------------------------------------------------------

def test_no_holdout_staging_fallback_remains() -> None:
    for rel in ("scripts/salvage_multi_asset.py", "scripts/salvage_crypto.py",
                "tests/test_multi_asset_salvage.py"):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "holdout_staging" not in text, f"{rel} still references the holdout staging fallback"


def test_holdout_resolution_reason_is_used_for_an_unpinned_manifest(tmp_path: Path) -> None:
    """A holdout role never becomes eligible from exploratory pins alone."""
    panel = _panel(last_offset_hours=5)
    (tmp_path / "data" / "exploratory").mkdir(parents=True, exist_ok=True)
    panel.write_parquet(tmp_path / "data" / "exploratory" / "fence_fx.parquet")
    sha = hashlib.sha256((tmp_path / "data" / "exploratory" / "fence_fx.parquet").read_bytes()).hexdigest()
    contract = _contract(data_snapshot_sha256=sha)
    contract = EvaluationContract(**{**contract.__dict__, "data_provenance": "verified_market"})
    (tmp_path / "contracts" / "datasets").mkdir(parents=True, exist_ok=True)
    (tmp_path / "contracts" / "datasets" / "FENCE_FX_V1.json").write_text(json.dumps({
        "contract_id": contract.contract_id, "path": contract.exploratory_data_path,
        "sha256": sha, "provenance": "verified_market", "source": "fixture"}), encoding="utf-8")

    assert resolve_dataset(contract, tmp_path).eligible_market is True
    holdout = resolve_dataset(contract, tmp_path, role="holdout")
    assert holdout.eligible_market is False and "holdout_path" in holdout.reason
