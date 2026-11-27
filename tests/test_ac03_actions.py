"""A05–A12/A22: durable requests call the existing Warden admission/kernel."""
from copy import deepcopy

import pytest

from tests.conftest import write_fixture_checksums
from tests.test_rg03_candidate_admission import passing_repo
from tests.test_rg05_research_intent import approve, review
from warden_ops.control import Control


def ready(tiny_repo, *, passing=True):
    f = passing_repo(tiny_repo) if passing else tiny_repo()
    runner, _, _ = approve(f)
    review(runner, f)
    paths = [p.relative_to(f.repo).as_posix() for p in (f.repo / "contracts").rglob("*")
             if p.is_file()]
    write_fixture_checksums(f.repo, *paths)
    return f, Control(f.repo, registry_path=f.registry.db_path)


def prepare(control, kind="screen"):
    result = control.handle({"schema_version": 1, "operation": "prepare",
                             "action": {"kind": kind, "spec_path": "spec.yaml"}})
    assert result["ok"], result
    return result["value"]["plan"]


def act(control, plan, request_id="screen-one"):
    return control.handle({"schema_version": 1, "operation": "act",
                           "request_id": request_id, "plan": plan})


def test_lost_response_and_duplicate_request_recover_original_science(tiny_repo):
    f, control = ready(tiny_repo)
    plan = prepare(control)
    first = act(control, plan)
    assert first["status"] == "COMPLETE", first
    assert first["recorded_verdict"] == "PASS"
    original = control.handle({"schema_version": 1, "operation": "receipt",
                               "request_id": "screen-one"})
    assert original["value"]["operation_id"] == first["value"]["operation_id"]
    assert act(control, plan)["value"] == first["value"]
    assert f.registry.candidate_budget("HYP-TINY")["used"] == 1
    changed = deepcopy(plan)
    changed["action"]["kind"] = "backtest"
    conflict = act(control, changed)
    assert conflict["status"] == "REFUSED"
    assert "request-ID conflict" in conflict["blockers"][0]["predicate"]
    full = act(control, prepare(control, "backtest"), "full-one")
    assert full["status"] == "COMPLETE", full
    assert f.registry.candidate_budget("HYP-TINY")["used"] == 1


def test_stale_plan_and_rejected_screen_never_advance(tiny_repo):
    f, control = ready(tiny_repo, passing=False)
    plan = prepare(control)
    f.spec.write_text(f.spec.read_text() + "\n# changed bytes\n")
    stale = act(control, plan)
    assert stale["blockers"][0]["classification"] == "STALE_STATE"
    assert f.registry.candidate_budget("HYP-TINY")["used"] == 0
    first = act(control, prepare(control))
    assert first["recorded_verdict"] == "REJECT", first
    full = act(control, prepare(control, "backtest"), "full-reject")
    assert not full["ok"], full
    assert f.registry.candidate_budget("HYP-TINY")["used"] == 1


def test_crash_after_reservation_retains_request_and_charge(tiny_repo, monkeypatch):
    from warden.evaluation import EvaluationKernel

    f, control = ready(tiny_repo)
    plan = prepare(control)
    calls = []
    original = EvaluationKernel.run
    def crash(*args, **kwargs):
        calls.append(1)
        raise KeyboardInterrupt("killed after reservation")
    monkeypatch.setattr(EvaluationKernel, "run", crash)
    with pytest.raises(KeyboardInterrupt):
        act(control, plan)
    assert calls == [1]
    monkeypatch.setattr(EvaluationKernel, "run", original)
    recovered = act(control, plan)
    assert recovered["status"] == "UNCERTAIN"
    assert f.registry.candidate_budget("HYP-TINY")["used"] == 1
    marked = control.handle({"schema_version": 1, "operation": "reconcile",
                             "request_id": "screen-one", "mode": "mark_interrupted"})
    assert marked["value"]["state"] == "INTERRUPTED_REVIEW"
    assert act(control, plan)["status"] == "UNCERTAIN"


def test_concurrent_duplicate_submission_admits_only_once(tiny_repo):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    f, control = ready(tiny_repo)
    plan = prepare(control)
    barrier = Barrier(2)
    def submit():
        barrier.wait(timeout=10)
        return act(Control(f.repo, registry_path=f.registry.db_path), plan)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(submit) for _ in range(2)]
        results = [future.result(timeout=30) for future in futures]
    assert any(result["status"] == "COMPLETE" for result in results)
    assert all(result["status"] == "COMPLETE" or result["blockers"][0]["classification"] == "BUSY"
               for result in results)
    assert f.registry.candidate_budget("HYP-TINY")["used"] == 1
    assert act(control, plan)["recorded_verdict"] == "PASS"


def test_preparation_never_imports_candidate_or_reads_data(tiny_repo):
    f, control = ready(tiny_repo)
    module = f.repo / "strategies" / "flat.py"
    # Inspect the actual module named by the fixture, not a guessed helper.
    import yaml
    module = f.repo / yaml.safe_load(f.spec.read_text())["strategy_module"]
    marker = f.repo / "imported"
    module.write_text(module.read_text() + f"\nraise RuntimeError({str(marker)!r})\n")
    before = f.registry.candidate_budget("HYP-TINY")["used"]
    result = prepare(control)
    assert result["action"]["kind"] == "screen"
    assert not marker.exists()
    assert f.registry.candidate_budget("HYP-TINY")["used"] == before
