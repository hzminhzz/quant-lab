"""One Linux supervisor: bounded children, actual exit, and per-child wait4 cost.

The logical I/O counter is sampled (a lower bound), not a disk quota. RLIMIT and
affinity constrain each process; this is not an aggregate hostile-process cgroup.
The stateless worker adds a PID/filesystem/network namespace around this owner.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path


def _sample(pid):
    try:
        status = dict(line.split(":", 1) for line in Path(f"/proc/{pid}/status").read_text().splitlines()
                      if ":" in line)
        io = dict(line.split(":", 1) for line in Path(f"/proc/{pid}/io").read_text().splitlines())
        return float(status.get("VmHWM", "0 kB").split()[0]) / 1024, int(io["rchar"]) + int(io["wchar"])
    except (OSError, KeyError, ValueError):
        return 0., 0


def _output_bytes(output):
    total, count = 0, 0
    for root, _dirs, files in os.walk(output, followlinks=False):
        for name in files:
            count += 1
            if count > 10000:
                raise ValueError("worker output file count exceeded")
            total += (Path(root) / name).lstat().st_size
    return total


def run_bounded(command, *, cwd, env, output, limits, heartbeat=None):
    """Only trusted owners supply commands. Reap before returning or raising."""
    started = time.monotonic()
    peak, io_bytes, output_bytes = 0., 0, 0
    stopped, reason, cost, proc = False, None, None, None
    next_heartbeat = 0.
    with (output / "stdout").open("xb") as stdout, (output / "stderr").open("xb") as stderr:
        try:
            proc = subprocess.Popen(command, cwd=cwd, env=env, stdout=stdout, stderr=stderr,
                                    start_new_session=True, close_fds=True)
            try:
                os.sched_setaffinity(proc.pid, set(sorted(os.sched_getaffinity(0))[:limits.cpu]))
            except ProcessLookupError:
                pass  # A fast child is still reaped and measured below.
            while True:
                pid, status, observed = os.wait4(proc.pid, os.WNOHANG)
                if pid:
                    proc.returncode, cost = os.waitstatus_to_exitcode(status), observed
                    break
                rss, io = _sample(proc.pid)
                peak, io_bytes = max(peak, rss), max(io_bytes, io)
                output_bytes = max(output_bytes, _output_bytes(output))
                elapsed = time.monotonic() - started
                usage = {"elapsed_seconds": elapsed, "peak_rss_mb": peak, "io_bytes": io_bytes}
                if (elapsed > limits.wall_seconds or peak > limits.memory_mb
                        or max(io_bytes, output_bytes) > limits.io_mb * 1024 * 1024):
                    stopped, reason = True, "declared resource ceiling exceeded"
                    break
                if heartbeat is not None and elapsed >= next_heartbeat:
                    next_heartbeat = elapsed + .25
                    if not heartbeat(usage):
                        stopped, reason = True, "cancelled or fenced"
                        break
                time.sleep(.025)
        except Exception as exc:  # noqa: BLE001 -- kill/reap before propagating supervisor faults
            stopped, reason = True, f"supervisor failure: {type(exc).__name__}: {str(exc)[:300]}"
        finally:
            if proc is not None and proc.returncode is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                _, status, cost = os.wait4(proc.pid, 0)
                proc.returncode = os.waitstatus_to_exitcode(status)
    if cost is not None:
        peak = max(peak, cost.ru_maxrss / 1024)
    return {
        "state": "STOPPED" if stopped or (proc and proc.returncode < 0) else
                 "FINISHED" if proc and proc.returncode == 0 else "FAILED",
        "exit_code": proc.returncode if proc else None, "process_exited": True, "reason": reason,
        "usage": {"elapsed_seconds": time.monotonic() - started,
                  "cpu_seconds": cost.ru_utime + cost.ru_stime if cost else 0.,
                  "peak_rss_mb": peak, "io_bytes": io_bytes},
        "measurement": {"cpu": "wait4 child user+system", "memory": "wait4 maximum RSS",
                        "io": "sampled logical read/write bytes; lower bound",
                        "output_bytes_observed": output_bytes,
                        "aggregate_hostile_process_quota": False},
    }
