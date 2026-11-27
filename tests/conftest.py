"""Shared isolated-fixture helpers for the issue-4 hardening tests."""

from __future__ import annotations

import os

# Avoid 8x8 thread oversubscription when running under pytest-xdist workers.
os.environ.setdefault("POLARS_MAX_THREADS", "2")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import hashlib
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest
import yaml

from warden.contracts import EvaluationContract, HypothesisSpec, utc
from warden.registry import ResearchRegistry


def write_fixture_checksums(repo: Path, *protected_paths: str) -> None:
    """Pin only the files declared by a scratch fixture; no approval or signature."""
    lines = [f"{hashlib.sha256((repo / rel).read_bytes()).hexdigest()}  {rel}\n"
             for rel in protected_paths]
    (repo / ".warden_manifest.sha256").write_text("".join(lines), encoding="utf-8")


@pytest.fixture
def cli_repo(tmp_path: Path) -> Path:
    """A scratch checkout with a real empty ledger and no production inputs."""
    repo = tmp_path / "cli_repo"
    repo.mkdir()
    git = ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgSign=false",
           "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid"]
    subprocess.run([*git, "init", "-q"], cwd=repo, check=True)
    subprocess.run([*git, "commit", "--allow-empty", "-qm", "fixture"], cwd=repo, check=True)
    ResearchRegistry(repo / "registry/registry.db")
    contract = "contracts/fixture.yaml"
    (repo / contract).parent.mkdir()
    panel_pin = hashlib.sha256(b"fixture-only panel identity").hexdigest()
    (repo / contract).write_text(
        f"contract_id: FIXTURE\ndata_snapshot_sha256: {panel_pin}\n", encoding="utf-8",
    )
    write_fixture_checksums(repo, contract)
    return repo


FLAT_MODULE = """
from ml4t.backtest import Strategy
from ml4t.backtest.execution.rebalancer import RebalanceConfig, TargetWeightExecutor


def required_symbols(params):
    return None


class _FlatStrategy(Strategy):
    def __init__(self):
        self._bar = 0
        self._exec = TargetWeightExecutor(
            RebalanceConfig(allow_short=True, allow_fractional=True, max_single_weight=0.5, max_gross_leverage=1.0)
        )

    def on_data(self, timestamp, data, context, broker):
        w = 0.1 if self._bar % 4 == 0 else 0.0
        self._exec.execute({a: w for a in data}, data, broker, timestamp=timestamp)
        self._bar += 1


def configure_strategy(contract, params):
    return _FlatStrategy()
"""


# 24 hourly bars from 2025-01-01T00:00 end at 2025-01-01T23:00, strictly before the
# fixture contract's holdout_start (2025-01-02T00:00).  The #88 exploratory fence rejects
# any panel bar at/after holdout_start, so a synthetic panel may not exceed the
# train+validation window it is meant to model.
def tiny_panel(n_bars: int = 24, assets: tuple[str, ...] = ("AAA", "BBB")) -> pl.DataFrame:
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
    builds = {"n": 0}

    def build(provenance: str = "verified_market", module_source: str = FLAT_MODULE,
              module_name: str = "flat", contract_overrides: dict | None = None,
              n_bars: int = 24, panel_override: pl.DataFrame | None = None) -> SimpleNamespace:
        builds["n"] += 1
        root = tmp_path / f"case{builds['n']}"
        repo = root / "repo"
        panel = tiny_panel(n_bars) if panel_override is None else panel_override
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


@pytest.fixture
def pre_rg05_policy(tmp_path, monkeypatch):
    """Isolate pre-RG-05 evaluator-mechanics tests from the new approval dependency.

    These tests deliberately construct scratch, pre-v6 hypotheses (some seed
    historical trial debt). Their purpose is execution, causality, statistical
    gates or historical ledger behavior, not plan approval. Stub ONLY the new
    intent/review boundary, never RG-03/04 accounting, the kernel, or data gates.
    No fake approval or binding rows are written. The real RG-05 public paths
    are exercised without this double in test_rg05_research_intent.py.
    """
    from warden import research_intent
    from warden.runner import ExperimentRunner

    def scratch(db_path):
        assert Path(db_path).resolve().is_relative_to(tmp_path.resolve()), (
            "pre-RG05 test double may only operate on this test's scratch ledger"
        )

    def require_intent(runner, spec, contract):
        scratch(runner.registry.db_path)
        assert spec.research_intent_sha256 is None

    def require_dataset(dataset, document):
        assert document is None

    def require_review(conn, candidate, robustness_plan, intent_sha):
        scratch(conn.execute("PRAGMA database_list").fetchone()[2])
        assert intent_sha is None

    def bind_review(conn, candidate_id, intent_link):
        scratch(conn.execute("PRAGMA database_list").fetchone()[2])
        assert intent_link is None

    def qualify(registry, run_id, contract, code_sha, root):
        scratch(registry.db_path)

    monkeypatch.setattr(ExperimentRunner, "_require_intent", require_intent)
    monkeypatch.setattr(research_intent, "require_dataset_resolution", require_dataset)
    monkeypatch.setattr(research_intent, "require_review", require_review)
    monkeypatch.setattr(research_intent, "bind_review", bind_review)
    monkeypatch.setattr(research_intent, "require_qualification_conformance", qualify)


def write_novelty_fixture(repo: Path):
    """Synthetic, explicitly empty memory sources for prospective governance tests."""
    from warden.novelty import LESSONS
    from warden_ops.research_outcome_memory import INDEX

    lessons = repo / LESSONS
    if not lessons.exists():
        lessons.parent.mkdir(parents=True, exist_ok=True)
        lessons.write_text("# Synthetic fixture lessons\nNo prior lessons in this fixture.\n")
    index = repo / INDEX
    if not index.exists():
        index.parent.mkdir(parents=True, exist_ok=True)
        index.write_text(json.dumps({
            "schema_version": 1,
            "source_refs": [{"pr": 40, "commit": "abc1234"}],
            "outcomes": [], "original_unattempted": [],
        }))


def novelty_fixture_review(binding):
    """Test-only principal attestation; explicit relations from fixture identities."""
    memory = binding["prior_memory"]
    return {
        "snapshot_sha256": memory["snapshot_sha256"],
        "lessons_disposition": "Synthetic lessons inspected for fixture.",
        "search_disposition": "Synthetic catalog and indexed outcomes inspected.",
        "relationships": {
            hid: {
                "relation": "RELATED" if hid in memory["must_share_lineage"] else "DISTINCT",
                "mechanism": "Synthetic fixture comparison of mechanism",
                "signal": "Synthetic fixture signal comparison",
                "universe": "Synthetic fixture universe comparison",
                "outcome_disposition": "Retain all fixture prior evidence",
            }
            for hid in memory["required_relationships"]
        },
    }
