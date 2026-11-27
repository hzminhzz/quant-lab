"""Owned process exit is observed before durable capacity is released."""
import os
import signal
import sys
from pathlib import Path

from warden_ops.control_contracts import Resources
from warden_ops.process_limits import run_bounded


def active(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rpartition(") ")[2].split()[0] not in {"Z", "X"}
    except FileNotFoundError:
        return False


def test_parent_exit_cannot_leave_an_owned_child_running(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    program = (
        "import subprocess, sys\n"
        "from pathlib import Path\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        f"Path({str(output / 'child.pid')!r}).write_text(str(child.pid))\n"
    )
    result = run_bounded([sys.executable, "-c", program], cwd=tmp_path,
                         env={"PATH": "/usr/bin:/bin"}, output=output,
                         limits=Resources(cpu=1, memory_mb=128, io_mb=1, wall_seconds=5))
    pid = int((output / "child.pid").read_text())
    try:
        assert not active(pid), "capacity must remain held until all owned processes exit"
        assert result["process_exited"] is True
        assert result["state"] == "STOPPED"  # Cleanup was required after the parent exited.
    finally:
        if active(pid):
            os.kill(pid, signal.SIGKILL)


def test_cpu_reservation_is_inherited_before_any_child_starts(tmp_path):
    import json
    output = tmp_path / "output"
    output.mkdir()
    cpus = [max(os.sched_getaffinity(0))]
    program = ("import os, json, subprocess, sys\n"
               "child = subprocess.check_output([sys.executable, '-c', "
               "'import os,json; print(json.dumps(sorted(os.sched_getaffinity(0))))'])\n"
               "print(json.dumps([sorted(os.sched_getaffinity(0)), json.loads(child)]))\n")
    result = run_bounded([sys.executable, "-c", program], cwd=tmp_path,
                         env={"PATH": "/usr/bin:/bin"}, output=output, cpu_ids=cpus,
                         limits=Resources(cpu=1, memory_mb=128, io_mb=16, wall_seconds=5))
    assert result["state"] == "FINISHED", json.dumps(result)
    assert json.loads((output / "stdout").read_text()) == [cpus, cpus]


def test_unconfirmed_process_exit_keeps_job_capacity(tiny_repo, monkeypatch):
    from tests.test_ac03_actions import prepare, ready
    from warden_ops import job_runner
    f, control = ready(tiny_repo)
    plan = prepare(control)
    control.handle({"schema_version": 1, "operation": "enqueue", "request_id": "unknown-exit",
                    "plan": plan, "objective": "retain unknown process ownership"})
    monkeypatch.setattr(job_runner, "run_bounded", lambda *a, **kw: {
        "state": "STOPPED", "process_exited": False, "usage": {},
        "exit_code": None, "reason": "process remains uninterruptible"})
    result = job_runner.run_job(f.repo, f.registry.db_path, "unknown-exit")
    assert result["state"] == "CANCEL_REQUESTED"
    assert result["resources_held"] is True
    assert result["details"]["receipt"]["supervisor"]["process_exited"] is False
    assert job_runner.run_job(f.repo, f.registry.db_path, "unknown-exit")["claimed"] is False
