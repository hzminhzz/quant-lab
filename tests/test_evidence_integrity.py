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
from warden.contract_source import load_active_contract, load_recorded_contract
from warden.contracts import ExperimentResult, HypothesisSpec
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


def test_repeated_closure_reuses_parsing_but_reads_every_source_byte(tmp_path, monkeypatch):
    """A warm parse cannot hide same-size edits with restored timestamps."""
    import os

    entry = tmp_path / "strategy.py"
    helper = tmp_path / "local_helper.py"
    runtime = tmp_path / "runtime.py"
    entry.write_text("import local_helper\nCODE_DEPENDENCIES = ('runtime.py',)\n")
    helper.write_text("VALUE = 1  # closure byte-invalidation fixture\n")
    runtime.write_text("RUNTIME = 3  # declared dependency fixture\n")
    original_parse, original_read = ast.parse, Path.read_bytes
    parsed, read = [], []

    def parse(raw, *args, **kwargs):
        parsed.append(raw)
        return original_parse(raw, *args, **kwargs)

    def read_bytes(path):
        read.append(path)
        return original_read(path)

    monkeypatch.setattr(ast, "parse", parse)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    first = code_closure(tmp_path, "strategy.py")
    assert code_closure(tmp_path, "strategy.py") == first
    assert len(parsed) == 3
    assert len(read) == 6
    assert all(read.count(path) == 2 for path in (entry, runtime, helper))
    recorded = helper.stat()
    helper.write_text("VALUE = 2  # closure byte-invalidation fixture\n")
    os.utime(helper, ns=(recorded.st_atime_ns, recorded.st_mtime_ns))
    changed = code_closure(tmp_path, "strategy.py")
    assert changed[0] != first[0]
    assert changed[1] == first[1]
    assert len(parsed) == 4
    assert len(read) == 9
    assert all(read.count(path) == 3 for path in (entry, runtime, helper))


def test_warm_closure_still_checks_new_dependencies_and_source_links(tmp_path, monkeypatch):
    entry = tmp_path / "strategy.py"
    helper = tmp_path / "late_helper.py"
    entry.write_text("import late_helper  # filesystem bindings are never cached\n")
    initial = code_closure(tmp_path, "strategy.py")
    helper.write_text("VALUE = 5\n")
    resolved = code_closure(tmp_path, "strategy.py")
    assert resolved[0] != initial[0]
    assert resolved[1] == ["late_helper.py", "strategy.py"]
    target = tmp_path / "outside-source"
    target.write_bytes(b"must never be read")
    helper.unlink()
    helper.symlink_to(target)

    def forbid_target(path):
        assert path != target
        return original_read(path)

    original_read = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", forbid_target)
    with pytest.raises(ValueError, match="symlinked source input refused"):
        code_closure(tmp_path, "strategy.py")


def test_register_contract_is_idempotent_and_immutable(tmp_path: Path) -> None:
    reg = ResearchRegistry(tmp_path / "reg.db")
    contract = load_recorded_contract(ROOT, "CRYPTO_PERPS_1H_V1")
    reg.register_contract(contract)
    reg.register_contract(contract)  # identical re-registration is a no-op (salvage re-run)
    changed = replace(contract, taker_fee_bps=contract.taker_fee_bps + 1.0)
    with pytest.raises(ValueError, match="immutable"):
        reg.register_contract(changed)  # changed contract must fail closed, never overwrite


def _qualify_setup(tmp_path: Path, strategy_module: str, code_sha: str):
    reg = ResearchRegistry(tmp_path / "reg.db")
    runner = ExperimentRunner(registry=reg, repo_root=ROOT)
    # A live contract: qualification is new work, and #114 refuses an archived contract
    # before any code-binding check runs (that refusal has its own test in lane G).
    contract = load_active_contract(ROOT, "CRYPTO_PERPS_5M_V1")
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
    with pytest.raises(ValueError, match="unsafe frozen strategy module"):
        qualify_candidate("RUN-EV", "YES_UNSEAL_HOLDOUT", runner, holdout_override_path=holdout)


def test_qualify_fails_closed_on_absent_module_file(tmp_path: Path) -> None:
    runner, holdout = _qualify_setup(tmp_path, strategy_module="strategies/candidates/does_not_exist_1h.py", code_sha="0" * 64)
    with pytest.raises(FileNotFoundError, match="unavailable"):
        qualify_candidate("RUN-EV", "YES_UNSEAL_HOLDOUT", runner, holdout_override_path=holdout)


def test_qualify_fails_closed_on_absent_code_binding(tmp_path: Path) -> None:
    runner, holdout = _qualify_setup(tmp_path, strategy_module=VOL, code_sha="")
    with pytest.raises(ValueError, match="no recorded strategy_code_sha256"):
        qualify_candidate("RUN-EV", "YES_UNSEAL_HOLDOUT", runner, holdout_override_path=holdout)


def test_qualify_fails_closed_on_changed_code(tmp_path: Path) -> None:
    runner, holdout = _qualify_setup(tmp_path, strategy_module=VOL, code_sha="0" * 64)
    with pytest.raises(ValueError, match="changed since the trial"):
        qualify_candidate("RUN-EV", "YES_UNSEAL_HOLDOUT", runner, holdout_override_path=holdout)