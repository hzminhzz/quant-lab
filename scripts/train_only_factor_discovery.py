"""Train-only, disclosed eight-factor screen: NO Stage 1 validation access.

All features are timestamp-causal and each label is the *executable* future
Open[t+1]..Open[t+5] return. Research feature selection must occur before
Warden validation. Every feature tried is reported; no threshold optimization.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import polars as pl
from scipy.stats import rankdata

from strategies.candidates.arxiv_price_pressure_xs_1h import (
    PricePressurePipeline,
    _forward_label,
    _piv,
)
from warden.contracts import load_contract
from warden.gates import compute_hac_tstat
from warden.provenance import resolve_dataset

CONTRACT_PATH = Path("contracts/crypto_perps_top100_1h_v1.yaml")
OUT = Path(".workspace/stage1_optimization/train_only_factor_audit.json")


def hour_rank_ic(factor: np.ndarray, labels: np.ndarray, liquid: np.ndarray) -> np.ndarray:
    valid = np.isfinite(factor) & np.isfinite(labels) & np.isfinite(liquid) & (liquid >= 25000)
    x = rankdata(np.where(valid, factor, np.nan), axis=1, nan_policy="omit")
    y = rankdata(np.where(valid, labels, np.nan), axis=1, nan_policy="omit")
    count = valid.sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        divisor = np.maximum(count[:, None], 1)
        xm = np.nansum(x, axis=1, keepdims=True) / divisor
        ym = np.nansum(y, axis=1, keepdims=True) / divisor
        a = x - xm
        b = y - ym
        num = np.nansum(a * b, axis=1)
        den = np.sqrt(np.nansum(a * a, axis=1) * np.nansum(b * b, axis=1))
        ic = num / den
    ic[count < 30] = np.nan
    return ic


def score_training_only() -> dict:
    contract = load_contract(CONTRACT_PATH)
    data = resolve_dataset(contract, Path("."))
    if not data.eligible_market:
        raise ValueError("Training source is not verified market data")
    # The lazy filter is *before* collect. No validation or holdout rows loaded.
    raw = (
        pl.scan_parquet(data.path)
        .filter((pl.col("timestamp") >= contract.train_start)
                & (pl.col("timestamp") < contract.val_start))
        .collect()
    )
    df = PricePressurePipeline().compute(raw, contract)
    assets = sorted(df["asset"].unique().to_list())
    stamps = sorted(df["timestamp"].unique().to_list())
    n = len(stamps)
    if n < 2500:
        raise ValueError("Insufficient training-only hours")
    opens = _piv(df, "open", assets)
    liquidity = _piv(df, "liquidity_per_hour", assets)
    labels = _forward_label(opens, contract.execution_delay_bars, contract.label_horizon_bars)
    r1 = _piv(df, "relative_return_1h", assets)
    r6 = _piv(df, "relative_return_6h", assets)
    r24 = _piv(df, "relative_return_24h", assets)
    clv = _piv(df, "close_location_value", assets)
    shock = _piv(df, "volume_surprise_168h", assets)
    vol = _piv(df, "realized_volatility_24h", assets)
    funding = _piv(df, "funding_rate", assets)
    strength = np.clip(shock, 0, 2)
    factors = {
        "short_1h_residual_reversal": -r1,
        "short_6h_residual_reversal": -r6,
        "relative_24h_momentum": r24,
        "close_location_continuation": clv,
        "high_volume_wick_absorption": -clv * strength,
        "high_volume_1h_reversal": -r1 * strength,
        "high_volume_6h_continuation": r6 * strength,
        "inverse_volatility_quality": -vol,
        "funding_contrarian": -funding,
        "vol_normalized_24h_trend": r24 / (vol + 1e-8),
    }
    # Split the pre-existing TRAINING period in two contiguous parts,
    # excluding its final 5 hours so labels cannot incorporate validation.
    midpoint = n // 2
    entries = []
    for name, signal in factors.items():
        ic = hour_rank_ic(signal, labels, liquidity)
        a, b = ic[:midpoint], ic[midpoint:]
        a, b = a[np.isfinite(a)], b[np.isfinite(b)]
        entries.append({
            "factor": name,
            "first_half_hours": len(a),
            "second_half_hours": len(b),
            "first_half_mean_ic": float(np.mean(a)) if len(a) else None,
            "second_half_mean_ic": float(np.mean(b)) if len(b) else None,
            "first_half_hac_t": compute_hac_tstat(a),
            "second_half_hac_t": compute_hac_tstat(b),
        })
    eligible = [
        item for item in entries
        if min(item["first_half_mean_ic"], item["second_half_mean_ic"]) > 0.015
        and min(item["first_half_hac_t"], item["second_half_hac_t"]) > 2.0
    ]
    eligible.sort(key=lambda item: min(item["first_half_mean_ic"], item["second_half_mean_ic"]), reverse=True)
    result = {
        "train_only_end_exclusive": str(contract.val_start),
        "train_only_first": str(stamps[0]),
        "train_only_last": str(stamps[-1]),
        "data_sha256": data.sha256,
        "contract_id": contract.contract_id,
        "n_train_hours": n,
        "factor_probes_disclosed": len(factors),
        "selection_rule_frozen": "Both training halves mean IC>0.015 and HAC t>2; maximize smaller mean IC",
        "chosen_factor": eligible[0]["factor"] if eligible else None,
        "probes": entries,
        "warning": "In-sample factor exploration; not Warden validation, alpha, or independent research",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if OUT.exists():
        raise FileExistsError("Training-only factor screen is write-once")
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main() -> None:
    result = score_training_only()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
