"""Train-only PIT low-volatility factor preflight (NOT Warden Stage 1).

Consumes only 2023-01-01..2024-07-01 exclusive observations from the proposed
as-of universe. Never examines the contract's validation interval or sealed
holdout, and never tunes features/sign against validation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import polars as pl

from scripts.train_only_factor_discovery import hour_rank_ic
from strategies.candidates.arxiv_lowvol_xs_1h import (
    LowVolatilityPipeline,
    _forward_label,
    _piv,
)
from warden.contracts import load_contract
from warden.gates import compute_hac_tstat

PROPOSAL = Path("docs/contract-proposals/CRYPTO_PERPS_PIT100_1H_V1.yaml")
MIN_LIQUIDITY = 25000.0


def calculate() -> dict:
    contract = load_contract(PROPOSAL)
    if contract.contract_id != "CRYPTO_PERPS_PIT100_1H_V1":
        raise ValueError("Unexpected proposed PIT contract")
    import hashlib
    src = Path(contract.exploratory_data_path)
    if hashlib.sha256(src.read_bytes()).hexdigest() != contract.data_snapshot_sha256:
        raise ValueError("Historical PIT source digest drift")
    data = pl.scan_parquet(src).filter(
        (pl.col("timestamp") >= contract.train_start)
        & (pl.col("timestamp") < contract.val_start)
    ).collect()
    if data["timestamp"].max() >= contract.val_start:
        raise ValueError("Validation information reached training-only factor preflight")
    pipeline = LowVolatilityPipeline()
    feat = pipeline.compute(data,contract)
    assets = sorted(feat["asset"].unique().to_list())
    timestamps = sorted(feat["timestamp"].unique().to_list())
    vol = _piv(feat, "realized_volatility_24h", assets)
    liq = _piv(feat, "liquidity_per_hour", assets)
    opens = _piv(feat, "open", assets)
    labels = _forward_label(opens,contract.execution_delay_bars,contract.label_horizon_bars)
    signal = -vol
    ic = hour_rank_ic(signal,labels,liq)
    n = len(timestamps)
    halves = []
    for part in (ic[:n//2],ic[n//2:]):
        valid = part[np.isfinite(part)]
        halves.append({
            "hours_total":len(part),
            "hours_eligible":len(valid),
            "mean_spearman_ic":float(np.mean(valid)) if len(valid) else None,
            "hac_t":float(compute_hac_tstat(valid)) if len(valid) else None,
        })
    return {
        "dataset_sha256":contract.data_snapshot_sha256,
        "candidate":"frozen sign of inverse 24h realized volatility",
        "contract_id":contract.contract_id,
        "train_end_exclusive":str(contract.val_start),
        "actual_train_last":str(data["timestamp"].max()),
        "assets_observed_training_only":len(assets),
        "source_rows_training_only":data.height,
        "preflight_halves":halves,
        "same_fixed_signal_as_hindsight_version":True,
        "data_selection":"monthly asof only; last 30d volume, min 21 observed days",
        "status":"TRAINING_ONLY_PREFLIGHT_NOT_WARDEN_VALIDATION",
    }


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",required=True,type=Path)
    args=parser.parse_args()
    if args.output.exists():
        raise FileExistsError("PIT training-only audit is write-once")
    result=calculate()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    print(json.dumps(result,indent=2,sort_keys=True))


if __name__=="__main__":
    main()
