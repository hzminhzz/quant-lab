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


def _group_alive(group):
    """Observe running members of the owned process group; zombies do no work."""
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "stat").read_text().rpartition(") ")[2].split()
        except FileNotFoundError:
            continue
        if int(fields[2]) == group and fields[0] not in {"Z", "X"}:
            return True
    return False


def run_bounded(command, *, cwd, env, output, limits, heartbeat=None, cpu_ids=None,
                ownership_fd=None, on_spawn=None):
    """Only trusted owners supply commands. Reap before returning or raising."""
    started = time.monotonic()
    peak, io_bytes, output_bytes, cpu_seconds = 0., 0, 0, 0.
    observed = {}
    allowed = os.sched_getaffinity(0)
    cpus = set(cpu_ids) if cpu_ids is not None else set(sorted(allowed)[:limits.cpu])
    if not cpus or not cpus <= allowed or len(cpus) > limits.cpu:
        raise ValueError("invalid reserved CPU set")
    stopped, reason, cost, proc = False, None, None, None
    process_exited = True  # No process exists until Popen succeeds.
    next_heartbeat = 0.
    with (output / "stdout").open("xb") as stdout, (output / "stderr").open("xb") as stderr:
        try:
            # Pin before the command can spawn children; parent-side affinity
            # changes after Popen have an inheritance race.
            pinned = ["/usr/bin/taskset", "--cpu-list", ",".join(map(str, sorted(cpus))), *command]
            if ownership_fd is None:
                if on_spawn is not None:
                    raise ValueError("worker publication requires an ownership lease")
                proc = subprocess.Popen(pinned, cwd=cwd, env=env, stdout=stdout, stderr=stderr,
                                        start_new_session=True, close_fds=True)
                process_exited = False
            else:
                from warden_ops.job_ownership import gated_command, process_identity

                if on_spawn is None:
                    raise ValueError("gated launch requires durable session publication")
                read_fd, write_fd = os.pipe()
                try:
                    proc = subprocess.Popen(gated_command(pinned, read_fd), cwd=cwd, env=env,
                                            stdout=stdout, stderr=stderr, start_new_session=True,
                                            close_fds=True, pass_fds=(read_fd, ownership_fd))
                    process_exited = False
                    os.close(read_fd)
                    read_fd = None
                    on_spawn(process_identity(proc.pid))
                    os.write(write_fd, b"1")
                finally:
                    if read_fd is not None:
                        os.close(read_fd)
                    os.close(write_fd)
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
            if proc is not None:
                try:
                    if proc.returncode is not None and _group_alive(proc.pid):
                        stopped, reason = True, "owned descendants outlived parent"
                    # A normally exiting parent can leave live children behind.
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    deadline = time.monotonic() + 2
                    while True:
                        if proc.returncode is None:
                            pid, status, reaped_usage = os.wait4(proc.pid, os.WNOHANG)
                            if pid:
                                proc.returncode, cost = os.waitstatus_to_exitcode(status), reaped_usage
                        process_exited = proc.returncode is not None and not _group_alive(proc.pid)
                        if process_exited or time.monotonic() >= deadline:
                            break
                        time.sleep(.025)
                except (OSError, ValueError, IndexError) as exc:
                    reason = f"owned process exit unverified: {type(exc).__name__}"
                if not process_exited:
                    stopped = True
                    reason = reason or "owned process exit unverified; retain resource reservation"
    if cost is not None:
        peak = max(peak, cost.ru_maxrss / 1024)
        cpu_seconds = max(cpu_seconds, cost.ru_utime + cost.ru_stime)
    terminated = (proc is not None and proc.returncode is not None and
                  (proc.returncode < 0 or proc.returncode in
                   {128 + signal.SIGKILL, 128 + signal.SIGXCPU, 128 + signal.SIGXFSZ}))
    if terminated and reason is None:
        reason = "owned process reported a termination signal"
    return {
        "state": "STOPPED" if stopped or terminated else
                 "FINISHED" if proc and proc.returncode == 0 else "FAILED",
        "exit_code": proc.returncode if proc else None, "process_exited": process_exited, "reason": reason,
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
