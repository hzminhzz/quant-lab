"""A12/A19/A20: derived memory retains conflicts and never grants admission."""
import json

from tests.test_ac03_actions import act, prepare, ready


def findings(control, **kwargs):
    return control.handle({"schema_version": 1, "operation": "inspect", "view": "findings", **kwargs})


def test_prior_failure_and_repair_have_provenance_and_conflicts(tiny_repo):
    f, control = ready(tiny_repo, passing=False)
    receipt = act(control, prepare(control))
    assert receipt["recorded_verdict"] == "REJECT"
    lesson = f.repo / ".workspace/memory/LESSONS.md"
    lesson.write_text("# Lessons\n## HYP-TINY repair\nKeep the negative screen. Repair labels before a new review.\n")
    index = f.repo / "docs/research-outcomes/branch-result-index-v1.json"
    doc = json.loads(index.read_text())
    doc["outcomes"].append({
        "hypothesis_id": "HYP-TINY", "contract_id": f.contract.contract_id,
        "disposition": "STAGE1_PASS_UNQUALIFIED", "stage": "STAGE_1_SIGNAL",
        "source_pr": doc["source_refs"][0]["pr"], "evidence_id": "fixture-conflicting-pass",
        "data_sha256": "a" * 64, "summary": "Scoped contrary fixture; unqualified",
        "auto_retry": "DENY_SAME_CONFIG", "strategy_module": "strategies/candidates/fixture.py",
    })
    index.write_text(json.dumps(doc))
    before = f.registry.db_path.read_bytes()
    result = findings(control, hypothesis_id="HYP-TINY")
    assert result["ok"], result
    value = result["value"]
    assert {row["source_kind"] for row in value["findings"]} == {"ledger", "branch_index", "lesson"}
    assert any("Repair labels" in row["claim"] for row in value["findings"])
    assert any(row["contradictions"] for row in value["findings"])
    assert all(row["support"] and row["revalidate"] for row in value["findings"])
    assert all(row["authorizes_research"] is False for row in value["findings"])
    assert value["mandatory_negative_history"] == "unchanged full RG-07 admission"
    assert value["negative_count"] >= 1
    assert f.registry.db_path.read_bytes() == before
    assert f.registry.candidate_budget("HYP-TINY")["used"] == 1


def test_findings_pagination_is_bounded_and_stale_cursor_is_refused(tiny_repo):
    f, control = ready(tiny_repo, passing=False)
    act(control, prepare(control))
    lesson = f.repo / ".workspace/memory/LESSONS.md"
    lesson.write_text("# Lessons\n" + "\n".join(
        f"## HYP-TINY repair {i}\nObserved failure {i}; preserve evidence. " + "detail " * 400
        for i in range(30)))
    first = findings(control, hypothesis_id="HYP-TINY", limit=2)
    assert first["ok"], first
    assert len(json.dumps(first).encode()) <= 12288
    assert first["value"]["complete"] is False
    assert first["value"]["omitted_count"] > 0
    cursor = first["value"]["next_cursor"]
    second = findings(control, hypothesis_id="HYP-TINY", cursor=cursor, limit=2)
    assert second["ok"]
    assert not {r["finding_id"] for r in first["value"]["findings"]} & {
        r["finding_id"] for r in second["value"]["findings"]}
    lesson.write_text(lesson.read_text() + "\n## HYP-TINY later correction\nContrary evidence requires a fresh review.")
    stale = findings(control, hypothesis_id="HYP-TINY", cursor=cursor)
    assert stale["blockers"][0]["classification"] == "STALE_STATE"
    refreshed = findings(control, hypothesis_id="HYP-TINY", limit=1)
    assert refreshed["value"]["negative_count"] == first["value"]["negative_count"]
    assert refreshed["value"]["revision"] != first["value"]["revision"]


def test_missing_memory_is_explicit_and_does_not_create_it(tmp_path):
    from warden_ops.control import Control
    result = findings(Control(tmp_path))
    assert result["ok"], result
    assert len(result["value"]["unavailable_sources"]) == 3
    assert result["value"]["source_completeness"] is False
    assert result["value"]["negative_count"] is None
    assert list(tmp_path.iterdir()) == []
