"""Unapproved-contract order-flow Stage-1 preflight with Warden's native IC gate.

This does NOT call warden screen/backtest, register a trial, attest a new
contract, or touch protected holdout. It preserves all exploratory attempts
as immutable JSON and requires later Warden authorization for qualification.

Labels are EXACT 72-hour next-open returns, never next available bar or
a price forward-fill. Nonoverlapping 72h UTC decision anchors reduce
overlap-driven HAC inflation. Fit Ridge/scaler inside purged training folds.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from bisect import bisect_left
from datetime import timedelta
from pathlib import Path

import numpy as np
import polars as pl
from scipy.stats import spearmanr
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from scripts.prepare_crypto_orderflow import sha256
from strategies.candidates.recovery.binance_orderflow_factors import FACTOR_FIELDS, build_features
from warden.contracts import EvaluationContract, load_contract
from warden.gates import evaluate_stage1_cross_sectional_ml
from warden.leakage import purged_walk_forward_splits

DATA = Path("data/exploratory/crypto_perps_top100_1h_orderflow_v1.parquet")
PROPOSAL = Path("docs/contract-proposals/crypto_perps_top100_orderflow_1h_h72_v1.yaml")
HYPOTHESES = {
    "ats": "HYP-ORDERFLOW-ATS-H72-20261102",
    "ntr": "HYP-ORDERFLOW-NTR-H72-20261102",
}
ALPHA = 10.0
ANCHOR_HOURS = 72
MIN_CROSS_SECTION = 20


def price_matrix(frame: pl.DataFrame, column: str, assets: list[str]) -> np.ndarray:
    pivot = frame.select("timestamp", "asset", column).pivot(
        index="timestamp", on="asset", values=column
    ).sort("timestamp")
    absent = set(assets) - set(pivot.columns)
    if absent:
        raise ValueError(f"Missing pivot assets: {sorted(absent)}")
    return pivot.select(assets).to_numpy()


def causal_labels(
    timestamps: list, opens: np.ndarray, tradable: np.ndarray,
    horizon: int, delay: int,
) -> np.ndarray:
    """Exact hourly entry/exit existence, no ffill, no label outside input."""
    if horizon < 1 or delay < 1:
        raise ValueError("Horizon and execution delay must both be positive")
    if opens.shape != tradable.shape or opens.shape[0] != len(timestamps):
        raise ValueError("Timestamp/matrix shape mismatch")
    hour_to_idx = {int(ts.timestamp() // 3600): i for i, ts in enumerate(timestamps)}
    if len(hour_to_idx) != len(timestamps):
        raise ValueError("Duplicate timestamps")
    out = np.full(opens.shape, np.nan, dtype=np.float64)
    for i, ts in enumerate(timestamps):
        h = int(ts.timestamp() // 3600)
        j = hour_to_idx.get(h + delay)
        k = hour_to_idx.get(h + delay + horizon)
        if j is None or k is None:
            continue
        entry, end = opens[j], opens[k]
        ok = (tradable[i] & tradable[j] & tradable[k]
              & np.isfinite(entry) & np.isfinite(end)
              & (entry > 0) & (end > 0))
        out[i, ok] = end[ok] / entry[ok] - 1.0
    return out


def _matrix_features(features: pl.DataFrame, kind: str, assets: list[str]) -> np.ndarray:
    vectors = [price_matrix(features, col, assets) for col in FACTOR_FIELDS[kind]]
    return np.stack(vectors, axis=2)


def evaluate(
    panel: pl.DataFrame, contract: EvaluationContract, kind: str,
    *, export_positions: bool = False,
) -> dict | tuple[dict, np.ndarray]:
    if kind not in HYPOTHESES or contract.label_horizon_bars != 72:
        raise ValueError("Frozen 72h ATS/NTR study only")
    frame = panel.sort("timestamp", "asset")
    assets = sorted(frame["asset"].unique().to_list())
    timestamps = sorted(frame["timestamp"].unique().to_list())
    start = bisect_left(timestamps, contract.val_start)
    end = bisect_left(timestamps, contract.val_end)
    if start < 720 or end <= start or timestamps[end - 1] >= contract.val_end:
        raise ValueError("Incorrect validation boundaries or insufficient warmup")
    open_mat = price_matrix(frame, "open", assets)
    traded = price_matrix(frame, "flow_valid", assets).astype(bool)
    tradable = traded & np.isfinite(open_mat) & (open_mat > 0)
    ys = causal_labels(
        timestamps, open_mat, tradable,
        contract.label_horizon_bars, contract.execution_delay_bars,
    )
    alt = np.array([a != "BTCUSDT" for a in assets])
    ys[:, ~alt] = np.nan
    # Cross-sectional demean using only valid forward returns at the
    # SAME decision clock; no future information in feature matrices.
    n_observed = np.isfinite(ys).sum(axis=1)
    center = np.divide(
        np.nansum(ys, axis=1),
        n_observed,
        out=np.zeros(len(ys), dtype=float),
        where=n_observed > 0,
    )
    ys = ys - center[:, None]

    feat = build_features(frame, kind)
    xs = _matrix_features(feat, kind, assets)
    anchors = np.array([
        (int(ts.timestamp() // 3600) % ANCHOR_HOURS) == 0 for ts in timestamps
    ], dtype=bool)
    oos_scores = np.full(open_mat.shape, np.nan, dtype=np.float64)
    folds = []
    splits = purged_walk_forward_splits(
        n_timestamps=end, n_folds=contract.n_folds,
        min_train_timestamps=start,
        label_horizon=contract.label_horizon_bars,
        embargo=contract.embargo_bars,
        execution_delay=contract.execution_delay_bars,
    )
    for fid, (train_idx, test_idx) in enumerate(splits):
        train_idx = train_idx[anchors[train_idx] & (train_idx >= 720)]
        test_idx = test_idx[anchors[test_idx]]
        if len(train_idx) == 0 or len(test_idx) == 0:
            raise ValueError(f"Fold {fid} has no approved training or testing anchors")
        # Use actual wall-clock latency, not merely the number of
        # observed timestamps, for the final purge assertion.
        last_train_exit = timestamps[int(train_idx[-1])] + timedelta(
            hours=contract.execution_delay_bars + contract.label_horizon_bars
        )
        test_start = timestamps[int(test_idx[0])]
        if last_train_exit + timedelta(hours=contract.embargo_bars) > test_start:
            raise ValueError("Fold training label would violate forward embargo")
        x = xs[train_idx].reshape(-1, xs.shape[-1])
        y = ys[train_idx].reshape(-1)
        allowed = np.tile(alt, len(train_idx))
        ok = np.isfinite(x).all(axis=1) & np.isfinite(y) & allowed
        if int(ok.sum()) < 100:
            raise ValueError(f"Fold {fid} lacks 100 causal training samples")
        scaler = StandardScaler().fit(x[ok])
        model = Ridge(alpha=ALPHA).fit(scaler.transform(x[ok]), y[ok])
        x_test = xs[test_idx].reshape(-1, xs.shape[-1])
        ok_test = np.isfinite(x_test).all(axis=1) & np.tile(alt, len(test_idx))
        scores = np.full(len(x_test), np.nan, dtype=np.float64)
        scores[ok_test] = model.predict(scaler.transform(x_test[ok_test]))
        oos_scores[test_idx] = scores.reshape(len(test_idx), -1)
        folds.append({
            "fold": fid + 1,
            "train_max": str(timestamps[int(train_idx[-1])]),
            "test_start": str(timestamps[int(test_idx[0])]),
            "training_rows": int(ok.sum()),
            "test_anchors": len(test_idx),
        })
    ic_series: list[float] = []
    eligible_count: list[int] = []
    skipped = 0
    for t in range(start, end):
        if not anchors[t]:
            continue
        scores = oos_scores[t]
        label = ys[t]
        ok = alt & np.isfinite(scores) & np.isfinite(label)
        if int(ok.sum()) < MIN_CROSS_SECTION:
            skipped += 1
            continue
        ic = float(spearmanr(scores[ok], label[ok]).statistic)
        if not np.isfinite(ic):
            skipped += 1
            continue
        ic_series.append(ic)
        eligible_count.append(int(ok.sum()))
    if not ic_series:
        raise ValueError("No valid independent validation anchors")
    # Warden's actual family gate; this is an explicit preflight because
    # the contract is NOT signed or registered in the approved manifest.
    gate = evaluate_stage1_cross_sectional_ml(ic_series)
    result = {
        "hypothesis_id": HYPOTHESES[kind],
        "original_hypothesis": "HYP-2026-050" if kind == "ats" else "HYP-2026-054",
        "factor": kind,
        "label_horizon_hours": contract.label_horizon_bars,
        "decision_sampling_hours": ANCHOR_HOURS,
        "validation_start": str(contract.val_start),
        "validation_end_exclusive": str(contract.val_end),
        "n_valid_ics": len(ic_series),
        "n_skipped_anchors": skipped,
        "median_assets": float(np.median(eligible_count)),
        "folds": folds,
        "gate": gate.to_dict(),
        "ic_series": ic_series,
        "preview_only": True,
        "native_warden_screen": False,
        "scientific_qualification": False,
    }
    if not export_positions:
        return result
    # Desired portfolio targets, not assured fills. Do not use realised
    # future prices/labels to decide trade eligibility or ranking.
    target = np.zeros_like(open_mat, dtype=float)
    last_target = np.zeros(len(assets), dtype=float)
    for i in range(start, end):
        if anchors[i]:
            # The study cutoff was frozen in advance. Require a complete
            # 72h holding interval within validation before initiating
            # new exposure; never let Stage3 mark a truncated trade.
            exit_time = timestamps[i] + timedelta(
                hours=contract.execution_delay_bars + contract.label_horizon_bars
            )
            scores = oos_scores[i]
            eligible = alt & tradable[i] & np.isfinite(scores)
            idx = np.flatnonzero(eligible)
            if exit_time < contract.val_end and len(idx) >= 2 * 5:
                ranked = idx[np.argsort(scores[idx])]
                last_target = np.zeros(len(assets), dtype=float)
                last_target[ranked[-5:]] = 0.1
                last_target[ranked[:5]] = -0.1
            else:
                last_target = np.zeros(len(assets), dtype=float)
        target[i] = last_target
    return result, target


def write_once(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        if path.read_bytes() != payload:
            raise FileExistsError(f"Immutable preflight evidence already differs: {path}")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
        temp = Path(fh.name)
    try:
        os.link(temp, path)
    except FileExistsError:
        if path.read_bytes() != payload:
            raise
    finally:
        temp.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    contract = load_contract(PROPOSAL)
    if contract.contract_id != "CRYPTO_PERPS_TOP100_ORDERFLOW_1H_H72_V1":
        raise ValueError("Wrong, non-frozen proposal")
    h = sha256(DATA)
    if h != contract.data_snapshot_sha256:
        raise ValueError(f"Unexpected Binance data digest: {h}")
    panel = pl.read_parquet(DATA)
    if panel["timestamp"].max() >= contract.val_end:
        raise ValueError("Protected holdout/time-boundary contamination")
    outputs = [evaluate(panel, contract, key) for key in ("ats", "ntr")]
    result = {
        "mode": "UNAPPROVED_WARDEN_GATE_PREFLIGHT",
        "contract_proposal": str(PROPOSAL),
        "data_path": str(DATA),
        "data_sha256": h,
        "protocol": "4 purged walk-forward folds; Ridge alpha10; exact 72h delayed open labels",
        "global_search_debt": "Original registry has 149 hypotheses/75 full trials; this is NOT a fresh independent family",
        "results": outputs,
    }
    payload = (json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    write_once(args.output, payload)
    print(json.dumps({
        "output": str(args.output),
        "output_sha256": hashlib.sha256(payload).hexdigest(),
        "gate_summary": [
            {k: out[k] for k in ("hypothesis_id", "n_valid_ics", "n_skipped_anchors")}
            | {"passed": out["gate"]["passed"], "metrics": out["gate"]["metrics"]}
            for out in outputs
        ],
    }, indent=2))


if __name__ == "__main__":
    main()
