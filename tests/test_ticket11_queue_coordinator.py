"""Ticket 11 tests for the typed Python hourly queue coordinator and shell wrapper."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from scripts.hourly_queue_coordinator import (
    CommandResult,
    HourlyQueueCoordinator,
    QueueRoute,
    parse_attempt_receipt,
    parse_backtest_output,
    parse_json_objects,
    parse_spec_status,
)
from warden.contracts import HypothesisSpec, load_contract
from warden.lease import mutation_lease
from warden.registry import ResearchRegistry
from warden_ops.integrity import generate_manifest

REPO_ROOT = Path(__file__).resolve().parents[1]
WRAPPER_SCRIPT = REPO_ROOT / "scripts" / "run_hourly_pipeline.sh"


def _setup_real_warden_repo(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    """Build an isolated repository backed by real Warden CLI, SQLite, and outcome memory."""
    repo = tmp_path / "repo"
    for rel in (
        "experiments/queue",
        "experiments/active/archive",
        "experiments/active/failed",
        "experiments/active/review",
        ".workspace/hypotheses/queue",
        ".workspace/hypotheses/registered",
        ".workspace/hypotheses/failed",
        "contracts",
        "docs/research-outcomes",
        "registry",
        "scripts",
    ):
        (repo / rel).mkdir(parents=True, exist_ok=True)

    # Copy contract & minimal valid outcome-memory index (no network or live ledger writes)
    (repo / "contracts" / "crypto_perps_1h_v1.yaml").write_text(
        (REPO_ROOT / "contracts" / "crypto_perps_1h_v1.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (repo / "docs" / "research-outcomes" / "branch-result-index-v1.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_refs": [{"pr": 999, "commit": "abcdef1"}],
                "outcomes": [],
                "original_unattempted": [],
            }
        ),
        encoding="utf-8",
    )
    (repo / "scripts" / "herdr_dispatch.sh").write_text(
        (REPO_ROOT / "scripts" / "herdr_dispatch.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (repo / "scripts" / "herdr_dispatch.sh").chmod(0o755)
    generate_manifest(repo)

    reg = ResearchRegistry(repo / "registry" / "registry.db")
    reg.register_contract(load_contract(repo / "contracts" / "crypto_perps_1h_v1.yaml"))
    reg.register_hypothesis(
        HypothesisSpec(
            hypothesis_id="HYP-T11-001",
            contract_id="CRYPTO_PERPS_1H_V1",
            title="Ticket 11 coordinator test hypothesis",
            strategy_family="rules_ts",
            economic_mechanism="Volatility-scaled momentum under contract cost floor",
            counterparty_or_friction="10 bps round-trip taker cost",
            expected_holding_bars=8,
            falsification_criteria="Reject if net Sharpe or DSR fails floor",
            strategy_module="strategies/candidates/test_t11_mod.py",
        )
    )

    # uv shim that executes the real warden.cli and scripts.research_outcome_memory inside tmp_path
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    uv_shim = bin_dir / "uv"
    uv_shim.write_text(
        f"""#!/usr/bin/env python3
import os
import subprocess
import sys

args = sys.argv[1:]
if args[:2] == ["run", "--frozen"]:
    args = args[2:]
elif args[:1] == ["run"]:
    args = args[1:]

env = dict(os.environ)
env["PYTHONPATH"] = "{REPO_ROOT}:{REPO_ROOT / 'src'}"
if args[:1] == ["warden"]:
    cmd = ["{sys.executable}", "-m", "warden.cli", *args[1:]]
elif args[:1] == ["python"]:
    cmd = ["{sys.executable}", *args[1:]]
else:
    raise SystemExit(f"Unsupported uv shim invocation: {{sys.argv}}")
raise SystemExit(subprocess.run(cmd, env=env).returncode)
""",
        encoding="utf-8",
    )
    uv_shim.chmod(0o755)
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("HERDR_") and k != "QUANT_LAB_USE_HERDR"},
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "QUANT_LAB_REPO_DIR": str(repo),
        "QUANT_LAB_HOURLY_LOCK_FILE": str(tmp_path / "hourly.lock"),
    }
    return repo, repo / "registry" / "registry.db", env


def test_shell_wrapper_has_no_brittle_json_text_parsing() -> None:
    text = WRAPPER_SCRIPT.read_text(encoding="utf-8")
    assert "grep" not in text
    assert "cut " not in text
    assert "hourly_queue_coordinator.py" in text


def test_json_deserialization_handles_sentinels_multiline_and_partial_output() -> None:
    single = '{"run_id": "RUN-1", "verdict": "REVISE"}\nWARDEN_RUN_COMPLETE\n'
    assert parse_json_objects(single) == [{"run_id": "RUN-1", "verdict": "REVISE"}]
    assert parse_backtest_output(single) == ("RUN-1", "REVISE")

    multiline = (
        "log preamble line\n"
        + json.dumps(
            {
                "ok": True,
                "value": {"state": "STAGE1_PASS", "reason": "ok", "run_id": None},
                "source": "warden.spec-status",
            },
            indent=2,
        )
        + "\nWARDEN_RUN_COMPLETE\n"
    )
    status = parse_spec_status(multiline)
    assert status is not None
    assert status.state == "STAGE1_PASS"
    assert status.run_id is None

    # Partial / truncated / wrong-source / missing-fields output must fail closed (None)
    assert parse_spec_status('{"ok": true, "value": {"state": "STAGE1_PASS"') is None
    assert parse_spec_status('{"ok": true, "value": {"state": "NEW"}, "source": "other"}') is None
    assert (
        parse_spec_status('{"ok": true, "value": {"reason": "no state"}, "source": "warden.spec-status"}')
        is None
    )
    assert (
        parse_attempt_receipt('{"ok": true, "value": {"state": "FINALIZED"}, "source": "warden.attempt-status"}')
        is None
    )

    receipt_raw = json.dumps(
        {
            "ok": True,
            "value": {
                "run_id": "RUN-1",
                "state": "FINALIZED",
                "eligible": True,
                "verdict": "REVISE",
                "reason": "",
            },
            "source": "warden.attempt-status",
        }
    )
    receipt = parse_attempt_receipt(receipt_raw)
    assert receipt is not None
    assert receipt.state == "FINALIZED"
    assert receipt.eligible is True
    assert receipt.verdict == "REVISE"


def test_falsifier_1_crashed_mid_trial_reserved_routes_to_review_without_spending_slot(
    tmp_path: Path,
) -> None:
    repo, db_path, env = _setup_real_warden_repo(tmp_path)
    reg = ResearchRegistry(db_path)
    reg.register_trial_start("HYP-T11-001", "RUN-CRASH-001")

    spec = repo / "experiments" / "queue" / "crash_spec.yaml"
    spec.write_text(
        "hypothesis_id: HYP-T11-001\ncontract_id: CRYPTO_PERPS_1H_V1\n"
        "strategy_module: strategies/candidates/test_t11_mod.py\nparameters: {}\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(WRAPPER_SCRIPT), "--json"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    summary = json.loads(proc.stdout)
    assert summary["processed"] == 1
    assert summary["outcomes"][0]["route"] == "review"
    assert summary["outcomes"][0]["screen_called"] is False
    assert summary["outcomes"][0]["backtest_called"] is False
    assert (repo / "experiments" / "active" / "review" / "crash_spec.yaml").is_file()
    assert not (repo / "experiments" / "active" / "archive" / "crash_spec.yaml").exists()

    with sqlite3.connect(db_path) as conn:
        trials_used = conn.execute(
            "SELECT trials_used FROM hypotheses WHERE hypothesis_id='HYP-T11-001'"
        ).fetchone()[0]
        receipts = conn.execute("SELECT COUNT(*) FROM attempt_receipts").fetchone()[0]
    assert trials_used == 1
    assert receipts == 1


def test_falsifier_2_active_mutation_lease_busy_defers_without_trial_spend(tmp_path: Path) -> None:
    repo, db_path, env = _setup_real_warden_repo(tmp_path)
    reg = ResearchRegistry(db_path)
    # Mark Stage 1 passed so coordinator goes straight to mutating backtest (which acquires lease)
    skey = reg.reserve_screen("HYP-T11-001", "CRYPTO_PERPS_1H_V1", "strategies/candidates/test_t11_mod.py", {})
    reg.record_selection_attempt(
        hypothesis_id="HYP-T11-001",
        contract_id="CRYPTO_PERPS_1H_V1",
        kind="stage1",
        stage="STAGE_1_SIGNAL",
        outcome="PASS",
        strategy_family="rules_ts",
        strategy_module="strategies/candidates/test_t11_mod.py",
        config_hash=reg.screen_status("HYP-T11-001", "CRYPTO_PERPS_1H_V1", "strategies/candidates/test_t11_mod.py", {})["screen_key"],
        parameters={},
        screen_key=skey,
    )

    spec = repo / "experiments" / "queue" / "busy_spec.yaml"
    spec.write_text(
        "hypothesis_id: HYP-T11-001\ncontract_id: CRYPTO_PERPS_1H_V1\n"
        "strategy_module: strategies/candidates/test_t11_mod.py\nparameters: {}\n",
        encoding="utf-8",
    )

    with mutation_lease(db_path):
        proc = subprocess.run(
            ["bash", str(WRAPPER_SCRIPT), "--json"],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    summary = json.loads(proc.stdout)
    assert summary["outcomes"][0]["route"] == "defer"
    assert spec.is_file(), "BUSY spec must remain in queue for next cycle"
    assert not (repo / "experiments" / "active" / "review" / "busy_spec.yaml").exists()
    assert not (repo / "experiments" / "active" / "archive" / "busy_spec.yaml").exists()

    with sqlite3.connect(db_path) as conn:
        trials_used = conn.execute(
            "SELECT trials_used FROM hypotheses WHERE hypothesis_id='HYP-T11-001'"
        ).fetchone()[0]
    assert trials_used == 0


def test_falsifier_3_duplicate_spec_replay_blocked_by_outcome_memory(tmp_path: Path) -> None:
    repo, _db_path, env = _setup_real_warden_repo(tmp_path)
    # Seed branch-result-index-v1.json with a locked outcome for HYP-T11-001
    index_path = repo / "docs" / "research-outcomes" / "branch-result-index-v1.json"
    index_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_refs": [{"pr": 48, "commit": "1234567"}],
                "outcomes": [
                    {
                        "hypothesis_id": "HYP-T11-001",
                        "contract_id": "CRYPTO_PERPS_1H_V1",
                        "disposition": "STAGE1_REJECT",
                        "source_pr": 48,
                        "data_sha256": "a" * 64,
                        "evidence_id": "EV-48-001",
                        "strategy_module": "strategies/candidates/test_t11_mod.py",
                        "auto_retry": "DENY_SAME_CONFIG",
                    }
                ],
                "original_unattempted": [],
            }
        ),
        encoding="utf-8",
    )

    spec = repo / "experiments" / "queue" / "dup_spec.yaml"
    spec.write_text(
        "hypothesis_id: HYP-T11-001\ncontract_id: CRYPTO_PERPS_1H_V1\n"
        "strategy_module: strategies/candidates/test_t11_mod.py\nparameters: {}\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(WRAPPER_SCRIPT), "--json"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    summary = json.loads(proc.stdout)
    outcome = summary["outcomes"][0]
    assert outcome["route"] == "review"
    assert "research_outcome_memory" in outcome["reason"]
    assert outcome["screen_called"] is False
    assert outcome["backtest_called"] is False
    assert (repo / "experiments" / "active" / "review" / "dup_spec.yaml").is_file()


def test_falsifier_4_stage1_pass_to_stage3_final_skips_rescreen_and_archives(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "experiments" / "queue").mkdir(parents=True)
    spec = repo / "experiments" / "queue" / "stage1_pass.yaml"
    spec.write_text("hypothesis_id: HYP-1\n", encoding="utf-8")

    calls: list[list[str]] = []
    bt_done = {"done": False}

    def fake_runner(argv: list[str], *, cwd: Path, env: dict[str, str], timeout: int) -> CommandResult:
        assert env["WARDEN_NON_INTERACTIVE"] == "1"
        calls.append(argv)
        cmd = argv[3] if argv[:3] == ["uv", "run", "warden"] else argv[-1]
        if cmd == "check-worktree":
            return CommandResult(0, "ok")
        if cmd == "spec-status":
            if bt_done["done"]:
                return CommandResult(
                    0,
                    json.dumps(
                        {
                            "ok": True,
                            "value": {"state": "STAGE3_FINAL", "run_id": "RUN-42", "verdict": "REVISE"},
                            "source": "warden.spec-status",
                        }
                    ),
                )
            return CommandResult(
                0,
                json.dumps(
                    {
                        "ok": True,
                        "value": {"state": "STAGE1_PASS", "reason": "FINALIZED", "run_id": None},
                        "source": "warden.spec-status",
                    }
                ),
            )
        if cmd == "backtest":
            bt_done["done"] = True
            return CommandResult(0, '{"run_id": "RUN-42", "verdict": "REVISE"}\nWARDEN_RUN_COMPLETE\n')
        if cmd == "attempt-status":
            return CommandResult(
                0,
                json.dumps(
                    {
                        "ok": True,
                        "value": {
                            "run_id": "RUN-42",
                            "state": "FINALIZED",
                            "eligible": True,
                            "verdict": "REVISE",
                            "reason": "",
                        },
                        "source": "warden.attempt-status",
                    }
                ),
            )
        if cmd == "handoff":
            return CommandResult(0, "ok")
        raise AssertionError(f"Unexpected command invoked: {argv}")

    coord = HourlyQueueCoordinator(repo, env={"PATH": os.environ["PATH"], "WARDEN_NON_INTERACTIVE": "0"}, runner=fake_runner)
    rc, outcomes = coord.run()
    assert rc == 0
    assert len(outcomes) == 1
    assert outcomes[0].route == QueueRoute.ARCHIVE
    assert outcomes[0].screen_called is False
    assert outcomes[0].backtest_called is True
    assert outcomes[0].run_id == "RUN-42"
    assert (repo / "experiments" / "active" / "archive" / "stage1_pass.yaml").is_file()
    assert not any("screen" in c for c in calls)


def test_idempotent_restart_after_timeout_and_herdr_dispatch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "experiments" / "queue").mkdir(parents=True)
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "herdr_dispatch.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "herdr").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (bin_dir / "herdr").chmod(0o755)

    spec = repo / "experiments" / "queue" / "herdr_spec.yaml"
    spec.write_text("hypothesis_id: HYP-HERDR\n", encoding="utf-8")

    # Simulate timeout / SIGKILL on backtest where Warden already committed CANDIDATE_FOR_QUALIFICATION
    seen_herdr_dispatch = {"called": False}

    def runner_with_herdr_timeout(argv: list[str], *, cwd: Path, env: dict[str, str], timeout: int) -> CommandResult:
        if argv[0] == "bash" and argv[1].endswith("herdr_dispatch.sh"):
            seen_herdr_dispatch["called"] = True
            return CommandResult(124, "", "timed out", timed_out=True)
        cmd = argv[3]
        if cmd == "check-worktree":
            return CommandResult(0, "ok")
        if cmd == "spec-status":
            if seen_herdr_dispatch["called"]:
                return CommandResult(
                    0,
                    json.dumps(
                        {
                            "ok": True,
                            "value": {
                                "state": "STAGE3_FINAL",
                                "run_id": "RUN-CAND-1",
                                "verdict": "CANDIDATE_FOR_QUALIFICATION",
                            },
                            "source": "warden.spec-status",
                        }
                    ),
                )
            return CommandResult(
                0,
                json.dumps(
                    {
                        "ok": True,
                        "value": {"state": "STAGE1_PASS", "reason": "FINALIZED", "run_id": None},
                        "source": "warden.spec-status",
                    }
                ),
            )
        if cmd == "attempt-status":
            return CommandResult(
                0,
                json.dumps(
                    {
                        "ok": True,
                        "value": {
                            "run_id": "RUN-CAND-1",
                            "state": "FINALIZED",
                            "eligible": True,
                            "verdict": "CANDIDATE_FOR_QUALIFICATION",
                            "reason": "",
                        },
                        "source": "warden.attempt-status",
                    }
                ),
            )
        if cmd == "audit-check":
            return CommandResult(0, json.dumps({"audit_passed": True, "verdict": "APPROVED"}, indent=2))
        if cmd == "handoff":
            return CommandResult(0, "ok")
        raise AssertionError(f"Unexpected command: {argv}")

    coord = HourlyQueueCoordinator(
        repo,
        env={"PATH": f"{bin_dir}:{os.environ['PATH']}", "HERDR_ENV": "1", "HERDR_PANE": "w1:p9"},
        runner=runner_with_herdr_timeout,
        backtest_timeout_sec=120,
    )
    rc, outcomes = coord.run()
    assert rc == 0
    assert seen_herdr_dispatch["called"] is True
    assert outcomes[0].route == QueueRoute.ARCHIVE
    assert outcomes[0].audit_called is True
    assert (repo / "experiments" / "active" / "archive" / "herdr_spec.yaml").is_file()


def test_end_to_end_partial_malformed_json_and_missing_verdict_fail_closed_to_review(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    (repo / "experiments" / "queue").mkdir(parents=True)
    spec_a = repo / "experiments" / "queue" / "a_partial.yaml"
    spec_b = repo / "experiments" / "queue" / "b_missing_verdict.yaml"
    spec_a.write_text("hypothesis_id: HYP-A\n", encoding="utf-8")
    spec_b.write_text("hypothesis_id: HYP-B\n", encoding="utf-8")

    def malformed_runner(argv: list[str], *, cwd: Path, env: dict[str, str], timeout: int) -> CommandResult:
        cmd = argv[3] if argv[:3] == ["uv", "run", "warden"] else ""
        if cmd in {"check-worktree", "handoff"}:
            return CommandResult(0, "ok")
        if cmd == "spec-status":
            spec_arg = argv[4]
            if spec_arg.endswith("a_partial.yaml"):
                return CommandResult(0, '{"ok": true, "value": {"state": "STAGE3_FINAL"')
            return CommandResult(
                0,
                json.dumps(
                    {
                        "ok": True,
                        "value": {"state": "STAGE3_FINAL", "run_id": "RUN-NO-VERDICT"},
                        "source": "warden.spec-status",
                    }
                ),
            )
        raise AssertionError(f"Unexpected command on malformed/incomplete spec-status: {argv}")

    coord = HourlyQueueCoordinator(repo, env={"PATH": os.environ["PATH"]}, runner=malformed_runner)
    rc, outcomes = coord.run()
    assert rc == 0
    assert [o.route for o in outcomes] == [QueueRoute.REVIEW, QueueRoute.REVIEW]
    assert (repo / "experiments" / "active" / "review" / "a_partial.yaml").is_file()
    assert (repo / "experiments" / "active" / "review" / "b_missing_verdict.yaml").is_file()
