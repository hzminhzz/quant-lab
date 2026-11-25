"""Read-only campaign projection from immutable Warden SQLite evidence.

A report is never authority: no screen/backtest/holdout is invoked. An
interrupted attempt stays review-required, not a rejected or passing result.

The ledger is read through ``warden_ops.readonly`` domain projections, so this
module holds no SQL and never takes a writer lock on a live registry.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from warden_ops.readonly import ReadOnlyResearchRegistry


def project(specs: list[Path], registry: Path) -> list[dict]:
    if not registry.is_file():
        raise FileNotFoundError(f"Warden registry missing: {registry}")
    ledger = ReadOnlyResearchRegistry(registry)
    out = []
    for spec in sorted(specs):
        cfg = yaml.safe_load(spec.read_text(encoding="utf-8"))
        if not isinstance(cfg, dict) or not all(isinstance(cfg.get(k), str) for k in ("hypothesis_id", "contract_id")):
            raise ValueError(f"invalid campaign spec identity: {spec}")
        hid, cid = cfg["hypothesis_id"], cfg["contract_id"]
        module = cfg.get("strategy_module") or ""
        registered = ledger.read_hypothesis(hid)
        conflicting = (registered or {}).get("contract_id")
        if conflicting and conflicting != cid:
            state, proof, reason = "REVIEW", None, "contract revision does not match frozen hypothesis"
        else:
            trials = ledger.read_trials_for(hid, cid)
            attempts = ledger.read_selection_attempts(hid, cid)
            pending = ledger.read_pending_attempts(hid, cid)
            if trials:
                value = trials[0]
                state, proof, reason = ("STAGE3_CANDIDATE" if value["verdict"] == "CANDIDATE_FOR_QUALIFICATION" else
                                        "STAGE3_REVISE" if value["verdict"] == "REVISE" else
                                        "RECORDED_TRIAL", value["run_id"], value["verdict"])
            elif pending:
                state, proof, reason = "INTERRUPTED_REVIEW", pending[0]["run_id"], pending[0]["state"]
            elif attempts:
                row = attempts[0]
                if module and row["strategy_module"] and module != row["strategy_module"]:
                    state, proof, reason = "REVIEW", row["attempt_id"], "strategy-module provenance drift"
                else:
                    state, proof, reason = ("STAGE1_PASS" if row["outcome"] == "PASS" else
                                            "STAGE1_REJECT" if row["outcome"] == "REJECT" else
                                            "REVIEW", row["attempt_id"], row["outcome"])
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
