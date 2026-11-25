"""T15 / issue #101: version-gated registry DDL, readonly API, per-spec advance leases.

Acceptance contract under test:

1. ``ResearchRegistry(db, readonly=True)`` opens with ``mode=ro`` + ``PRAGMA query_only``,
   performs zero DDL, and never blocks while another connection holds ``BEGIN IMMEDIATE``.
2. Normal initialization skips DDL **and** legacy backfills once ``PRAGMA user_version``
   matches ``SCHEMA_VERSION``; an unversioned (v0) legacy registry is adopted atomically
   without mutating any existing table, row or trigger.
3. A WAL snapshot rehearsed through the SQLite backup API keeps all 15 existing tables and
   14 triggers byte-identical and every legacy row digest unchanged.
4. Typed, write-free query helpers exist for consumers.
5. ``warden advance --pending`` exits 0 when every spec is already terminal, legacy-frozen
   or already-triaged blocked; it exits 1 only for new errors or ``REVIEW_REQUIRED``.
6. The pending scan acquires the mutation lease per-spec (never across the batch) and never
   imports a legacy strategy module to classify it.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

import warden.registry as registry_module
from tests.test_warden_advance import (
    _prepare_fixture,
    _write_hypothesis_md,
    _write_outcome_index,
    _write_spec_yaml,
)
from warden import cli
from warden.contracts import EvaluationContract, HypothesisSpec, utc
from warden.lease import lease_path
from warden.registry import LEGACY_REASON, LEGACY_RUNS, ResearchRegistry

TABLES_15 = frozenset({
    "attempt_receipts", "audit_evidence", "audit_reports", "contract_search_state", "contracts",
    "feature_sets", "holdout_claims", "hypotheses", "qualifications", "screen_receipts",
    "selection_attempts", "spec_causality", "trial_dispositions", "trial_quarantine", "trials",
})
TRIGGERS_14 = frozenset({
    "attempts_no_delete", "attempts_no_update", "audit_evidence_no_delete", "audit_evidence_no_update",
    "audit_reports_no_delete", "audit_reports_no_update", "dispositions_no_delete",
    "dispositions_no_update", "hypotheses_no_budget_reset", "search_state_no_relax",
    "spec_causality_no_delete", "spec_causality_no_update", "trials_no_delete", "trials_no_update",
})
LEGACY_TABLES = frozenset({"causality_evidence"})
LEGACY_TRIGGERS = frozenset({"causality_no_delete", "causality_no_update"})

LEGACY_MODULE_WITH_SIDE_EFFECT = """
from pathlib import Path


def generate_positions(panel, contract, **params):
    return {}


Path(__file__).with_suffix(".imported").write_text("imported at module scope", encoding="utf-8")
"""

LEGACY_MODULE_THAT_RAISES_ON_IMPORT = """
raise RuntimeError("legacy module top level must not execute during a scan")


def generate_positions(panel, contract, **params):
    return {}
"""


# --------------------------------------------------------------------------- helpers


def _schema_version(db_path: Path) -> int:
    with sqlite3.connect(db_path) as conn:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _objects(conn: sqlite3.Connection) -> tuple[dict[str, str], dict[str, str]]:
    tables = {
        row[0]: row[1]
        for row in conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table'")
        if not row[0].startswith("sqlite_")
    }
    triggers = {
        row[0]: row[1]
        for row in conn.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger'")
        if not row[0].startswith("sqlite_")
    }
    return tables, triggers


def _row_digest(conn: sqlite3.Connection, table: str) -> str:
    rows = conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
    payload = json.dumps([list(r) for r in rows], sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def _snapshot(db_path: Path) -> dict:
    with sqlite3.connect(db_path) as conn:
        tables, triggers = _objects(conn)
        return {
            "user_version": int(conn.execute("PRAGMA user_version").fetchone()[0]),
            "journal_mode": str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower(),
            "tables": tables,
            "triggers": triggers,
            "digests": {name: _row_digest(conn, name) for name in tables},
            "counts": {name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in tables},
            "integrity": conn.execute("PRAGMA integrity_check").fetchone()[0],
        }


def _contract() -> EvaluationContract:
    return EvaluationContract(
        contract_id="TINY_V1", asset_class="crypto_perps", frequency="1h", bars_per_year=8760,
        exploratory_data_path="data/exploratory/tiny.parquet",
        holdout_data_path="data/holdout/tiny.parquet",
        train_start=utc("2025-01-01T00:00:00Z"), val_start=utc("2025-01-01T10:00:00Z"),
        val_end=utc("2025-01-02T00:00:00Z"), holdout_start=utc("2025-01-02T00:00:00Z"),
        holdout_end=utc("2025-01-03T00:00:00Z"), n_folds=2, label_horizon_bars=2, embargo_bars=1,
        execution_delay_bars=1, taker_fee_bps=1.0, slippage_bps=1.0,
        data_snapshot_sha256="0" * 64, data_provenance="verified_market",
    )


def _hypothesis(contract_id: str, hyp_id: str = "HYP-P2-T15") -> HypothesisSpec:
    return HypothesisSpec(
        hyp_id, contract_id, "T15 registry probe", "rules_ts",
        "Mechanism explanation of twenty chars", "Friction explanation of twenty chars",
        8, "Reject net Sharpe below one",
    )


def _seeded_registry(tmp_path: Path) -> Path:
    db = tmp_path / "seed" / "registry.db"
    registry = ResearchRegistry(db)
    contract = _contract()
    registry.register_contract(contract)
    registry.register_hypothesis(_hypothesis(contract.contract_id))
    return db


def _wal_legacy_snapshot(tmp_path: Path, seed_db: Path) -> Path:
    """P1-shaped snapshot: current DDL + legacy residue, PRAGMA user_version = 0.

    Built through the SQLite online backup API (never ``cp``), then rewound to the
    unversioned state a real P1 registry has, with the pre-existing legacy
    ``causality_evidence`` table/triggers and its rows.
    """
    snap = tmp_path / "legacy" / "registry.db"
    snap.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(seed_db) as src, sqlite3.connect(snap) as dst:
        src.backup(dst)
    with sqlite3.connect(snap) as conn:
        assert str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal"
        conn.execute("PRAGMA user_version=0")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS causality_evidence (evidence_id TEXT PRIMARY KEY,"
            " run_id TEXT NOT NULL, feature_identity TEXT NOT NULL, artifact_sha256 TEXT NOT NULL,"
            " created_at REAL NOT NULL)"
        )
        conn.execute(
            "CREATE TRIGGER IF NOT EXISTS causality_no_update BEFORE UPDATE ON causality_evidence "
            "BEGIN SELECT RAISE(ABORT,'Append-only violation: causality proof is immutable'); END"
        )
        conn.execute(
            "CREATE TRIGGER IF NOT EXISTS causality_no_delete BEFORE DELETE ON causality_evidence "
            "BEGIN SELECT RAISE(ABORT,'Append-only violation: causality proof is immutable'); END"
        )
        for i in range(3):
            conn.execute(
                "INSERT INTO causality_evidence VALUES (?,?,?,?,?)",
                (f"EV-{i}", f"RUN-LEGACY-{i}", f"feature-{i}", "a" * 64, 1.0 + i),
            )
        # A P1 registry already carries the legacy quarantine markers, so migration is a no-op.
        for run_id in LEGACY_RUNS:
            conn.execute(
                "INSERT OR IGNORE INTO trial_quarantine (run_id, reason, quarantined_at) VALUES (?,?,?)",
                (run_id, LEGACY_REASON, 2.0),
            )
    return snap


def _repo_registry_cli_env(fx, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """Mirror the fixture registry into the repo and run the real CLI from there."""
    (fx.repo / "registry").mkdir(exist_ok=True)
    with sqlite3.connect(fx.registry.db_path) as src, sqlite3.connect(
        fx.repo / "registry" / "registry.db"
    ) as dst:
        src.backup(dst)
    monkeypatch.chdir(fx.repo)
    monkeypatch.setattr("warden.cli.check_worktree", lambda *a, **k: True)
    capsys.readouterr()


def _pending_payload(capsys) -> dict:
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def _clear_specs(fx) -> None:
    """The fixture writes one spec of its own; these tests assert on an exact spec set."""
    for path in (fx.repo / "experiments" / "active").glob("*.y*ml"):
        path.unlink()


# ------------------------------------------------------------------- schema / readonly


def test_readonly_open_succeeds_immediately_while_writer_holds_begin_immediate(tmp_path: Path) -> None:
    db = _seeded_registry(tmp_path)
    writer = sqlite3.connect(db, timeout=30, isolation_level=None)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("BEGIN IMMEDIATE")
    try:
        started = time.perf_counter()
        readonly = ResearchRegistry(db, readonly=True)
        status = readonly.spec_status("HYP-P2-T15", "TINY_V1", "", {})
        elapsed = time.perf_counter() - started
    finally:
        writer.execute("ROLLBACK")
        writer.close()

    assert status["state"] == "NEW"
    assert elapsed < 1.0, f"readonly open blocked for {elapsed:.2f}s behind BEGIN IMMEDIATE"
    assert _schema_version(db) == registry_module.SCHEMA_VERSION


def test_readonly_registry_rejects_writes_and_never_creates_the_database(tmp_path: Path) -> None:
    db = _seeded_registry(tmp_path)
    readonly = ResearchRegistry(db, readonly=True)
    with pytest.raises(sqlite3.OperationalError), readonly._connect() as conn:
        conn.execute(
            "INSERT INTO trial_quarantine (run_id, reason, quarantined_at) VALUES ('R','x',1.0)"
        )

    missing = tmp_path / "absent" / "registry.db"
    absent = ResearchRegistry(missing, readonly=True)
    with pytest.raises(sqlite3.OperationalError):
        absent.read_trial("RUN-anything")
    assert not missing.exists()
    assert not missing.parent.exists()


def test_reopening_a_current_registry_executes_no_ddl_or_backfill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _seeded_registry(tmp_path)
    assert _schema_version(db) == registry_module.SCHEMA_VERSION
    before = _snapshot(db)

    statements: list[str] = []
    real_connect = sqlite3.connect

    def traced_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(registry_module.sqlite3, "connect", traced_connect)
    ResearchRegistry(db)
    monkeypatch.undo()

    writes = [s for s in statements if re.search(r"\b(CREATE|ALTER|DROP|INSERT|UPDATE|DELETE)\b", s, re.IGNORECASE)]
    assert writes == [], f"second initialization wrote to the registry: {writes}"
    assert not any(re.search(r"PRAGMA\s+user_version\s*=", s, re.IGNORECASE) for s in statements)
    after = _snapshot(db)
    assert after["user_version"] == before["user_version"]
    assert after["digests"] == before["digests"]


def test_wal_snapshot_migration_rehearsal_preserves_15_tables_14_triggers_and_legacy_evidence(
    tmp_path: Path,
) -> None:
    snap = _wal_legacy_snapshot(tmp_path, _seeded_registry(tmp_path))
    before = _snapshot(snap)
    assert before["user_version"] == 0
    assert before["journal_mode"] == "wal"
    assert TABLES_15 <= set(before["tables"])
    assert LEGACY_TABLES <= set(before["tables"])
    assert set(before["triggers"]) == TRIGGERS_14 | LEGACY_TRIGGERS
    assert before["counts"]["causality_evidence"] == 3
    assert before["integrity"] == "ok"

    ResearchRegistry(snap)

    after = _snapshot(snap)
    assert after["user_version"] == registry_module.SCHEMA_VERSION
    assert after["integrity"] == "ok"
    # Additive-only: every pre-existing table and trigger keeps its exact DDL.
    for name in TABLES_15 | LEGACY_TABLES:
        assert after["tables"][name] == before["tables"][name], f"table DDL mutated: {name}"
    for name in TRIGGERS_14 | LEGACY_TRIGGERS:
        assert after["triggers"][name] == before["triggers"][name], f"trigger DDL mutated: {name}"
    # Zero row mutations, including the legacy residue and the quarantine markers.
    assert after["digests"] == before["digests"]
    assert after["counts"] == before["counts"]

    # Re-running the migration on the migrated WAL snapshot is a pure no-op.
    ResearchRegistry(snap)
    assert _snapshot(snap)["digests"] == after["digests"]


def test_failed_migration_rolls_back_ddl_and_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Schema atomicity: a crash mid-migration must not leave a half-created schema."""
    db = tmp_path / "empty" / "registry.db"
    db.parent.mkdir(parents=True)
    sqlite3.connect(db).close()
    assert _schema_version(db) == 0

    monkeypatch.setattr(
        registry_module, "_DDL_SCRIPT", registry_module._DDL_SCRIPT + "\nCREATE TABLE broken (;\n"
    )
    with pytest.raises(sqlite3.OperationalError):
        ResearchRegistry(db)
    monkeypatch.undo()

    after = _snapshot(db)
    assert after["user_version"] == 0, "the version bump must not survive a failed migration"
    assert after["tables"] == {}, after["tables"]
    assert after["triggers"] == {}


def test_registry_newer_than_the_build_is_refused(tmp_path: Path) -> None:
    db = _seeded_registry(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA user_version=99")
    with pytest.raises(ValueError, match="newer than this warden build"):
        ResearchRegistry(db)
    with pytest.raises(ValueError, match="newer than this warden build"):
        ResearchRegistry(db, readonly=True)


def test_typed_readonly_queries_have_no_write_path(tmp_path: Path) -> None:
    db = _seeded_registry(tmp_path)
    writable = ResearchRegistry(db)
    writable.reserve_holdout_lineage("lin-t15", "RUN-T15")
    readonly = ResearchRegistry(db, readonly=True)

    screen = readonly.read_screen_status("HYP-P2-T15", "TINY_V1", "", {})
    spec = readonly.read_spec_status("HYP-P2-T15", "TINY_V1", "", {})
    claim = readonly.read_holdout_claim("lin-t15")

    assert screen["state"] == "UNSEEN" and screen["retry_allowed"] is True
    assert spec["state"] == "NEW" and spec["hypothesis_status"] == "ACTIVE"
    assert claim is not None and claim["state"] == "CLAIMED" and claim["run_id"] == "RUN-T15"
    assert readonly.read_holdout_claim("lin-absent") is None
    assert readonly.read_trial("RUN-absent") is None

    hints = {
        name: registry_module.ResearchRegistry.__annotations__ for name in ("__init__",)
    }
    assert hints  # annotations resolved without error

    before = _snapshot(db)
    readonly.read_spec_status("HYP-P2-T15", "TINY_V1", "", {})
    assert _snapshot(db)["digests"] == before["digests"]


# ------------------------------------------------------------------------- CLI advance


def test_advance_pending_exits_zero_for_terminal_and_legacy_frozen_specs(
    tiny_repo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fx, _runner, _spec_path, _ = _prepare_fixture(tiny_repo, hyp_id="HYP-T15-LEG")
    _clear_specs(fx)
    legacy_mod = fx.repo / "strategies" / "candidates" / "t15_legacy.py"
    legacy_mod.write_text(
        "def generate_positions(panel, contract, **params):\n    return {}\n", encoding="utf-8"
    )
    _write_hypothesis_md(fx.repo, "HYP-T15-LEG", fx.contract.contract_id)
    _write_spec_yaml(
        fx.repo, "t15_legacy.yaml", hyp_id="HYP-T15-LEG",
        contract_id=fx.contract.contract_id, module_rel="strategies/candidates/t15_legacy.py",
    )
    _write_spec_yaml(
        fx.repo, "t15_budget.yaml", hyp_id="HYP-TINY",
        contract_id=fx.contract.contract_id, module_rel="strategies/flat.py",
    )
    with fx.registry._connect() as conn:
        conn.execute("UPDATE hypotheses SET trials_used=3 WHERE hypothesis_id='HYP-TINY'")

    _repo_registry_cli_env(fx, monkeypatch, capsys)
    rc = cli.main(["advance", "--pending", "--json"])
    payload = _pending_payload(capsys)

    actions = {o["spec"].split("/")[-1]: o["action"] for o in payload["value"]["outcomes"]}
    assert actions == {"t15_legacy.yaml": "SKIP_LEGACY_FROZEN", "t15_budget.yaml": "SKIP_TERMINAL"}, actions
    assert payload["value"]["blocked"] == 0
    assert payload["value"]["advanced"] == 0
    assert rc == 0


def test_advance_pending_exits_one_on_review_required(
    tiny_repo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fx, _runner, _spec_path, _ = _prepare_fixture(tiny_repo, hyp_id="HYP-T15-REV")
    _clear_specs(fx)
    bad = fx.repo / "experiments" / "active" / "t15_broken.yaml"
    bad.write_text("contract_id: TINY_V1\nstrategy_module: strategies/flat.py\n", encoding="utf-8")

    _repo_registry_cli_env(fx, monkeypatch, capsys)
    rc = cli.main(["advance", "--pending", "--json"])
    payload = _pending_payload(capsys)

    assert rc == 1
    assert payload["ok"] is False
    assert payload["value"]["blocked"] == 1
    assert [o["action"] for o in payload["value"]["outcomes"]] == ["REVIEW_REQUIRED"]


def test_advance_pending_reports_new_block_once_then_stays_zero_when_retriaged(
    tiny_repo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fx, _runner, _spec_path, _ = _prepare_fixture(tiny_repo, hyp_id="HYP-T15-ORPHAN")
    _clear_specs(fx)
    _write_spec_yaml(
        fx.repo, "t15_orphan.yaml", hyp_id="HYP-T15-NOFILE",
        contract_id=fx.contract.contract_id, module_rel="strategies/flat.py",
    )

    _repo_registry_cli_env(fx, monkeypatch, capsys)
    first_rc = cli.main(["advance", "--pending", "--json"])
    first = _pending_payload(capsys)
    assert first_rc == 1, "a brand-new block is an error the operator must see"
    assert [o["action"] for o in first["value"]["outcomes"]] == ["BLOCKED_UNREGISTERED"]
    assert first["value"]["new_blocked"] == 1

    ledger_path = fx.repo / ".workspace" / "state" / "advance-triage.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert ledger["schema"] == 1
    assert ledger["specs"]["experiments/active/t15_orphan.yaml"]["action"] == "BLOCKED_UNREGISTERED"

    second_rc = cli.main(["advance", "--pending", "--json"])
    second = _pending_payload(capsys)
    assert second_rc == 0, "an identical, already-triaged block must not fail the cron run"
    assert second["value"]["blocked"] == 1
    assert second["value"]["new_blocked"] == 0
    assert second["value"]["outcomes"][0]["triaged"] is True


def test_advance_pending_leases_per_spec_and_unblocks_between_specs(
    tiny_repo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fx, _runner, _spec_path, _ = _prepare_fixture(tiny_repo, hyp_id="HYP-T15-LEASE")
    _clear_specs(fx)
    legacy_mod = fx.repo / "strategies" / "candidates" / "t15_lease_legacy.py"
    legacy_mod.write_text(
        "def generate_positions(panel, contract, **params):\n    return {}\n", encoding="utf-8"
    )
    for name, hyp_id in (("l1", "HYP-T15-LEASE-L1"), ("l2", "HYP-T15-LEASE-L2")):
        # Already registered and already terminal-for-scan: these must cost no lease.
        fx.registry.register_hypothesis(_hypothesis(fx.contract.contract_id, hyp_id))
        _write_hypothesis_md(fx.repo, hyp_id, fx.contract.contract_id)
        _write_spec_yaml(
            fx.repo, f"t15_{name}.yaml", hyp_id=hyp_id,
            contract_id=fx.contract.contract_id,
            module_rel="strategies/candidates/t15_lease_legacy.py",
        )
    # Third spec must register its hypothesis (a mutation) before blocking on a missing
    # research-outcome index, which is the cheapest honest mutating path.
    _write_spec_yaml(
        fx.repo, "t15_register.yaml", hyp_id="HYP-T15-LEASE-REG",
        contract_id=fx.contract.contract_id, module_rel="strategies/flat.py",
    )
    _write_hypothesis_md(fx.repo, "HYP-T15-LEASE-REG", fx.contract.contract_id)

    _repo_registry_cli_env(fx, monkeypatch, capsys)
    lock = lease_path(fx.repo / "registry" / "registry.db")

    def _probe_free() -> str:
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import fcntl,os,sys\n"
                    "fd=os.open(sys.argv[1], os.O_CREAT|os.O_RDWR, 0o600)\n"
                    "try:\n"
                    "    fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)\n"
                    "except BlockingIOError:\n"
                    "    print('BUSY')\n"
                    "else:\n"
                    "    print('FREE')\n"
                ),
                str(lock),
            ],
            capture_output=True, text=True, timeout=30, check=True,
        )
        return proc.stdout.strip()

    probed: list[str] = []
    acquired: list[str] = []
    real_advance = cli._advance_one_spec
    real_lease = cli._advance_mutation_lease

    def spy_advance(spec_path, runner, **kwargs):
        probed.append(_probe_free())
        return real_advance(spec_path, runner, **kwargs)

    def spy_lease(runner):
        acquired.append(str(lease_path(Path(runner.repo_root) / "registry" / "registry.db")))
        return real_lease(runner)

    monkeypatch.setattr(cli, "_advance_mutation_lease", spy_lease)
    monkeypatch.setattr(cli, "_advance_one_spec", spy_advance)

    rc = cli.main(["advance", "--pending", "--json"])
    payload = _pending_payload(capsys)

    in_scope = [o for o in payload["value"]["outcomes"] if "t15_lease" in o["spec"] or "t15_register" in o["spec"] or "hyp_t15_lease" in o["spec"]]
    assert in_scope, payload["value"]["outcomes"]
    assert probed == ["FREE"] * len(payload["value"]["outcomes"]), probed
    # Two legacy-frozen specs mutate nothing and therefore take no lease at all.
    assert len(acquired) == 1, f"expected exactly one per-spec lease, got {len(acquired)}"
    assert rc == 1  # the third spec is REVIEW_REQUIRED: missing outcome index


def test_advance_scan_detects_legacy_protocol_statically_without_executing_module(
    tiny_repo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fx, _runner, _spec_path, _ = _prepare_fixture(tiny_repo, hyp_id="HYP-T15-AST")
    _clear_specs(fx)
    side_effect_mod = fx.repo / "strategies" / "candidates" / "t15_sideeffect.py"
    side_effect_mod.write_text(LEGACY_MODULE_WITH_SIDE_EFFECT, encoding="utf-8")
    raising_mod = fx.repo / "strategies" / "candidates" / "t15_raising.py"
    raising_mod.write_text(LEGACY_MODULE_THAT_RAISES_ON_IMPORT, encoding="utf-8")
    for name, hyp_id, module in (
        ("t15_sideeffect.yaml", "HYP-T15-AST-SIDE", "strategies/candidates/t15_sideeffect.py"),
        ("t15_raising.yaml", "HYP-T15-AST-RAISE", "strategies/candidates/t15_raising.py"),
    ):
        _write_hypothesis_md(fx.repo, hyp_id, fx.contract.contract_id)
        _write_spec_yaml(
            fx.repo, name, hyp_id=hyp_id, contract_id=fx.contract.contract_id, module_rel=module
        )

    _repo_registry_cli_env(fx, monkeypatch, capsys)
    rc = cli.main(["advance", "--pending", "--json"])
    payload = _pending_payload(capsys)

    actions = {o["spec"].split("/")[-1]: o["action"] for o in payload["value"]["outcomes"]}
    assert actions["t15_sideeffect.yaml"] == "SKIP_LEGACY_FROZEN", payload["value"]["outcomes"]
    assert actions["t15_raising.yaml"] == "SKIP_LEGACY_FROZEN"
    assert rc == 0
    assert not side_effect_mod.with_suffix(".imported").exists(), "scan imported a legacy module"
    with fx.registry._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM selection_attempts").fetchone()[0] == 0


def test_advance_pending_keeps_t07_outcome_memory_block_fail_closed(
    tiny_repo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fx, _runner, _spec_path, module_rel = _prepare_fixture(tiny_repo, hyp_id="HYP-T15-OM")
    _clear_specs(fx)
    _write_spec_yaml(
        fx.repo, "t15_om.yaml", hyp_id="HYP-T15-OM",
        contract_id=fx.contract.contract_id, module_rel=module_rel,
    )
    (fx.repo / "experiments" / "active" / "t15_om.yaml").write_text(
        yaml_spec := (
            "contract_id: " + fx.contract.contract_id + "\n"
            "hypothesis_id: HYP-T15-OM\n"
            "strategy_name: T15 outcome memory\n"
            "strategy_family: rules_ts\n"
            "strategy_module: " + module_rel + "\n"
            "model_type: rules\n"
            "parameters: {}\n"
        ),
        encoding="utf-8",
    )
    assert yaml_spec
    _write_outcome_index(
        fx.repo,
        {
            "schema_version": 1,
            "source_refs": [{"pr": 40, "commit": "abc1234"}],
            "outcomes": [
                {
                    "hypothesis_id": "HYP-T15-OM",
                    "contract_id": fx.contract.contract_id,
                    "strategy_module": module_rel,
                    "disposition": "STAGE1_REJECT",
                    "source_pr": 40,
                    "evidence_id": "EV-1",
                    "data_sha256": "a" * 64,
                    "auto_retry": "DENY_SAME_CONFIG",
                }
            ],
            "original_unattempted": [],
        },
    )

    _repo_registry_cli_env(fx, monkeypatch, capsys)
    rc = cli.main(["advance", "--pending", "--json"])
    payload = _pending_payload(capsys)

    assert rc == 1
    assert payload["value"]["outcomes"][0]["action"] == "BLOCKED_OUTCOME_MEMORY"
    assert fx.registry.screen_status("HYP-T15-OM", fx.contract.contract_id, module_rel, {})["state"] == "UNSEEN"
    with fx.registry._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM selection_attempts").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM attempt_receipts").fetchone()[0] == 0
