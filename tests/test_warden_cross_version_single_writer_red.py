"""RED-phase: legacy governance writer versus merged Warden CLI, using real processes.

Run on the research VPS:
  PYTHONPATH=src /home/quant/dev/quant-lab/.venv/bin/python -m pytest -q -s \
    tests/test_warden_cross_version_single_writer_red.py::test_old_and_new_warden_cannot_both_admit_same_research_attempt

Only the disposable SQLite ONLINE BACKUP is opened read-write. Source DB is mode=ro.
No holdout bytes or external research credentials are used. Do not xfail this test.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest
import yaml

from warden.contracts import EvaluationContract, HypothesisSpec
from warden.lease import lease_path
from warden.registry import ResearchRegistry

CONTRACT_ID = "RED_WRITER_1H_V1"
HYPOTHESIS_ID = "HYP-RED-WRITER-20261107"
RUN_ID = "RUN-RED-WRITER-20261107-001"
SPEC_REL = "experiments/active/warden_red_spec.yaml"
STRATEGY_REL = "strategies/candidates/warden_red_barrier.py"
EXPECTED_MERGED_COMMIT = "b654352ff2e3d2ed7163a212916a49766457590c"

# This is an executable fixture, NOT an altered production lease/admission module.
# It signals only AFTER the real runner has committed its reservation and entered
# the strategy computation subprocess. File creation is atomic; the barrier is
# released independently per role by the parent test process.
STRATEGY = """
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

def generate_positions(panel_df, contract, **kwargs):
    events = Path(os.environ["WARDEN_RED_EVENTS"])
    role = os.environ["WARDEN_RED_ROLE"]
    db = Path("registry/registry.db").resolve()
    identity = db.stat()
    event = {
        "role": role,
        "strategy_pid": os.getpid(),
        "parent_pid": os.getppid(),
        "admitted_at_utc": datetime.now(timezone.utc).isoformat(),
        "registry_path": str(db),
        "db_device": identity.st_dev,
        "db_inode": identity.st_ino,
    }
    marker = events / ("entered-" + role + ".json")
    with marker.open("x", encoding="utf-8") as handle:
        json.dump(event, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    limit = time.monotonic() + 120
    while not (events / ("release-" + role)).exists():
        if time.monotonic() > limit:
            raise TimeoutError("deterministic strategy barrier timed out")
        time.sleep(0.02)
    times = panel_df["timestamp"].n_unique()
    assets = panel_df["asset"].n_unique()
    return {"positions": np.full((times, assets), 0.1)}
"""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                            text=True, check=True, timeout=25)
    return result.stdout.strip()


def _sandbox_from_protected_manifest(root: Path, source: Path) -> None:
    """Copy only Warden's listed protected code/contracts; NEVER copy holdout data."""
    manifest = source / ".warden_manifest.sha256"
    raw = manifest.read_bytes()
    for line in raw.decode().splitlines():
        if not line.strip():
            continue
        expected, name = line.strip().split(maxsplit=1)
        rel = Path(name)
        assert not rel.is_absolute() and ".." not in rel.parts
        assert not (len(rel.parts) >= 2 and rel.parts[:2] == ("data", "holdout"))
        original = source / rel
        assert original.is_file() and _sha256(original) == expected, name
        destination = root / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, destination)
    for name in (".warden_manifest.sha256", ".warden_manifest.sha256.meta.json", "README.md"):
        original = source / name
        if original.is_file():
            shutil.copy2(original, root / name)
    assert not (root / "data" / "holdout").exists()
    # Legacy preflight requires a provenance sidecar even though this merged
    # release does not ship one. Provide TEST-ONLY metadata bound to the
    # unmodified manifest; never copy it to production or claim approval.
    if not (root / ".warden_manifest.sha256.meta.json").is_file():
        (root / ".warden_manifest.sha256.meta.json").write_text(json.dumps({
            "manifest_sha256": hashlib.sha256(raw).hexdigest(),
            "mode": "red_test_fixture_unapproved",
            "approver": "none",
        }), encoding="utf-8")
    # Legacy guard compares the immutable manifest against HEAD. This is a
    # *disposable* git baseline, not a fabricated Warden manifest or policy.
    subprocess.run(["git", "init", "-q", str(root)], check=True, timeout=20)
    subprocess.run(["git", "-C", str(root), "add", ".warden_manifest.sha256"],
                   check=True, timeout=20)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=RED Fixture",
                    "-c", "user.email=red-fixture@example.invalid",
                    "commit", "-qm", "Unmodified release manifest baseline"],
                   check=True, timeout=20)


def _fixture(root: Path, live_db: Path, src: Path) -> dict:
    _sandbox_from_protected_manifest(root, src)
    db = root / "registry" / "registry.db"
    db.parent.mkdir(parents=True)
    # SQLite ONLINE BACKUP: includes committed WAL frames; never copies a
    # database file or its WAL with filesystem-level copy.
    with (sqlite3.connect(f"file:{live_db.resolve()}?mode=ro", uri=True, timeout=20) as input_db,
          sqlite3.connect(db, timeout=20) as output_db):
        input_db.backup(output_db, pages=512, sleep=0.01)
    source_stat, clone_stat = live_db.stat(), db.stat()
    assert (source_stat.st_dev, source_stat.st_ino) != (clone_stat.st_dev, clone_stat.st_ino)
    assert live_db.resolve() != db.resolve()

    rows = []
    for i in range(72):
        ts = datetime(2025, 1, 1, tzinfo=UTC) + timedelta(hours=i)
        price = float(100 + i * 0.09 + (i % 7) * 0.02)
        rows.append({"timestamp": ts, "asset": "RED_SYNTH", "open": price,
                     "high": price * 1.005, "low": price * 0.995,
                     "close": price * 1.001, "volume": 100.0, "funding_rate": 0.0})
    panel = pl.DataFrame(rows).with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))
    market = root / "data" / "exploratory" / "warden_red_synthetic.parquet"
    market.parent.mkdir(parents=True, exist_ok=True)
    panel.write_parquet(market)
    market_sha = _sha256(market)
    contract = EvaluationContract(
        contract_id=CONTRACT_ID, asset_class="crypto_perps", frequency="1h",
        bars_per_year=8760,
        exploratory_data_path=str(market.relative_to(root)),
        holdout_data_path="data/holdout/never-created-writer-red.parquet",
        train_start=datetime(2025, 1, 1, tzinfo=UTC),
        val_start=datetime(2025, 1, 1, 12, tzinfo=UTC),
        val_end=datetime(2025, 1, 2, 12, tzinfo=UTC),
        holdout_start=datetime(2025, 1, 2, 12, tzinfo=UTC),
        holdout_end=datetime(2025, 1, 4, tzinfo=UTC),
        n_folds=2, label_horizon_bars=2, embargo_bars=1,
        execution_delay_bars=1, taker_fee_bps=1.0, slippage_bps=1.0,
        data_snapshot_sha256=market_sha, data_provenance="synthetic",
    )
    contract_path = root / "contracts" / f"{CONTRACT_ID.lower()}.yaml"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(yaml.safe_dump(contract.__dict__), encoding="utf-8")
    (root / "contracts" / "datasets" / f"{CONTRACT_ID}.json").write_text(
        json.dumps({"contract_id": CONTRACT_ID,
                    "path": contract.exploratory_data_path,
                    "sha256": market_sha, "provenance": "synthetic",
                    "source": "deterministic_generated_fixture"}), encoding="utf-8")
    strategy = root / STRATEGY_REL
    strategy.parent.mkdir(parents=True, exist_ok=True)
    strategy.write_text(STRATEGY, encoding="utf-8")
    spec = root / SPEC_REL
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(yaml.safe_dump({
        "hypothesis_id": HYPOTHESIS_ID,
        "contract_id": CONTRACT_ID,
        "strategy_name": "Deterministic red barrier",
        "strategy_family": "rules_ts",
        "strategy_module": STRATEGY_REL,
        "model_type": "rules",
        "parameters": {},
        "run_id": RUN_ID,
    }), encoding="utf-8")
    reg = ResearchRegistry(db)
    reg.register_contract(contract)
    reg.register_hypothesis(HypothesisSpec(
        HYPOTHESIS_ID, CONTRACT_ID, "Red writer lease race", "rules_ts",
        "Synthetic deterministic trend tests scheduling exclusion between versions",
        "Synthetic fees and slippage are accounted for but no alpha is claimed",
        8, "Reject under the unchanged Stage 1 scientific gates",
    ))
    return {"db_path": str(db), "db_device": db.stat().st_dev,
            "db_inode": db.stat().st_ino, "market_sha": market_sha,
            "holdout_path_exists": (root / contract.holdout_data_path).exists()}


def _counts(db: Path) -> dict:
    with sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True, timeout=10) as conn:
        con = conn
        tables = {
            "screen_receipts": ("SELECT state,attempt_id,screen_key FROM screen_receipts WHERE hypothesis_id=?",
                                (HYPOTHESIS_ID,)),
            "attempt_receipts": ("SELECT state,run_id,artifact_sha256 FROM attempt_receipts WHERE hypothesis_id=?",
                                 (HYPOTHESIS_ID,)),
            "selection_attempts": ("SELECT attempt_id,kind,outcome FROM selection_attempts WHERE hypothesis_id=?",
                                   (HYPOTHESIS_ID,)),
            "trials": ("SELECT run_id,verdict FROM trials WHERE hypothesis_id=?", (HYPOTHESIS_ID,)),
        }
        output = {key: [list(x) for x in con.execute(query, params)]
                  for key, (query, params) in tables.items()}
        output["trials_used"] = con.execute(
            "SELECT trials_used FROM hypotheses WHERE hypothesis_id=?", (HYPOTHESIS_ID,)
        ).fetchone()[0]
        return output


def _historic_digest(db: Path) -> str:
    digest = hashlib.sha256()
    with sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True, timeout=10) as con:
        for table, key in (("hypotheses", "hypothesis_id"),
                           ("trials", "run_id"),
                           ("selection_attempts", "attempt_id"),
                           ("trial_dispositions", "disposition_id"),
                           ("holdout_claims", "lineage_id")):
            digest.update(table.encode())
            for row in con.execute(f"SELECT * FROM {table} ORDER BY {key}"):
                # Only exclude the one new synthetic hypothesis, never skip
                # any pre-existing historical registry row.
                if table == "hypotheses" and row[0] == HYPOTHESIS_ID:
                    continue
                if table == "selection_attempts" and row[3] == HYPOTHESIS_ID:
                    continue
                if table == "trials" and row[1] == HYPOTHESIS_ID:
                    continue
                digest.update(json.dumps(list(row), default=str, sort_keys=True).encode())
    return digest.hexdigest()


def _locked(path: Path) -> bool:
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def _wait_observation(events: Path, role: str, proc: subprocess.Popen, seconds: int = 55) -> dict | None:
    marker = events / f"entered-{role}.json"
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if marker.exists():
            return json.loads(marker.read_text(encoding="utf-8"))
        if proc.poll() is not None:
            return None
        time.sleep(0.03)
    raise TimeoutError(f"{role}: no computation barrier or process completion in {seconds}s")


def _kill_group(proc: subprocess.Popen | None) -> None:
    if proc is not None and proc.poll() is None:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=10)


def _run_cli(root: Path, env: dict, *cmd: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "warden.cli", *cmd],
                          cwd=root, env=env, capture_output=True,
                          text=True, check=False, timeout=timeout)


def test_old_and_new_warden_cannot_both_admit_same_research_attempt() -> None:
    """Old governance backtest and new Warden screen of SAME hypothesis/spec.

    They use separate legitimate one-shot reservation tables, so a unique
    screen_key alone cannot prove process-wide single-writer admission.
    """
    source = Path(__file__).resolve().parents[1]
    old = Path(os.environ.get("WARDEN_RED_OLD_REPO", "/home/quant/dev/AI-research-loop"))
    live_db = Path(os.environ.get("WARDEN_RED_SOURCE_REGISTRY",
                                  "/home/quant/dev/quant-lab/registry/registry.db"))
    if not old.joinpath("src/workflow/governance_guard.py").is_file() or not live_db.is_file():
        pytest.skip("VPS-only RED integration: old governance checkout and canonical registry required")
    # Future GREEN-phase code must be able to run this *unchanged* test.
    # Require the tested baseline to be an ancestor; record the actual HEAD.
    baseline_ancestor = subprocess.run([
        "git", "-C", str(source), "merge-base", "--is-ancestor",
        EXPECTED_MERGED_COMMIT, "HEAD",
    ], check=False, timeout=15)
    assert baseline_ancestor.returncode == 0, "Release baseline not present in checkout history"
    source_sha_before = _sha256(live_db)
    observations: dict = {
        "merged_revision": _git(source, "rev-parse", "HEAD"),
        "legacy_revision": _git(old, "rev-parse", "HEAD"),
        "source_registry_sha256_before": source_sha_before,
        "old_process_pid": None, "new_process_pid": None,
    }
    with tempfile.TemporaryDirectory(prefix="warden-cross-version-red-") as tmp:
        root = Path(tmp) / "quant-lab"
        root.mkdir()
        fixture = _fixture(root, live_db, source)
        db = root / "registry" / "registry.db"
        hist_before = _historic_digest(db)
        baseline = _counts(db)
        events = Path(tmp) / "barriers"
        events.mkdir()
        legacy_lock = root / "registry" / "warden-writer.lock"
        new_lock = lease_path(db)
        env = os.environ.copy()
        env.update({
            "PYTHONPATH": os.pathsep.join([str(source / "src"), str(old)]),
            "UV_PROJECT_ENVIRONMENT": sys.prefix,
            "UV_NO_SYNC": "1",
            "WARDEN_NON_INTERACTIVE": "1",
            "WARDEN_RED_EVENTS": str(events),
        })
        observations.update({
            "fixture": fixture,
            "sandbox_root": str(root),
            "legacy_lock_path": str(legacy_lock),
            "merged_lock_path": str(new_lock),
            "baseline": baseline,
        })
        old_proc = None
        new_proc = None
        try:
            old_env = {**env, "WARDEN_RED_ROLE": "old"}
            old_cmd = [
                sys.executable, "-m", "src.workflow.governance_guard",
                "--quant-lab", str(root), "run", "uv", "run", "warden",
                "backtest", SPEC_REL, "--json",
            ]
            observations["old_command"] = old_cmd
            old_proc = subprocess.Popen(old_cmd, cwd=root, env=old_env,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, start_new_session=True)
            observations["old_process_pid"] = old_proc.pid
            old_event = _wait_observation(events, "old", old_proc)
            if old_event is None:
                stdout, stderr = old_proc.communicate(timeout=10)
                pytest.fail("TEST_ENVIRONMENT_INVALID: old governance entrypoint "
                            f"exited before computation: rc={old_proc.returncode}, "
                            f"stdout={stdout[-2500:]}, stderr={stderr[-2500:]}")
            observations["old_admitted"] = old_event
            observations["old_lock_held"] = _locked(legacy_lock)
            observations["merged_lock_held_by_old"] = new_lock.exists() and _locked(new_lock)
            assert observations["old_lock_held"], "TEST_ENVIRONMENT_INVALID: old guard did not own its lock"
            assert (old_event["db_device"], old_event["db_inode"]) == (
                fixture["db_device"], fixture["db_inode"])
            assert not fixture["holdout_path_exists"]
            observations["after_old_admission"] = _counts(db)

            new_env = {**env, "WARDEN_RED_ROLE": "new"}
            new_cmd = [sys.executable, "-m", "warden.cli", "screen", SPEC_REL, "--json"]
            observations["new_command"] = new_cmd
            new_proc = subprocess.Popen(new_cmd, cwd=root, env=new_env,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, start_new_session=True)
            observations["new_process_pid"] = new_proc.pid
            new_event = _wait_observation(events, "new", new_proc)
            observations["new_admitted"] = new_event
            observations["new_lease_held"] = new_lock.exists() and _locked(new_lock)
            if new_event is not None:
                assert (new_event["db_device"], new_event["db_inode"]) == (
                    fixture["db_device"], fixture["db_inode"])
            observations["simultaneous_computation"] = bool(
                old_event is not None and new_event is not None and old_proc.poll() is None)
            observations["during_concurrency"] = _counts(db)
            observations["legacy_lock_inode"] = legacy_lock.stat().st_ino
            observations["merged_lock_inode"] = new_lock.stat().st_ino if new_lock.exists() else None

            # Crash the whole old command process group, including the actual
            # Warden and isolated strategy child, without touching any service.
            _kill_group(old_proc)
            observations["old_sigkill_returncode"] = old_proc.returncode
            observations["legacy_lock_held_after_sigkill"] = _locked(legacy_lock)
            observations["new_lock_held_after_old_sigkill"] = (
                new_lock.exists() and _locked(new_lock))
            observations["after_crash"] = _counts(db)

            if new_event is not None:
                (events / "release-new").touch()
                new_stdout, new_stderr = new_proc.communicate(timeout=40)
                observations["new_stdout_tail"] = new_stdout[-1400:]
                observations["new_stderr_tail"] = new_stderr[-1400:]
            else:
                new_stdout, new_stderr = new_proc.communicate(timeout=15)
                observations["new_stdout_tail"] = new_stdout[-1400:]
                observations["new_stderr_tail"] = new_stderr[-1400:]
            observations["new_exit_code"] = new_proc.returncode
            observations["new_response_busy"] = (
                new_proc.returncode == 75
                and "BUSY" in (new_stdout + new_stderr))
            if new_proc.returncode == 75:
                # A correct implementation can now admit a single screen after
                # the old process has died and released the authoritative lease.
                (events / "release-recovered").touch()
                resumed = _run_cli(root, {**env, "WARDEN_RED_ROLE": "recovered"},
                                   "screen", SPEC_REL, "--json", timeout=45)
                observations["postcrash_screen_exit_code"] = resumed.returncode
                observations["postcrash_screen_stdout_tail"] = resumed.stdout[-1400:]

            # Reconcile the killed backtest once, then again after a simulated
            # lost response. This MUST NOT replenish its consumed trial budget.
            state_one = _run_cli(root, env, "reconcile-attempt", "--run-id", RUN_ID, "--json")
            state_two = _run_cli(root, env, "reconcile-attempt", "--run-id", RUN_ID, "--json")
            observations["first_reconcile"] = [state_one.returncode, state_one.stdout[-1000:]]
            observations["lost_response_reconcile"] = [state_two.returncode, state_two.stdout[-1000:]]

            # A finalized Stage 1 screen is recovered from SQLite, never its
            # transport response. The same-spec retry must never invoke science.
            post = _run_cli(root, env, "spec-status", SPEC_REL, "--json")
            post_again = _run_cli(root, env, "spec-status", SPEC_REL, "--json")
            observations["screen_reconciled"] = [post.returncode, post.stdout[-1000:]]
            observations["screen_reconciled_again"] = [post_again.returncode, post_again.stdout[-1000:]]
            retry = _run_cli(root, {**env, "WARDEN_RED_ROLE": "retry"},
                             "screen", SPEC_REL, "--json", timeout=30)
            observations["screen_retry"] = [retry.returncode, retry.stdout[-300:], retry.stderr[-500:]]
            observations["after_recovery"] = _counts(db)
            observations["historical_rows_unchanged"] = (_historic_digest(db) == hist_before)

            # Distinguish the *hypothetical* legacy nested wrapper from the
            # shipped systemd command, which runs social_campaign directly.
            nested = subprocess.run([
                "/usr/bin/flock", "-n", str(legacy_lock),
                sys.executable, "-m", "src.workflow.governance_guard",
                "--quant-lab", str(root), "run",
                "uv", "run", "warden", "status", "--json",
            ], cwd=root, env=env, capture_output=True, text=True, timeout=25, check=False)
            observations["nested_legacy_lock"] = {
                "exit_code": nested.returncode, "stdout_tail": nested.stdout[-300:],
                "stderr_tail": nested.stderr[-800:],
            }
        finally:
            _kill_group(new_proc)
            _kill_group(old_proc)
        observations["source_registry_sha256_after"] = _sha256(live_db)
        observations["source_registry_unchanged"] = (
            observations["source_registry_sha256_after"] == source_sha_before)
        observations["holdout_path_exists_after"] = (root / "data" / "holdout").exists()
        observations["classification"] = (
            "SPLIT_BRAIN_ADMISSION" if observations["simultaneous_computation"]
            else "SAFE_SINGLE_WRITER" if observations["new_response_busy"]
            else "TEST_ENVIRONMENT_INVALID"
        )
        payload = json.dumps(observations, sort_keys=True, indent=2)
        output = os.environ.get("WARDEN_RED_EVIDENCE_JSON")
        if output:
            dest = Path(output).resolve()
            if dest.is_relative_to(source):
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(payload + "\n", encoding="utf-8")
        print("WARDEN_CROSS_VERSION_RED_EVIDENCE=" + json.dumps(observations, sort_keys=True), flush=True)

        assert observations["source_registry_unchanged"], "Source registry SHA-256 changed during RED test"
        assert not observations["holdout_path_exists_after"], "Protected holdout present in disposable sandbox"
        assert observations["historical_rows_unchanged"], "Historic rows in disposable registry changed"
        assert observations["old_sigkill_returncode"] == -signal.SIGKILL
        assert not observations["legacy_lock_held_after_sigkill"]
        assert len(observations["after_recovery"]["attempt_receipts"]) == 1
        assert observations["after_recovery"]["trials_used"] == 1
        assert len(observations["after_recovery"]["screen_receipts"]) == 1
        assert len(observations["after_recovery"]["selection_attempts"]) == 1
        assert observations["after_recovery"]["attempt_receipts"][0][0] == "INTERRUPTED_REVIEW"
        assert observations["after_recovery"]["screen_receipts"][0][0] == "FINALIZED"
        assert observations["screen_retry"][0] != 0
        assert not (events / "entered-retry.json").exists(), "Retry re-entered science"
        assert observations["after_recovery"]["screen_receipts"][0][1] == (
            observations["after_recovery"]["selection_attempts"][0][0])
        assert observations["after_recovery"]["screen_receipts"][0][2]
        assert state_one.returncode == state_two.returncode == 0
        assert post.stdout == post_again.stdout or (
            json.loads(post.stdout)["value"]["state"]
            == json.loads(post_again.stdout)["value"]["state"])
        assert observations["new_response_busy"] and not observations["simultaneous_computation"], (
            "SPLIT_BRAIN_ADMISSION: legacy and merged entrypoints admitted concurrent "
            "scientific computation against the same SQLite device/inode, despite "
            "distinct OS lockfiles. Evidence is printed above. "
            f"new_rc={observations['new_exit_code']}; "
            f"both_compute={observations['simultaneous_computation']}")
