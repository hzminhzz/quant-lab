"""Checkpoint history/current publication and read-only recovery contracts."""

from __future__ import annotations

import argparse
import json

import pytest

from warden.runner import ExperimentRunner
from warden_ops import checkpoint


def _fixture(tiny_repo, monkeypatch):
    fixture = tiny_repo()
    monkeypatch.setattr(checkpoint.subprocess, "check_output", lambda *a, **k: "a1b2c3d\n")
    monkeypatch.setattr("warden.cli.check_worktree", lambda *a, **k: True)
    runner = ExperimentRunner(registry=fixture.registry, repo_root=fixture.repo)
    return fixture, runner


def _handoff(runner, notes="checkpoint"):
    return checkpoint.cmd_handoff(argparse.Namespace(ticket="T-13", notes=notes), runner)


def _continue(runner):
    return checkpoint.cmd_continue(argparse.Namespace(transition_path=None, json=True), runner)


def test_checkpoint_commit_is_historically_attributed_and_replayable(tiny_repo, monkeypatch, capsys) -> None:
    fixture, runner = _fixture(tiny_repo, monkeypatch)
    assert _handoff(runner) == 0
    current = json.loads((fixture.repo / checkpoint.STATE_FILE).read_text())
    history = [json.loads(x) for x in (fixture.repo / checkpoint.HISTORY_FILE).read_text().splitlines()]
    assert history[-1] == current
    assert current["checkpoint_id"] == checkpoint._checkpoint_id(current)
    assert _continue(runner) == 0
    state = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert state["value"]["history_consistent"] is True
    assert state["value"]["evidence_source"] == "live-replayed"


def test_interruption_before_current_pointer_reports_drifts(tiny_repo, monkeypatch, capsys) -> None:
    fixture, runner = _fixture(tiny_repo, monkeypatch)
    assert _handoff(runner, "first") == 0
    current_path = fixture.repo / checkpoint.STATE_FILE
    original = current_path.read_bytes()

    def killed_after_history(*args, **kwargs):
        raise OSError("injected crash before current pointer publication")

    monkeypatch.setattr(checkpoint.os, "replace", killed_after_history)
    with pytest.raises(OSError, match="injected crash"):
        _handoff(runner, "second")
    assert current_path.read_bytes() == original
    assert _continue(runner) == 1
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result["value"]["history_consistent"] is False
    assert result["value"]["evidence_source"] == "cached-review-required"


def test_tampered_checkpoint_hash_fails_closed(tiny_repo, monkeypatch, capsys) -> None:
    fixture, runner = _fixture(tiny_repo, monkeypatch)
    assert _handoff(runner) == 0
    path = fixture.repo / checkpoint.STATE_FILE
    state = json.loads(path.read_text())
    state["notes"] = "tampered"
    path.write_text(json.dumps(state))
    assert _continue(runner) == 1
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result["value"]["drift"] is True
