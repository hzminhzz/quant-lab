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


def _sample_tree(pid, observed):
    pending, seen, rss = [pid], set(), 0.
    ticks = os.sysconf("SC_CLK_TCK")
    while pending:
        child = pending.pop()
        if child in seen:
            continue
        seen.add(child)
        if len(seen) > 128:
            raise ValueError("supervised process count exceeded")
        base = Path(f"/proc/{child}")
        try:
            status = dict(line.split(":", 1) for line in (base / "status").read_text().splitlines() if ":" in line)
            io = dict(line.split(":", 1) for line in (base / "io").read_text().splitlines())
            fields = (base / "stat").read_text().rpartition(") ")[2].split()
            key = (child, fields[19])  # PID plus start time prevents reuse confusion.
            cpu = (int(fields[11]) + int(fields[12])) / ticks
            logical_io = int(io["rchar"]) + int(io["wchar"])
            old = observed.get(key, (0., 0))
            observed[key] = max(old[0], cpu), max(old[1], logical_io)
            rss += float(status.get("VmRSS", "0 kB").split()[0]) / 1024
            for task in (base / "task").iterdir():
                pending.extend(int(x) for x in (task / "children").read_text().split())
        except (OSError, KeyError, ValueError, IndexError):
            continue  # Exited children stay in observed; samples remain lower bounds.
    return rss, sum(x[0] for x in observed.values()), sum(x[1] for x in observed.values())


def _output_bytes(output):
    total, count = 0, 0
    for root, _dirs, files in os.walk(output, followlinks=False):
        for name in files:
            count += 1
            if count > 10000:
                raise ValueError("worker output file count exceeded")
            total += (Path(root) / name).lstat().st_size
    return total


def run_bounded(command, *, cwd, env, output, limits, heartbeat=None, cpu_ids=None):
    """Only trusted owners supply commands. Reap before returning or raising."""
    started = time.monotonic()
    peak, io_bytes, output_bytes, cpu_seconds = 0., 0, 0, 0.
    observed = {}
    allowed = os.sched_getaffinity(0)
    cpus = set(cpu_ids) if cpu_ids is not None else set(sorted(allowed)[:limits.cpu])
    if not cpus or not cpus <= allowed or len(cpus) > limits.cpu:
        raise ValueError("invalid reserved CPU set")
    stopped, reason, cost, proc = False, None, None, None
    next_heartbeat = 0.
    with (output / "stdout").open("xb") as stdout, (output / "stderr").open("xb") as stderr:
        try:
            proc = subprocess.Popen(command, cwd=cwd, env=env, stdout=stdout, stderr=stderr,
                                    start_new_session=True, close_fds=True)
            try:
                os.sched_setaffinity(proc.pid, cpus)
            except ProcessLookupError:
                pass  # A fast child is still reaped and measured below.
            while True:
                pid, status, reaped_usage = os.wait4(proc.pid, os.WNOHANG)
                if pid:
                    proc.returncode, cost = os.waitstatus_to_exitcode(status), reaped_usage
                    break
                rss, cpu, io = _sample_tree(proc.pid, observed)
                peak, io_bytes, cpu_seconds = max(peak, rss), max(io_bytes, io), max(cpu_seconds, cpu)
                output_bytes = max(output_bytes, _output_bytes(output))
                elapsed = time.monotonic() - started
                usage = {"elapsed_seconds": elapsed, "peak_rss_mb": peak, "io_bytes": io_bytes,
                         "cpu_seconds": cpu_seconds}
                if (elapsed > limits.wall_seconds or peak > limits.memory_mb
                        or cpu_seconds > limits.cpu * limits.wall_seconds
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
        cpu_seconds = max(cpu_seconds, cost.ru_utime + cost.ru_stime)
    return {
        "state": "STOPPED" if stopped or (proc and proc.returncode < 0) else
                 "FINISHED" if proc and proc.returncode == 0 else "FAILED",
        "exit_code": proc.returncode if proc else None, "process_exited": True, "reason": reason,
        "usage": {"elapsed_seconds": time.monotonic() - started,
                  "cpu_seconds": cpu_seconds,
                  "peak_rss_mb": peak, "io_bytes": io_bytes},
        "measurement": {"cpu": "max(wait4 owner, sampled descendant sum); process-tree lower bound",
                        "memory": "max(wait4 owner peak, sampled tree RSS sum); shared pages counted per process",
                        "io": "sampled process-tree logical read/write bytes; lower bound",
                        "cpu_ids": sorted(cpus),
                        "output_bytes_observed": output_bytes,
                        "aggregate_hostile_process_quota": False},
    }
