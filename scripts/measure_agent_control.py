"""Reproducible AC-09 engineering measurements; no scoring or sealed inputs.

Print JSON to the caller. Temporary worker inputs/outputs and byte staging are
removed on exit. The owner explicitly supplies an unsealed exploratory file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from warden.registry import ResearchRegistry
from warden_ops.control_contracts import Resources
from warden_ops.jobs import JobQueue
from warden_ops.process_limits import run_bounded
from warden_ops.sandbox_worker import run_worker


def _env():
    return {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            "POLARS_MAX_THREADS": "1", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"}


def _command(args, scratch, name, memory=1024):
    output = scratch / name
    output.mkdir()
    limits = Resources(cpu=1, memory_mb=memory, io_mb=4096, wall_seconds=90)
    command = ["/usr/bin/prlimit", f"--as={memory * 1024 * 1024}", "--cpu=90", "--", *args]
    result = run_bounded(command, cwd=Path.cwd(), env=_env(), output=output, limits=limits,
                         cpu_ids=sorted(os.sched_getaffinity(0))[-1:])
    raw = (output / "stdout").read_text()
    result["output_bytes"] = len(raw.encode())
    try:
        result["result"] = json.loads(raw)
    except ValueError:
        result["result"] = None
        result["stderr"] = (output / "stderr").read_text()[-1000:]
    return result


def _summarize(samples):
    return {"count": len(samples), "elapsed_seconds": [s["usage"]["elapsed_seconds"] for s in samples],
            "median_seconds": statistics.median(s["usage"]["elapsed_seconds"] for s in samples),
            "max_seconds": max(s["usage"]["elapsed_seconds"] for s in samples),
            "max_rss_mb": max(s["usage"]["peak_rss_mb"] for s in samples),
            "max_output_bytes": max(s["output_bytes"] for s in samples),
            "all_finished": all(s["state"] == "FINISHED" for s in samples)}


def observe_measurements(scratch):
    old = [sys.executable, "-m", "warden.cli", "status", "--json"]
    new = [sys.executable, "-m", "warden.cli", "control", "--json"]
    baseline = [_command(old, scratch, f"old-{i}") for i in range(5)]
    idle = [_command(new, scratch, f"idle-{i}") for i in range(5)]
    registry = ResearchRegistry(scratch / "fixtures/registry.db")
    queue = JobQueue(registry)
    resources = Resources(cpu=1, memory_mb=128, io_mb=64, wall_seconds=12)
    stage = scratch / "worker-input"
    stage.mkdir()
    (stage / "program.py").write_text(
        "import time\nend = time.monotonic() + 8\nwhile time.monotonic() < end: pass\n")
    for job in ("worker-one", "worker-two"):
        queue.submit(job, {"fixture": "CPU contention only; no science"}, objective="finish fixed CPU fixture",
                     resources=resources)

    def worker(job):
        claim = queue.claim(job)
        result = run_worker(stage, scratch / job, resources, cpu_ids=claim["details"]["cpu_ids"])
        if result["state"] != "FINISHED":
            raise ValueError(f"contention worker failed: {result}")
        return queue.finish(job, claim["fence"], receipt={"fixture": result["state"]}, usage=result["usage"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, job) for job in ("worker-one", "worker-two")]
        deadline = time.monotonic() + 5
        while not all(queue.status(job)["state"] == "RUNNING" for job in ("worker-one", "worker-two")):
            if time.monotonic() > deadline:
                raise ValueError("contention fixtures did not start")
            time.sleep(.01)
        queue.submit("oversubscribed", {"fixture": "must remain queued"}, objective="shared ceiling witness",
                     resources=Resources(cpu=4, memory_mb=128, io_mb=64, wall_seconds=12))
        rejected = queue.claim("oversubscribed")
        busy = [_command(new, scratch, f"busy-{i}") for i in range(5)]
        workers = [future.result() for future in futures]
    return {"baseline_status": _summarize(baseline), "control_idle": _summarize(idle),
            "control_contended": _summarize(busy), "shared_cpu_refusal": rejected["state"],
            "workers": workers, "scientific_evaluator_calls": 0,
            "note": "five cold processes per condition; no cache or speedup claim"}


def appliance_measurements(home, scratch):
    """Matched cold launcher requests, idle and with two bounded CPU contenders."""
    home = Path(home).resolve(strict=True)
    launcher = home / "warden"
    cpus = sorted(os.sched_getaffinity(0))[-2:]
    if len(cpus) != 2:
        raise ValueError("matched benchmark requires two available CPUs")
    limits = Resources(cpu=2, memory_mb=2048, io_mb=4096, wall_seconds=60)
    samples = {"idle": [], "contended": []}
    caller = scratch / "caller"
    caller.mkdir()
    argv = [str(launcher), "control", "--json"]
    for condition, rows in samples.items():
        for number in range(5):
            output = scratch / f"{condition}-{number}"
            output.mkdir()
            burners = []
            try:
                if condition == "contended":
                    for cpu in cpus:
                        burners.append(subprocess.Popen(
                            ["/usr/bin/taskset", "--cpu-list", str(cpu), sys.executable, "-c",
                             "import time; end=time.monotonic()+60\nwhile time.monotonic()<end: pass"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
                result = run_bounded(argv, cwd=caller, env=_env(), output=output,
                                     limits=limits, cpu_ids=cpus)
            finally:
                for burner in burners:
                    if burner.poll() is None:
                        burner.terminate()
                    burner.wait(timeout=5)
            raw = (output / "stdout").read_bytes()
            value = json.loads(raw)["value"]
            if (result["state"] != "FINISHED" or not value["runtime"]["healthy"]
                    or value["scientific_writes"] or value["evaluator_calls"] or value["sealed_access"]
                    or len(raw) > 12288):
                raise ValueError("observation benchmark failed health/inertness/output gate")
            rows.append({**result["usage"], "output_bytes": len(raw),
                         "release_id": value["runtime"]["release_id"]})
    # Source inventory size is deterministic metadata, not a fabricated I/O
    # counter. The request profile below measures actual reads separately.
    release = samples["idle"][0]["release_id"]
    candidate = home / "releases" / release
    runtime = home / "runtimes" / release
    manifest = json.loads((candidate / "candidate.json").read_text())
    sizes = {name: sum((path / rel).stat().st_size for rel in manifest["inventory"])
             for name, path in (("candidate", candidate), ("runtime", runtime))}
    trace = scratch / "read.trace"
    subprocess.run(["/usr/bin/strace", "-f", "-yy", "-e", "trace=read,pread64",
                    "-o", str(trace), *argv], cwd=caller, env=_env(),
                   capture_output=True, check=True, timeout=120)
    import re

    read_bytes = {"candidate": 0, "runtime": 0, "verification": 0}
    for line in trace.read_text(errors="replace").splitlines():
        match = re.search(r"= (\d+)(?:\s|$)", line)
        if match:
            for name, path in (("candidate", candidate), ("runtime", runtime),
                               ("verification", home / "verification" / release)):
                if str(path) + "/" in line:
                    read_bytes[name] += int(match.group(1))
                    break
    return {"release_id": release, "source_commit": manifest["source"]["commit"],
            "cpu_ids": cpus, "samples": samples,
            "median_seconds": {name: statistics.median(r["elapsed_seconds"] for r in rows)
                               for name, rows in samples.items()},
            "source_inventory_bytes": sizes, "separate_strace_logical_read_bytes": read_bytes,
            "limits": "five new processes per condition; warm host page cache, no cache flush; "
                      "sampled process-tree CPU/RSS/I/O lower bounds; strace separate from timing; "
                      "path-attributed reads include non-hash reads"}


def byte_method(path, method):
    # Reject protected names and every symlink before opening any bytes.
    path = Path(path).absolute()
    if ("holdout" in str(path).lower() or "/data/exploratory/" not in str(path)
            or any(p.is_symlink() for p in (path, *path.parents))):
        raise ValueError("explicit regular unsealed exploratory input required")
    size = path.stat().st_size
    if size > 512 * 1024 * 1024:
        raise ValueError("byte benchmark exceeds 512 MiB engineering ceiling")
    h = hashlib.sha256()
    rows = None
    if method.startswith("buffer"):
        payload = path.read_bytes()
        h.update(payload)
        if method.endswith("parse"):
            from warden.market_data import read_parquet_bytes
            rows = read_parquet_bytes(payload).height
    else:
        with tempfile.TemporaryFile("w+b") as staged, path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                h.update(block)
                staged.write(block)
            staged.seek(0)
            if method.endswith("parse"):
                import polars as pl
                rows = pl.read_parquet(staged).height
    return {"method": method, "bytes_read": size, "bytes_hashed": size,
            "bytes_staged": size if method.startswith("staged") else 0,
            "sha256": h.hexdigest(), "rows_parsed": rows, "scoring": False,
            "cache_hits": 0, "sealed_access": False}


def byte_measurements(path, scratch):
    results = []
    for method in ("buffer", "staged", "buffer-parse", "staged-parse"):
        results.append(_command([sys.executable, "-m", "scripts.measure_agent_control",
                                 "--child-method", method, "--path", str(path)], scratch, method, memory=4096))
    hashes = {r["result"]["sha256"] for r in results if r["result"]}
    rows = {r["result"]["rows_parsed"] for r in results if r["result"] and r["result"]["rows_parsed"] is not None}
    return {"path": str(path), "measurements": results, "hashes_agree": len(hashes) == 1,
            "parsed_row_counts_agree": len(rows) == 1,
            "comparison_only": "staging experiment; production gateway is unchanged",
            "limits": "sequential warm filesystem cache; parser includes validation overhead; no model memory measured"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--measure", choices=("observe", "bytes", "appliance"))
    parser.add_argument("--path", type=Path)
    parser.add_argument("--home", type=Path)
    parser.add_argument("--child-method", choices=("buffer", "staged", "buffer-parse", "staged-parse"))
    args = parser.parse_args()
    if args.child_method:
        print(json.dumps(byte_method(args.path, args.child_method)))
        return
    with tempfile.TemporaryDirectory(prefix="warden-ac-measure-") as directory:
        scratch = Path(directory)
        if args.measure == "appliance":
            result = appliance_measurements(args.home, scratch)
        else:
            result = observe_measurements(scratch) if args.measure == "observe" else byte_measurements(args.path, scratch)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
