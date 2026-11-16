"""Delisting rule for the HYP-2026-125 strategy module.

A symbol whose data ends must be closed at its own last tradable bar. The current 100-symbol panel
has no delistings, so this is exercised on a synthetic panel with one symbol that stops trading
mid-window: the failure it guards against is a weakly-held dead name at a stale price, which turns
a missing loss into a free short-leg credit.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl

from warden.contracts import EvaluationContract

MODULE = Path(__file__).resolve().parents[2] / "strategies" / "candidates" / "xs_weekly_momentum_1h.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("xs_weekly_momentum_1h", MODULE)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _contract(val_end: datetime) -> EvaluationContract:
    return EvaluationContract(
        contract_id="SYNTH_1H_V1",
        asset_class="crypto_perps",
        frequency="1h",
        bars_per_year=8760,
        exploratory_data_path="data/exploratory/synth_1h.parquet",
        holdout_data_path="data/holdout/synth_1h.parquet",
        train_start=datetime(2023, 12, 1, tzinfo=UTC),
        val_start=datetime(2024, 1, 1, tzinfo=UTC),
        val_end=val_end,
        holdout_start=val_end,
        holdout_end=datetime(2024, 3, 1, tzinfo=UTC),
        n_folds=2,
        label_horizon_bars=168,
        embargo_bars=168,
        execution_delay_bars=1,
        taker_fee_bps=4.0,
        slippage_bps=2.0,
        funding_bps_per_8h=1.0,
        execution_mode="NEXT_BAR",
        min_expected_move_to_cost_ratio=2.5,
        data_provenance="unknown",
    )


def _panel(n_bars: int = 400, dead_last: int = 250) -> tuple[pl.DataFrame, dict[str, int]]:
    t0 = datetime(2024, 1, 1, tzinfo=UTC)
    stamps = [t0 + timedelta(hours=i) for i in range(n_bars)]
    # DEAD trends up hardest so it must be picked as the long leg, then stops trading at dead_last.
    paths = {
        "DEADUSDT": (lambda t: 100.0 * 1.01**t, dead_last + 1),
        "AAAUSDT": (lambda t: 100.0 * 0.99**t, n_bars),
        "BBBUSDT": (lambda t: 100.0 * 1.002**t, n_bars),
        "CCCUSDT": (lambda t: 100.0 * 0.998**t, n_bars),
        "DDDUSDT": (lambda t: 100.0, n_bars),
    }
    frames = []
    for sym, (f, n) in paths.items():
        c = np.array([f(t) for t in range(n)], dtype=float)
        frames.append(
            pl.DataFrame(
                {
                    "timestamp": stamps[:n],
                    "asset": [sym] * n,
                    "open": c,
                    "high": c * 1.001,
                    "low": c * 0.999,
                    "close": c,
                    "volume": np.full(n, 1000.0),
                    "funding_rate": np.zeros(n),
                }
            )
        )
    return pl.concat(frames).sort("timestamp", "asset"), {s: n for s, (_f, n) in paths.items()}


def test_dead_symbol_is_flat_from_its_last_tradable_bar() -> None:
    mod = _load_module()
    panel, lengths = _panel()
    val_end = datetime(2024, 1, 16, tzinfo=UTC)
    out = mod.generate_positions(panel, _contract(val_end), universe_size=4, quintile_fraction=0.25)
    pos = out["positions"]
    assets = sorted(panel["asset"].unique().to_list())
    dead = assets.index("DEADUSDT")
    last = lengths["DEADUSDT"] - 1

    assert np.allclose(pos[last:, dead], 0.0), "delisted symbol must be flat at and after its last bar"
    assert np.any(np.abs(pos[:last, dead]) > 1e-12), "the dead symbol must have been held before it stopped trading"


def test_book_holds_exactly_two_legs_until_a_delisting() -> None:
    mod = _load_module()
    panel, lengths = _panel()
    out = mod.generate_positions(panel, _contract(datetime(2024, 1, 16, tzinfo=UTC)), universe_size=4, quintile_fraction=0.25)
    pos = out["positions"]
    assets = sorted(panel["asset"].unique().to_list())
    dead = assets.index("DEADUSDT")
    dead_last = lengths["DEADUSDT"] - 1
    gross = np.abs(pos).sum(axis=1)
    active = gross > 1e-12
    both_legs = active.copy()
    both_legs[dead_last:] = False

    assert active.any()
    assert np.allclose(gross[both_legs], 1.0), "with both legs alive the book is fully invested"
    assert np.allclose(pos.sum(axis=1)[both_legs], 0.0, atol=1e-12), "with both legs alive the book is neutral"
    assert set(np.unique((np.abs(pos[both_legs]) > 1e-12).sum(axis=1))) == {2}, "exactly n_leg long + n_leg short"

    # Between the delisting and the next weekly rebalance the surviving leg runs one-sided: half
    # gross, net exposure. That is the measured consequence of a weekly cadence, not a choice.
    next_reb = ((dead_last // 168) + 1) * 168
    between = np.zeros_like(active)
    between[dead_last:next_reb] = True
    between &= active
    if between.any():
        assert np.allclose(gross[between], 0.5), "only the surviving leg should remain after the delisting"
        assert set(np.unique((np.abs(pos[between]) > 1e-12).sum(axis=1))) == {1}

    # The next rebalance rebuilds a full book from the survivors only.
    rebuilt = np.zeros_like(active)
    rebuilt[next_reb:] = True
    rebuilt &= active
    if rebuilt.any():
        assert np.allclose(gross[rebuilt], 1.0), "the next rebalance must restore a fully invested book"
        assert np.allclose(pos[rebuilt, dead], 0.0), "the dead symbol must never be re-selected"


def test_no_position_is_held_on_a_bar_without_data() -> None:
    mod = _load_module()
    panel, _ = _panel()
    out = mod.generate_positions(panel, _contract(datetime(2024, 1, 16, tzinfo=UTC)), universe_size=4, quintile_fraction=0.25)
    pos = out["positions"]
    assets = sorted(panel["asset"].unique().to_list())
    close = (
        panel.pivot(index="timestamp", on="asset", values="close").sort("timestamp").select(assets).to_numpy()
    )
    missing = ~np.isfinite(close)
    assert not np.any(np.abs(pos[missing]) > 1e-12), "a weight survived a bar with no price for that asset"


def test_window_opens_on_its_first_bar() -> None:
    mod = _load_module()
    panel, _ = _panel()
    out = mod.generate_positions(panel, _contract(datetime(2024, 1, 16, tzinfo=UTC)), universe_size=4, quintile_fraction=0.25)
    pos = out["positions"]
    active = np.where(np.abs(pos).sum(axis=1) > 1e-12)[0]
    # val_start is bar 0; the first legal bar after the 168-bar lookback is bar 168.
    assert active[0] == 168, f"expected the book to open at bar 168, opened at {active[0]}"
