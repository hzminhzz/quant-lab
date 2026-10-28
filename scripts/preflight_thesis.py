#!/usr/bin/env python3
"""Read-only thesis preflight: current state card and the next legal command.

Writes nothing, ever. SQLite is opened with ``mode=ro`` and only these three tables are read;
``ResearchRegistry`` is deliberately not imported, because its constructor creates schema and
inserts quarantine/migration rows (``registry.py:149-198``). Contracts are loaded from
``contracts/<id>.yaml`` and datasets resolved through ``provenance.resolve_dataset`` — both pure
filesystem reads. See AGENT_RUNBOOK.md.
"""

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import polars as pl

from warden.cli import MANIFEST_FILE, check_worktree
from warden.contracts import EvaluationContract, load_contract
from warden.provenance import resolve_dataset
from warden.runner import require_execution_columns

ROOT = Path(".")
DEFAULT_REGISTRY = ROOT / "registry" / "registry.db"

BAR_WIDTH = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=False).stdout.strip()


def connect_ro(db: Path) -> sqlite3.Connection | None:
    """Open an existing database read-only. Never creates a file, schema, or row."""
    if not db.is_file():
        return None
    return sqlite3.connect(f"file:{quote(str(db.resolve()), safe='/')}?mode=ro", uri=True)


def _count(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int | None:
    try:
        return int(conn.execute(sql, params).fetchone()[0])
    except sqlite3.Error:
        return None  # fresh or older registry: table/column absent, report unknown


def prior_search_floor_ro(conn: sqlite3.Connection, contract: EvaluationContract) -> tuple[float | None, float | None]:
    """Read-only copy of ResearchRegistry.prior_search_floor (registry.py:437-448)."""
    try:
        state = conn.execute(
            "SELECT MAX(s.max_keff_gate), MAX(s.max_noise) FROM contract_search_state s "
            "JOIN contracts c ON c.contract_id = s.contract_id "
            "WHERE s.contract_id=? OR (c.asset_class=? AND c.frequency=?)",
            (contract.contract_id, contract.asset_class, contract.frequency)).fetchone()
        trial_max = conn.execute("SELECT MAX(effective_trials_keff) FROM trials WHERE contract_id=?",
                                 (contract.contract_id,)).fetchone()[0]
    except sqlite3.Error:
        return None, None
    return max(float(state[0] or 1.0), float(trial_max or 1.0)), float(state[1] or 0.0)


def validation_gap_covered(val_end: datetime, last: datetime | None, frequency: str) -> tuple[bool, float]:
    """True when the exploratory data reaches val_end to within one bar."""
    if last is None or frequency not in BAR_WIDTH:
        return True, 0.0
    gap_days = (val_end - last).total_seconds() / 86400
    return gap_days * 1440 <= BAR_WIDTH[frequency], round(gap_days, 2)


def manifest_state() -> dict:
    ok = check_worktree()
    if ok:
        return {"ok": True, "unsatisfied": []}  # check_worktree already hashed every manifest line
    bad = []
    manifest = ROOT / MANIFEST_FILE
    if manifest.is_file():
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            expected, rel = line.strip().split(maxsplit=1)
            target = ROOT / rel
            if not target.is_file():
                bad.append({"path": rel, "state": "MISSING"})
            elif hashlib.sha256(target.read_bytes()).hexdigest() != expected:
                bad.append({"path": rel, "state": "CHANGED"})
    return {"ok": ok, "unsatisfied": bad}


def contract_card(cid: str, registry: Path) -> dict:
    card: dict = {"contract_id": cid}
    try:
        path = ROOT / "contracts" / f"{cid.lower()}.yaml"
        if not path.is_file():
            path = ROOT / "contracts" / f"{cid.lower()}.yml"
        contract = load_contract(path)  # filesystem only; no registry construction
    except Exception as exc:  # noqa: BLE001
        card["contract_error"] = f"{type(exc).__name__}: {exc}"
        return card
    card.update(
        asset_class=contract.asset_class, frequency=contract.frequency,
        execution_mode=contract.execution_mode,
        execution_delay_bars=contract.execution_delay_bars,
        label_horizon_bars=contract.label_horizon_bars,
        cost_bps_per_side=contract.taker_fee_bps + contract.slippage_bps,
        cost_bps_round_trip=2 * (contract.taker_fee_bps + contract.slippage_bps),
        val_start=str(contract.val_start), val_end=str(contract.val_end),
        holdout_start=str(contract.holdout_start))
    try:
        dataset = resolve_dataset(contract, ROOT)  # hashes the pinned file; writes nothing
        data_path = ROOT / dataset.path
        # resolve_dataset reports digest/path/provenance mismatches as a reason, not an exception,
        # so nothing here may claim the digest was verified without comparing it.
        card.update(exploratory_path=dataset.path, dataset_provenance=dataset.provenance,
                    dataset_eligible=dataset.eligible_market, dataset_reason=dataset.reason,
                    digest_matches_contract=dataset.sha256 == contract.data_snapshot_sha256)
        panel = pl.read_parquet(data_path, n_rows=2000)
        try:
            require_execution_columns(panel, contract)
            card["execution_columns_ok"] = True
        except Exception as exc:  # noqa: BLE001 - the point is to report why it is unusable
            card["execution_columns_ok"] = False
            card["execution_columns_error"] = f"{type(exc).__name__}: {exc}"
        span = pl.scan_parquet(data_path).select(
            pl.col("timestamp").min().alias("first"), pl.col("timestamp").max().alias("last"),
            pl.col("asset").n_unique().alias("assets")).collect().row(0, named=True)
        card.update(data_first=str(span["first"]), data_last=str(span["last"]), assets=span["assets"])
        covered, gap_days = validation_gap_covered(contract.val_end, span["last"], contract.frequency)
        card.update(validation_gap_days=gap_days, validation_window_covered=covered)
    except Exception as exc:  # noqa: BLE001
        card["dataset_error"] = f"{type(exc).__name__}: {exc}"

    conn = connect_ro(registry)
    if conn is None:
        card["registry"] = "absent"
        return card
    try:
        keff, noise = prior_search_floor_ro(conn, contract)
        card.update(prior_search_floor_keff=None if keff is None else round(keff, 2),
                    prior_max_noise=None if noise is None else round(noise, 2),
                    selection_attempts=_count(conn, "SELECT COUNT(*) FROM selection_attempts WHERE contract_id=?",
                                              (contract.contract_id,)),
                    hypotheses=_count(conn, "SELECT COUNT(*) FROM hypotheses WHERE contract_id=?",
                                      (contract.contract_id,)))
    finally:
        conn.close()
    # Declared path only: the holdout is never opened, read or stat'ed here, and no permission
    # inference is reported (an execute-only ACL on the directory makes such hints misleading).
    card["holdout_path_declared"] = contract.holdout_data_path
    return card


def hypothesis_card(hid: str, registry: Path) -> dict:
    card: dict = {"hypothesis_id": hid, "registered": False}
    conn = connect_ro(registry)
    if conn is None:
        card["registry"] = "absent"
        return card
    try:
        row = conn.execute(
            "SELECT contract_id, strategy_family, trials_used, status, expected_holding_bars, title "
            "FROM hypotheses WHERE hypothesis_id=?", (hid,)).fetchone()
    except sqlite3.Error:
        card["registry"] = "schema lacks hypotheses table (older registry)"
        return card
    finally:
        conn.close()
    if not row:
        return card
    card.update(registered=True, contract_id=row[0], strategy_family=row[1], trials_used=row[2],
                trials_remaining=max(0, 3 - int(row[2] or 0)), status=row[3],
                expected_holding_bars=row[4], title=row[5])
    return card


def _benign_synthetic(contract: dict) -> bool:
    """Synthetic data is usable for non-promotable work only when nothing failed.

    resolve_dataset substitutes the reason "not market eligible" exactly when every integrity
    check passed but the provenance is not market data; a digest, path or manifest failure
    carries a specific reason instead and must stop.
    """
    return (contract.get("dataset_provenance") == "synthetic"
            and contract.get("dataset_reason") == "not market eligible"
            and contract.get("digest_matches_contract") is True)


def next_step(state: dict, contract: dict | None, hypothesis: dict | None) -> dict:
    if not state["ok"]:
        names = ", ".join(f"{u['path']} {u['state']}" for u in state["unsatisfied"]) or "manifest missing"
        return {"command": "HUMAN ONLY: uv run warden generate-manifest --human-approve-manifest",
                "why": f"check-worktree fails ({names}); every research command refuses until the human re-signs"}
    if contract and contract.get("contract_error"):
        return {"command": "STOP", "why": f"contract not resolvable: {contract['contract_error']}"}
    if contract and contract.get("dataset_error"):
        return {"command": "STOP", "why": f"dataset unusable: {contract['dataset_error']}"}
    if contract and contract.get("dataset_eligible") is not True and not _benign_synthetic(contract):
        return {"command": "STOP",
                "why": f"dataset not eligible ({contract.get('dataset_provenance')}): {contract.get('dataset_reason')}"}
    if (contract and hypothesis and hypothesis.get("registered")
            and hypothesis["contract_id"] != contract.get("contract_id")):
        return {"command": "STOP",
                "why": f"contract mismatch: {hypothesis['hypothesis_id']} belongs to {hypothesis['contract_id']}"}
    if contract and contract.get("execution_columns_ok") is False:
        return {"command": "STOP", "why": f"contract is not executable: {contract.get('execution_columns_error')}"}
    if contract and contract.get("validation_window_covered") is False:
        return {"command": "STOP",
                "why": f"exploratory data ends {contract.get('validation_gap_days')} days before val_end"}
    if hypothesis and not hypothesis.get("registered"):
        return {"command": f"uv run warden register-hypothesis .workspace/hypotheses/{hypothesis['hypothesis_id']}.md --json",
                "why": "hypothesis is not in the ledger; template in .workspace/templates/hypothesis.md"}
    if hypothesis and hypothesis.get("trials_remaining") == 0:
        return {"command": "STOP", "why": "3-trial budget exhausted; write the post-mortem in .workspace/memory/LESSONS.md"}
    if hypothesis:
        stem = hypothesis["hypothesis_id"].lower().replace("-", "_")
        specs = sorted(Path("experiments/active").glob(f"{stem}*.yaml"), reverse=True)
        if specs:
            return {"command": f"uv run warden verify {specs[0]} --json",
                    "why": "after a PASS run screen, then one backtest; see AGENT_RUNBOOK.md"}
        return {"command": f"write experiments/active/{stem}.yaml from .workspace/templates/experiment.yaml",
                "why": "registered hypothesis has no experiment spec yet"}
    return {"command": "write the hypothesis file first", "why": "no hypothesis given"}


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only thesis preflight.")
    ap.add_argument("--contract", help="contract id, e.g. CRYPTO_MAJORS_1H_V1")
    ap.add_argument("--hypothesis", help="hypothesis id, e.g. HYP-2026-101")
    ap.add_argument("--registry", default=str(DEFAULT_REGISTRY), help="registry path (opened read-only)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    registry = Path(args.registry)

    state = manifest_state()
    contract = contract_card(args.contract, registry) if args.contract else None
    hypothesis = hypothesis_card(args.hypothesis, registry) if args.hypothesis else None
    if contract is None and hypothesis and hypothesis.get("registered"):
        contract = contract_card(hypothesis["contract_id"], registry)
    result = {"git_head": _git("rev-parse", "--short", "HEAD"),
              "dirty_entries": len(_git("status", "--porcelain").splitlines()),
              "manifest": state, "registry": str(registry), "contract": contract,
              "hypothesis": hypothesis, "next": next_step(state, contract, hypothesis),
              "runbook": "AGENT_RUNBOOK.md"}

    blocked = result["next"]["command"] == "STOP"
    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 3 if blocked else (0 if state["ok"] else 2)
    print(f"runbook      {result['runbook']}")
    print(f"git          {result['git_head']} ({result['dirty_entries']} dirty entries)")
    print(f"manifest     {'OK' if state['ok'] else 'FAILED: ' + ', '.join(u['path'] for u in state['unsatisfied'])}")
    print(f"registry     {registry} (read-only)")
    if contract:
        print(f"contract     {contract.get('contract_id')} {contract.get('frequency')} "
              f"{contract.get('execution_mode')} cost={contract.get('cost_bps_round_trip')}bps RT "
              f"label={contract.get('label_horizon_bars')} assets={contract.get('assets')}")
        print(f"data         {contract.get('data_first')} .. {contract.get('data_last')} "
              f"digest_matches_contract={contract.get('digest_matches_contract')} "
              f"eligible={contract.get('dataset_eligible')} ({contract.get('dataset_reason')})")
        print(f"executable   {contract.get('execution_columns_ok')}   "
              f"floor k_eff={contract.get('prior_search_floor_keff')} noise={contract.get('prior_max_noise')} "
              f"attempts={contract.get('selection_attempts')}")
    if hypothesis:
        print(f"hypothesis   {hypothesis['hypothesis_id']} trials {hypothesis.get('trials_used')}/3 "
              f"status={hypothesis.get('status')}")
    print(f"NEXT         {result['next']['command']}")
    print(f"             {result['next']['why']}")
    return 3 if blocked else (0 if state["ok"] else 2)


if __name__ == "__main__":
    sys.exit(main())
