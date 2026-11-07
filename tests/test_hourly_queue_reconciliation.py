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
FAILED_RECEIPT = ('{"ok": true, "value": {"state": "FINALIZED", "eligible": true, '
                  '"verdict": "FAILED"}, "source": "warden.attempt-status"}')


@pytest.mark.parametrize(
    ("output", "exit_code", "receipt", "expected", "initial", "after_screen", "screen_exit", "screen_called", "backtest_called"),
    [
        ('Backtest failed: fixture\nWARDEN_RUN_FAILED', 1, "", "review", "", "STAGE1_PASS", 0, True, True),
        ('{"run_id":"RUN-A","verdict":"REVISE"}\nWARDEN_RUN_COMPLETE', 0, FINAL_RECEIPT, "archive", "", "STAGE1_PASS", 0, True, True),
        ('{"run_id":"RUN-A"}', 124, FINAL_RECEIPT, "archive", "", "STAGE1_PASS", 0, True, True),
        ('{"run_id":"RUN-A","verdict":"REVISE"}', 0, "", "review", "", "STAGE1_PASS", 0, True, True),
        ('{"run_id":"RUN-A","verdict":"REVISE"}', 0, STALE_RECEIPT, "review", "", "STAGE1_PASS", 0, True, True),
        ('WARDEN_RUN_COMPLETE', 0, FINAL_RECEIPT, "archive", "", "STAGE1_PASS", 0, True, True),
        ('', 124, FINAL_RECEIPT, "archive", "", "STAGE1_PASS", 0, True, True),
        ('{"run_id":"RUN-OTHER","verdict":"REVISE"}', 0, FINAL_RECEIPT,
         "review", "", "STAGE1_PASS", 0, True, True),
        ('{"run_id":"RUN-A","verdict":"FAILED"}', 1, FAILED_RECEIPT, "review", "",
         "STAGE1_PASS", 0, True, True),
        ("", 0, "", "archive", "STAGE3_FINAL", "STAGE1_PASS", 0, False, False),
        ('{"run_id":"RUN-A"}', 124, FINAL_RECEIPT, "archive", "STAGE1_PASS", "STAGE1_PASS", 0, False, True),
        ("", 0, "", "failed", "", "STAGE1_REJECT", 1, True, False),
        ("", 0, "", "review", "", "NEW", 1, True, False),
        ("", 0, "", "review", "REQUIRES_RECONCILIATION", "STAGE1_PASS", 0, False, False),
    ],
)
def test_backtest_requires_final_receipt(tmp_path: Path, output: str, exit_code: int,
                                         receipt: str, expected: str, initial: str, after_screen: str,
                                         screen_exit: int, screen_called: bool, backtest_called: bool) -> None:
    repository = tmp_path / "repo"
    (repository / "experiments" / "queue").mkdir(parents=True)
    spec = repository / "experiments" / "queue" / "test.yaml"
    spec.write_text("hypothesis_id: TEST\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_uv = bin_dir / "uv"
    fake_uv.write_text("""#!/usr/bin/env bash
case " $* " in
  *' warden backtest '*) touch "$FIXTURE_BACKTEST_MARKER"; printf '%s\\n' "$FIXTURE_OUTPUT"; exit "$FIXTURE_CODE" ;;
  *' warden attempt-status '*) printf '%s\n' "$FIXTURE_RECEIPT"; exit 0 ;;
  *' warden spec-status '*)
     if [ -f "$FIXTURE_BACKTEST_MARKER" ]; then STATE="$FIXTURE_FINAL_SPEC_STAGE"
     elif [ -n "$FIXTURE_INITIAL_SPEC_STAGE" ]; then STATE="$FIXTURE_INITIAL_SPEC_STAGE"
     elif [ -f "$FIXTURE_SCREEN_MARKER" ]; then STATE="$FIXTURE_SCREEN_STAGE"
     else STATE=NEW; fi
     printf '{"ok": true, "value": {"state": "%s", "reason": "fixture", "run_id": "RUN-A", "verdict": "%s"}, "source": "warden.spec-status"}\n' "$STATE" "$FIXTURE_SPEC_VERDICT"
     exit 0 ;;
  *' warden check-worktree '*) exit 0 ;;
  *' warden screen '*) touch "$FIXTURE_SCREEN_MARKER"; exit "$FIXTURE_SCREEN_EXIT" ;;
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
           "FIXTURE_RECEIPT": receipt,
           "FIXTURE_SCREEN_MARKER": str(tmp_path / "screen-done"),
           "FIXTURE_SCREEN_STAGE": after_screen, "FIXTURE_SCREEN_EXIT": str(screen_exit),
           "FIXTURE_INITIAL_SPEC_STAGE": initial, "FIXTURE_SPEC_VERDICT": "REVISE",
           "FIXTURE_FINAL_SPEC_STAGE": "STAGE3_FINAL",
           "FIXTURE_BACKTEST_MARKER": str(tmp_path / "backtest-called")}
    proc = subprocess.run(["bash", str(SCRIPT)], cwd=repository, env=env,
                          capture_output=True, text=True, timeout=15, check=False)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert (repository / "experiments" / "active" / expected / "test.yaml").is_file(), proc.stdout
    assert not (repository / "experiments" / "queue" / "test.yaml").exists()
    assert (tmp_path / "screen-done").exists() == screen_called
    assert (tmp_path / "backtest-called").exists() == backtest_called
