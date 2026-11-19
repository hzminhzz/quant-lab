"""Evidence-integrity gates: complete code closure, fail-closed qualification, contract immutability.

Pins the three HIGH fixes so a regression re-opening any of them fails the suite:
  * codeid under-coverage  -> closure must cover runtime-loaded / subdir code (CODE_DEPENDENCIES)
  * qualify substitution   -> qualification must bind to the trial's exact code, fail closed otherwise
  * salvage bypass         -> contract registration is idempotent-identical / immutable-different
"""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from warden.codeid import _declared_dependencies, code_closure
from warden.contracts import ExperimentResult, HypothesisSpec, load_contract
from warden.qualify import qualify_candidate
from warden.registry import ResearchRegistry
from warden.runner import ExperimentRunner
from warden.synthetic import generate_canonical_market_panel

ROOT = Path(".")
VOL = "strategies/candidates/vol_breakout_1h.py"
BOOK = "strategies/books/af30_handcrafted_book_1h.py"
S183 = "strategies/candidates/s183_composite.py"


def test_code_closure_covers_book_runtime_constituents() -> None:
    _, files = code_closure(ROOT, BOOK)
    declared = _declared_dependencies(ast.parse((ROOT / BOOK).read_text()))
    assert len(declared) >= 20  # the book's full constituent set
    assert all(d in files for d in declared)
    assert any(f.endswith("ts_common.py") for f in files)  # constituents' shared helper is covered too


def test_code_closure_covers_s183_subdir_helper() -> None:
    _, files = code_closure(ROOT, S183)
    assert any(f.endswith("carver_daily/carver_common.py") for f in files)


def test_runtime_loaders_declare_code_dependencies() -> None:
    """Any strategy that loads code at runtime or bootstraps sys.path must declare CODE_DEPENDENCIES."""
    offenders = []
    paths = list((ROOT / "strategies/candidates").rglob("*.py")) + list((ROOT / "strategies/books").rglob("*.py"))
    for p in paths:
        src = p.read_text()
        if ("load_module_from_path(" in src or "sys.path.insert" in src) and not _declared_dependencies(ast.parse(src)):
            offenders.append(str(p))
    assert offenders == []


def test_register_contract_is_idempotent_and_immutable(tmp_path: Path) -> None:
    reg = ResearchRegistry(tmp_path / "reg.db")
    contract = load_contract(ROOT / "contracts/crypto_perps_1h_v1.yaml")
    reg.register_contract(contract)
    reg.register_contract(contract)  # identical re-registration is a no-op (salvage re-run)
    changed = replace(contract, taker_fee_bps=contract.taker_fee_bps + 1.0)
    with pytest.raises(ValueError, match="immutable"):
        reg.register_contract(changed)  # changed contract must fail closed, never overwrite


def _qualify_setup(tmp_path: Path, strategy_module: str, code_sha: str):
    reg = ResearchRegistry(tmp_path / "reg.db")
    runner = ExperimentRunner(registry=reg, repo_root=ROOT)
    contract = load_contract(ROOT / "contracts/crypto_perps_1h_v1.yaml")
    reg.register_contract(contract)
    hyp = HypothesisSpec("HYP-EV", contract.contract_id, "T", "rules_ts",
                         "Mechanism at least twenty chars", "Friction at least twenty chars", 8, "Crit")
    reg.register_hypothesis(hyp)
    res = ExperimentResult("RUN-EV", hyp.hypothesis_id, contract.contract_id, "S", "rules_ts", "x",
                           "STAGE_3_STRESS", "CANDIDATE_FOR_QUALIFICATION", 1.5, 1.2, 0.95, 0.1, 1, 1.0,
                           None, None, 0.5, 1.2, 5.0, 10.0, 20.0, 0.8, 1.0, "notes", 0.1, 0.0, 0,
                           contract.data_snapshot_sha256, strategy_module, "rules", "",
                           data_provenance="verified_market", strategy_code_sha256=code_sha)
    reg.record_trial(res, {}, np.zeros(100))
    holdout = tmp_path / "holdout.parquet"
    generate_canonical_market_panel().write_parquet(holdout)
    return runner, holdout


def test_qualify_fails_closed_on_missing_module(tmp_path: Path) -> None:
    runner, holdout = _qualify_setup(tmp_path, strategy_module="", code_sha="")
    with pytest.raises(ValueError, match="no recorded strategy_module"):
        qualify_candidate("RUN-EV", "YES_UNSEAL_HOLDOUT", runner, holdout_override_path=holdout)


def test_qualify_fails_closed_on_absent_module_file(tmp_path: Path) -> None:
    runner, holdout = _qualify_setup(tmp_path, strategy_module="strategies/candidates/does_not_exist_1h.py", code_sha="0" * 64)
    with pytest.raises(FileNotFoundError, match="absent"):
        qualify_candidate("RUN-EV", "YES_UNSEAL_HOLDOUT", runner, holdout_override_path=holdout)


def test_qualify_fails_closed_on_absent_code_binding(tmp_path: Path) -> None:
    runner, holdout = _qualify_setup(tmp_path, strategy_module=VOL, code_sha="")
    with pytest.raises(ValueError, match="no recorded strategy_code_sha256"):
        qualify_candidate("RUN-EV", "YES_UNSEAL_HOLDOUT", runner, holdout_override_path=holdout)


def test_qualify_fails_closed_on_changed_code(tmp_path: Path) -> None:
    runner, holdout = _qualify_setup(tmp_path, strategy_module=VOL, code_sha="0" * 64)
    with pytest.raises(ValueError, match="changed since the trial"):
        qualify_candidate("RUN-EV", "YES_UNSEAL_HOLDOUT", runner, holdout_override_path=holdout)