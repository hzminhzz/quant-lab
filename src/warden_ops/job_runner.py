"""Bounded supervisor for an existing trusted Warden owner, not an untrusted worker.

Only the Warden owner writes canonical science. Stateless workers use
sandbox_worker; this launcher does not claim to isolate arbitrary strategy
imports in the existing owner. Protected installation must establish that trust.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import ExitStack
from pathlib import Path

from warden_ops.control import Control, current_registry
from warden_ops.control_contracts import Resources
from warden_ops.jobs import JobQueue
from warden_ops.process_limits import run_bounded


def run_job(root, registry_path, job_id):
    from warden_ops.runtime_boundary import operational_admission

    from warden_ops.job_ownership import job_lease, owner_identity

    root = Path(root).resolve()
    with ExitStack() as stack:
        with operational_admission(root, registry_path):
            registry = current_registry(registry_path, writable=True)
            queue = JobQueue(registry)
            current = queue.status(job_id)
            if current["state"] != "QUEUED":
                return current
            fd = stack.enter_context(job_lease(registry_path, job_id, create=True))
            claim = queue.claim(job_id, ownership=owner_identity(fd))
        if not claim["claimed"]:
            return claim
        return _execute_claim(root, registry_path, job_id, queue, claim, fd)


def _execute_claim(root, registry_path, job_id, queue, claim, fd):
    fence, limits = claim["fence"], Resources.model_validate(claim["resources"])
    output = root / ".workspace" / "jobs" / f"{job_id}-{fence}"
    env = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "QUANT_LAB_APPLIANCE_HOME")
           if k in os.environ}
    env.update(PYTHONPATH=str(Path(__file__).resolve().parents[1]), WARDEN_NON_INTERACTIVE="1",
               PYTHONDONTWRITEBYTECODE="1",
               POLARS_MAX_THREADS=str(limits.cpu), OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
    command = ["prlimit", f"--as={limits.memory_mb * 1024 * 1024}",
               f"--cpu={limits.cpu * limits.wall_seconds}", f"--fsize={limits.io_mb * 1024 * 1024}",
               "--", sys.executable, "-m", "warden_ops.job_worker", "--root", str(root),
               "--registry", str(Path(registry_path).resolve()), "--job", job_id, "--fence", fence]
    last_heartbeat = [0.]

    def heartbeat(usage):
        if queue.status(job_id)["state"] != "RUNNING":
            return False
        if usage["elapsed_seconds"] - last_heartbeat[0] >= 1:
            result = queue.heartbeat(job_id, fence, progress={"completed": 0, "total": 1}, usage=usage)
            last_heartbeat[0] = usage["elapsed_seconds"]
            return result["state"] == "RUNNING"
        return True

    try:
        if any(p.is_symlink() for p in (output, *output.parents)):
            raise ValueError("job output symlink refused")
        output.mkdir(parents=True, exist_ok=False, mode=0o700)
        result = run_bounded(command, cwd=root, env=env, output=output, limits=limits, heartbeat=heartbeat,
                             cpu_ids=claim["details"]["cpu_ids"], ownership_fd=fd,
                             on_spawn=lambda identity: queue.register_worker(job_id, fence, identity))
    except (OSError, ValueError) as exc:
        # Setup/launch failures have no child; unverified exit is returned explicitly.
        # Neither outcome refunds any existing scientific admission.
        result = {"state": "FAILED", "usage": {}, "process_exited": True, "reason": str(exc)[:300]}
    receipt = Control(root, registry_path=registry_path).handle(
        {"schema_version": 1, "operation": "receipt", "request_id": claim["payload"]["request_id"]})
    receipt["supervisor"] = {k: v for k, v in result.items() if k != "usage"}
    if (result["state"] != "FINISHED" or result.get("process_exited") is not True
            or queue.status(job_id)["state"] != "RUNNING"):
        return queue.confirm_stopped(job_id, fence, receipt=receipt, usage=result["usage"],
                                     process_exited=result.get("process_exited") is True)
    return queue.finish(job_id, fence, receipt=receipt, usage=result["usage"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--job", required=True)
    args = parser.parse_args()
    print(json.dumps(run_job(args.root, args.registry, args.job)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
