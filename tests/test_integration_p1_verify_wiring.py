"""Integration regression: cmd_verify validates #90 spec and #88 fence before work.

These tests pin the two integration-only edits in ``warden.cli.cmd_verify``:

* the frozen ``ExperimentSpec`` is loaded before any contract/dataset/registry/cache
  or strategy work, so an unknown or policy-injecting key is rejected with exit 1 and
  no side effects; and
* the #88 exploratory fence (no bar at/after ``holdout_start``, no empty validation
  window) runs before the strategy proofs and before the feature cache is written.

``check_worktree`` is stubbed here because this branch is intentionally unsigned: the
manifest re-sign is a separate, principal-authorized step.  The fence and schema checks
are what these tests exercise, not manifest integrity.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from warden import cli
from warden.contracts import utc
from warden.runner import ExperimentRunner


def _runner(fixture: SimpleNamespace) -> ExperimentRunner:
    return ExperimentRunner(registry=fixture.registry, repo_root=fixture.repo)


def _verify(fixture: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, capsys) -> tuple[int, str]:
    monkeypatch.setattr(cli, "check_worktree", lambda *args, **kwargs: True)
    ret = cli.cmd_verify(SimpleNamespace(spec_yaml=str(fixture.spec), json=False), _runner(fixture))
    return ret, capsys.readouterr().err


def _counts(fixture: SimpleNamespace) -> dict[str, int]:
    with fixture.registry._connect() as conn:
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("trials", "selection_attempts", "spec_causality")
        }


def test_cmd_verify_rejects_policy_key_before_any_side_effect(tiny_repo, monkeypatch, capsys) -> None:
    fixture = tiny_repo()
    fixture.spec.write_text(fixture.spec.read_text() + "simulation_epoch: 1\n")
    before = _counts(fixture)

    ret, err = _verify(fixture, monkeypatch, capsys)

    assert ret == 1
    assert "simulation_epoch" in err
    assert _counts(fixture) == before
    assert not (fixture.repo / "cache").exists()


def test_cmd_verify_refuses_panel_reaching_holdout_start(tiny_repo, monkeypatch, capsys) -> None:
    # Digest-pinned panel (the manifest matches the bytes) whose last bar is past the
    # contract's holdout cutoff: the fence must reject it, not the digest check.
    fixture = tiny_repo(
        contract_overrides={
            "val_start": utc("2025-01-01T05:00:00Z"),
            "val_end": utc("2025-01-01T10:00:00Z"),
            "holdout_start": utc("2025-01-01T10:00:00Z"),
        }
    )
    before = _counts(fixture)

    ret, err = _verify(fixture, monkeypatch, capsys)

    assert ret == 1
    assert "holdout_start" in err
    assert _counts(fixture) == before
    assert not (fixture.repo / "cache").exists()


def test_cmd_verify_refuses_empty_validation_window(tiny_repo, monkeypatch, capsys) -> None:
    # Six hourly bars end at 05:00; the declared validation window 10:00..20:00 is empty.
    fixture = tiny_repo(n_bars=6)
    before = _counts(fixture)

    ret, err = _verify(fixture, monkeypatch, capsys)

    assert ret == 1
    assert "no bars" in err
    assert _counts(fixture) == before
    assert not (fixture.repo / "cache").exists()
