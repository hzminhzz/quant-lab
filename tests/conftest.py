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
from warden.runner import ExperimentRunner

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


@pytest.fixture(autouse=True)
def _isolated_runtime(tmp_path: Path, monkeypatch):
    """Every test gets its own registry, HOME and TMPDIR; no test may touch the live state."""
    for var in ("WARDEN_HUMAN_SIGNING_KEY", "WARDEN_NON_INTERACTIVE", "WARDEN_AGENT_UID",
                "WARDEN_OPERATOR", "WARDEN_HUMAN_KEY_FILE", "WARDEN_REPO_ROOT"):
        monkeypatch.delenv(var, raising=False)
    for name in ("home", "tmp"):
        (tmp_path / name).mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    monkeypatch.setenv("WARDEN_REGISTRY", str(tmp_path / "env_registry.db"))
    yield


@pytest.fixture
def seeded_env_registry(monkeypatch):
    """One verified trial in the isolated WARDEN_REGISTRY, for subprocess CLI tests."""
    import os

    import numpy as np

    from warden.contracts import ExperimentResult, HypothesisSpec, load_contract
    from warden.registry import ResearchRegistry

    contract = load_contract(Path(__file__).resolve().parents[1] / "contracts" / "crypto_perps_1h_v1.yaml")
    reg = ResearchRegistry(Path(os.environ["WARDEN_REGISTRY"]))
    reg.register_contract(contract)
    reg.register_hypothesis(HypothesisSpec("HYP-ENV", contract.contract_id, "Env", "rules_ts",
                                           "Mechanism explanation of twenty chars",
                                           "Friction explanation of twenty chars", 8, "Criteria"))
    result = ExperimentResult(
        "RUN-ENV-001", "HYP-ENV", contract.contract_id, "Flat", "rules_ts", "cfg", "STAGE_3_STRESS",
        "REVISE", 1.0, 0.8, 0.5, 0.2, 1, 1.0, None, None, 0.5, 1.2, 4.0, 10.0, 20.0, 0.4, 1.0,
        "fixture", 0.0, 1.0, 0, contract.data_snapshot_sha256, "strategies/flat.py", "rules", "",
        data_provenance="verified_market")
    reg.record_trial(result, {}, np.linspace(0.001, -0.0005, 20))
    return reg


@pytest.fixture
def signed_repo(tmp_path: Path, monkeypatch):
    """A tmp repo whose manifest is signed by a pinned test key; no live files are touched."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    from warden import trust_root
    from warden.cli import MANIFEST_FILE, MANIFEST_SIG, generate_manifest

    repo = tmp_path / "signed_repo"
    (repo / "contracts" / "datasets").mkdir(parents=True)
    (repo / "scripts").mkdir()
    (repo / "strategies").mkdir()
    (repo / "contracts" / "crypto_perps_1h_v1.yaml").write_text(
        (Path(__file__).resolve().parents[1] / "contracts" / "crypto_perps_1h_v1.yaml").read_text())
    (repo / "scripts" / "check_loc_budget.py").write_text("raise SystemExit(0)\n")
    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(trust_root, "MANIFEST_PUBLIC_KEY_HEX",
                        key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex())
    (repo / MANIFEST_SIG).write_text(key.sign(generate_manifest(repo).encode()).hex())
    monkeypatch.setenv("WARDEN_REPO_ROOT", str(repo))
    assert MANIFEST_FILE in {p.name for p in repo.iterdir()}
    return repo


@pytest.fixture
def qualify_repo(tmp_path: Path):
    """Verified-market exploratory+holdout datasets, role manifests, and an APPROVED audit."""
    from warden.contracts import ExperimentResult

    builds = {"n": 0}

    def build(audit_approved: bool = True, reviewer: str = "human-operator", n_bars: int = 200,
              drop_funding: bool = False, holdout_manifest: bool = True, net_sharpe: float = 1.2):
        builds["n"] += 1
        repo = tmp_path / f"q{builds['n']}" / "repo"
        panel = tiny_panel(n_bars)
        if drop_funding:
            panel = panel.drop("funding_rate")
        exploratory = repo / "data" / "exploratory" / "tiny.parquet"
        holdout = repo / "data" / "holdout" / "tiny.parquet"
        for path in (exploratory, holdout):
            path.parent.mkdir(parents=True, exist_ok=True)
            panel.write_parquet(path)
        sha_exp, sha_hold = (hashlib.sha256(p.read_bytes()).hexdigest() for p in (exploratory, holdout))
        contract = EvaluationContract(
            contract_id="QUAL_V1", asset_class="crypto_perps", frequency="1h", bars_per_year=8760,
            exploratory_data_path="data/exploratory/tiny.parquet",
            holdout_data_path="data/holdout/tiny.parquet",
            train_start=utc("2025-01-01T00:00:00Z"), val_start=utc("2025-01-01T10:00:00Z"),
            val_end=utc("2025-01-02T00:00:00Z"), holdout_start=utc("2025-01-02T00:00:00Z"),
            holdout_end=utc("2025-01-07T00:00:00Z"), n_folds=2, label_horizon_bars=2,
            embargo_bars=1, execution_delay_bars=1, taker_fee_bps=1.0, slippage_bps=1.0,
            data_snapshot_sha256=sha_exp, data_provenance="verified_market")
        (repo / "contracts" / "datasets").mkdir(parents=True, exist_ok=True)
        (repo / "contracts" / "datasets" / "QUAL_V1.json").write_text(json.dumps(
            {"contract_id": "QUAL_V1", "path": contract.exploratory_data_path, "sha256": sha_exp,
             "provenance": "verified_market", "source": "fixture"}))
        if holdout_manifest:
            (repo / "contracts" / "datasets" / "QUAL_V1.holdout.json").write_text(json.dumps(
                {"contract_id": "QUAL_V1", "path": contract.holdout_data_path, "sha256": sha_hold,
                 "provenance": "verified_market", "source": "fixture"}))
        (repo / "contracts" / "qual_v1.yaml").write_text(yaml.safe_dump(contract.__dict__))
        (repo / "strategies").mkdir(parents=True, exist_ok=True)
        (repo / "strategies" / "flat.py").write_text(FLAT_MODULE)
        reg = ResearchRegistry(repo / "registry" / "registry.db")
        reg.register_contract(contract)
        reg.register_hypothesis(HypothesisSpec("HYP-Q", contract.contract_id, "Q", "rules_ts",
                                               "Mechanism explanation of twenty chars",
                                               "Friction explanation of twenty chars", 8, "Criteria"))
        result = ExperimentResult(
            "RUN-Q1", "HYP-Q", contract.contract_id, "Flat", "rules_ts", "cfg", "STAGE_3_STRESS",
            "CANDIDATE_FOR_QUALIFICATION", 1.0, net_sharpe, 0.95, 0.2, 1, 1.0, None, None, 0.55, 1.4,
            3.0, 60.0, 12.0, 0.9, 0.9, "fixture", 0.1, 1.0, 0, sha_exp,
            "strategies/flat.py", "rules", "", data_provenance="verified_market")
        reg.record_trial(result, {}, np.linspace(0.01, -0.004, 14))
        checks = {f"{n}_check": True for n in range(1, 7)}
        reg.record_audit("RUN-Q1", contract.contract_id, "APPROVED" if audit_approved else "REJECTED",
                         checks if audit_approved else {**checks, "5_check": False}, "fixture")
        return SimpleNamespace(repo=repo, registry=reg, contract=contract, panel=panel,
                               runner=ExperimentRunner(registry=reg, repo_root=repo), reviewer=reviewer)
    return build


@pytest.fixture
def tiny_repo(tmp_path: Path):
    """Contract + dataset manifest + registry + hypothesis + spec, all inside tmp_path."""
    builds = {"n": 0}

    def build(provenance: str = "verified_market", module_source: str = FLAT_MODULE,
              module_name: str = "flat", contract_overrides: dict | None = None,
              n_bars: int = 40) -> SimpleNamespace:
        builds["n"] += 1
        root = tmp_path / f"case{builds['n']}"
        repo = root / "repo"
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
        reg = ResearchRegistry(root / "registry.db")
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
