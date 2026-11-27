"""Corrective AC witnesses: protected dependencies, environment and job recovery."""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from tests.test_ac03_actions import ready


@pytest.mark.parametrize("reference", ["entry", "declared", "symlink", "protected-inventory-symlink"])
def test_prepare_never_inspects_protected_dependency(tiny_repo, monkeypatch, reference):
    fixture, control = ready(tiny_repo)
    target = fixture.repo / "data/holdout/synthetic.py"
    target.parent.mkdir()
    target.write_text("VALUE = 123\n")
    spec = yaml.safe_load(fixture.spec.read_text())
    module = fixture.repo / spec["strategy_module"]
    if reference == "entry":
        spec["strategy_module"] = "data/holdout/synthetic.py"
        fixture.spec.write_text(yaml.safe_dump(spec))
    elif reference == "protected-inventory-symlink":
        dependency = fixture.repo / "src/warden/protected.py"
        dependency.parent.mkdir(parents=True, exist_ok=True)
        dependency.symlink_to(target)
    else:
        dependency = target
        if reference == "symlink":
            dependency = module.parent / "linked.py"
            dependency.symlink_to(target)
        module.write_text(module.read_text() + "\nCODE_DEPENDENCIES = (" +
                          repr(dependency.relative_to(fixture.repo).as_posix()) + ",)\n")

    seen = []
    for name in ("stat", "lstat", "open"):
        original = getattr(Path, name)

        def guarded(path, *args, _original=original, _name=name, **kwargs):
            if path == target:
                seen.append(_name)
            return _original(path, *args, **kwargs)

        monkeypatch.setattr(Path, name, guarded)
    before = fixture.registry.candidate_budget("HYP-TINY")["used"]
    result = control.handle({"schema_version": 1, "operation": "prepare",
                             "action": {"kind": "screen", "spec_path": "spec.yaml"}})
    assert result["status"] == "REFUSED", result
    assert seen == []
    assert fixture.registry.candidate_budget("HYP-TINY")["used"] == before


def test_prepare_binds_trusted_holdout_policy_script_without_data_access(tiny_repo):
    from tests.test_ac03_actions import prepare

    fixture, control = ready(tiny_repo)
    script = fixture.repo / "scripts/lock_holdout_permissions.sh"
    script.parent.mkdir(exist_ok=True)
    script.write_text("# synthetic trusted policy script\n")
    plan = prepare(control)
    assert any(row["source"] == "file:scripts/lock_holdout_permissions.sh"
               for row in plan["revisions"])


def test_environment_identity_binds_dependency_code_not_only_version(tmp_path):
    from warden_ops.appliance import _environment

    env = tmp_path / "environment"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(env)], check=True)
    python = env / "bin/python"
    site = Path(subprocess.check_output(
        [str(python), "-I", "-B", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        text=True).strip())
    module = site / "fixture_dependency.py"
    module.write_text("VALUE = 1\n")
    metadata = site / "fixture_dependency-1.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Name: fixture-dependency\nVersion: 1.0\n")
    before = _environment(python)
    module.write_text("VALUE = 2\n")  # Same file length, package and version.
    assert _environment(python) != before
    module.write_text("VALUE = 1\n")
    assert _environment(python) == before
    (site / "unexpected_module.py").write_text("VALUE = 3\n")
    assert _environment(python) != before


def test_lost_supervisor_before_dispatch_recovers_via_control(tiny_repo):
    from tests.test_ac03_actions import prepare

    fixture, control = ready(tiny_repo)
    plan = prepare(control)
    queued = control.handle({"schema_version": 1, "operation": "enqueue",
                             "request_id": "lost-owner", "plan": plan,
                             "objective": "synthetic recovery witness"})
    assert queued["value"]["state"] == "QUEUED"
    code = (
        "import os,signal; from warden_ops import job_runner;"
        "job_runner.run_bounded=lambda *a,**kw: os.kill(os.getpid(),signal.SIGKILL);"
        f"job_runner.run_job({str(fixture.repo)!r},{str(fixture.registry.db_path)!r},'lost-owner')"
    )
    before = fixture.registry.candidate_budget("HYP-TINY")["used"]
    proc = subprocess.run([sys.executable, "-B", "-c", code], check=False, timeout=30)
    assert proc.returncode == -signal.SIGKILL
    status = control.handle({"schema_version": 1, "operation": "job-status", "job_id": "lost-owner"})
    assert status["value"]["resources_held"]
    request = {"schema_version": 1, "operation": "recover-job", "job_id": "lost-owner"}
    recovered = control.handle(request)
    assert recovered["ok"], recovered
    assert recovered["value"]["state"] == "STOPPED_REVIEW"
    assert recovered["value"]["resources_held"] is False
    assert recovered["value"]["retry_allowed"] is False
    assert control.handle(request)["value"] == recovered["value"]
    assert fixture.registry.candidate_budget("HYP-TINY")["used"] == before


@pytest.mark.parametrize("descendant", [False, True])
def test_lost_supervisor_after_dispatch_recovers_only_after_session_exit(tiny_repo, descendant):
    from tests.test_ac03_actions import prepare

    fixture, control = ready(tiny_repo)
    plan = prepare(control)
    job = "dispatched-owner"
    request = {"schema_version": 1, "operation": "recover-job", "job_id": job}
    status_request = {**request, "operation": "job-status"}
    queued = control.handle({"schema_version": 1, "operation": "enqueue",
                             "request_id": job, "plan": plan,
                             "objective": "synthetic live session recovery"})
    assert queued["value"]["state"] == "QUEUED"
    marker = fixture.repo / "worker.json"
    child = (
        "import os,sys,time,json,subprocess; from pathlib import Path;"
        + ("p=subprocess.Popen([sys.executable,'-B','-c','import time; time.sleep(30)']);"
           if descendant else "p=None;")
        + f"Path({str(marker)!r}).write_text(json.dumps([os.getpid(),p.pid if p else None]));"
        + "time.sleep(30)"
    )
    code = (
        "import sys; from warden_ops import job_runner; from warden_ops.process_limits import run_bounded;"
        f"job_runner.run_bounded=lambda command,**kw:run_bounded([sys.executable,'-B','-c',{child!r}],**kw);"
        f"job_runner.run_job({str(fixture.repo)!r},{str(fixture.registry.db_path)!r},{job!r})"
    )
    before = fixture.registry.candidate_budget("HYP-TINY")["used"]
    supervisor = subprocess.Popen([sys.executable, "-B", "-c", code])
    owned = []
    try:
        deadline = time.monotonic() + 15
        while not marker.exists() and time.monotonic() < deadline and supervisor.poll() is None:
            time.sleep(.05)
        owned = [pid for pid in json.loads(marker.read_text()) if pid is not None]
        status = control.handle(status_request)["value"]
        assert status["details"]["worker"]["pid"] == owned[0]
        assert status["details"]["ownership"]["supervisor"]["pid"] == supervisor.pid
        assert status["resources_held"]
        assert not control.handle(request)["ok"]  # Live supervisor owns the lease.
        supervisor.kill()
        assert supervisor.wait(timeout=10) == -signal.SIGKILL

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            recovered = control.handle(request)
            if recovered["ok"]:
                break
            time.sleep(.05)
        assert recovered["ok"], recovered
        if descendant:
            assert recovered["value"]["resources_held"]
            assert "session" in recovered["value"]["recovery"]
            # This PID is from our own disposable fixture, not an operator process.
            os.kill(owned[1], signal.SIGKILL)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                recovered = control.handle(request)
                if recovered["ok"] and not recovered["value"]["resources_held"]:
                    break
                time.sleep(.05)
        assert recovered["value"]["state"] == "STOPPED_REVIEW", recovered
        assert recovered["value"]["details"]["recovery"]["process_exited"]
        assert recovered["value"]["retry_allowed"] is False
        assert fixture.registry.candidate_budget("HYP-TINY")["used"] == before
    finally:
        if supervisor.poll() is None:
            supervisor.kill()
            supervisor.wait(timeout=10)
        # Kill only the fixture's known session, and only while a recorded
        # member is still in that session. Test failure must not leak workers.
        for pid in owned:
            try:
                fields = Path(f"/proc/{pid}/stat").read_text().rpartition(") ")[2].split()
                if int(fields[3]) == owned[0] and fields[0] not in {"Z", "X"}:
                    os.kill(pid, signal.SIGKILL)
            except (FileNotFoundError, ProcessLookupError):
                pass
