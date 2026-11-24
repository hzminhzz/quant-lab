"""Typed queue coordinator for the Warden hourly research pipeline.

Replaces brittle shell ``grep``/``cut`` parsing with strict JSON-envelope
deserialization and deterministic crash-recovery routing across ``NEW``,
``STAGE1_PASS``, ``STAGE1_REJECT``, ``STAGE3_FINAL``, ``REQUIRES_RECONCILIATION``,
and ``BUSY`` (exit 75) states.

The coordinator never acquires ``warden.lease.mutation_lease`` directly; each
mutating ``warden`` CLI subprocess acquires and releases that non-reentrant
single-writer lease on its own.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class QueueRoute(str, Enum):
    """Destination bucket for a queued experiment specification."""

    ARCHIVE = "archive"
    FAILED = "failed"
    REVIEW = "review"
    DEFER = "defer"


@dataclass(frozen=True)
class CommandResult:
    """Captured result from a bounded subprocess invocation."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False

    @property
    def is_busy(self) -> bool:
        """True when Warden rejected the mutation due to active registry lease contention."""
        if self.exit_code == 75:
            return True
        for obj in parse_json_objects(self.stdout):
            if obj.get("source") == "warden.mutation-lease":
                value = obj.get("value")
                if isinstance(value, dict) and value.get("status") == "BUSY":
                    return True
        return False


@dataclass(frozen=True)
class SpecStatus:
    """Typed projection of a ``warden.spec-status`` JSON envelope."""

    state: str
    reason: str = ""
    run_id: str | None = None
    verdict: str | None = None


@dataclass(frozen=True)
class AttemptReceipt:
    """Typed projection of a ``warden.attempt-status`` JSON envelope."""

    run_id: str | None
    state: str
    eligible: bool
    verdict: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class SpecOutcome:
    """Structured routing outcome for a single queued experiment spec."""

    spec: str
    route: QueueRoute
    reason: str
    run_id: str | None = None
    verdict: str | None = None
    screen_called: bool = False
    backtest_called: bool = False
    audit_called: bool = False


CommandRunner = Callable[..., CommandResult]


def parse_json_objects(raw: str) -> list[dict[str, Any]]:
    """Extract every complete top-level JSON object from mixed stdout/stderr text."""
    if not raw:
        return []
    decoder = json.JSONDecoder()
    objects: list[dict[str, Any]] = []
    idx = 0
    length = len(raw)
    while idx < length:
        pos = raw.find("{", idx)
        if pos == -1:
            break
        try:
            obj, end = decoder.raw_decode(raw, pos)
        except ValueError:
            idx = pos + 1
            continue
        if isinstance(obj, dict):
            objects.append(obj)
        idx = end
    return objects


def parse_envelope(raw: str, expected_source: str) -> dict[str, Any] | None:
    """Return the last valid ``value`` dict matching ``expected_source``."""
    matched: dict[str, Any] | None = None
    for obj in parse_json_objects(raw):
        if obj.get("source") == expected_source and isinstance(obj.get("value"), dict):
            matched = obj["value"]
    return matched


def _nonempty_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def parse_spec_status(raw: str) -> SpecStatus | None:
    """Deserialize a ``warden spec-status --json`` envelope, failing closed on corruption."""
    value = parse_envelope(raw, "warden.spec-status")
    if value is None:
        return None
    state = _nonempty_str(value.get("state"))
    if state is None:
        return None
    reason = value.get("reason")
    return SpecStatus(
        state=state,
        reason=reason if isinstance(reason, str) else "",
        run_id=_nonempty_str(value.get("run_id")),
        verdict=_nonempty_str(value.get("verdict")),
    )


def parse_attempt_receipt(raw: str) -> AttemptReceipt | None:
    """Deserialize a ``warden attempt-status --json`` envelope, failing closed on corruption."""
    value = parse_envelope(raw, "warden.attempt-status")
    if value is None:
        return None
    state = _nonempty_str(value.get("state"))
    eligible = value.get("eligible")
    if state is None or not isinstance(eligible, bool):
        return None
    reason = value.get("reason")
    return AttemptReceipt(
        run_id=_nonempty_str(value.get("run_id")),
        state=state,
        eligible=eligible,
        verdict=_nonempty_str(value.get("verdict")),
        reason=reason if isinstance(reason, str) else "",
    )


def parse_backtest_output(raw: str) -> tuple[str | None, str | None]:
    """Extract optional ``run_id`` and ``verdict`` from ``warden backtest --json`` output."""
    run_id: str | None = None
    verdict: str | None = None
    for obj in parse_json_objects(raw):
        if obj.get("source") == "warden.mutation-lease":
            continue
        payload = obj["value"] if isinstance(obj.get("value"), dict) and "source" in obj else obj
        found_run = _nonempty_str(payload.get("run_id"))
        found_verdict = _nonempty_str(payload.get("verdict"))
        if found_run is not None:
            run_id = found_run
        if found_verdict is not None:
            verdict = found_verdict
    return run_id, verdict


def _decode_stream(data: str | bytes | None) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data


def default_command_runner(
    argv: list[str], *, cwd: Path, env: dict[str, str], timeout: int
) -> CommandResult:
    """Run one bounded subprocess without shell interpolation."""
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            exit_code=124,
            stdout=_decode_stream(exc.stdout),
            stderr=_decode_stream(exc.stderr),
            timed_out=True,
        )
    return CommandResult(exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


class HourlyQueueCoordinator:
    """Deterministic queue orchestrator backed by Warden's JSON state projections."""

    def __init__(
        self,
        repo_dir: Path,
        *,
        env: dict[str, str] | None = None,
        runner: CommandRunner = default_command_runner,
        command_timeout_sec: int = 300,
        backtest_timeout_sec: int | None = None,
    ) -> None:
        self.repo_dir = Path(repo_dir)
        base_env = dict(os.environ if env is None else env)
        base_env["WARDEN_NON_INTERACTIVE"] = "1"
        self.env = base_env
        self.runner = runner
        self.command_timeout_sec = max(int(command_timeout_sec), 1)
        if backtest_timeout_sec is None:
            raw_bt = self.env.get("QUANT_LAB_BACKTEST_TIMEOUT_SEC", "1800")
            try:
                parsed_bt = int(raw_bt)
            except ValueError:
                parsed_bt = 1800
            self.backtest_timeout_sec = max(parsed_bt, 1)
        else:
            self.backtest_timeout_sec = max(int(backtest_timeout_sec), 1)

    def _run_cmd(self, argv: list[str], *, timeout: int | None = None) -> CommandResult:
        return self.runner(
            argv,
            cwd=self.repo_dir,
            env=self.env,
            timeout=self.command_timeout_sec if timeout is None else max(int(timeout), 1),
        )

    def _run_warden(self, *args: str, timeout: int | None = None) -> CommandResult:
        return self._run_cmd(["uv", "run", "warden", *args], timeout=timeout)

    def _run_backtest(self, spec_rel: str) -> CommandResult:
        backtest_argv = ["uv", "run", "warden", "backtest", spec_rel, "--json"]
        herdr_script = self.repo_dir / "scripts" / "herdr_dispatch.sh"
        herdr_requested = (
            self.env.get("HERDR_ENV") == "1"
            and (self.env.get("QUANT_LAB_USE_HERDR") == "1" or bool(self.env.get("HERDR_PANE")))
        )
        if (
            herdr_requested
            and shutil.which("herdr", path=self.env.get("PATH"))
            and herdr_script.is_file()
        ):
            pane = self.env.get("HERDR_PANE", "w1:p2")
            dispatch_argv = [
                "bash",
                str(herdr_script),
                "--pane",
                pane,
                "--timeout",
                str(self.backtest_timeout_sec * 1000),
                shlex.join(backtest_argv),
            ]
            return self._run_cmd(dispatch_argv, timeout=self.backtest_timeout_sec + 30)
        return self._run_cmd(backtest_argv, timeout=self.backtest_timeout_sec)

    def _ensure_dirs(self) -> None:
        for rel in (
            "experiments/queue",
            "experiments/active/failed",
            "experiments/active/archive",
            "experiments/active/review",
            ".workspace/hypotheses/queue",
            ".workspace/hypotheses/registered",
            ".workspace/hypotheses/failed",
        ):
            (self.repo_dir / rel).mkdir(parents=True, exist_ok=True)

    def _move_spec(self, spec: Path, route: QueueRoute) -> None:
        if route is QueueRoute.DEFER:
            return
        dest_dir = self.repo_dir / "experiments" / "active" / route.value
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(spec), str(dest_dir / spec.name))

    def _audit_candidate(self, run_id: str) -> tuple[QueueRoute, str]:
        audit = self._run_warden("audit-check", "--run-id", run_id, "--json")
        if audit.is_busy:
            return QueueRoute.DEFER, f"BUSY during audit-check for {run_id}"
        if audit.exit_code != 0:
            return QueueRoute.REVIEW, f"Audit incomplete/negative for {run_id} (exit={audit.exit_code})"
        return QueueRoute.ARCHIVE, f"Candidate {run_id} audited and archived"

    def _route_stage3_verdict(self, run_id: str, verdict: str) -> tuple[QueueRoute, str, bool]:
        if verdict == "CANDIDATE_FOR_QUALIFICATION":
            route, reason = self._audit_candidate(run_id)
            return route, reason, True
        return QueueRoute.ARCHIVE, f"Finalized {run_id} ({verdict})", False

    def _check_outcome_memory(self, spec_rel: str) -> tuple[bool, str]:
        mem = self._run_cmd(
            ["uv", "run", "--frozen", "python", "-m", "scripts.research_outcome_memory", "--check-spec", spec_rel]
        )
        if mem.exit_code != 0:
            return False, f"research_outcome_memory blocked spec (exit={mem.exit_code})"
        if mem.stdout.strip():
            objs = parse_json_objects(mem.stdout)
            if not objs or objs[-1].get("eligible") is not True:
                return False, "research_outcome_memory reported ineligible or malformed response"
        return True, "ok"

    def _register_queued_hypotheses(self) -> None:
        hyp_dir = self.repo_dir / ".workspace" / "hypotheses" / "queue"
        hyp_files = sorted(
            p for p in hyp_dir.iterdir()
            if p.is_file() and p.suffix.lower() in {".md", ".yaml", ".yml"}
        ) if hyp_dir.is_dir() else []
        for hyp in hyp_files:
            rel = hyp.relative_to(self.repo_dir).as_posix()
            res = self._run_warden("register-hypothesis", rel, "--json")
            if res.is_busy:
                print(f"[BUSY] Deferring hypothesis registration: {rel}", file=sys.stderr)
                continue
            target = (
                self.repo_dir / ".workspace" / "hypotheses"
                / ("registered" if res.exit_code == 0 else "failed")
                / hyp.name
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(hyp), str(target))

    def process_spec(self, spec: Path) -> SpecOutcome:
        """Evaluate one queued YAML spec and move/defer it strictly from JSON evidence."""
        spec_rel = spec.relative_to(self.repo_dir).as_posix()
        screen_called = False
        backtest_called = False
        audit_called = False

        def finish(
            route: QueueRoute,
            reason: str,
            *,
            run_id: str | None = None,
            verdict: str | None = None,
        ) -> SpecOutcome:
            self._move_spec(spec, route)
            return SpecOutcome(
                spec=spec_rel,
                route=route,
                reason=reason,
                run_id=run_id,
                verdict=verdict,
                screen_called=screen_called,
                backtest_called=backtest_called,
                audit_called=audit_called,
            )

        status_res = self._run_warden("spec-status", spec_rel, "--json")
        initial = parse_spec_status(status_res.stdout)
        if initial is None:
            return finish(QueueRoute.REVIEW, "Warden spec-status unavailable or malformed")

        if initial.state == "STAGE3_FINAL":
            if not initial.run_id or not initial.verdict or initial.verdict in {"FAILED", "REJECTED_EARLY"}:
                return finish(
                    QueueRoute.REVIEW,
                    f"Recorded non-archivable Stage 3 state ({initial.verdict})",
                    run_id=initial.run_id,
                    verdict=initial.verdict,
                )
            route, reason, audit_called = self._route_stage3_verdict(initial.run_id, initial.verdict)
            return finish(route, reason, run_id=initial.run_id, verdict=initial.verdict)

        if initial.state == "STAGE1_REJECT":
            return finish(QueueRoute.FAILED, "Existing Warden Stage 1 rejection")

        if initial.state not in {"NEW", "STAGE1_PASS"}:
            return finish(
                QueueRoute.REVIEW,
                f"Requires reconciliation: {initial.state} ({initial.reason})",
                run_id=initial.run_id,
                verdict=initial.verdict,
            )

        if initial.state == "NEW":
            mem_ok, mem_reason = self._check_outcome_memory(spec_rel)
            if not mem_ok:
                return finish(QueueRoute.REVIEW, mem_reason)

            verify_res = self._run_warden("verify", spec_rel, "--json")
            if verify_res.is_busy:
                return finish(QueueRoute.DEFER, "BUSY during warden verify")
            if verify_res.exit_code != 0:
                return finish(QueueRoute.REVIEW, f"Verify failed (exit={verify_res.exit_code})")

            screen_called = True
            screen_res = self._run_warden("screen", spec_rel, "--json")
            if screen_res.is_busy:
                return finish(QueueRoute.DEFER, "BUSY during warden screen")

            after_screen = parse_spec_status(self._run_warden("spec-status", spec_rel, "--json").stdout)
            if after_screen is None:
                return finish(QueueRoute.REVIEW, "Stage 1 result could not be verified")
            if after_screen.state == "STAGE1_REJECT":
                return finish(QueueRoute.FAILED, f"Stage 1 rejected (exit={screen_res.exit_code})")
            if after_screen.state != "STAGE1_PASS":
                return finish(
                    QueueRoute.REVIEW,
                    f"Unexpected post-screen state {after_screen.state} (exit={screen_res.exit_code})",
                )

        backtest_called = True
        bt_res = self._run_backtest(spec_rel)
        if bt_res.is_busy:
            return finish(QueueRoute.DEFER, "BUSY during warden backtest")
        cli_run_id, cli_verdict = parse_backtest_output(bt_res.stdout)

        after_bt = parse_spec_status(self._run_warden("spec-status", spec_rel, "--json").stdout)
        if after_bt is None or after_bt.state != "STAGE3_FINAL" or not after_bt.run_id:
            return finish(
                QueueRoute.REVIEW,
                f"No finalized Stage 3 trial after backtest (exit={bt_res.exit_code})",
            )
        if cli_run_id is not None and cli_run_id != after_bt.run_id:
            return finish(
                QueueRoute.REVIEW,
                f"CLI run_id {cli_run_id} disagrees with ledger run_id {after_bt.run_id}",
                run_id=after_bt.run_id,
            )

        receipt = parse_attempt_receipt(
            self._run_warden("attempt-status", "--run-id", after_bt.run_id, "--json").stdout
        )
        if (
            receipt is None
            or receipt.state != "FINALIZED"
            or not receipt.eligible
            or not receipt.verdict
        ):
            return finish(
                QueueRoute.REVIEW,
                f"Attempt receipt not finalized/eligible for {after_bt.run_id}",
                run_id=after_bt.run_id,
            )
        if cli_verdict is not None and cli_verdict != receipt.verdict:
            return finish(
                QueueRoute.REVIEW,
                f"CLI verdict {cli_verdict} disagrees with ledger verdict {receipt.verdict}",
                run_id=after_bt.run_id,
                verdict=receipt.verdict,
            )
        if receipt.verdict in {"FAILED", "REJECTED_EARLY"}:
            return finish(
                QueueRoute.REVIEW,
                f"Recorded incomplete/early verdict {receipt.verdict} for {after_bt.run_id}",
                run_id=after_bt.run_id,
                verdict=receipt.verdict,
            )

        route, reason, audit_called = self._route_stage3_verdict(after_bt.run_id, receipt.verdict)
        return finish(route, reason, run_id=after_bt.run_id, verdict=receipt.verdict)

    def run(self) -> tuple[int, list[SpecOutcome]]:
        """Run one complete non-interactive hourly queue cycle."""
        self._ensure_dirs()
        wt = self._run_warden("check-worktree")
        if wt.exit_code != 0:
            if wt.stderr:
                print(wt.stderr, file=sys.stderr, end="" if wt.stderr.endswith("\n") else "\n")
            return wt.exit_code, []

        self._register_queued_hypotheses()

        queue_dir = self.repo_dir / "experiments" / "queue"
        specs = sorted(
            p for p in queue_dir.iterdir()
            if p.is_file() and p.suffix.lower() in {".yaml", ".yml"}
        ) if queue_dir.is_dir() else []
        if not specs:
            return 0, []

        outcomes = [self.process_spec(spec) for spec in specs]
        handoff = self._run_warden(
            "handoff",
            "--notes",
            f"Automated hourly execution completed: {len(outcomes)} spec(s) processed.",
        )
        if handoff.is_busy:
            print("[BUSY] Handoff deferred because another Warden writer holds the lease.", file=sys.stderr)
        return 0, outcomes


def _acquire_hourly_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hourly_queue_coordinator",
        description="Typed queue coordinator for the Warden hourly pipeline.",
    )
    parser.add_argument("--json", action="store_true", help="Emit structured JSON summary.")
    args = parser.parse_args(argv)

    os.environ["WARDEN_NON_INTERACTIVE"] = "1"
    repo_dir = Path(os.environ.get("QUANT_LAB_REPO_DIR", "/home/quant/dev/quant-lab"))
    lock_path = Path(os.environ.get("QUANT_LAB_HOURLY_LOCK_FILE", "/tmp/quant_lab_hourly.lock"))

    lock_handle = _acquire_hourly_lock(lock_path)
    if lock_handle is None:
        if args.json:
            print(json.dumps({"locked": True, "processed": 0, "outcomes": []}))
        else:
            print("Job already running. Exiting cleanly.")
        return 0

    try:
        coordinator = HourlyQueueCoordinator(repo_dir)
        rc, outcomes = coordinator.run()
    finally:
        lock_handle.close()

    if args.json:
        print(
            json.dumps(
                {
                    "locked": False,
                    "exit_code": rc,
                    "processed": len(outcomes),
                    "outcomes": [
                        {**asdict(o), "route": o.route.value} for o in outcomes
                    ],
                }
            )
        )
    else:
        for outcome in outcomes:
            print(f"[{outcome.route.value.upper()}] {outcome.spec}: {outcome.reason}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
