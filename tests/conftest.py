"""Shared isolated-fixture helpers for the issue-4 hardening tests."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest
import yaml

from warden.contracts import EvaluationContract, HypothesisSpec, utc
from warden.registry import ResearchRegistry

FLAT_MODULE = """
import numpy as np


def generate_positions(panel_df, contract, **kwargs):
    ts = sorted(panel_df["timestamp"].unique().to_list())
    assets = sorted(panel_df["asset"].unique().to_list())
    pos = np.zeros((len(ts), len(assets)))
    pos[::4] = 0.1
    return {"positions": pos, "ic_series": np.full(max(len(ts) - 8, 0), 0.02)}
"""


def tiny_panel(n_bars: int = 40, assets: tuple[str, ...] = ("AAA", "BBB")) -> pl.DataFrame:
    rows, rng = [], np.random.default_rng(7)
    for asset, base in zip(assets, (100.0, 50.0, 25.0)):
        price = base
        for t in range(n_bars):
            rows.append({"timestamp": datetime(2025, 1, 1, tzinfo=UTC) + timedelta(hours=t),
                         "asset": asset, "open": price, "high": price * 1.01, "low": price * 0.99,
                         "close": price * (1 + rng.normal(0, 0.002)), "volume": 10.0,
                         "funding_rate": 0.0001})
            price = rows[-1]["close"]
    return pl.DataFrame(rows).sort("timestamp", "asset").with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))


@pytest.fixture
def tiny_repo(tmp_path: Path):
    """Contract + dataset manifest + registry + hypothesis + spec, all inside tmp_path."""
    def build(provenance: str = "verified_market", module_source: str = FLAT_MODULE,
              module_name: str = "flat", contract_overrides: dict | None = None,
              n_bars: int = 40) -> SimpleNamespace:
        repo = tmp_path / "repo"
        panel = tiny_panel(n_bars)
        data_path = repo / "data" / "exploratory" / "tiny.parquet"
        data_path.parent.mkdir(parents=True, exist_ok=True)
        panel.write_parquet(data_path)
        sha = hashlib.sha256(data_path.read_bytes()).hexdigest()
        fields = {
            "contract_id": "TINY_V1", "asset_class": "crypto_perps", "frequency": "1h", "bars_per_year": 8760,
            "exploratory_data_path": "data/exploratory/tiny.parquet",
            "holdout_data_path": "data/holdout/tiny.parquet",
            "train_start": utc("2025-01-01T00:00:00Z"), "val_start": utc("2025-01-01T10:00:00Z"),
            "val_end": utc("2025-01-02T00:00:00Z"), "holdout_start": utc("2025-01-02T00:00:00Z"),
            "holdout_end": utc("2025-01-03T00:00:00Z"), "n_folds": 2, "label_horizon_bars": 2,
            "embargo_bars": 1, "execution_delay_bars": 1, "taker_fee_bps": 1.0, "slippage_bps": 1.0,
            "data_snapshot_sha256": sha, "data_provenance": provenance}
        fields.update(contract_overrides or {})
        contract = EvaluationContract(**fields)
        manifest = repo / "contracts" / "datasets" / f"{contract.contract_id}.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps({"contract_id": contract.contract_id, "path": contract.exploratory_data_path,
                                        "sha256": sha, "provenance": provenance, "source": "fixture"}))
        (repo / "contracts" / "tiny_v1.yaml").write_text(yaml.safe_dump(contract.__dict__))
        (repo / "strategies").mkdir(parents=True, exist_ok=True)
        (repo / "strategies" / f"{module_name}.py").write_text(module_source)
        reg = ResearchRegistry(tmp_path / "registry.db")
        reg.register_contract(contract)
        hyp = HypothesisSpec("HYP-TINY", contract.contract_id, "Tiny", "rules_ts",
                             "Mechanism explanation of twenty chars", "Friction explanation of twenty chars",
                             8, "Reject net Sharpe below one")
        reg.register_hypothesis(hyp)
        spec = repo / "spec.yaml"
        spec.write_text(yaml.safe_dump({"contract_id": contract.contract_id, "hypothesis_id": hyp.hypothesis_id,
                                        "strategy_name": "Tiny", "strategy_family": "rules_ts",
                                        "model_type": "rules", "strategy_module": f"strategies/{module_name}.py",
                                        "parameters": {}}))
        return SimpleNamespace(repo=repo, registry=reg, contract=contract, spec=spec, panel=panel, sha=sha)
    return build
