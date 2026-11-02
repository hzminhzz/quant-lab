"""Warden Stage 1 with an immutable backup of the historical source registry.

The actual source registry, contracts, and protected holdout are never modified.
An isolated SQLite snapshot carries *all* pre-existing trial/selection debt.
This script refuses a second screen of the same hypothesis rather than
turning candidate selection into unrecorded repeated attempts.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import yaml

from scripts.prepare_research_snapshot import snapshot_registry
from warden.cli import check_worktree
from warden.contracts import load_hypothesis
from warden.provenance import resolve_dataset
from warden.registry import ResearchRegistry
from warden.runner import ExperimentRunner


def research_screen(
    source_db: Path,
    snapshot_db: Path,
    hypothesis_path: Path,
    spec_path: Path,
) -> dict:
    root = Path.cwd().resolve()
    if not check_worktree(root):
        raise ValueError("Protected Warden worktree integrity failed")
    if not source_db.resolve().is_file() or source_db.resolve() == snapshot_db.resolve():
        raise ValueError("Source research history unavailable or unsafe")
    h = load_hypothesis(hypothesis_path)
    cfg = yaml.safe_load(spec_path.read_text())
    if cfg["hypothesis_id"] != h.hypothesis_id or cfg["contract_id"] != h.contract_id:
        raise ValueError("Experiment must reference its pre-registered hypothesis/contract")
    if cfg.get("strategy_module") != h.strategy_module:
        raise ValueError("Strategy module differs from preregistered source")

    # A write-once snapshot of all research history. If present, do not
    # recopy or reset it. Original WAL transactions are copied by SQLite.
    snapshot = snapshot_registry(source_db, snapshot_db)
    registry = ResearchRegistry(snapshot_db)
    runner = ExperimentRunner(registry=registry, repo_root=root)
    contract = runner.resolve_contract(h.contract_id)
    dataset = resolve_dataset(contract, root)
    if not dataset.eligible_market:
        raise ValueError("Only SHA-pinned observed market exploratory data is eligible")
    with sqlite3.connect(f"file:{source_db.resolve()}?mode=ro", uri=True) as conn:
        existing_live = conn.execute(
            "SELECT COUNT(*) FROM selection_attempts WHERE hypothesis_id=?",
            (h.hypothesis_id,),
        ).fetchone()[0]
        prior_hyp = conn.execute(
            "SELECT COUNT(*) FROM hypotheses WHERE hypothesis_id=?",
            (h.hypothesis_id,),
        ).fetchone()[0]
    if existing_live or prior_hyp:
        raise ValueError("Hypothesis is already in live research history; do not reissue")
    with registry._connect() as conn:
        prior = conn.execute(
            "SELECT COUNT(*) FROM selection_attempts WHERE hypothesis_id=?",
            (h.hypothesis_id,),
        ).fetchone()[0]
    if prior:
        raise ValueError("Screen already attempted in snapshot; preserve previous evidence")

    registry.register_contract(contract)
    registry.register_hypothesis(h)
    report = runner.run_stage1_screen(spec_path)
    with registry._connect() as conn:
        rows = conn.execute(
            "SELECT attempt_id,outcome,returns_sha256,data_provenance,diagnostics_json "
            "FROM selection_attempts WHERE hypothesis_id=? AND kind='standalone_screen'",
            (h.hypothesis_id,),
        ).fetchall()
        totals = {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("hypotheses", "trials", "selection_attempts")
        }
    if len(rows) != 1:
        raise ValueError("Expected exactly one recorded Stage 1 screen attempt")
    result = {
        "stage1": report.to_dict(),
        "outcome": rows[0][1],
        "attempt_id": rows[0][0],
        "returns_sha256": rows[0][2],
        "data_provenance": rows[0][3],
        "data_sha256": dataset.sha256,
        "contract_id": contract.contract_id,
        "hypothesis_id": h.hypothesis_id,
        "snapshot_origin": snapshot,
        "snapshot_counts_after_screen": totals,
        "lineage_warning": "Independent historical snapshot, not authoritative live registry",
    }
    if result["outcome"] != ("PASS" if report.passed else "REJECT"):
        raise ValueError("Recorded Warden outcome mismatches returned report")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-checkout", required=True, type=Path)
    parser.add_argument("--hypothesis", required=True, type=Path)
    parser.add_argument("--experiment", required=True, type=Path)
    parser.add_argument("--snapshot-path", default=".workspace/stage1_optimization/registry.db", type=Path)
    args = parser.parse_args()
    output = research_screen(
        args.source_checkout / "registry" / "registry.db",
        args.snapshot_path, args.hypothesis, args.experiment,
    )
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
