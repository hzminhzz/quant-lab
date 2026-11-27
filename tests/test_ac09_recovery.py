"""Real OS cancellation after canonical admission, and separated runtime identity."""
import time
from concurrent.futures import ThreadPoolExecutor

from tests.conftest import FLAT_MODULE, write_fixture_checksums
from tests.test_ac03_actions import act, prepare
from tests.test_rg05_research_intent import approve, review
from warden_ops.control import Control
from warden_ops.job_runner import run_job


def test_dirty_checkout_never_becomes_the_pinned_active_runtime(cli_repo):
    (cli_repo / "uncommitted.txt").write_text("development only")
    control = Control(cli_repo, runtime_probe=lambda: {
        "status": "AVAILABLE", "release_id": "separately-pinned-fixture", "healthy": True})
    result = control.handle({"schema_version": 1, "operation": "observe"})
    assert result["value"]["checkout"]["dirty"] is True
    assert result["value"]["runtime"]["release_id"] == "separately-pinned-fixture"
    assert result["value"]["authority"]["status"] == "UNVERIFIED"


def test_os_kill_after_reservation_keeps_debt_and_never_replays(tiny_repo):
    source = FLAT_MODULE.replace("from ml4t.backtest import Strategy",
                                 "import time\nfrom ml4t.backtest import Strategy")
    source = source.replace("        w = 0.1 if", "        time.sleep(30)\n        w = 0.1 if")
    f = tiny_repo(module_source=source)
    runner, _, _ = approve(f)
    review(runner, f)
    write_fixture_checksums(f.repo, *[p.relative_to(f.repo).as_posix()
                                    for p in (f.repo / "contracts").rglob("*") if p.is_file()])
    control = Control(f.repo, registry_path=f.registry.db_path)
    plan = prepare(control)
    enqueued = control.handle({"schema_version": 1, "operation": "enqueue", "request_id": "kill-real",
                               "plan": plan, "objective": "retain a real interrupted admission"})
    assert enqueued["value"]["state"] == "QUEUED"
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_job, f.repo, f.registry.db_path, "kill-real")
        deadline = time.monotonic() + 15
        while True:
            record = f.registry.control_request("kill-real")
            if record and record["operation_id"]:
                break
            assert not future.done(), future.result() if future.done() else "worker ended before admission"
            assert time.monotonic() < deadline, "no canonical admission before deadline"
            time.sleep(.025)
        assert record["kind"] == "screen" and record["state"] != "FINALIZED"
        control.handle({"schema_version": 1, "operation": "cancel-job", "job_id": "kill-real"})
        stopped = future.result(timeout=15)
    assert stopped["state"] == "STOPPED_REVIEW"
    supervisor = stopped["details"]["receipt"]["supervisor"]
    assert supervisor["process_exited"] is True
    assert supervisor["exit_code"] == -9
    assert stopped["resources_held"] is False
    assert f.registry.candidate_budget("HYP-TINY")["used"] == 1
    assert act(control, plan, "kill-real")["status"] == "UNCERTAIN"
    assert run_job(f.repo, f.registry.db_path, "kill-real")["claimed"] is False
    with f.registry._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM selection_attempts").fetchone()[0] == 0
