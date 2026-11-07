"""Hourly scheduler must never archive a command without a final Warden receipt."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_hourly_pipeline.sh"
FINAL_RECEIPT = ('{"ok": true, "value": {"state": "FINALIZED", "eligible": true, '
                 '"verdict": "REVISE"}, "source": "warden.attempt-status"}')
STALE_RECEIPT = ('{"ok": true, "value": {"state": "EVIDENCE_DRIFT", "eligible": false, '
                 '"verdict": "REVISE"}, "source": "warden.attempt-status"}')


@pytest.mark.parametrize(
    ("output", "exit_code", "receipt", "expected"),
    [
        ('Backtest failed: fixture\nWARDEN_RUN_FAILED', 1, "", "review"),
        ('{"run_id":"RUN-A","verdict":"REVISE"}\nWARDEN_RUN_COMPLETE', 0, FINAL_RECEIPT, "archive"),
        ('{"run_id":"RUN-A"}', 124, FINAL_RECEIPT, "archive"),
        ('{"run_id":"RUN-A","verdict":"REVISE"}', 0, "", "review"),
        ('{"run_id":"RUN-A","verdict":"REVISE"}', 0, STALE_RECEIPT, "review"),
        ('WARDEN_RUN_COMPLETE', 0, FINAL_RECEIPT, "review"),
    ],
)
def test_backtest_requires_final_receipt(tmp_path: Path, output: str, exit_code: int,
                                         receipt: str, expected: str) -> None:
    repository = tmp_path / "repo"
    (repository / "experiments" / "queue").mkdir(parents=True)
    spec = repository / "experiments" / "queue" / "test.yaml"
    spec.write_text("hypothesis_id: TEST\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_uv = bin_dir / "uv"
    fake_uv.write_text("""#!/usr/bin/env bash
case " $* " in
  *' warden backtest '*) printf '%s\\n' "$FIXTURE_OUTPUT"; exit "$FIXTURE_CODE" ;;
  *' warden attempt-status '*) printf '%s\\n' "$FIXTURE_RECEIPT"; exit 0 ;;
  *' warden check-worktree '*) exit 0 ;;
  *' warden screen '*) exit 0 ;;
  *' warden verify '*) exit 0 ;;
  *research_outcome_memory*) exit 0 ;;
  *' warden handoff '*) exit 0 ;;
esac
exit 5
""")
    fake_uv.chmod(0o700)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
           "QUANT_LAB_REPO_DIR": str(repository),
           "QUANT_LAB_HOURLY_LOCK_FILE": str(tmp_path / "hourly.lock"),
           "FIXTURE_OUTPUT": output, "FIXTURE_CODE": str(exit_code),
           "FIXTURE_RECEIPT": receipt}
    proc = subprocess.run(["bash", str(SCRIPT)], cwd=repository, env=env,
                          capture_output=True, text=True, timeout=15, check=False)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert (repository / "experiments" / "active" / expected / "test.yaml").is_file(), proc.stdout
    assert not (repository / "experiments" / "queue" / "test.yaml").exists()
