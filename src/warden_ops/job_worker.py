"""Trusted Warden owner subprocess; this is not an untrusted-code sandbox.

Only a persisted typed action is accepted. Keys are absent from the launch
environment. Scientific writes still go through the canonical Warden owners.
"""
import argparse
import json
from pathlib import Path

from warden.registry import ResearchRegistry
from warden_ops.control import Control


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--registry", type=Path, required=True)
    p.add_argument("--job", required=True)
    p.add_argument("--fence", required=True)
    args = p.parse_args()
    registry = ResearchRegistry(args.registry, readonly=True)
    with registry._connect() as conn:
        row = conn.execute("SELECT payload_json FROM control_jobs WHERE job_id=?", (args.job,)).fetchone()
        event = conn.execute("SELECT state,fence FROM control_job_events WHERE job_id=? "
                             "ORDER BY sequence DESC LIMIT 1", (args.job,)).fetchone()
    if not row or not event or tuple(event) != ("RUNNING", args.fence):
        raise ValueError("job owner fenced before execution")
    result = Control(args.root, registry_path=args.registry).handle(json.loads(row[0]))
    print(json.dumps(result))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
