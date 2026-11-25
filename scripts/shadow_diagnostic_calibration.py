"""Read-only shadow calibration comparing Warden gates/registry math vs ml4t-diagnostic."""

from __future__ import annotations

import argparse
import itertools
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from ml4t.diagnostic.evaluation.stats import (
    compute_pbo,
    deflated_sharpe_ratio,
    deflated_sharpe_ratio_from_statistics,
    rademacher_complexity,
    ras_sharpe_adjustment,
)
from ml4t.diagnostic.metrics import compute_ic_hac_stats
from scipy import stats as scipy_stats

from warden.gates import IC_T_FLOOR, compute_hac_tstat, per_bar_sharpe
from warden.registry import compute_selection_report
from warden_ops.readonly import ReadOnlyResearchRegistry


def _valid_return_vector(values: Any) -> np.ndarray | None:
    arr = np.asarray(values, dtype=float)
    if arr.ndim == 1 and len(arr) >= 10 and np.isfinite(arr).all() and float(np.std(arr)) >= 1e-12:
        return arr
    return None


def _load_trial_returns(row: dict[str, Any], repo_root: Path, returns_dir: Path) -> np.ndarray | None:
    parquet_raw = row["returns_parquet_path"] or ""
    candidates: list[Path] = []
    if parquet_raw:
        p = Path(parquet_raw)
        candidates.append(p if p.is_absolute() else (repo_root / p))
    candidates.append(returns_dir / row["contract_id"] / f"{row['run_id']}.parquet")
    for candidate in candidates:
        if candidate.is_file():
            df = pl.read_parquet(candidate)
            if "net_return" in df.columns:
                vec = _valid_return_vector(df["net_return"].to_numpy())
                if vec is not None:
                    return vec
    r_json = row["returns_json"] or ""
    if r_json and r_json != "[]":
        return _valid_return_vector(json.loads(r_json))
    return None


def _matrix_per_bar_sharpes(matrix: np.ndarray) -> np.ndarray:
    return np.asarray([per_bar_sharpe(matrix[:, j]) for j in range(matrix.shape[1])], dtype=float)


def _calibrate_dsr_pbo_ras(
    contracts: dict[str, dict[str, Any]],
    trial_rows: list[dict[str, Any]],
    selection_attempt_rows: list[dict[str, Any]],
    repo_root: Path,
    returns_dir: Path,
    *,
    pbo_blocks: int,
    ras_simulations: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    # Group non-degenerate trial return streams by (contract_id, length) and index aligned selection_attempts.
    grouped: dict[tuple[str, int], list[tuple[dict[str, Any], np.ndarray]]] = {}
    unvectorized_by_contract: dict[str, int] = {}
    vectorized_attempts_by_key: dict[tuple[str, int], list[np.ndarray]] = {}

    for row in trial_rows:
        cid = row["contract_id"]
        if row["stage_reached"] == "STAGE_1_SIGNAL":
            unvectorized_by_contract[cid] = unvectorized_by_contract.get(cid, 0) + 1
            continue
        vec = _load_trial_returns(row, repo_root, returns_dir)
        if vec is None:
            unvectorized_by_contract[cid] = unvectorized_by_contract.get(cid, 0) + 1
            continue
        grouped.setdefault((cid, len(vec)), []).append((row, vec))

    for arow in selection_attempt_rows:
        cid = arow["contract_id"]
        kind = arow["kind"]
        fam = arow["strategy_family"]
        r_json = arow["returns_json"] or ""
        if arow["returns_observed"] and (kind == "param_grid" or (kind == "standalone_screen" and fam == "rules_ts")) and r_json:
            vec = _valid_return_vector(json.loads(r_json))
            if vec is not None:
                vectorized_attempts_by_key.setdefault((cid, len(vec)), []).append(vec)
                continue
        unvectorized_by_contract[cid] = unvectorized_by_contract.get(cid, 0) + 1

    dsr_comparisons: list[dict[str, Any]] = []
    pbo_diagnostics: list[dict[str, Any]] = []
    ras_diagnostics: list[dict[str, Any]] = []
    family_floors: dict[str, tuple[float, float]] = {}

    for (cid, n_bars), entries in sorted(grouped.items()):
        cinfo = contracts.get(cid, {})
        bars_per_year = int(cinfo.get("bars_per_year") or 252)
        fam_key = f"{cinfo.get('asset_class', 'unknown')}:{cinfo.get('frequency', 'unknown')}"
        prior_keff, prior_noise = family_floors.get(fam_key, (1.0, 0.0))

        aligned_attempts = vectorized_attempts_by_key.get((cid, n_bars), [])
        unvec_count = unvectorized_by_contract.get(cid, 0)
        prior_streams: list[np.ndarray] = list(aligned_attempts)

        for row, vec in entries:
            raw_attempts = len(prior_streams) + unvec_count + 1
            legacy_report = compute_selection_report(
                vec,
                prior_streams,
                bars_per_year,
                raw_attempt_count=raw_attempts,
                prior_max_keff=prior_keff,
                prior_max_noise=prior_noise,
            )
            prior_keff = max(prior_keff, float(legacy_report["k_eff_gate"]))
            prior_noise = max(prior_noise, float(legacy_report["expected_max_sharpe_noise"]))
            family_floors[fam_key] = (prior_keff, prior_noise)

            all_streams = [*prior_streams, vec]
            sharpes = [per_bar_sharpe(s) for s in all_streams]
            sr_std = max(float(np.std(sharpes, ddof=1)) if len(sharpes) > 1 else 0.0, 1.0 / math.sqrt(n_bars))
            sr_bar = sharpes[-1]
            skew = float(scipy_stats.skew(vec))
            ex_kurt = float(scipy_stats.kurtosis(vec, fisher=True))
            ml4t_stats_res = deflated_sharpe_ratio_from_statistics(
                observed_sharpe=sr_bar,
                n_samples=n_bars,
                n_trials=int(legacy_report["raw_attempt_count"]),
                variance_trials=sr_std**2,
                skewness=skew,
                excess_kurtosis=ex_kurt,
                effective_trials=float(legacy_report["k_eff_gate"]),
                periods_per_year=bars_per_year,
            )
            ml4t_matrix_prob: float | None = None
            if len(all_streams) >= 2:
                mat = np.column_stack(all_streams)
                ml4t_matrix_prob = float(
                    deflated_sharpe_ratio(
                        mat, periods_per_year=bars_per_year, correlation_method="effective_rank"
                    ).probability
                )
            recorded_prob = float(row["deflated_sharpe_prob"]) if row["deflated_sharpe_prob"] is not None else None
            dsr_comparisons.append(
                {
                    "run_id": row["run_id"],
                    "contract_id": cid,
                    "n_bars": n_bars,
                    "recorded_verdict": row["verdict"],
                    "recorded_dsr_prob": recorded_prob,
                    "legacy_dsr_prob": float(legacy_report["probability"]),
                    "ml4t_dsr_from_stats_prob": float(ml4t_stats_res.probability),
                    "ml4t_dsr_matrix_prob": ml4t_matrix_prob,
                    "delta_ml4t_vs_legacy": float(ml4t_stats_res.probability) - float(legacy_report["probability"]),
                    "delta_ml4t_vs_recorded": (
                        float(ml4t_stats_res.probability) - recorded_prob if recorded_prob is not None else None
                    ),
                }
            )
            prior_streams.append(vec)

        if len(entries) >= 2:
            matrix = np.column_stack([vec for _, vec in entries])
            n_strat = matrix.shape[1]

            z_mat = matrix / (np.std(matrix, axis=0, ddof=1) + 1e-12)
            rademacher = float(rademacher_complexity(z_mat, n_simulations=ras_simulations, random_state=42))
            sharpe_vec = _matrix_per_bar_sharpes(matrix)
            ras_res = ras_sharpe_adjustment(
                sharpe_vec,
                complexity=rademacher,
                n_samples=n_bars,
                n_strategies=n_strat,
                delta=0.05,
                return_result=True,
            )
            ras_diagnostics.append(
                {
                    "contract_id": cid,
                    "n_bars": n_bars,
                    "n_strategies": n_strat,
                    "rademacher_complexity": rademacher,
                    "data_snooping_penalty": float(ras_res.data_snooping_penalty),
                    "max_observed_per_bar_sr": float(np.max(sharpe_vec)),
                    "max_adjusted_per_bar_sr": float(np.max(ras_res.adjusted_values)),
                    "n_significant": int(ras_res.n_significant),
                }
            )

            if pbo_blocks >= 4 and pbo_blocks % 2 == 0 and n_bars >= pbo_blocks * 4:
                usable = (n_bars // pbo_blocks) * pbo_blocks
                blocks = np.split(matrix[:usable], pbo_blocks, axis=0)
                is_rows, oos_rows = [], []
                all_idx = set(range(pbo_blocks))
                for is_idx in itertools.combinations(range(pbo_blocks), pbo_blocks // 2):
                    oos_idx = tuple(sorted(all_idx - set(is_idx)))
                    is_rows.append(_matrix_per_bar_sharpes(np.concatenate([blocks[i] for i in is_idx], axis=0)))
                    oos_rows.append(_matrix_per_bar_sharpes(np.concatenate([blocks[i] for i in oos_idx], axis=0)))
                pbo_res = compute_pbo(np.asarray(is_rows), np.asarray(oos_rows))
                pbo_diagnostics.append(
                    {
                        "contract_id": cid,
                        "n_bars": n_bars,
                        "n_strategies": n_strat,
                        "pbo_blocks": pbo_blocks,
                        "n_combinations": int(pbo_res.n_combinations),
                        "pbo": float(pbo_res.pbo),
                        "pbo_pct": float(pbo_res.pbo_pct),
                        "degradation_mean": float(pbo_res.degradation_mean),
                    }
                )

    return dsr_comparisons, pbo_diagnostics, ras_diagnostics


def _calibrate_ic_hac(
    contracts: dict[str, dict[str, Any]],
    trial_rows: list[dict[str, Any]],
    selection_attempt_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    ic_sources: list[tuple[str, str, str, np.ndarray]] = []
    for row in trial_rows:
        if row["strategy_family"] != "cross_sectional_ml":
            continue
        diag = json.loads(row["diagnostics_json"] or "{}")
        vec = (diag.get("stage1_signal") or {}).get("vector")
        if vec:
            ic_sources.append(("trial", row["run_id"], row["contract_id"], np.asarray(vec, dtype=float)))
    for row in selection_attempt_rows:
        if row["strategy_family"] != "cross_sectional_ml" or row["kind"] != "standalone_screen":
            continue
        r_json = row["returns_json"] or ""
        if r_json and r_json != "[]":
            ic_sources.append(("screen", row["attempt_id"], row["contract_id"], np.asarray(json.loads(r_json), dtype=float)))

    comparisons: list[dict[str, Any]] = []
    for source_kind, item_id, cid, raw_ic in ic_sources:
        arr = raw_ic[np.isfinite(raw_ic)]
        if len(arr) < 10 or float(np.std(arr)) < 1e-6:
            continue
        legacy_t = float(compute_hac_tstat(arr, max_lags=5))
        ml4t_lag5 = compute_ic_hac_stats(arr, maxlags=5)
        horizon = int(contracts.get(cid, {}).get("label_horizon_bars") or 1)
        ml4t_auto = compute_ic_hac_stats(arr, maxlags=None, label_horizon=horizon)
        t_lag5 = float(ml4t_lag5["t_stat"])
        t_auto = float(ml4t_auto["t_stat"])
        comparisons.append(
            {
                "source_kind": source_kind,
                "id": item_id,
                "contract_id": cid,
                "n_periods": len(arr),
                "legacy_hac_tstat": legacy_t,
                "ml4t_hac_lag5_tstat": t_lag5,
                "ml4t_hac_auto_tstat": t_auto,
                "ml4t_auto_effective_lags": int(ml4t_auto["effective_lags"]),
                "delta_lag5": t_lag5 - legacy_t,
                "delta_auto": t_auto - legacy_t,
                "would_flip_gate_lag5": (legacy_t >= IC_T_FLOOR) != (t_lag5 >= IC_T_FLOOR),
                "would_flip_gate_auto": (legacy_t >= IC_T_FLOOR) != (t_auto >= IC_T_FLOOR),
            }
        )
    return comparisons


def run_shadow_calibration(
    db_path: Path,
    returns_dir: Path,
    *,
    pbo_blocks: int = 8,
    ras_simulations: int = 2000,
) -> dict[str, Any]:
    """Compare ml4t-diagnostic stats against historical Warden gates without mutating registry."""
    db_resolved = Path(db_path).resolve()
    returns_resolved = Path(returns_dir).resolve()
    repo_root = db_resolved.parent.parent

    ledger = ReadOnlyResearchRegistry(db_resolved)
    contracts = {row["contract_id"]: row for row in ledger.read_contracts()}
    trial_rows = ledger.read_trials()
    attempt_rows = ledger.read_attempts()

    dsr_comparisons, pbo_diagnostics, ras_diagnostics = _calibrate_dsr_pbo_ras(
        contracts,
        trial_rows,
        attempt_rows,
        repo_root,
        returns_resolved,
        pbo_blocks=pbo_blocks,
        ras_simulations=ras_simulations,
    )
    ic_hac_comparisons = _calibrate_ic_hac(contracts, trial_rows, attempt_rows)

    dsr_legacy_deltas = [abs(x["delta_ml4t_vs_legacy"]) for x in dsr_comparisons]
    dsr_recorded_deltas = [abs(x["delta_ml4t_vs_recorded"]) for x in dsr_comparisons if x["delta_ml4t_vs_recorded"] is not None]
    hac_deltas = [abs(x["delta_lag5"]) for x in ic_hac_comparisons]
    return {
        "verdicts_mutated": False,
        "summary": {
            "n_trials_evaluated": len(dsr_comparisons),
            "n_ic_series_evaluated": len(ic_hac_comparisons),
            "n_pbo_groups": len(pbo_diagnostics),
            "n_ras_groups": len(ras_diagnostics),
            "max_abs_dsr_delta": max(dsr_legacy_deltas) if dsr_legacy_deltas else 0.0,
            "mean_abs_dsr_delta": float(np.mean(dsr_legacy_deltas)) if dsr_legacy_deltas else 0.0,
            "max_abs_dsr_delta_vs_recorded": max(dsr_recorded_deltas) if dsr_recorded_deltas else 0.0,
            "mean_abs_dsr_delta_vs_recorded": float(np.mean(dsr_recorded_deltas)) if dsr_recorded_deltas else 0.0,
            "max_abs_hac_lag5_delta": max(hac_deltas) if hac_deltas else 0.0,
            "hac_lag5_gate_flips": sum(1 for x in ic_hac_comparisons if x["would_flip_gate_lag5"]),
            "hac_auto_gate_flips": sum(1 for x in ic_hac_comparisons if x["would_flip_gate_auto"]),
        },
        "dsr_comparisons": dsr_comparisons,
        "ic_hac_comparisons": ic_hac_comparisons,
        "pbo_diagnostics": pbo_diagnostics,
        "ras_diagnostics": ras_diagnostics,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only shadow calibration vs ml4t-diagnostic.")
    parser.add_argument("--db", type=Path, default=Path("registry/registry.db"))
    parser.add_argument("--returns-dir", type=Path, default=Path("registry/returns"))
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--pbo-blocks", type=int, default=8)
    parser.add_argument("--ras-simulations", type=int, default=2000)
    args = parser.parse_args()

    report = run_shadow_calibration(
        args.db,
        args.returns_dir,
        pbo_blocks=args.pbo_blocks,
        ras_simulations=args.ras_simulations,
    )
    payload = json.dumps(report, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(json.dumps({"verdicts_mutated": report["verdicts_mutated"], "summary": report["summary"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
