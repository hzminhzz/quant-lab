"""Median wall time of a bare child import: warden.runner (HEAD child) vs warden.strategy_loader.

Run with the core under test on PYTHONPATH.
"""
import json
import subprocess
import sys
import time


def median_import(module: str, reps: int = 9) -> dict:
    times = []
    for _ in range(reps):
        t0 = time.perf_counter()
        proc = subprocess.run([sys.executable, "-c", f"import {module}"], capture_output=True, text=True)
        times.append(time.perf_counter() - t0)
        if proc.returncode != 0:
            return {"module": module, "error": proc.stderr.strip()[:200]}
    times.sort()
    return {"module": module, "reps": reps, "median_s": round(times[len(times) // 2], 3),
            "min_s": round(times[0], 3), "max_s": round(times[-1], 3)}


out = [median_import("warden.runner"), median_import("warden.strategy_loader")]
print(json.dumps(out, indent=1))
