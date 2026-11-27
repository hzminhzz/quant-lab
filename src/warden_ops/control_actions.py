"""Prepared scientific actions, executed exclusively through existing Warden rules."""
from __future__ import annotations

import hashlib
import json
import os
import time

from warden.codeid import code_closure
from warden.lease import mutation_lease
from warden.registry import ResearchRegistry
from warden.schemas import load_experiment_spec
from warden_ops.control_contracts import ActionPlan, Result, Revision, digest
from warden_ops.integrity import check_worktree, protected_manifest_entries


def safe_file(root, relative):
    target = root / relative
    if ("holdout" in str(relative).lower() or not target.resolve().is_relative_to(root)
            or "holdout" in str(target.resolve()).lower()):
        raise ValueError("protected or external input refused")
    return target


def revisions(control, action):
    root = control.root
    path = safe_file(root, action.spec_path)
    spec = load_experiment_spec(path)
    registry = ResearchRegistry(control.registry_path, readonly=True)
    if registry.schema_version < 8:
        raise ValueError("current governed ledger required; observation never migrates it")
    result = [Revision(source="ledger:" + key, sha256=value)
              for key, value in registry.control_dependencies(spec.hypothesis_id).items()]
    paths = set(protected_manifest_entries(root))
    paths.update({action.spec_path, ".workspace/memory/LESSONS.md",
                  "docs/research-outcomes/branch-result-index-v1.json",
                  ".warden_manifest.sha256"})
    _, closure = code_closure(root, spec.strategy_module)
    paths.update(closure)
    with registry._connect() as conn:
        intent = conn.execute("SELECT intent_json FROM research_intents WHERE hypothesis_id=?",
                              (spec.hypothesis_id,)).fetchone()
    if intent:
        benchmark = json.loads(intent[0])["plan"].get("benchmark_spec")
        if benchmark:
            paths.add(benchmark)
            benchmark_spec = load_experiment_spec(safe_file(root, benchmark))
            _, closure = code_closure(root, benchmark_spec.strategy_module)
            paths.update(closure)
    for relative in sorted(paths):
        target = safe_file(root, relative)
        h = hashlib.sha256()
        with target.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                h.update(block)
        result.append(Revision(source="file:" + relative, sha256=h.hexdigest()))
    result.append(Revision(source="registry-identity", sha256=digest(str(control.registry_path.resolve()))))
    return result


def prepare(control, request):
    now = time.time()
    fields = {"schema_version": 1, "action": request.action.model_dump(),
              "revisions": [x.model_dump() for x in revisions(control, request.action)],
              "resources": request.resources.model_dump(), "created_at": now, "expires_at": now + 900.,
              "authority": "existing Warden admission", "charge": "existing candidate and selection accounting",
              "sealed_access": False}
    plan = ActionPlan(**fields, plan_sha256=digest(fields))
    return Result(ok=True, status="OK", observed_at=now,
                  value={"plan": plan.model_dump(mode="json"), "effects": {
                      "scientific_admission": request.action.kind, "sealed_access": False,
                      "cost_estimate": "UNKNOWN; bounded by declared resource ceiling",
                      "retry": "receipt lookup only after possible admission"},
                      "completion": "FINALIZED existing domain receipt"}).model_dump(mode="json")


def receipt(control, request_id):
    registry = ResearchRegistry(control.registry_path, readonly=True)
    record = registry.control_request(request_id)
    if record is None:
        return control.refuse("TECHNICAL_PRE_ADMISSION", "unknown request", request_id=request_id)
    verdict = record["recorded_verdict"]
    state = record["state"]
    if state == "PRE_ADMISSION_REFUSED":
        return control.refuse("TECHNICAL_PRE_ADMISSION", record["error"], request_id=request_id,
                              remedy="repair the fault and prepare a new request; original has no domain link")
    uncertain = state != "FINALIZED"
    value = {k: v for k, v in record.items() if k not in {"plan", "fingerprint", "actor"}}
    return Result(ok=not uncertain, status="UNCERTAIN" if uncertain else "COMPLETE",
                  observed_at=time.time(), request_id=request_id, recorded_verdict=verdict,
                  current_eligibility="INELIGIBLE" if verdict in {"REJECT", "REJECTED_EARLY", "FAILED"} else "UNKNOWN",
                  value=value, next_actions=["reconcile"] if uncertain else ["observe"]).model_dump(mode="json")


def act(control, request):
    actor = f"uid:{os.getuid()}"
    fields = request.plan.model_dump(mode="json")
    fingerprint = digest({"actor": actor, "plan": fields})
    view = ResearchRegistry(control.registry_path, readonly=True)
    existing = view.control_request(request.request_id)
    if existing:
        if existing["fingerprint"] != fingerprint:
            return control.refuse("INTEGRITY_INCIDENT", "request-ID conflict: changed payload or actor",
                                  request_id=request.request_id)
        return receipt(control, request.request_id)
    if digest({k: v for k, v in fields.items() if k != "plan_sha256"}) != request.plan.plan_sha256:
        return control.refuse("INTEGRITY_INCIDENT", "changed plan digest")
    with mutation_lease(control.registry_path):
        # Repeat lookup under the lease; no duplicate invocation after a lost reply.
        existing = view.control_request(request.request_id)
        if existing:
            if existing["fingerprint"] != fingerprint:
                return control.refuse("INTEGRITY_INCIDENT", "request-ID conflict: changed payload or actor")
            return receipt(control, request.request_id)
        current = {r.source: r.sha256 for r in revisions(control, request.plan.action)}
        expected = {r.source: r.sha256 for r in request.plan.revisions}
        changed = sorted(k for k in current.keys() | expected.keys() if current.get(k) != expected.get(k))
        if changed or request.plan.expires_at <= time.time():
            return control.refuse("STALE_STATE", "refresh: " + ", ".join(changed or ["plan expiry"]),
                                  request_id=request.request_id)
        if not check_worktree(control.root):
            return control.refuse("INTEGRITY_INCIDENT", "worktree integrity failed; source development is not runtime authority")
        registry = ResearchRegistry(control.registry_path)
        registry.start_control_request(request.request_id, fingerprint, fields, actor)
        registry.control_request_id = request.request_id
        from warden.runner import ExperimentRunner
        runner = ExperimentRunner(registry=registry, repo_root=control.root)
        try:
            path = safe_file(control.root, request.plan.action.spec_path)
            if request.plan.action.kind == "screen":
                runner.run_stage1_screen(path)
            else:
                runner.run_spec(path)
        except Exception as exc:  # noqa: BLE001 -- persist any runner fault; never retry science
            registry.record_control_error(request.request_id, str(exc))
        return receipt(control, request.request_id)


def reconcile(control, request):
    with mutation_lease(control.registry_path):
        registry = ResearchRegistry(control.registry_path)
        record = registry.control_request(request.request_id)
        if not record:
            return control.refuse("TECHNICAL_PRE_ADMISSION", "unknown request")
        if record["kind"] == "trial":
            registry.reconcile_attempt(record["operation_id"], confirm_interrupted=True)
        elif record["kind"] == "screen":
            # Read the frozen receipt, never a mutable spec or candidate module.
            with registry._connect() as conn:
                row = conn.execute("SELECT hypothesis_id,contract_id,strategy_module,parameters_json "
                                   "FROM screen_receipts WHERE screen_key=?", (record["operation_id"],)).fetchone()
            registry.reconcile_screen(*row[:3], json.loads(row[3]), confirm_interrupted=True)
        else:
            registry.record_control_error(request.request_id,
                                          "interrupted before domain link; no scientific reservation")
        return receipt(control, request.request_id)
