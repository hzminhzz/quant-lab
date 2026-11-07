"""Read-only campaign projection from immutable Warden SQLite evidence.

A report is never authority: no screen/backtest/holdout is invoked. An
interrupted attempt stays review-required, not a rejected or passing result.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import yaml


def project(specs: list[Path], registry: Path) -> list[dict]:
    if not registry.is_file():
        raise FileNotFoundError(f"Warden registry missing: {registry}")
    out = []
    with sqlite3.connect(f"file:{registry.resolve()}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for spec in sorted(specs):
            cfg = yaml.safe_load(spec.read_text(encoding="utf-8"))
            if not isinstance(cfg, dict) or not all(isinstance(cfg.get(k), str) for k in ("hypothesis_id", "contract_id")):
                raise ValueError(f"invalid campaign spec identity: {spec}")
            hid, cid = cfg["hypothesis_id"], cfg["contract_id"]
            module = cfg.get("strategy_module") or ""
            conflicting = conn.execute("SELECT contract_id FROM hypotheses WHERE hypothesis_id=?", (hid,)).fetchone()
            if conflicting and conflicting[0] != cid:
                state, proof, reason = "REVIEW", None, "contract revision does not match frozen hypothesis"
            else:
                trials = conn.execute("SELECT run_id,verdict,stage_reached FROM trials WHERE hypothesis_id=? AND contract_id=? ORDER BY created_at DESC", (hid, cid)).fetchall()
                attempts = conn.execute("SELECT attempt_id,outcome,stage,diagnostics_json,strategy_module FROM selection_attempts WHERE hypothesis_id=? AND contract_id=? ORDER BY created_at DESC", (hid, cid)).fetchall()
                pending = []
                if "attempt_receipts" in tables:
                    pending = conn.execute("SELECT run_id,state FROM attempt_receipts WHERE hypothesis_id=? AND contract_id=? AND state!='FINALIZED'", (hid, cid)).fetchall()
                if trials:
                    value = trials[0]
                    state, proof, reason = ("STAGE3_CANDIDATE" if value[1] == "CANDIDATE_FOR_QUALIFICATION" else
                                            "STAGE3_REVISE" if value[1] == "REVISE" else
                                            "RECORDED_TRIAL", value[0], value[1])
                elif pending:
                    state, proof, reason = "INTERRUPTED_REVIEW", pending[0][0], pending[0][1]
                elif attempts:
                    row = attempts[0]
                    if module and row[4] and module != row[4]:
                        state, proof, reason = "REVIEW", row[0], "strategy-module provenance drift"
                    else:
                        state, proof, reason = ("STAGE1_PASS" if row[1] == "PASS" else
                                                "STAGE1_REJECT" if row[1] == "REJECT" else
                                                "REVIEW", row[0], row[1])
                else:
                    state, proof, reason = "PENDING", None, "no authoritative Warden attempt"
            out.append({"spec": str(spec), "hypothesis_id": hid, "contract_id": cid,
                        "status": state, "evidence_id": proof, "reason": reason,
                        "automatic_replay": "DENIED" if state != "PENDING" else "REQUIRES_NEW_APPROVAL"})
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--registry", type=Path, required=True)
    p.add_argument("--spec-dir", type=Path, required=True)
    args = p.parse_args()
    print(json.dumps({"source": "warden-sqlite-readonly", "records": project(
        list(args.spec_dir.glob("*.yaml")), args.registry)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
