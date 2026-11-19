"""AF30 Handcrafted Book: forecast-level combination of 20 AF30 strategies on MULTI_ASSET_1H_V1.

Diversifies across 17 liquid multi-asset instruments (6 equity indices, 8 FX pairs, 2 metals, 1 energy commodity)
with equal asset-class shares (0.25 each) and equal weighting within each class.
Uses train-window correlation for Instrumental Diversification Multiplier (IDM) and cash volatility targeting.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from warden.contracts import EvaluationContract
from warden.strategy_loader import load_module_from_path

STRATEGY_MODULES: list[str] = [
    "strategies/candidates/ts_s01_buy_hold_single_1h.py",
    "strategies/candidates/ts_s02_buy_hold_risk_scaled_1h.py",
    "strategies/candidates/ts_s03_buy_hold_variable_risk_1h.py",
    "strategies/candidates/ts_s04_buy_hold_portfolio_scaling_1h.py",
    "strategies/candidates/ts_s05_slow_trend_long_only_1h.py",
    "strategies/candidates/ts_s06_slow_trend_long_short_1h.py",
    "strategies/candidates/ts_s07_slow_trend_strength_1h.py",
    "strategies/candidates/ts_s08_fast_trend_strength_1h.py",
    "strategies/candidates/ts_ewmac_multi_1h.py",
    "strategies/candidates/ts_s12_adjusted_trend_1h.py",
    "strategies/candidates/ts_s13_trend_carry_regimes_1h.py",
    "strategies/candidates/ts_s14_spot_trend_1h.py",
    "strategies/candidates/ts_s17_normalised_trend_1h.py",
    "strategies/candidates/ts_s18_trend_asset_classes_1h.py",
    "strategies/candidates/ts_s19_xs_momentum_1h.py",
    "strategies/candidates/ts_breakout_20_1h.py",
    "strategies/candidates/ts_s22_value_1h.py",
    "strategies/candidates/ts_s23_acceleration_1h.py",
    "strategies/candidates/ts_s26_fast_mean_reversion_1h.py",
    "strategies/candidates/ts_s27_safer_fast_mean_reversion_1h.py",
]

CLASS_OF: dict[str, str] = {
    "ES": "equity",
    "NQ": "equity",
    "DAX": "equity",
    "NIKKEI225": "equity",
    "EUSIDXEUR": "equity",
    "HK33": "equity",
    "EURUSD": "fx",
    "GBPUSD": "fx",
    "USDJPY": "fx",
    "AUDUSD": "fx",
    "USDCAD": "fx",
    "USDCHF": "fx",
    "CADJPY": "fx",
    "GBPJPY": "fx",
    "XAUUSD": "metals",
    "XAGUSD": "metals",
    "CL": "energy",
}

_class_counts: dict[str, int] = {}
for _c in CLASS_OF.values():
    _class_counts[_c] = _class_counts.get(_c, 0) + 1

HANDCRAFTED_WEIGHTS: dict[str, float] = {
    sym: 0.25 / _class_counts[cls] for sym, cls in CLASS_OF.items()
}
assert abs(sum(HANDCRAFTED_WEIGHTS.values()) - 1.0) < 1e-12

LAST_BOOK_STATS: dict[str, Any] = {}

_LOADED_MODULES: dict[str, Any] = {}


def _get_module(path_str: str) -> Any:
    if path_str not in _LOADED_MODULES:
        _LOADED_MODULES[path_str] = load_module_from_path(Path(path_str))
    return _LOADED_MODULES[path_str]


class BookPipeline:
    """Combines forecasts across 20 AF30 strategy pipelines prefix-invariantly."""

    def __init__(self, strategy_modules: list[str] = STRATEGY_MODULES) -> None:
        self.strategy_modules = strategy_modules

    def compute(self, panel_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = panel_df.collect() if isinstance(panel_df, pl.LazyFrame) else panel_df
        if "symbol" in df.columns and "asset" not in df.columns:
            df = df.rename({"symbol": "asset"})

        f_frames = []
        for idx, m_path in enumerate(self.strategy_modules):
            mod = _get_module(m_path)
            pipe = getattr(mod, "pipeline", None)
            if pipe is None:
                raise AttributeError(f"{m_path} does not expose a pipeline")
            res = pipe.compute(df, contract).select(["timestamp", "asset", "forecast"])
            res = res.rename({"forecast": f"fc_{idx}"})
            f_frames.append(res)

        joined = f_frames[0]
        for idx in range(1, len(f_frames)):
            joined = joined.join(f_frames[idx], on=["timestamp", "asset"], how="full", coalesce=True)

        fc_cols = [f"fc_{idx}" for idx in range(len(f_frames))]
        joined = joined.with_columns([pl.col(c).fill_null(0.0) for c in fc_cols])
        out = (
            joined.with_columns(forecast=pl.mean_horizontal(*fc_cols))
            .select(["timestamp", "asset", "forecast"])
            .sort("timestamp", "asset")
        )
        return out


pipeline = BookPipeline()


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    vol_target: float = 0.20,
    per_instrument_cap: float = 0.5,
    stop_multiple: float = 3.0,
    rebalance_bars: int | None = None,
    deadband: float = 0.10,
    warmup: int | None = None,
    idm_bounds: tuple[float, float] = (1.0, 2.5),
    target_symbols: list[str] | None = None,
    strategy_modules: list[str] = STRATEGY_MODULES,
    **kwargs: Any,
) -> dict[str, Any]:
    """Generate positions for handcrafted book combining 20 AF30 strategies."""
    if rebalance_bars is None:
        rebalance_bars = 5 if contract.frequency == "1d" else (30 if contract.frequency == "4h" else 24)
    if warmup is None:
        warmup = 60 if contract.frequency == "1d" else 241
    vol_window = kwargs.get("vol_window", 60 if contract.frequency == "1d" else 240)
    if "symbol" in panel_df.columns and "asset" not in panel_df.columns:
        panel_df = panel_df.rename({"symbol": "asset"})

    assets = sorted(panel_df["asset"].unique().to_list())
    timestamps = sorted(panel_df["timestamp"].unique().to_list())
    n_bars, n_assets = len(timestamps), len(assets)

    if n_bars == 0 or n_assets == 0:
        return {"positions": np.zeros((n_bars, n_assets), dtype=float), "forecast": np.zeros((n_bars, n_assets), dtype=float)}

    w = np.array([HANDCRAFTED_WEIGHTS.get(a, 1.0 / n_assets) for a in assets], dtype=float)
    w = w / np.sum(w)

    # Observation mask for asynchronous sessions
    obs_mat = (
        panel_df.with_columns(_obs=pl.lit(True))
        .pivot(index="timestamp", on="asset", values="_obs")
        .fill_null(False)
        .sort("timestamp")
        .select(assets)
        .to_numpy()
    )

    close_mat = (
        panel_df.pivot(index="timestamp", on="asset", values="close")
        .sort("timestamp")
        .fill_null(strategy="forward")
        .fill_null(strategy="backward")
        .fill_null(0.0)
        .select(assets)
        .to_numpy()
    )
    high_mat = (
        panel_df.pivot(index="timestamp", on="asset", values="high")
        .sort("timestamp")
        .fill_null(strategy="forward")
        .fill_null(strategy="backward")
        .fill_null(0.0)
        .select(assets)
        .to_numpy()
    )
    low_mat = (
        panel_df.pivot(index="timestamp", on="asset", values="low")
        .sort("timestamp")
        .fill_null(strategy="forward")
        .fill_null(strategy="backward")
        .fill_null(0.0)
        .select(assets)
        .to_numpy()
    )

    prev_close_col = pl.col("close").shift(1).over("asset")
    tr = pl.max_horizontal(
        pl.col("high") - pl.col("low"),
        (pl.col("high") - prev_close_col).abs(),
        (pl.col("low") - prev_close_col).abs(),
    )
    atr_mat = (
        panel_df.with_columns(atr_14=tr.rolling_mean(window_size=14).over("asset"))
        .pivot(index="timestamp", on="asset", values="atr_14")
        .sort("timestamp")
        .fill_null(strategy="forward")
        .fill_null(strategy="backward")
        .fill_null(0.0)
        .select(assets)
        .to_numpy()
    )

    ret = np.zeros_like(close_mat)
    prev_close = close_mat[:-1]
    denom = np.where(prev_close > 1e-6, prev_close, 1.0)
    ret[1:] = np.where(obs_mat[1:], (close_mat[1:] - prev_close) / denom, 0.0)

    all_pos: list[np.ndarray] = []
    all_fc: list[np.ndarray] = []
    for m_path in strategy_modules:
        mod = _get_module(m_path)
        out = mod.generate_positions(panel_df, contract)
        pos = out["positions"]
        fc = out.get("forecast")
        if fc is None or fc.shape != pos.shape:
            fc = np.zeros_like(pos)
        all_pos.append(pos)
        all_fc.append(fc)

    combined = np.mean(all_fc, axis=0)

    effective_warmup = min(warmup, max(1, n_bars // 4)) if warmup >= n_bars else warmup
    train_indices = [t for t, ts in enumerate(timestamps) if ts < contract.val_start and t >= effective_warmup]
    if len(train_indices) < 20:
        train_indices = [t for t, ts in enumerate(timestamps) if ts < contract.val_start]
    if len(train_indices) < 20:
        train_indices = list(range(n_bars))

    val_indices = [t for t, ts in enumerate(timestamps) if contract.val_start <= ts < contract.val_end]

    mean_pos = np.mean(all_pos, axis=0)
    r_instr = mean_pos * ret

    r_train = r_instr[train_indices, :]
    if r_train.shape[0] > 1:
        rho = np.asarray(np.corrcoef(r_train, rowvar=False), dtype=float)
        rho = np.nan_to_num(rho, nan=0.0)
        np.fill_diagonal(rho, 1.0)
    else:
        rho = np.eye(n_assets, dtype=float)

    port_var = float(w @ rho @ w)
    raw_idm = 1.0 / math.sqrt(max(port_var, 1e-12))
    idm = float(np.clip(raw_idm, idm_bounds[0], idm_bounds[1]))

    vol_mat = np.full((n_bars, n_assets), 1e-4, dtype=float)
    for i in range(n_assets):
        s = pl.Series(ret[:, i]).rolling_std(window_size=vol_window).shift(1).fill_null(1e-4).to_numpy()
        vol_mat[:, i] = np.maximum(s, 1e-6)

    raw_risk_share = w[None, :] / vol_mat
    sum_risk = np.sum(raw_risk_share, axis=1, keepdims=True)
    sum_risk = np.where(sum_risk > 1e-12, sum_risk, 1.0)
    risk_share = raw_risk_share / sum_risk
    pos_unit = (combined / 20.0) * risk_share * idm

    port_ret_train = np.sum(pos_unit[train_indices] * ret[train_indices], axis=1)
    ann_std = float(np.std(port_ret_train) * math.sqrt(contract.bars_per_year))
    if ann_std < 1e-6 or np.isnan(ann_std):
        ann_std = vol_target
    vol_scale = float(np.clip(vol_target / ann_std, 0.5, 3.0))

    target_pos = np.clip(pos_unit * vol_scale, -per_instrument_cap, per_instrument_cap)

    if target_symbols is not None:
        active_indices = [i for i, a in enumerate(assets) if a in target_symbols]
    else:
        active_indices = list(range(n_assets))

    positions = np.zeros((n_bars, n_assets), dtype=float)
    peak_prices = np.zeros(n_assets, dtype=float)
    trough_prices = np.full(n_assets, 1e12, dtype=float)

    for t in range(effective_warmup, n_bars):
        if t % rebalance_bars == 0:
            for idx in active_indices:
                held = positions[t - 1, idx]
                if not obs_mat[t, idx]:
                    positions[t, idx] = held
                else:
                    tgt = target_pos[t, idx]
                    if abs(held) < 1e-6 or abs(tgt - held) > deadband * abs(held):
                        positions[t, idx] = tgt
                    else:
                        positions[t, idx] = held
                    peak_prices[idx] = high_mat[t, idx]
                    trough_prices[idx] = low_mat[t, idx]
        else:
            positions[t] = positions[t - 1].copy()

        for idx in active_indices:
            if not obs_mat[t, idx]:
                continue
            pos = positions[t, idx]
            atr = atr_mat[t, idx]
            if pos > 1e-6:
                peak_prices[idx] = max(peak_prices[idx], high_mat[t, idx])
                stop_price = peak_prices[idx] - stop_multiple * atr
                if close_mat[t, idx] < stop_price:
                    positions[t, idx] = 0.0
            elif pos < -1e-6:
                trough_prices[idx] = min(trough_prices[idx], low_mat[t, idx])
                stop_price = trough_prices[idx] + stop_multiple * atr
                if close_mat[t, idx] > stop_price:
                    positions[t, idx] = 0.0
    eye_mask = ~np.eye(n_assets, dtype=bool)
    off_diag = rho[eye_mask]
    corr_mean_off_diag = float(np.mean(off_diag)) if len(off_diag) else 0.0
    corr_max_off_diag = float(np.max(off_diag)) if len(off_diag) else 0.0

    LAST_BOOK_STATS.clear()
    LAST_BOOK_STATS.update({
        "weights": {a: float(w[i]) for i, a in enumerate(assets)},
        "idm": float(idm),
        "vol_scale": float(vol_scale),
        "train_ann_vol": float(ann_std),
        "corr_mean_off_diag": corr_mean_off_diag,
        "corr_max_off_diag": corr_max_off_diag,
        "corr_matrix": rho.tolist(),
        "assets": assets,
        "train_indices": train_indices,
        "val_indices": val_indices,
        "returns": ret,
        "per_strategy_positions": all_pos,
        "strategy_modules": strategy_modules,
    })

    return {"positions": positions, "forecast": combined}
