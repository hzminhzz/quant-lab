"""Hourly pipeline wrapper must delegate idempotently to `warden advance --pending` under flock."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_hourly_pipeline.sh"


def test_run_hourly_pipeline_delegates_to_warden_advance_with_noninteractive_and_flock(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    capture = tmp_path / "captured.json"
    fake_uv = bin_dir / "uv"
    fake_uv.write_text(
        f"""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
Path({str(capture)!r}).write_text(json.dumps({{
    "argv": sys.argv[1:],
    "non_interactive": os.environ.get("WARDEN_NON_INTERACTIVE"),
    "cwd": os.getcwd(),
}}))
print(json.dumps({{"ok": True, "value": {{"mode": "pending", "processed": 0, "advanced": 0, "blocked": 0, "outcomes": []}}, "source": "warden.advance"}}))
""",
        encoding="utf-8",
    )
    fake_uv.chmod(0o700)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "QUANT_LAB_REPO_DIR": str(repo),
        "QUANT_LAB_HOURLY_LOCK_FILE": str(tmp_path / "hourly.lock"),
    }
    proc = subprocess.run(
        ["bash", str(SCRIPT), "--json"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    info = json.loads(capture.read_text(encoding="utf-8"))
    assert info["argv"] == ["run", "--frozen", "warden", "advance", "--pending", "--json"]
    assert info["non_interactive"] == "1"
    assert Path(info["cwd"]).resolve() == repo.resolve()


def test_run_hourly_pipeline_exits_cleanly_when_locked(tmp_path: Path) -> None:
    import fcntl

    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    lock_file = tmp_path / "hourly.lock"
    with lock_file.open("w", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        env = {
            **os.environ,
            "QUANT_LAB_REPO_DIR": str(repo),
            "QUANT_LAB_HOURLY_LOCK_FILE": str(lock_file),
        }
        proc = subprocess.run(
            ["bash", str(SCRIPT), "--json"],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads(proc.stdout.strip())
    assert payload == {"locked": True, "processed": 0, "outcomes": []}
