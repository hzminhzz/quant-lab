"""Export immutable four-hypothesis Warden evidence without mutating the ledger.

Must be executed after the frozen Stage 1 set and the one legitimate
Stage 3 trial. The authoritatively seeded SQLite remains gitignored.
This versioned JSON makes trial outcomes and return hashes reviewable.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path

from strategies.candidates.four_xs4h.common import HYPOTHESES

DB = Path("registry/registry.db")
OUT = Path(".workspace/campaigns/four-xs4h/evidence.json")
STAGE3_RUN = "RUN-XS4H-SIGN-REVERSAL-20261102-001"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as inp:
        for chunk in iter(lambda: inp.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def export() -> dict:
    with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        attempts: dict[str, dict] = {}
        for kind, hid in HYPOTHESES.items():
            rows = conn.execute(
                "SELECT attempt_id,contract_id,stage,outcome,returns_sha256,"
                "returns_observed,diagnostics_json FROM selection_attempts "
                "WHERE hypothesis_id=? ORDER BY created_at", (hid,)
            ).fetchall()
            if len(rows) != 1:
                raise ValueError(f"Expected exactly one immutable screen for {hid}; got {len(rows)}")
            a = dict(rows[0])
            if (a["stage"] != "STAGE_1_SIGNAL"
                    or a["contract_id"] != "CRYPTO_PERPS_TOP100_1H_V1"
                    or a["returns_observed"] != 1):
                raise ValueError(f"Screen {hid} missing required native evidence")
            a["metrics"] = json.loads(a.pop("diagnostics_json"))
            attempts[kind] = {"hypothesis_id": hid, **a}
        row = conn.execute(
            "SELECT run_id,verdict,contract_id,data_snapshot_sha256,"
            "gross_sharpe,net_sharpe,profit_factor,max_drawdown_pct,"
            "annualized_turnover,breakeven_cost_bps,deflated_sharpe_prob,"
            "stress_2x_cost_sharpe,delay_plus1_sharpe,total_trials_n,"
            "effective_trials_keff,mean_ic,ic_hac_tstat,returns_sha256,"
            "returns_parquet_path,diagnostics_json FROM trials WHERE run_id=?",
            (STAGE3_RUN,),
        ).fetchone()
        if row is None:
            raise ValueError("Missing Stage 3 ledger trial")
        trial = dict(row)
        if trial["verdict"] != "REVISE":
            raise ValueError("Trial scientific disposition has changed")
        returns_path = Path(trial["returns_parquet_path"])
        if not returns_path.is_file() or digest(returns_path) != trial["returns_sha256"]:
            raise ValueError("Full Stage 3 net-return artifact is not integrity-pinned")
        trial["metrics_diagnostics"] = json.loads(trial.pop("diagnostics_json"))
        audit_rows = conn.execute(
            "SELECT verdict,checks_json FROM audit_evidence WHERE run_id=?",
            (STAGE3_RUN,),
        ).fetchall()
        if len(audit_rows) != 1 or audit_rows[0]["verdict"] != "REJECTED":
            raise ValueError("Missing rejected six-check Warden audit")
        audit = {"verdict": audit_rows[0]["verdict"],
                 "checks": json.loads(audit_rows[0]["checks_json"])}
        counts = {
            name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            for name in ("hypotheses", "trials", "selection_attempts")
        }
    return {
        "contract": "CRYPTO_PERPS_TOP100_1H_V1",
        "source_snapshot": json.loads(
            Path(".workspace/campaigns/four-xs4h/source-snapshot.json").read_text()
        ),
        "stage1": attempts,
        "stage3": trial,
        "audit": audit,
        "counts_after": counts,
        "protected_holdout_read": False,
        "live_execution": False,
        "research_status": "REJECTED_FOR_TRADING",
    }


def main() -> None:
    payload = (json.dumps(export(), indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if OUT.exists():
        if OUT.read_bytes() != payload:
            raise FileExistsError(f"Refusing to rewrite published selection evidence: {OUT}")
    else:
        with tempfile.NamedTemporaryFile(dir=OUT.parent, delete=False) as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
            temporary = Path(fh.name)
        try:
            os.link(temporary, OUT)
        finally:
            temporary.unlink(missing_ok=True)
    print(json.dumps({"path": str(OUT), "sha256": hashlib.sha256(payload).hexdigest()}, indent=2))


if __name__ == "__main__":
    main()
