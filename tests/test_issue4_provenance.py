"""Issue #4 ticket 01: strict dataset provenance and resolution.

A matching digest is not evidence of origin: the declared provenance and the
exact contract path must agree, and a missing file is never substituted.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest
import yaml

from warden.contracts import EvaluationContract, HypothesisSpec, load_contract, utc
from warden.provenance import DatasetProvenanceError, resolve_dataset
from warden.registry import ResearchRegistry
from warden.runner import ExperimentRunner

REPO = Path(".")


def _tiny_panel(n_bars: int = 40) -> pl.DataFrame:
    rows, rng = [], np.random.default_rng(7)
    for asset, base in (("AAA", 100.0), ("BBB", 50.0)):
        price = base
        for t in range(n_bars):
            rows.append({"timestamp": datetime(2025, 1, 1, tzinfo=UTC) + timedelta(hours=t), "asset": asset, "open": price, "high": price * 1.01, "low": price * 0.99,
                "close": price * (1 + rng.normal(0, 0.002)), "volume": 10.0, "funding_rate": 0.0001})
            price = rows[-1]["close"]
    return pl.DataFrame(rows).sort("timestamp", "asset").with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))


def _contract(tmp_path: Path, panel: pl.DataFrame, provenance: str = "verified_market", pin: str | None = None) -> EvaluationContract:
    data_path = tmp_path / "data" / "exploratory" / "tiny.parquet"
    data_path.parent.mkdir(parents=True, exist_ok=True)
    panel.write_parquet(data_path)
    sha = hashlib.sha256(data_path.read_bytes()).hexdigest()
    return EvaluationContract(
        contract_id="TINY_V1", asset_class="crypto_perps", frequency="1h", bars_per_year=8760,
        exploratory_data_path="data/exploratory/tiny.parquet", holdout_data_path="data/holdout/tiny.parquet",
        train_start=utc("2025-01-01T00:00:00Z"), val_start=utc("2025-01-01T10:00:00Z"),
        val_end=utc("2025-01-02T00:00:00Z"), holdout_start=utc("2025-01-02T00:00:00Z"),
        holdout_end=utc("2025-01-03T00:00:00Z"), n_folds=2, label_horizon_bars=2, embargo_bars=1,
        execution_delay_bars=1, taker_fee_bps=1.0, slippage_bps=1.0, data_snapshot_sha256=pin or sha,
        data_provenance=provenance)


def _manifest(tmp_path: Path, contract: EvaluationContract, provenance: str = "verified_market", **override) -> Path:
    record = {"contract_id": contract.contract_id, "path": contract.exploratory_data_path,
              "sha256": contract.data_snapshot_sha256, "provenance": provenance,
              "source": "fixture", "recorded_at": "2026-10-26T00:00:00+00:00"}
    record.update(override)
    path = tmp_path / "contracts" / "datasets" / f"{contract.contract_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record))
    return path


def test_manifest_provenance_is_verified_independently_of_the_digest(tmp_path: Path) -> None:
    contract = _contract(tmp_path, _tiny_panel())
    _manifest(tmp_path, contract)
    assert resolve_dataset(contract, tmp_path).eligible_market is True

    # A matching digest cannot turn declared synthetic data into market evidence.
    _manifest(tmp_path, contract, provenance="synthetic")
    synthetic = resolve_dataset(contract, tmp_path)
    assert synthetic.eligible_market is False
    assert "contract provenance" in synthetic.reason and synthetic.provenance == "synthetic"

    _manifest(tmp_path, contract, provenance="unattested")
    assert resolve_dataset(contract, tmp_path).eligible_market is False

    _manifest(tmp_path, contract, sha256="0" * 64)
    assert "digest differs from manifest" in resolve_dataset(contract, tmp_path).reason

    _manifest(tmp_path, contract, path="data/exploratory/other.parquet")
    assert "!= contract path" in resolve_dataset(contract, tmp_path).reason

    (tmp_path / "contracts" / "datasets" / "TINY_V1.json").unlink()
    missing_manifest = resolve_dataset(contract, tmp_path)
    assert missing_manifest.eligible_market is False and "missing dataset manifest" in missing_manifest.reason


def test_contract_pin_mismatch_is_not_market_eligible(tmp_path: Path) -> None:
    contract = _contract(tmp_path, _tiny_panel())
    _manifest(tmp_path, contract)
    assert resolve_dataset(contract, tmp_path).eligible_market is True
    res = resolve_dataset(replace(contract, data_snapshot_sha256="1" * 64), tmp_path)
    assert res.eligible_market is False and "data_snapshot_sha256 differs" in res.reason


def test_missing_dataset_never_falls_back_to_another_contract(tmp_path: Path) -> None:
    contract = _contract(tmp_path, _tiny_panel())
    (tmp_path / contract.exploratory_data_path).unlink()
    decoy = tmp_path / "data" / "exploratory" / "crypto_perps_1h.parquet"
    decoy.write_bytes(b"decoy")
    with pytest.raises(DatasetProvenanceError) as exc:
        resolve_dataset(contract, tmp_path)
    assert "data/exploratory/tiny.parquet" in str(exc.value)
    assert "no cross-contract fallback" in str(exc.value)


def test_declared_provenance_requires_a_pin() -> None:
    contract = load_contract(REPO / "contracts" / "crypto_perps_1h_v1.yaml")
    with pytest.raises(ValueError, match="requires a data_snapshot_sha256 pin"):
        replace(contract, data_snapshot_sha256="", data_provenance="verified_market")
    with pytest.raises(ValueError, match="data_provenance must be"):
        replace(contract, data_provenance="market-ish")


def test_synthetic_run_is_recorded_and_never_promoted(tmp_path: Path) -> None:
    """End-to-end: synthetic provenance is observable in the trial and caps the verdict."""
    panel = _tiny_panel()
    contract = _contract(tmp_path, panel, provenance="synthetic")
    _manifest(tmp_path, contract, provenance="synthetic")
    (tmp_path / "strategies").mkdir(exist_ok=True)
    (tmp_path / "strategies" / "flat.py").write_text(
        "import numpy as np\n"
        "def generate_positions(panel_df, contract, **kwargs):\n"
        "    ts = sorted(panel_df['timestamp'].unique().to_list())\n"
        "    assets = sorted(panel_df['asset'].unique().to_list())\n"
        "    pos = np.zeros((len(ts), len(assets)))\n"
        "    pos[::4] = 0.1\n"
        "    return {'positions': pos}\n")
    (tmp_path / "contracts" / "tiny_v1.yaml").write_text(yaml.safe_dump({**contract.__dict__, "contract_id": "TINY_V1"}))
    reg = ResearchRegistry(tmp_path / "registry.db")
    reg.register_contract(contract)
    hyp = HypothesisSpec("HYP-TINY", contract.contract_id, "Tiny", "rules_ts",
                         "Mechanism explanation of twenty chars", "Friction explanation of twenty chars",
                         8, "Reject net Sharpe below one")
    reg.register_hypothesis(hyp)
    spec = tmp_path / "spec.yaml"
    spec.write_text(yaml.safe_dump({"contract_id": contract.contract_id, "hypothesis_id": hyp.hypothesis_id,
                                    "strategy_name": "Flat", "strategy_family": "rules_ts", "model_type": "rules",
                                    "strategy_module": "strategies/flat.py", "parameters": {}}))
    res = ExperimentRunner(registry=reg, repo_root=tmp_path).run_spec(spec)
    assert res.verdict != "CANDIDATE_FOR_QUALIFICATION"
    assert res.data_provenance == "synthetic"
    with reg._connect() as conn:
        row = conn.execute("SELECT data_provenance, verdict FROM trials WHERE run_id=?", (res.run_id,)).fetchone()
    assert tuple(row) == ("synthetic", res.verdict)
