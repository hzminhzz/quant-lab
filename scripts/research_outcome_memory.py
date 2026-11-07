"""Read-only Warden research memory and duplicate-screen preflight.

Source of truth: canonical append-only Warden registry. Additional immutable
outcomes on unmerged research PRs are indexed in docs/research-outcomes/.
The sidecar NEVER writes SQLite, resets budgets, alters Warden protected
files, signs a contract or reads holdouts.

Usage:
  uv run --frozen python -m scripts.research_outcome_memory --inventory
  uv run --frozen python -m scripts.research_outcome_memory --check-spec experiments/queue/foo.yaml

--check-spec exit codes: 0 NEW and eligible for standard Warden checks;
1 blocked previously tested, pending qualification, or known-data problem;
2 unavailable/inconsistent evidence (fail closed).
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import yaml

INDEX = Path("docs/research-outcomes/branch-result-index-v1.json")
REGISTRY = Path("registry/registry.db")
NEGATIVE = {"STAGE1_REJECT", "STAGE3_REVISE", "PREFLIGHT_FAIL"}
PENDING = {"STAGE1_PASS_UNQUALIFIED"}
KNOWN = NEGATIVE | PENDING


def load_index(path: Path = INDEX) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported research outcome memory schema")
    refs = {}
    for source in data.get("source_refs", []):
        pr, commit = source.get("pr"), source.get("commit", "")
        if not isinstance(pr, int) or pr in refs or not re.fullmatch(r"[0-9a-f]{7,40}", commit):
            raise ValueError("Invalid or duplicate branch-result source provenance")
        refs[pr] = commit
    if not refs:
        raise ValueError("Missing branch-result source provenance")
    seen = set()
    for row in data["outcomes"]:
        if row.get("source_pr") not in refs:
            raise ValueError("Unattributed branch-result source PR")
        key = row["hypothesis_id"], row["contract_id"]
        if key in seen or row["disposition"] not in KNOWN:
            raise ValueError(f"Duplicate or unknown research disposition: {key}")
        seen.add(key)
        if len(row.get("data_sha256", "")) != 64 or not row.get("evidence_id"):
            raise ValueError(f"Missing source SHA/evidence: {key}")
        if not row.get("strategy_module", "").startswith("strategies/candidates/"):
            raise ValueError(f"Missing frozen strategy module identity: {key}")
        if row.get("auto_retry") != "DENY_SAME_CONFIG":
            raise ValueError(f"Missing replay prohibition: {key}")
        economics = row.get("economic_preflight")
        if economics is not None:
            if (
                row["disposition"] != "STAGE1_PASS_UNQUALIFIED"
                or economics.get("status") != "PRELIMINARY_ECONOMICS_REJECT"
                or economics.get("full_native_stage3") is not False
                or not isinstance(economics.get("receipt_sha256"), str)
                or len(economics["receipt_sha256"]) != 64
            ):
                raise ValueError(f"Inconsistent PIT economic preflight evidence: {key}")
            for field in (
                "base_net_sharpe", "double_cost_net_sharpe", "delay_plus_one_net_sharpe"
            ):
                if not isinstance(economics.get(field), (float, int)):
                    raise TypeError(f"Missing preflight economic metric {field}: {key}")
    blockers = [r["hypothesis_id"] for r in data["original_unattempted"]]
    if len(set(blockers)) != len(blockers):
        raise ValueError("Duplicate original blocker ID")
    return data


def connect_registry(path: Path = REGISTRY) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(f"Warden registry not present; cannot assume no prior trials: {path}")
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def inventory(registry: Path = REGISTRY, index: Path = INDEX) -> dict:
    sidecar = load_index(index)
    with connect_registry(registry) as db:
        hypotheses = [dict(x) for x in db.execute(
            "SELECT hypothesis_id,contract_id,title,status,strategy_module "
            "FROM hypotheses ORDER BY hypothesis_id"
        )]
        trials: dict[str, list[dict]] = defaultdict(list)
        attempts: dict[str, list[dict]] = defaultdict(list)
        for row in db.execute(
            "SELECT hypothesis_id,stage_reached,verdict,run_id FROM trials"
        ):
            trials[row["hypothesis_id"]].append(dict(row))
        for row in db.execute(
            "SELECT hypothesis_id,stage,outcome,attempt_id FROM selection_attempts"
        ):
            attempts[row["hypothesis_id"]].append(dict(row))
        counts = {
            table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("hypotheses", "trials", "selection_attempts")
        }
    no_trial = [h for h in hypotheses if not trials[h["hypothesis_id"]]]
    no_stage3 = [h for h in hypotheses if not any(
        t["stage_reached"] == "STAGE_3_STRESS"
        for t in trials[h["hypothesis_id"]]
    )]
    never_evaluated = [h for h in no_trial if not attempts[h["hypothesis_id"]]]
    stage1_reject_no_stage3 = [h for h in no_stage3 if any(
        a["stage"] == "STAGE_1_SIGNAL" and a["outcome"] == "REJECT"
        for a in attempts[h["hypothesis_id"]]
    )]
    stage1_pass_no_stage3 = [h for h in no_stage3 if any(
        a["stage"] == "STAGE_1_SIGNAL" and a["outcome"] == "PASS"
        for a in attempts[h["hypothesis_id"]]
    )]
    early_trial_no_stage3 = [h for h in no_stage3 if trials[h["hypothesis_id"]]]
    status_counts = dict(sorted(Counter(h["status"] for h in hypotheses).items()))
    raw_blockers = {b["hypothesis_id"]: b for b in sidecar["original_unattempted"]}
    unattempted = []
    for h in never_evaluated:
        block = raw_blockers.get(h["hypothesis_id"])
        unattempted.append({
            "hypothesis_id": h["hypothesis_id"],
            "contract_id": h["contract_id"],
            "title": h["title"],
            "data_status": block["recovery_status"] if block else "NEEDS_SOURCE_TRIAGE",
            "detail": block["detail"] if block else "No original blocker diagnosis available",
            "related_evidence_id": block["related_evidence_id"] if block else None,
        })
    known_outcomes = sorted(
        sidecar["outcomes"], key=lambda r: (r["source_pr"], r["hypothesis_id"])
    )
    overlaps = [r["hypothesis_id"] for r in known_outcomes
                if any(h["hypothesis_id"] == r["hypothesis_id"] for h in hypotheses)]
    return {
        "source": str(registry),
        "registry": counts,
        "hypothesis_workflow_status": status_counts,
        "without_any_trial": len(no_trial),
        "without_stage3": len(no_stage3),
        "stage1_rejected_without_stage3": len(stage1_reject_no_stage3),
        "stage1_passed_without_stage3": len(stage1_pass_no_stage3),
        "early_trial_without_stage3": len(early_trial_no_stage3),
        "never_attempted": unattempted,
        "never_attempted_count": len(unattempted),
        "extra_outcomes_in_open_prs": {
            "total": len(known_outcomes),
            "negative": sum(r["disposition"] in NEGATIVE for r in known_outcomes),
            "stage1_pass_unqualified": sum(r["disposition"] in PENDING for r in known_outcomes),
            "overlapping_live_ids": overlaps,
            "records": known_outcomes,
        },
        "classification_notes": [
            "Workflow status ACTIVE is not scientific eligibility.",
            "Stage 1 REJECT does not require Stage 3 and is not an untested hypothesis.",
            "An original source/contract failure is not repaired by a failed proxy or rejected descendant.",
            "Open-PR evidence is not yet appended to the primary authoritative research SQLite.",
            "Updated market data never invalidates an existing negative result for its original frozen contract.",
        ],
    }


def _blocked(reason: str, *, evidence: list[str], status: str) -> dict:
    return {"eligible": False, "status": status, "reason": reason, "evidence": evidence}


def check_spec(spec: Path, registry: Path = REGISTRY, index: Path = INDEX) -> dict:
    config = yaml.safe_load(spec.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise TypeError("Candidate specification is not a YAML mapping")
    hid = config.get("hypothesis_id")
    cid = config.get("contract_id")
    if not isinstance(hid, str) or not hid or not isinstance(cid, str) or not cid:
        raise ValueError("Spec needs nonempty hypothesis_id and contract_id")
    module = config.get("strategy_module", "")
    if module and not isinstance(module, str):
        raise ValueError("strategy_module must be a string")
    # Canonical SQLite wins when it already records this identity. Sidecar
    # evidence may refuse an *unseen* rerun, never replace a native verdict.
    with connect_registry(registry) as db:
        registered = db.execute("SELECT contract_id,status FROM hypotheses WHERE hypothesis_id=?", (hid,)).fetchone()
        if registered and registered["contract_id"] != cid:
            return _blocked("Registered hypothesis is frozen under a different contract",
                            evidence=[registered["contract_id"]], status="CONTRACT_ID_MISMATCH")
        native_trials = db.execute("SELECT run_id,verdict FROM trials WHERE hypothesis_id=?", (hid,)).fetchall()
        if native_trials:
            return _blocked("Canonical Warden trial already recorded; sidecar is supplementary only",
                            evidence=[f"{x['run_id']}:{x['verdict']}" for x in native_trials],
                            status="HISTORICAL_TRIAL_ALREADY_RECORDED")
        native_screens = db.execute("SELECT attempt_id,stage,outcome FROM selection_attempts WHERE hypothesis_id=?", (hid,)).fetchall()
        if native_screens:
            return _blocked("Canonical Warden selection attempt already recorded; no automatic repeat",
                            evidence=[f"{x['attempt_id']}:{x['stage']}:{x['outcome']}" for x in native_screens],
                            status="HISTORICAL_SELECTION_ATTEMPT_RECORDED")
    sidecar = load_index(index)
    entry = [r for r in sidecar["outcomes"] if r["hypothesis_id"] == hid]
    identical = [r for r in entry if r["contract_id"] == cid]
    if identical:
        row = identical[0]
        return _blocked(
            f"Preserved {row['disposition']} for exact hypothesis/contract; "
            "new data does not reset its validation or trial history",
            evidence=[f"PR #{row['source_pr']}", row["evidence_id"]],
            status="HISTORICAL_RESULT_LOCKED",
        )
    if entry:
        return _blocked(
            "Original hypothesis ID has previous off-main research evidence on another "
            "contract; a genuinely new version/lineage and inherited selection debt need review",
            evidence=[f"PR #{r['source_pr']}" for r in entry],
            status="CROSS_CONTRACT_REVIEW_REQUIRED",
        )
    if module:
        aliases = [
            r for r in sidecar["outcomes"]
            if r["strategy_module"] == module and r["hypothesis_id"] != hid
        ]
        if aliases:
            return _blocked(
                "Proposed new ID reuses an off-main studied module; confirm legitimate "
                "new economic mechanism, contract, family selection debt and trial budget",
                evidence=[f"PR #{r['source_pr']}:{r['hypothesis_id']}" for r in aliases],
                status="CROSS_BRANCH_MODULE_ALIAS_REVIEW_REQUIRED",
            )
    blocker = next(
        (b for b in sidecar["original_unattempted"] if b["hypothesis_id"] == hid),
        None,
    )
    if blocker:
        return _blocked(
            blocker["detail"], evidence=[blocker["recovery_status"]],
            status="ORIGINAL_BLOCKER_UNRESOLVED",
        )
    with connect_registry(registry) as db:
        row = db.execute(
            "SELECT status,contract_id,strategy_module,parent_hypothesis_id "
            "FROM hypotheses WHERE hypothesis_id=?", (hid,)
        ).fetchone()
        if row and row["contract_id"] != cid:
            return _blocked(
                "Registered hypothesis is frozen under a different contract",
                evidence=[row["contract_id"]],
                status="CONTRACT_ID_MISMATCH",
            )
        tests = db.execute(
            "SELECT stage_reached,verdict,run_id FROM trials WHERE hypothesis_id=?",
            (hid,),
        ).fetchall()
        if tests:
            return _blocked(
                "Full or historical early trial already registered; do not auto-replay",
                evidence=[f"{t['run_id']}:{t['verdict']}" for t in tests],
                status="HISTORICAL_TRIAL_ALREADY_RECORDED",
            )
        old_screens = db.execute(
            "SELECT attempt_id,stage,outcome FROM selection_attempts "
            "WHERE hypothesis_id=?", (hid,),
        ).fetchall()
        if old_screens:
            return _blocked(
                "Previous Warden attempt exists; reuse evidence, do not repeat automatic screen",
                evidence=[f"{x['attempt_id']}:{x['stage']}:{x['outcome']}"
                          for x in old_screens],
                status="HISTORICAL_SELECTION_ATTEMPT_RECORDED",
            )
        if row and row["status"] != "ACTIVE":
            return _blocked(
                f"Registered workflow status {row['status']} requires manual review",
                evidence=[hid], status="HISTORICAL_DISPOSITION_LOCKED",
            )
        # Same module with another recorded identity can be a budget-reset
        # alias. Do not block independent new modules just because their
        # economic explanation is similar.
        if module:
            reused = db.execute(
                "SELECT hypothesis_id FROM hypotheses WHERE strategy_module=? "
                "AND hypothesis_id!=? LIMIT 1",
                (module, hid),
            ).fetchone()
            if reused:
                return _blocked(
                    "Strategy module already belongs to another registered hypothesis; "
                    "check lineage and budget before adding an alias",
                    evidence=[reused["hypothesis_id"]],
                    status="MODULE_ALIAS_REVIEW_REQUIRED",
                )
    return {
        "eligible": True,
        "status": "NOT_PREVIOUSLY_ATTEMPTED_ON_THIS_ID",
        "reason": "No recorded historical retry under this hypothesis ID. "
                  "This is NOT a data, causality, contract, or Warden approval.",
        "evidence": [],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--inventory", action="store_true")
    mode.add_argument("--check-spec", type=Path)
    parser.add_argument("--registry", type=Path, default=REGISTRY)
    parser.add_argument("--index", type=Path, default=INDEX)
    args = parser.parse_args()
    try:
        result = (
            inventory(args.registry, args.index)
            if args.inventory
            else check_spec(args.check_spec, args.registry, args.index)
        )
    except (FileNotFoundError, ValueError, TypeError, KeyError, sqlite3.DatabaseError) as exc:
        print(json.dumps({"status": "EVIDENCE_UNAVAILABLE", "eligible": False,
                          "reason": str(exc)}, indent=2))
        raise SystemExit(2) from exc
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.check_spec and not result["eligible"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
