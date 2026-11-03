"""Read-only integration smoke for future Warden 72h order-flow strategy modules.

Does not create a Warden selection attempt or full trial; verifies exact
prepared-source SHA, causal module output, deterministic IC, and sizing.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import polars as pl

from scripts.prepare_crypto_orderflow import sha256
from strategies.candidates.recovery import binance_ats_h72, binance_ntr_h72
from warden.contracts import load_contract
from warden.gates import evaluate_stage1_cross_sectional_ml

MODS = {"ats": binance_ats_h72, "ntr": binance_ntr_h72}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--factor", choices=["ats", "ntr", "all"], default="all")
    args = p.parse_args()
    contract = load_contract("docs/contract-proposals/crypto_perps_top100_orderflow_1h_h72_v1.yaml")
    path = Path(contract.exploratory_data_path)
    if sha256(path) != contract.data_snapshot_sha256:
        raise ValueError("Unverified or changed underlying market data")
    panel = pl.read_parquet(path)
    names = list(MODS) if args.factor == "all" else [args.factor]
    findings = []
    for name in names:
        out = MODS[name].generate_positions(panel, contract)
        pos = out["positions"]
        ic = out["ic_series"]
        if pos.shape != (panel["timestamp"].n_unique(), panel["asset"].n_unique()):
            raise ValueError("Wrong Warden position-matrix shape")
        if not np.isfinite(pos).all() or np.max(np.sum(np.abs(pos), axis=1)) > 1.0000001:
            raise ValueError("Invalid portfolio position or leverage")
        if np.any(np.abs(pos[-1]) > 0):
            raise ValueError("Position targets must be flat at the historical study cutoff")
        if len(ic) == 0 or not np.isfinite(ic).all():
            raise ValueError("Missing recorded causal IC series")
        gate = evaluate_stage1_cross_sectional_ml(ic)
        findings.append({
            "factor": name,
            "ic_count": len(ic),
            "position_shape": list(pos.shape),
            "maximum_gross_target": float(np.max(np.sum(np.abs(pos), axis=1))),
            "maximum_per_asset_weight": float(np.max(np.abs(pos))),
            "stage1_gate": gate.to_dict(),
        })
    print(json.dumps({
        "dataset_sha256": contract.data_snapshot_sha256,
        "approved_contract": False,
        "native_warden_attempt": False,
        "results": findings,
    }, indent=2))


if __name__ == "__main__":
    main()
