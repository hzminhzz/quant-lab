"""Linux ownership evidence for recoverable trusted job supervisors.

A stable, inherited flock covers the spawn/publication gap. A worker cannot
execute until its session identity is durable. Recovery never signals a reused
PID or assumes that an expired heartbeat proves process exit.
"""
from __future__ import annotations

import fcntl
import hashlib
import os
import sys
from contextlib import contextmanager
from pathlib import Path

from warden.lease import MutationBusy


def boot_id():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def _proc(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rpartition(") ")[2].split()
    except FileNotFoundError:
        return None
    return {"pid": pid, "state": fields[0], "session": int(fields[3]),
            "start_ticks": int(fields[19])}


def process_identity(pid):
    value = _proc(pid)
    if value is None or value["state"] in {"Z", "X"}:
        raise ValueError("process identity unavailable")
    return {"boot_id": boot_id(), **{k: v for k, v in value.items() if k != "state"}}


def _alive(identity):
    if identity["boot_id"] != boot_id():
        return False
    value = _proc(identity["pid"])
    return (value is not None and value["state"] not in {"Z", "X"}
            and value["start_ticks"] == identity["start_ticks"])


def _session_alive(identity):
    if identity["boot_id"] != boot_id():
        return False
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            value = _proc(int(entry.name))
            if (value is not None and value["session"] == identity["session"]
                    and value["state"] not in {"Z", "X"}):
                return True
    return False


@contextmanager
def job_lease(registry_path, job, *, create=False):
    """The same inode remains locked by supervisor and gated worker."""
    suffix = hashlib.sha256(job.encode()).hexdigest()
    lock = Path(registry_path).resolve().parent / (".warden-job-" + suffix + ".lock")
    flags = os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW
    fd = os.open(lock, flags | (os.O_CREAT if create else 0), 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise MutationBusy("job supervisor or worker still owns its lease") from exc
        yield fd
    finally:
        os.close(fd)


def owner_identity(fd):
    info = os.fstat(fd)
    return {"protocol": 1, "supervisor": process_identity(os.getpid()),
            "lease_device": info.st_dev, "lease_inode": info.st_ino}


def stop_evidence(details, fd):
    """Prove the owner and all trusted session members stopped; otherwise hold."""
    owner = details.get("ownership")
    if not owner or owner.get("protocol") != 1:
        return None, "legacy or missing process ownership evidence"
    info = os.fstat(fd)
    if (info.st_dev, info.st_ino) != (owner["lease_device"], owner["lease_inode"]):
        return None, "job lease identity changed"
    supervisor = owner["supervisor"]
    if _alive(supervisor):
        return None, "original supervisor still alive"
    worker = details.get("worker")
    live = worker is not None and _session_alive(worker)
    evidence = {"supervisor": supervisor, "worker": worker, "observed_boot_id": boot_id(),
                "lease_device": info.st_dev, "lease_inode": info.st_ino,
                "disposition": ("session_still_live_or_ambiguous" if live else
                                "session_stopped" if worker is not None else "dispatch_never_released"),
                "process_exited": not live}
    return evidence, "owned worker session still has live or ambiguous members" if live else None


# Executed in the new session before any trusted research command. An inherited
# lease covers even a supervisor death before the session receipt is published.
# Parent death kills the leader; remaining descendants keep recovery uncertain.
_GATE = """import ctypes, os, signal, sys
fd, parent = int(sys.argv[1]), int(sys.argv[2])
if ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
    sys.exit(125)
if os.getppid() != parent or os.read(fd, 1) != b'1':
    sys.exit(125)
os.close(fd)
os.execvpe(sys.argv[3], sys.argv[3:], os.environ)
"""


def gated_command(command, read_fd):
    return [sys.executable, "-B", "-c", _GATE, str(read_fd), str(os.getpid()), *command]
