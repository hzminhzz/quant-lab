"""Unit and property tests for AF30 Handcrafted Book."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import polars as pl

from warden.contracts import load_contract
from warden.runner import load_module_from_path

book_mod = load_module_from_path(Path("strategies/books/af30_handcrafted_book_1h.py"))
CLASS_OF = book_mod.CLASS_OF
HANDCRAFTED_WEIGHTS = book_mod.HANDCRAFTED_WEIGHTS
LAST_BOOK_STATS = book_mod.LAST_BOOK_STATS
generate_positions = book_mod.generate_positions


def test_handcrafted_weights_and_shares():
    # Weights sum to 1.0 within 1e-12
    total_w = sum(HANDCRAFTED_WEIGHTS.values())
    assert abs(total_w - 1.0) < 1e-12

    # CL is single energy commodity -> weight 0.25
    assert HANDCRAFTED_WEIGHTS["CL"] == 0.25

    # Each of the 4 classes gets exactly 0.25
    for cls in ("equity", "fx", "metals", "energy"):
        cls_w = sum(w for sym, w in HANDCRAFTED_WEIGHTS.items() if CLASS_OF[sym] == cls)
        assert abs(cls_w - 0.25) < 1e-12


def test_opposing_strategies_netting(tmp_path: Path):
    contract = load_contract(Path("contracts/multi_asset_1h_v1.yaml"))

    # Create dummy panel with 2 assets and 96 unique hourly timestamps
    ts_list = [f"2020-01-0{d}T{h:02d}:00:00Z" for d in range(1, 5) for h in range(24)]
    rows = []
    for t in ts_list:
        rows.append({"timestamp": t, "asset": "CL", "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0, "volume": 100.0})
        rows.append({"timestamp": t, "asset": "EURUSD", "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.1, "volume": 100.0})
    df = pl.DataFrame(rows).with_columns(pl.col("timestamp").str.to_datetime(time_zone="UTC"))

    p_long = tmp_path / "strat_long.py"
    p_long.write_text("def generate_positions(panel_df, contract, **kwargs):\n"
                      "    import numpy as np\n"
                      "    ts = sorted(panel_df['timestamp'].unique().to_list())\n"
                      "    a = sorted(panel_df['asset'].unique().to_list())\n"
                      "    return {'positions': np.ones((len(ts), len(a))) * 0.5, 'forecast': np.ones((len(ts), len(a))) * 20.0}\n")

    p_short = tmp_path / "strat_short.py"
    p_short.write_text("def generate_positions(panel_df, contract, **kwargs):\n"
                       "    import numpy as np\n"
                       "    ts = sorted(panel_df['timestamp'].unique().to_list())\n"
                       "    a = sorted(panel_df['asset'].unique().to_list())\n"
                       "    return {'positions': np.ones((len(ts), len(a))) * -0.5, 'forecast': np.ones((len(ts), len(a))) * -20.0}\n")

    out = generate_positions(df, contract, strategy_modules=[str(p_long), str(p_short)])
    # Net forecast is exactly 0.0, resulting in 0 positions
    assert np.allclose(out["forecast"], 0.0)
    assert np.allclose(out["positions"], 0.0)


def test_idm_bounds_and_smoke_call():
    contract = load_contract(Path("contracts/multi_asset_1h_v1.yaml"))
    df_full = pl.read_parquet(contract.exploratory_data_path)
    ts_200 = df_full["timestamp"].unique().sort().head(200)
    df = df_full.filter(pl.col("timestamp").is_in(ts_200))
    timestamps = sorted(df["timestamp"].unique().to_list())
    assert len(timestamps) == 200

    out = generate_positions(df, contract)
    assert "positions" in out
    assert "forecast" in out
    assert out["positions"].shape == (200, 17)
    assert np.all(np.isfinite(out["positions"]))

    idm = LAST_BOOK_STATS.get("idm", 0.0)
    assert 1.0 <= idm <= 2.5, f"IDM {idm} outside [1.0, 2.5]"


def test_asynchronous_sparse_union_session_handling(tmp_path: Path):
    contract = load_contract(Path("contracts/multi_asset_1h_v1.yaml"))

    # Create asynchronous panel where CL trades even hours and EURUSD trades odd hours
    rows = []
    for h in range(48):
        d = h // 24 + 1
        hr = h % 24
        ts = f"2020-01-0{d}T{hr:02d}:00:00Z"
        if h % 2 == 0:
            rows.append({"timestamp": ts, "asset": "CL", "open": 50.0, "high": 51.0, "low": 49.0, "close": 50.5, "volume": 100.0})
        else:
            rows.append({"timestamp": ts, "asset": "EURUSD", "open": 1.10, "high": 1.12, "low": 1.09, "close": 1.11, "volume": 100.0})
    df = pl.DataFrame(rows).with_columns(pl.col("timestamp").str.to_datetime(time_zone="UTC"))

    # Long mock strategy on all active assets
    p_strat = tmp_path / "strat_long.py"
    p_strat.write_text("def generate_positions(panel_df, contract, **kwargs):\n"
                       "    import numpy as np\n"
                       "    ts = sorted(panel_df['timestamp'].unique().to_list())\n"
                       "    a = sorted(panel_df['asset'].unique().to_list())\n"
                       "    return {'positions': np.ones((len(ts), len(a))) * 0.3, 'forecast': np.ones((len(ts), len(a))) * 10.0}\n")

    out = generate_positions(df, contract, strategy_modules=[str(p_strat)], rebalance_bars=1, warmup=2)
    pos = out["positions"]
    ret = LAST_BOOK_STATS["returns"]

    # 1. Ensure returns have zero phantom -100% drops
    assert ret.min() > -0.50
    # When asset is not trading, return is exactly 0.0
    for t in range(len(pos)):
        if t % 2 != 0:  # CL closed at odd timestamps
            assert ret[t, 0] == 0.0
        else:  # EURUSD closed at even timestamps
            assert ret[t, 1] == 0.0

    # 2. Ensure non-zero positions are opened for both assets
    assert np.any(pos[:, 0] > 0), "CL position never opened"
    assert np.any(pos[:, 1] > 0), "EURUSD position never opened"

    # 3. Ensure positions are strictly preserved across inactive bars (never falsely liquidated)
    for t in range(4, len(pos)):
        if t % 2 != 0:  # CL closed -> position must be held unchanged
            assert pos[t, 0] == pos[t - 1, 0], f"CL position changed on inactive bar {t}"
        else:  # EURUSD closed -> position must be held unchanged
            assert pos[t, 1] == pos[t - 1, 1], f"EURUSD position changed on inactive bar {t}"

    assert np.all(np.isfinite(pos))
