"""Metadata preflight cannot execute or expose a candidate outcome."""

from __future__ import annotations

import json
import runpy
import sqlite3
from pathlib import Path

from warden.registry import ResearchRegistry

REPO_ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = runpy.run_path(str(REPO_ROOT / "scripts" / "preflight_trial.py"))
prior_stream_lengths = PREFLIGHT["prior_stream_lengths"]


def _seed(db: Path, attempts: int = 2) -> sqlite3.Connection:
    """Seed a WAL registry and return the still-open writer, which keeps the WAL alive."""
    ResearchRegistry(db)  # schema, and switches the file to WAL
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO trials (run_id,hypothesis_id,contract_id,returns_json,trial_number_in_hyp) "
        "VALUES ('RUN-A','H','MULTI_ASSET_1D_V1',?,1)", (json.dumps([0.01] * 806),))
    for i in range(attempts):
        con.execute(
            "INSERT INTO selection_attempts (attempt_id,attempt_base,hypothesis_id,contract_id,kind,"
            "stage,outcome,returns_observed,returns_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f"ATT-{i}", f"base-{i}", "H", "MULTI_ASSET_1D_V1", "param_grid", "STAGE_3_STRESS",
             "GRID", 1, json.dumps([0.01] * 806), float(i)))
    con.commit()
    return con


def test_readonly_projection_sees_committed_wal_without_touching_source(tmp_path):
    live = tmp_path / "live.db"
    writer = _seed(live)
    wal = Path(str(live) + "-wal")
    assert wal.exists() and wal.stat().st_size > 0
    before = (live.read_bytes(), wal.read_bytes())
    assert prior_stream_lengths(live, "MULTI_ASSET_1D_V1") == [806]
    assert (live.read_bytes(), wal.read_bytes()) == before
    writer.close()


def test_preflight_never_returns_unaccounted_strategy_outcomes(tiny_repo, monkeypatch):
    import yaml

    fixture = tiny_repo()
    monkeypatch.chdir(fixture.repo)
    preflight = PREFLIGHT['preflight']
    first = preflight(fixture.spec, fixture.registry.db_path)
    config = yaml.safe_load(fixture.spec.read_text())
    config['parameters']['witness_variant'] = 2
    fixture.spec.write_text(yaml.safe_dump(config))
    second = preflight(fixture.spec, fixture.registry.db_path)
    with sqlite3.connect(fixture.registry.db_path) as conn:
        counts = {table: conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                  for table in ('trials', 'selection_attempts', 'attempt_receipts', 'screen_receipts')}
    assert counts == dict.fromkeys(counts, 0)
    for summary in (first, second):
        assert set(summary) == {
            "kind", "contract_id", "hypothesis_id", "strategy_source_sha256",
            "validation_bars", "selection_alignment", "strategy_execution",
        }, summary
        assert summary["kind"] == "METADATA_ONLY"
        assert summary["strategy_execution"] == "NOT_RUN"
        assert summary['validation_bars'] == 14


def test_metadata_preflight_does_not_import_strategy(tiny_repo, monkeypatch):
    fixture = tiny_repo(module_source='raise RuntimeError("strategy code executed")\n')
    monkeypatch.chdir(fixture.repo)
    summary = PREFLIGHT["preflight"](fixture.spec, fixture.registry.db_path)
    assert summary["strategy_execution"] == "NOT_RUN"
    assert summary["selection_alignment"]["aligned_bars"] == 14


def test_preflight_cli_refuses_invalid_metadata_without_writes(tiny_repo, monkeypatch, capsys):
    import yaml

    fixture = tiny_repo(contract_overrides={"execution_mode": "QUOTE_SIDE"})
    monkeypatch.chdir(fixture.repo)
    before = fixture.registry.db_path.read_bytes()
    args = [str(fixture.spec), "--db", str(fixture.registry.db_path)]
    assert PREFLIGHT["main"](args) == 1
    captured = capsys.readouterr()
    assert not captured.out and "PREFLIGHT_FAILED" in captured.err
    assert "QUOTE_SIDE" in captured.err and "bid" in captured.err
    assert fixture.registry.db_path.read_bytes() == before

    config = yaml.safe_load(fixture.spec.read_text())
    config["simulation_epoch"] = "injected"
    fixture.spec.write_text(yaml.safe_dump(config))
    assert PREFLIGHT["main"](args) == 1
    captured = capsys.readouterr()
    assert not captured.out and "simulation_epoch" in captured.err
    assert fixture.registry.db_path.read_bytes() == before



def test_preflight_refuses_source_outside_strategies(tiny_repo, monkeypatch, capsys):
    import yaml

    fixture = tiny_repo()
    monkeypatch.chdir(fixture.repo)
    (fixture.repo / "forbidden.py").write_text("raise RuntimeError('not a strategy')")
    (fixture.repo / "strategies" / "escape.py").symlink_to(fixture.repo / "forbidden.py")
    spec = yaml.safe_load(fixture.spec.read_text())
    spec["strategy_module"] = "strategies/escape.py"
    fixture.spec.write_text(yaml.safe_dump(spec))
    assert PREFLIGHT["main"]([str(fixture.spec), "--db", str(fixture.registry.db_path)]) == 1
    assert "under strategies/" in capsys.readouterr().err


def test_preflight_refuses_changed_registered_contract(tiny_repo, monkeypatch, capsys):
    import yaml

    fixture = tiny_repo()
    monkeypatch.chdir(fixture.repo)
    contract_path = next((fixture.repo / "contracts").glob("*.yaml"))
    contract = yaml.safe_load(contract_path.read_text())
    contract["taker_fee_bps"] += 1.0
    contract_path.write_text(yaml.safe_dump(contract))
    assert PREFLIGHT["main"]([str(fixture.spec), "--db", str(fixture.registry.db_path)]) == 1
    assert "registered immutable identity" in capsys.readouterr().err
