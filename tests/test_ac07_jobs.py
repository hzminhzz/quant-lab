"""A16/A21: durable operational jobs never refund or replay scientific work."""
import pytest

from warden.registry import ResearchRegistry
from warden_ops.jobs import JobQueue, ResourceLimits


def queue(tmp_path, **kwargs):
    registry = ResearchRegistry(tmp_path / "registry.db")
    return JobQueue(registry, limits=ResourceLimits(cpu=2, memory_mb=512, io_mb=64,
                                                   wall_seconds=60), **kwargs)


def test_shared_limits_fencing_and_restart_are_durable(tmp_path):
    q = queue(tmp_path, clock=lambda: 1000.)
    resources = ResourceLimits(cpu=2, memory_mb=512, io_mb=32, wall_seconds=20)
    q.submit("job-one", {"fixture": "one"}, objective="finish evidence", resources=resources)
    q.submit("job-two", {"fixture": "two"}, objective="independent objective", resources=resources)
    claim = q.claim("job-one")
    assert claim["state"] == "RUNNING"
    assert q.claim("job-two")["state"] == "BUSY"
    resumed = queue(tmp_path, clock=lambda: 1001.)
    assert resumed.status("job-one")["fence"] == claim["fence"]
    assert resumed.claim("job-one")["state"] == "RUNNING"
    assert resumed.claim("job-one")["claimed"] is False
    with pytest.raises(ValueError, match="fence"):
        resumed.finish("job-one", "wrong-fence", receipt={"status": "COMPLETE"}, usage={})
    resumed.finish("job-one", claim["fence"], receipt={"status": "COMPLETE"},
                   usage={"elapsed_seconds": 1., "cpu_seconds": .1, "peak_rss_mb": 50., "io_bytes": 1024})
    assert resumed.claim("job-two")["state"] == "RUNNING"


def test_simultaneous_jobs_reserve_distinct_host_cpus(tmp_path):
    q = queue(tmp_path)
    for job in ("job-one", "job-two"):
        q.submit(job, {"fixture": job}, objective="bounded parallelism",
                 resources=ResourceLimits(cpu=1, memory_mb=128, io_mb=1, wall_seconds=10))
    first, second = q.claim("job-one"), q.claim("job-two")
    assert len(first["details"]["cpu_ids"]) == len(second["details"]["cpu_ids"]) == 1
    assert set(first["details"]["cpu_ids"]).isdisjoint(second["details"]["cpu_ids"])


def test_cancel_expiry_and_late_completion_keep_unknown_effects(tmp_path):
    now = [1000.]
    q = queue(tmp_path, clock=lambda: now[0])
    q.submit("job-one", {"fixture": "one"}, objective="finish evidence",
             resources=ResourceLimits(cpu=1, memory_mb=128, io_mb=1, wall_seconds=10))
    claim = q.claim("job-one")
    q.cancel("job-one")
    with pytest.raises(ValueError, match="fenced"):
        q.finish("job-one", claim["fence"], receipt={"status": "COMPLETE"}, usage={})
    q.confirm_stopped("job-one", claim["fence"], receipt={"status": "UNCERTAIN"})
    assert q.status("job-one")["state"] == "STOPPED_REVIEW"
    assert q.claim("job-one")["claimed"] is False
    q.submit("job-two", {"fixture": "two"}, objective="finish evidence",
             resources=ResourceLimits(cpu=1, memory_mb=128, io_mb=1, wall_seconds=10))
    late = q.claim("job-two")
    now[0] = 1011.
    assert q.expire("job-two")["state"] == "EXPIRED_UNKNOWN"
    with pytest.raises(ValueError, match="fenced"):
        q.finish("job-two", late["fence"], receipt={"status": "COMPLETE"}, usage={})
    assert q.status("job-two")["resources_held"] is True


def test_job_identity_and_resource_measurements_cannot_be_changed(tmp_path):
    q = queue(tmp_path, clock=lambda: 1000.)
    resources = ResourceLimits(cpu=1, memory_mb=128, io_mb=1, wall_seconds=10)
    q.submit("job-one", {"fixture": "one"}, objective="finish evidence", resources=resources)
    with pytest.raises(ValueError, match="identity"):
        q.submit("job-one", {"fixture": "changed"}, objective="finish evidence", resources=resources)
    claim = q.claim("job-one")
    result = q.heartbeat("job-one", claim["fence"], progress={"completed": 1, "total": 3},
                         usage={"elapsed_seconds": 1., "cpu_seconds": .2,
                                "peak_rss_mb": 129., "io_bytes": 0})
    assert result["state"] == "STOP_REQUESTED"
    assert result["resources_held"] is True


def test_control_mutations_never_initialize_or_migrate_ledger(tmp_path):
    import sqlite3

    from warden_ops.control import Control
    control = Control(tmp_path)
    request = {"schema_version": 1, "operation": "cancel-job", "job_id": "unknown"}
    assert control.handle(request)["status"] == "REFUSED"
    assert not (tmp_path / "registry").exists()
    db = tmp_path / "legacy.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE old_evidence(value TEXT)")
        conn.execute("INSERT INTO old_evidence VALUES ('preserve')")
        conn.execute("PRAGMA user_version=8")
    before = db.read_bytes()
    result = Control(tmp_path, registry_path=db).handle(request)
    assert result["status"] == "REFUSED"
    assert "migration" in result["blockers"][0]["predicate"].lower()
    assert db.read_bytes() == before


def test_final_over_limit_cost_is_retained_as_stop_disposition(tmp_path):
    q = queue(tmp_path)
    q.submit("job-one", {"fixture": "one"}, objective="bounded work",
             resources=ResourceLimits(cpu=1, memory_mb=128, io_mb=1, wall_seconds=10))
    claim = q.claim("job-one")
    result = q.finish("job-one", claim["fence"], receipt={"status": "UNCERTAIN"},
                      usage={"elapsed_seconds": 12., "cpu_seconds": 1.,
                             "peak_rss_mb": 50., "io_bytes": 1024})
    assert result["state"] == "STOPPED_REVIEW"
    assert result["details"]["usage"]["elapsed_seconds"] == 12.
    assert result["resources_held"] is False


def test_real_queued_science_runs_once_and_reuses_its_receipt(tiny_repo):
    from tests.test_ac03_actions import prepare, ready
    from warden_ops.job_runner import run_job
    f, control = ready(tiny_repo)
    plan = prepare(control)
    result = control.handle({"schema_version": 1, "operation": "enqueue", "request_id": "async-one",
                             "plan": plan, "objective": "finalize synthetic screen once"})
    assert result["value"]["state"] == "QUEUED"
    result = run_job(f.repo, f.registry.db_path, "async-one")
    assert result["state"] == "FINISHED", result
    assert result["details"]["receipt"]["recorded_verdict"] == "PASS", result
    assert result["details"]["usage"]["elapsed_seconds"] > 0
    assert run_job(f.repo, f.registry.db_path, "async-one")["claimed"] is False
    assert f.registry.candidate_budget("HYP-TINY")["used"] == 1


def test_stateless_worker_cannot_read_parent_files_or_keys_and_is_bounded(tmp_path, monkeypatch):
    import json

    from warden_ops.sandbox_worker import run_worker
    job, output = tmp_path / "input", tmp_path / "output"
    job.mkdir()
    secret = tmp_path / "fixture-key"
    secret.write_text("fixture-secret")
    monkeypatch.setenv("FIXTURE_KEY", "must-not-cross")
    (job / "program.py").write_text(
        "import json, os\n"
        f"from pathlib import Path\nsecret = Path({str(secret)!r})\n"
        "print(json.dumps({'visible': secret.exists(), 'key': os.environ.get('FIXTURE_KEY')}))\n"
        "Path('/output/result').write_text('scoped output')\n")
    result = run_worker(job, output, ResourceLimits(cpu=1, memory_mb=128, io_mb=1, wall_seconds=5))
    assert result["state"] == "FINISHED", result
    observed = json.loads((output / "stdout").read_text())
    assert observed == {"visible": False, "key": None}
    assert secret.read_text() == "fixture-secret"
    assert (output / "result").read_text() == "scoped output"
    (job / "program.py").write_text("while True: pass\n")
    timeout = run_worker(job, tmp_path / "timeout", ResourceLimits(cpu=1, memory_mb=128, io_mb=1, wall_seconds=1))
    assert timeout["state"] == "STOPPED"
    assert timeout["process_exited"] is True
    assert timeout["usage"]["cpu_seconds"] > .05  # Measure real work inside bwrap, not only its monitor.
