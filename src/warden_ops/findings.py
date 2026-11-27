"""Deterministic derived findings; existing RG-07 remains the full admission gate."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time

from warden_ops.control_contracts import Result, digest
from warden_ops.readonly import open_readonly
from warden_ops.research_outcome_memory import INDEX, load_index

LESSONS = ".workspace/memory/LESSONS.md"


def _metadata(root, relative):
    path = root / relative
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("metadata symlink refused")
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("metadata source exceeds 2 MiB; explicit drill-down required")
    return path.read_bytes()


def _polarity(verdict):
    text = str(verdict or "").upper()
    if any(word in text for word in ("REJECT", "FAIL", "REVISE")):
        return "NEGATIVE"
    if "PASS" in text or text == "PROMOTED":
        return "POSITIVE_UNQUALIFIED"
    return "UNKNOWN"


def _finding(kind, key, record, claim, *, at=None):
    sha = digest(record)
    scope = {key: record.get(key) for key in
             ("hypothesis_id", "contract_id", "strategy_module", "config_hash", "data_sha256")}
    return {
        "finding_id": digest([kind, key, sha]), "source_kind": kind,
        "claim": claim[:1600], "claim_truncated": len(claim) > 1600,
        "scope": scope, "recorded_at": at, "cutoff": record.get("cutoff"),
        "polarity": _polarity(record.get("verdict", record.get("outcome", record.get("disposition")))),
        "support": [{"source": key, "sha256": sha, "artifact_bytes_verified": False}],
        "contradictions": [], "supersession": "not inferred; original evidence retained",
        "limits": "recorded scoped outcome, not current scientific eligibility" if kind != "lesson"
                  else "narrative claim; requires exact evidence and applicability review",
        "revalidate": ["exact contract/data/code/config/time scope", "all current RG-07 sources",
                       "current domain admission, costs, lineage and selection debt"],
        "authorizes_research": False,
    }


def inspect_findings(control, request):
    rows, unavailable, revisions = [], [], {}
    try:
        registry = open_readonly(control.registry_path)
        if registry is None:
            raise ValueError("ledger missing")
        observed = registry.read_finding_records()
        revisions["ledger"] = digest(observed)
        unavailable.extend("ledger:" + field for field in observed["missing_fields"])
        for record in observed["records"]:
            key = "ledger:" + record["source_table"] + ":" + str(record["evidence_id"])
            claim = f'{record.get("stage_reached", record.get("stage"))}: {record.get("verdict", record.get("outcome"))}'
            rows.append(_finding("ledger", key, record, claim, at=record.get("created_at")))
    except (OSError, ValueError, sqlite3.Error) as exc:
        unavailable.append("ledger: " + str(exc)[:200])
    try:
        raw = _metadata(control.root, INDEX)
        index = load_index(control.root / INDEX)
        if raw != _metadata(control.root, INDEX):
            raise ValueError("branch index changed while reading; refresh")
        revisions["branch_index"] = hashlib.sha256(raw).hexdigest()
        refs = {r["pr"]: r for r in index["source_refs"]}
        for record in index["outcomes"]:
            record = {**record, "provenance": refs[record["source_pr"]]}
            key = "branch:" + str(record["source_pr"]) + ":" + record["evidence_id"]
            row = _finding("branch_index", key, record, record.get("summary", record["disposition"]),
                           at=index.get("as_of_utc"))
            row["support"][0]["provenance"] = record["provenance"]
            rows.append(row)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        unavailable.append("branch_index: " + str(exc)[:200])
    try:
        raw = _metadata(control.root, LESSONS)
        revisions["lessons"] = hashlib.sha256(raw).hexdigest()
        for number, match in enumerate(re.finditer(r"(?ms)^## (.+?)\n(.*?)(?=^## |\Z)", raw.decode())):
            heading, body = match.groups()
            # Preserve prose without treating it as a policy or an executed repair.
            ids = sorted(set(re.findall(r"\bHYP-[A-Za-z0-9-]+", heading + "\n" + body))) or [None]
            for hyp in ids:
                record = {"hypothesis_id": hyp, "heading": heading, "body": body}
                rows.append(_finding("lesson", f"{LESSONS}#section-{number}", record, heading + "\n" + body))
    except (OSError, ValueError, UnicodeError) as exc:
        unavailable.append("lessons: " + str(exc)[:200])
    revision = digest({"sources": revisions, "unavailable": unavailable, "hypothesis": request.hypothesis_id})
    offset = 0
    if request.cursor:
        try:
            previous, offset_text = request.cursor.split(":")
            offset = int(offset_text)
            if previous != revision or offset < 0:
                raise ValueError("finding sources changed")
        except ValueError:
            return control.refuse("STALE_STATE", "finding source revision or cursor changed",
                                  remedy="inspect findings from the first page; recheck applicability")
    selected = [r for r in rows if not request.hypothesis_id
                or r["scope"]["hypothesis_id"] == request.hypothesis_id]
    selected.sort(key=lambda r: (r["source_kind"], r["finding_id"]))
    for row in selected:
        related = [other for other in rows if other["finding_id"] != row["finding_id"]
                   and row["scope"]["hypothesis_id"] is not None
                   and other["scope"]["hypothesis_id"] == row["scope"]["hypothesis_id"]]
        contrary = [other["finding_id"] for other in related
                    if {row["polarity"], other["polarity"]} == {"NEGATIVE", "POSITIVE_UNQUALIFIED"}]
        row["contradictions"] = contrary[:8]
        row["contradiction_count"] = len(contrary)
        row["contradiction_note"] = "different recorded outcomes; compare exact scopes before inference"
        row["related_evidence"] = [other["finding_id"] for other in related[:8]]
        row["related_evidence_count"] = len(related)
    if offset > len(selected):
        return control.refuse("TECHNICAL_PRE_ADMISSION", "finding cursor outside current result")
    page = selected[offset:offset + request.limit]
    value = {"revision": revision, "sources": revisions, "unavailable_sources": unavailable,
             "source_completeness": not unavailable, "total_count": len(selected),
             "negative_count": sum(r["polarity"] == "NEGATIVE" for r in selected) if not unavailable else None,
             "mandatory_negative_history": "unchanged full RG-07 admission",
             "scientific_writes": 0, "evaluator_calls": 0, "sealed_access": False,
             "findings": page}
    result = Result(ok=True, status="OK", observed_at=time.time(), value=value).model_dump(mode="json")
    while True:
        end = offset + len(page)
        value.update(complete=end == len(selected), omitted_count=len(selected) - len(page),
                     next_cursor=f"{revision}:{end}" if end < len(selected) else None)
        result["value"] = value
        if len(json.dumps(result).encode()) <= 12288:
            return result
        if len(page) <= 1:
            return control.refuse("EXTERNAL_DEPENDENCY", "single finding exceeds bounded context",
                                  remedy="inspect the named source; no evidence is authorized by this view")
        page.pop()
