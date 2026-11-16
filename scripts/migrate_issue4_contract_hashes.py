#!/usr/bin/env python3
"""Auditable migration: align registry.db contract spec_hash and data_provenance post-Issue-4."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from warden.runner import ExperimentRunner


def migrate() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    db_path = repo_root / "registry" / "registry.db"
    runner = ExperimentRunner(repo_root=repo_root)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    registered = [r[0] for r in conn.execute("SELECT contract_id FROM contracts").fetchall()]
    print(f"Migrating {len(registered)} registered contracts in {db_path}...")

    with conn:
        for cid in registered:
            contract = runner.resolve_contract(cid)
            new_hash = contract.spec_hash()
            new_prov = contract.data_provenance

            old = conn.execute(
                "SELECT spec_hash, data_provenance FROM contracts WHERE contract_id = ?", (cid,)
            ).fetchone()

            conn.execute(
                "UPDATE contracts SET spec_hash = ?, data_provenance = ? WHERE contract_id = ?",
                (new_hash, new_prov, cid),
            )
            print(
                f"  {cid}: spec_hash {old['spec_hash']} -> {new_hash}, "
                f"data_provenance {old['data_provenance']} -> {new_prov}"
            )

    conn.close()

    # Verification: assert register_contract succeeds for all contracts
    print("Verifying contract registration...")
    for cid in registered:
        contract = runner.resolve_contract(cid)
        runner.registry.register_contract(contract)
    print("All contracts successfully aligned and verified.")
    return 0


if __name__ == "__main__":
    sys.exit(migrate())
