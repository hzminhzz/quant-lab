"""Spec #109 review B4 — the ``warden`` exit-code vocabulary, pinned per command.

The four codes are a contract with agents and cron wrappers (``docs/EXIT-CODES.md``):
``0`` success, ``1`` ran and the evidence was refused, ``2`` the request is refused or its
input is untrusted, ``3`` the requirement is not satisfiable.  Each command below is driven
through its real CLI entry point in a scratch repo; the numbers are asserted, not described.

The lane-J fixtures (a signed scratch policy, a synthetic fenced minute lake, one compiled
artifact) are the single source of truth for the repo, so this file cannot drift from the
artifact layout it exercises.
"""

from __future__ import annotations

import json

import pytest

from test_spec109_j_compile_sandbox import (  # noqa: E402 - the lane's own fixtures
    _hypothesis_file,
    _requirement,
    _sandbox_workspace,
)
from warden.cli import main as cli_main

FENCE_OFF_GRID_TIMEOUT_MARKER = "WARDEN_DATA_COMPILE_FAILED"


@pytest.fixture
def compiled(tmp_path, monkeypatch):
    """A signed scratch repo with one compiled artifact and its lake."""
    repo, lake, (artifact,) = _sandbox_workspace(
        tmp_path, monkeypatch, baskets=(("EURUSD",),)
    )
    monkeypatch.chdir(repo)
    monkeypatch.setenv("ML4T_DATA_STORE", str(lake))
    return repo, lake, artifact


def _evidence_refused_compile(repo, lake) -> int:
    """A fence that cannot be held: ``min_start`` is at/after ``holdout_start``."""
    requirement = dict(_requirement(), min_start="2021-01-10T00:00:00Z")
    return cli_main(["data", "compile", "--requirement", json.dumps(requirement),
                     "--lake-root", str(lake), "--json"])


def _run(command: str, scenario: str, repo, lake, artifact) -> int:
    if command == "resolve-data":
        if scenario == "success":
            return cli_main(["resolve-data", "--requirement", json.dumps(_requirement()),
                             "--json"])
        if scenario == "request_refused":
            document = {**_requirement(), "taker_fee_bps": 0.01}
            return cli_main(["resolve-data", "--requirement", json.dumps(document), "--json"])
        if scenario == "not_satisfiable":
            return cli_main(["resolve-data", "--requirement",
                             json.dumps(_requirement(symbols=["EURJPY"])), "--json"])
    if command == "data compile":
        if scenario == "success":
            # The same requirement twice: the identical artifact is re-verified and reused.
            return cli_main(["data", "compile", "--requirement", json.dumps(_requirement()),
                             "--lake-root", str(lake), "--json"])
        if scenario == "evidence_refused":
            return _evidence_refused_compile(repo, lake)
        if scenario == "request_refused":
            return cli_main(["data", "compile", "--requirement", json.dumps(_requirement()),
                             "--lake-root", str(lake), "--override", "taker_fee_bps=0.05",
                             "--json"])
        if scenario == "not_satisfiable":
            return cli_main(["data", "compile", "--requirement",
                             json.dumps(_requirement(symbols=["EURJPY"])),
                             "--lake-root", str(lake), "--json"])
    if command == "promote-contract":
        if scenario == "success":
            return cli_main(["promote-contract", "--contract-id", artifact.contract_id,
                             "--human-confirm-promote", "YES_PROMOTE", "--json"])
        if scenario == "evidence_refused":
            # The Parquet moved after the compile: the promotion re-reads every digest.
            (repo / artifact.parquet_path).write_bytes(b"tampered-parquet")
            return cli_main(["promote-contract", "--contract-id", artifact.contract_id,
                             "--human-confirm-promote", "YES_PROMOTE", "--json"])
        if scenario == "request_refused":
            # No human token: the gate is a request refusal, not an evidence refusal.
            return cli_main(["promote-contract", "--contract-id", artifact.contract_id, "--json"])
    raise AssertionError(f"no such scenario: {command}/{scenario}")


@pytest.mark.parametrize(
    ("command", "scenario", "expected"),
    [
        ("resolve-data", "success", 0),
        ("resolve-data", "request_refused", 2),
        ("resolve-data", "not_satisfiable", 3),
        ("data compile", "success", 0),
        ("data compile", "evidence_refused", 1),
        ("data compile", "request_refused", 2),
        ("data compile", "not_satisfiable", 3),
        ("promote-contract", "success", 0),
        ("promote-contract", "evidence_refused", 1),
        ("promote-contract", "request_refused", 2),
    ],
)
def test_exit_code_vocabulary(compiled, capsys, command, scenario, expected) -> None:
    repo, lake, artifact = compiled
    assert _run(command, scenario, repo, lake, artifact) == expected
    capsys.readouterr()


def test_data_build_keeps_its_legacy_input_error_code(compiled, capsys, tmp_path) -> None:
    """Documented exception: ``data build`` folds input/parse errors into 1, not 2."""
    repo, _lake, _artifact = compiled
    broken = repo / "broken-dataset.json"
    broken.write_text("{not a dataset spec", encoding="utf-8")
    assert cli_main(["data", "build", str(broken), "--json"]) == 1
    capsys.readouterr()
