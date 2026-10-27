"""Unit tests for the Carver AF30 daily suite shared primitives (carver_common)."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl

from warden.contracts import load_contract
from warden.runner import load_module_from_path

common = load_module_from_path(Path("strategies/candidates/carver_daily/carver_common.py"))
ASSET_CLASSES = common.ASSET_CLASSES
ann_vol = common.ann_vol
apply_buffer = common.apply_buffer
cash_vol_positions = common.cash_vol_positions
ewmac = common.ewmac
forecast_from_trend = common.forecast_from_trend
fill_held = common.fill_held
observation_mask = common.observation_mask
price_vol = common.price_vol
value_matrix = common.value_matrix


def _bars(closes: list[float], asset: str = "AAA") -> pl.DataFrame:
    dates = [datetime(2020, 1, 1, tzinfo=UTC) + timedelta(days=i) for i in range(len(closes))]
    return pl.DataFrame({"timestamp": dates, "asset": [asset] * len(closes), "close": closes})


def test_ewmac_step_response_matches_closed_form():
    """span=1 means alpha=1, so EMA_1 is the price and EMA_2 is hand-computable."""
    # EMA_2 at the jump = (200 + 100/3 + 100/9 + 100/27) / (1 + 1/3 + 1/9 + 1/27) = 167.5;
    # ewmac = (200 - 167.5) / 200 = 13/80.
    df = _bars([100.0, 100.0, 100.0, 200.0])
    got = df.select(ewmac(pl.col("close"), 1, 2).over("asset").alias("e"))["e"].to_numpy()

    assert np.allclose(got[:3], 0.0, atol=1e-12)
    assert math.isclose(got[3], 13.0 / 80.0, rel_tol=0.0, abs_tol=1e-12)


def test_ewmac_is_computed_per_asset():
    df = pl.concat([_bars([100.0, 100.0, 100.0, 200.0], "AAA"), _bars([200.0] * 4, "BBB")])
    got = df.sort("timestamp", "asset").with_columns(e=ewmac(pl.col("close"), 1, 2).over("asset"))

    aaa = got.filter(pl.col("asset") == "AAA")["e"].to_numpy()
    bbb = got.filter(pl.col("asset") == "BBB")["e"].to_numpy()
    assert math.isclose(aaa[-1], 13.0 / 80.0, abs_tol=1e-12)
    assert np.allclose(bbb, 0.0, atol=1e-12)


def test_ann_vol_matches_hand_value_and_price_vol_is_lagged():
    df = _bars([100.0, 102.0, 102.0, 102.0])
    out = df.select(
        ann_vol(pl.col("close"), 2, 312).alias("ann"),
        price_vol(pl.col("close"), 2, 312).alias("pv"),
    )

    # Returns are [null, 0.02, 0.0, 0.0]; sample std of [0.02, 0.0] is 0.02/sqrt(2).
    assert math.isclose(out["ann"][2], 0.02 * math.sqrt(156.0), rel_tol=0.0, abs_tol=1e-12)
    assert out["ann"][3] == 0.0
    assert out["pv"][3] == out["ann"][2]


def test_forecast_from_trend_scales_and_clips():
    df = pl.DataFrame({"trend": [0.1, 1.0, -1.0], "vol": [0.2, 0.001, 0.001]})
    got = df.select(forecast_from_trend(pl.col("trend"), pl.col("vol"), 1.91).alias("f"))["f"].to_numpy()

    assert math.isclose(got[0], 0.1 / 0.2 * 1.91, rel_tol=0.0, abs_tol=1e-12)
    assert got[1] == 20.0
    assert got[2] == -20.0


def test_apply_buffer_deadband():
    # width = 0.10 * weight(0.1) = 0.01
    assert apply_buffer(0.055, 0.05, 0.10, 0.1) == 0.05
    assert apply_buffer(0.07, 0.05, 0.10, 0.1) == 0.07
    assert apply_buffer(-0.055, -0.05, 0.10, 0.1) == -0.05
    assert apply_buffer(-0.07, -0.05, 0.10, 0.1) == -0.07


def test_cash_vol_positions_formula_and_cap():
    assert math.isclose(cash_vol_positions(10.0, 0.64, 1.0, 0.16), 0.25, rel_tol=0.0, abs_tol=1e-12)
    assert math.isclose(cash_vol_positions(10.0, 0.16, 0.5, 0.16), 0.5, rel_tol=0.0, abs_tol=1e-12)
    assert cash_vol_positions(10.0, 0.16, 1.0, 0.16) == 0.5  # clipped by the per-instrument cap
    assert cash_vol_positions(-10.0, 0.16, 1.0, 0.16) == -0.5


def test_asset_classes_cover_the_17_instrument_panel():
    assert ASSET_CLASSES == {
        "ES": "equity_us", "NQ": "equity_us",
        "DAX": "equity_eu", "EUSIDXEUR": "equity_eu",
        "HK33": "equity_asia", "NIKKEI225": "equity_asia",
        "AUDUSD": "fx", "CADJPY": "fx", "EURUSD": "fx", "GBPJPY": "fx",
        "GBPUSD": "fx", "USDCAD": "fx", "USDCHF": "fx", "USDJPY": "fx",
        "XAGUSD": "metals", "XAUUSD": "metals", "CL": "energy",
    }


def test_value_matrix_and_observation_mask_fill_closed_bars():
    rows = [
        {"timestamp": datetime(2020, 1, 1, tzinfo=UTC), "asset": "AAA", "close": 10.0},
        {"timestamp": datetime(2020, 1, 1, tzinfo=UTC), "asset": "BBB", "close": 20.0},
        {"timestamp": datetime(2020, 1, 2, tzinfo=UTC), "asset": "AAA", "close": 11.0},
    ]
    df = pl.DataFrame(rows)
    values = value_matrix(df, "close", ["AAA", "BBB"])
    mask = observation_mask(df, ["AAA", "BBB"])

    assert values.shape == (2, 2)
    assert np.allclose(values, np.array([[10.0, 20.0], [11.0, 0.0]]))
    assert mask.dtype == bool
    assert mask.tolist() == [[True, True], [True, False]]


def test_fill_held_carries_position_across_closed_bars():
    target = np.array([[0.5, 0.5], [0.5, 0.5], [0.2, 0.5]])
    observed = np.array([[True, True], [True, False], [True, True]])

    got = fill_held(target, observed)

    assert np.allclose(got, np.array([[0.5, 0.5], [0.5, 0.5], [0.2, 0.5]]))
    # a bar before the first observation starts flat, not at the first later weight
    assert fill_held(np.array([[0.3], [0.4]]), np.array([[False], [True]]))[0, 0] == 0.0


# --- S01-S04 buy-and-hold family -------------------------------------------------

s01 = load_module_from_path(Path("strategies/candidates/carver_daily/s01_buy_hold_single.py"))
s02 = load_module_from_path(Path("strategies/candidates/carver_daily/s02_buy_hold_risk_scaled.py"))
s03 = load_module_from_path(Path("strategies/candidates/carver_daily/s03_buy_hold_variable_risk.py"))
s04 = load_module_from_path(Path("strategies/candidates/carver_daily/s04_buy_hold_portfolio_scaling.py"))
CONTRACT = load_contract(Path("contracts/multi_asset_1d_v1.yaml"))


def _panel(closes_by_asset: dict[str, list[float]]) -> pl.DataFrame:
    rows = []
    for asset, closes in closes_by_asset.items():
        for i, close in enumerate(closes):
            rows.append({
                "timestamp": datetime(2020, 1, 1, tzinfo=UTC) + timedelta(days=i),
                "asset": asset, "open": close, "high": close * 1.001,
                "low": close * 0.999, "close": close, "volume": 100.0, "funding_rate": 0.0,
            })
    return pl.DataFrame(rows).sort("timestamp", "asset")


def _alternating(n: int, base: float, step: float) -> list[float]:
    """Prices whose close-to-close returns are exactly +/-step."""
    prices = [base]
    for i in range(1, n):
        prices.append(prices[-1] * (1.0 + (step if i % 2 else -step)))
    return prices


def test_s01_positions_are_constant_and_price_scale_free():
    flat = _panel({"AAA": [100.0] * 40, "BBB": [50.0] * 40})
    scaled = _panel({"AAA": [300.0] * 40, "BBB": [150.0] * 40})

    pos = s01.generate_positions(flat, CONTRACT)["positions"]
    pos_scaled = s01.generate_positions(scaled, CONTRACT)["positions"]

    assert np.allclose(pos[1:], 0.5)
    assert np.allclose(pos, pos_scaled)


def test_s01_does_not_exit_on_a_40_percent_crash():
    closes = [100.0] * 100 + [60.0] + [60.0] * 10
    pos = s01.generate_positions(_panel({"AAA": closes}), CONTRACT)["positions"][:, 0]

    assert pos[99] == 1.0
    assert pos[100] == 1.0  # a 3x ATR stop would have flattened here
    assert pos[-1] == 1.0


def test_s03_weights_are_inversely_proportional_to_realised_volatility():
    # AAA alternates +/-1%, BBB +/-2%: identical construction, double the risk.
    panel = _panel({"AAA": _alternating(160, 100.0, 0.01), "BBB": _alternating(160, 100.0, 0.02)})
    pos = s03.generate_positions(panel, CONTRACT, vol_target=0.16, vol_window=60)["positions"]

    assert pos[-1, 0] > 0.0 and pos[-1, 1] > 0.0
    assert math.isclose(pos[-1, 1] / pos[-1, 0], 0.5, rel_tol=1e-9)  # double vol, half the weight


def test_s02_long_term_volatility_reacts_less_than_s03_to_a_short_spike():
    calm = _alternating(320, 100.0, 0.002)
    spike = [calm[-1] * (1.2 if i % 2 else 0.8) for i in range(5)]
    closes = calm + spike
    panel = _panel({"AAA": closes})

    common = {"vol_target": 0.01, "per_instrument_cap": 0.5, "warmup": 60}
    pos02 = s02.generate_positions(panel, CONTRACT, vol_window=1260, **common)["positions"][:, 0]
    pos03 = s03.generate_positions(panel, CONTRACT, vol_window=60, **common)["positions"][:, 0]

    drop02 = pos02[-1] / pos02[319]
    drop03 = pos03[-1] / pos03[319]
    assert drop02 < 1.0 and drop03 < 1.0
    assert drop03 < drop02  # the 60-day estimate de-risks harder than the 5-year one


def test_s04_is_s03_scaled_by_the_table16_idm():
    panel = _panel({"AAA": _alternating(160, 100.0, 0.01), "BBB": _alternating(160, 100.0, 0.02)})

    # vol_target 0.05 keeps both books below the 0.5 per-instrument cap
    pos03 = s03.generate_positions(panel, CONTRACT, vol_target=0.05, vol_window=60)["positions"]
    pos04 = s04.generate_positions(panel, CONTRACT, vol_target=0.05, vol_window=60, idm=2.30)["positions"]

    assert np.allclose(pos04, np.clip(pos03 * 2.30, -0.5, 0.5))
    assert np.allclose(pos04, pos03 * 2.30)
    assert np.abs(pos04).max() < 0.5


# --- S05-S09 trend family --------------------------------------------------------

s05 = load_module_from_path(Path("strategies/candidates/carver_daily/s05_slow_trend_long_only.py"))
s06 = load_module_from_path(Path("strategies/candidates/carver_daily/s06_slow_trend_long_short.py"))
s07 = load_module_from_path(Path("strategies/candidates/carver_daily/s07_slow_trend_strength.py"))
s08 = load_module_from_path(Path("strategies/candidates/carver_daily/s08_fast_trend_strength.py"))
s09 = load_module_from_path(Path("strategies/candidates/carver_daily/s09_ewmac_multi.py"))

BPY = 312


def _ramp(n: int, step: float, base: float = 100.0) -> list[float]:
    prices = [base]
    for _ in range(1, n):
        prices.append(prices[-1] * (1.0 + step))
    return prices


def _noisy(n: int, drift: float, sigma: float, seed: int = 11) -> list[float]:
    rets = np.random.default_rng(seed).normal(drift, sigma, n)
    return list(100.0 * np.cumprod(1.0 + rets))


def _feature_columns(panel: pl.DataFrame, fast: int, slow: int) -> tuple[np.ndarray, np.ndarray]:
    """Independent EMA-ratio trend and per-bar EWMA volatility, per asset."""
    trend = (pl.col("close").ewm_mean(span=fast) - pl.col("close").ewm_mean(span=slow)) / pl.col("close")
    vol_ann = (pl.col("close").pct_change().pow(2).ewm_mean(span=35).sqrt() * math.sqrt(BPY)).shift(1)
    out = panel.sort("timestamp", "asset").with_columns(trend=trend, vol_bar=vol_ann / math.sqrt(BPY))
    return out["trend"].to_numpy(), out["vol_bar"].to_numpy()


def test_s05_long_only_never_shorts_and_holds_flat_in_a_downtrend():
    panel = _panel({"UP": _ramp(120, 0.004), "DOWN": _ramp(120, -0.004)})
    pos = s05.generate_positions(panel, CONTRACT)["positions"]
    assets = sorted(panel["asset"].unique().to_list())

    assert pos.min() >= 0.0
    assert pos[-1, assets.index("UP")] > 0.0
    assert pos[-1, assets.index("DOWN")] == 0.0


def test_s06_sign_matches_the_ewma_crossover():
    panel = _panel({"UP": _ramp(120, 0.004), "DOWN": _ramp(120, -0.004)})
    pos = s06.generate_positions(panel, CONTRACT)["positions"]
    assets = sorted(panel["asset"].unique().to_list())
    trend, _ = _feature_columns(panel, 64, 256)

    for asset in assets:
        assert np.sign(pos[-1, assets.index(asset)]) == np.sign(trend[-1] if asset == "UP" else -1.0)
    assert pos[-1, assets.index("UP")] > 0.0
    assert pos[-1, assets.index("DOWN")] < 0.0


def test_s07_forecast_is_trend_over_bar_volatility_times_1_91():
    panel = _panel({"AAA": _noisy(200, 0.0005, 0.005)})
    feat = s07.pipeline.compute(panel, CONTRACT)
    trend, vol_bar = _feature_columns(panel, 64, 256)
    expected = np.clip(trend / vol_bar * 1.91, -20.0, 20.0)

    assert s07.SCALAR == 1.91
    assert np.allclose(feat["forecast"].to_numpy(), expected, equal_nan=True)


def test_s07_and_s08_forecasts_clip_at_20_on_a_steep_ramp():
    steep = _panel({"AAA": _ramp(200, 0.01)})
    f07 = s07.pipeline.compute(steep, CONTRACT)["forecast"].to_numpy()[-1]
    f08 = s08.pipeline.compute(steep, CONTRACT)["forecast"].to_numpy()[-1]

    assert f07 == 20.0
    assert f08 == 20.0
    assert s08.SCALAR == 4.10  # Table 29 EWMAC16, p. 205 (p. 193 states 4.1 for this crossover)


def test_s08_buffering_only_trades_outside_the_deadband():
    panel = _panel({"AAA": _noisy(220, 0.0004, 0.006, seed=5)})
    feat = s08.pipeline.compute(panel, CONTRACT)
    target = value_matrix(feat, "weight", ["AAA"])[:, 0]
    average = value_matrix(feat, "average", ["AAA"])[:, 0]
    pos = s08.generate_positions(panel, CONTRACT)["positions"][:, 0]

    moved = 0
    for t in range(1, len(pos)):
        if abs(target[t] - pos[t - 1]) <= 0.10 * average[t]:
            assert pos[t] == pos[t - 1]
        else:
            assert pos[t] == target[t]
            moved += 1
    assert moved > 0  # the deadband is a filter, not a freeze


def test_s09_is_the_six_speed_mean_times_the_table36_fdm_then_capped():
    panel = _panel({"AAA": _noisy(260, 0.0004, 0.006, seed=9)})
    feat = s09.pipeline.compute(panel, CONTRACT)

    assert s09.SPEEDS == ((2, 8), (4, 16), (8, 32), (16, 64), (32, 128), (64, 256))
    assert s09.FORECAST_SCALARS == {2: 12.1, 4: 8.53, 8: 5.95, 16: 4.10, 32: 2.79, 64: 1.91}

    speeds = []
    for fast, slow in s09.SPEEDS:
        trend, vol_bar = _feature_columns(panel, fast, slow)
        speeds.append(np.clip(trend / vol_bar * s09.FORECAST_SCALARS[fast], -20.0, 20.0))
    expected = np.clip(np.mean(speeds, axis=0) * 1.26, -20.0, 20.0)

    assert np.allclose(feat["forecast"].to_numpy(), expected, equal_nan=True)


def test_trend_modules_do_not_stop_out_on_a_crash_bar():
    closes = _ramp(120, 0.004) + [_ramp(120, 0.004)[-1] * 0.6] + [60.0] * 5
    panel = _panel({"AAA": closes})

    for module in (s05, s06, s07):
        pos = module.generate_positions(panel, CONTRACT)["positions"][:, 0]
        assert pos[120] != 0.0, module.__name__

    # S08/S09 are buffered: the crash bar must land on the target or the deadband,
    # never on an unconditional exit. S09's six speeds can cancel to a zero forecast.
    for module in (s08, s09):
        feat = module.pipeline.compute(panel, CONTRACT)
        target = value_matrix(feat, "weight", ["AAA"])[:, 0]
        average = value_matrix(feat, "average", ["AAA"])[:, 0]
        pos = module.generate_positions(panel, CONTRACT)["positions"][:, 0]
        assert abs(pos[120] - target[120]) <= 0.10 * average[120] + 1e-12, module.__name__


# --- S12, S14, S17 ---------------------------------------------------------------

s12 = load_module_from_path(Path("strategies/candidates/carver_daily/s12_adjusted_trend.py"))
s14 = load_module_from_path(Path("strategies/candidates/carver_daily/s14_spot_trend.py"))
s17 = load_module_from_path(Path("strategies/candidates/carver_daily/s17_normalised_trend.py"))


def test_s12_double_v_preserves_the_average_forecast_scale():
    grid = np.linspace(-20.0, 20.0, 4001)
    out = pl.DataFrame({"f": grid}).select(s12.double_v(pl.col("f")).alias("a"))["a"].to_numpy()

    # below the knee the forecast doubles, at and beyond the cap it is zero
    assert np.allclose(2.0 * grid[np.abs(grid) <= 10.0], out[np.abs(grid) <= 10.0])
    assert np.all(out[np.abs(grid) >= 20.0] == 0.0)
    # the prose requires the mapping to keep the average absolute size: E|F'| == E|F|.
    # Trapezoid both sides so the comparison is the integral, not a sampled endpoint count.
    assert math.isclose(
        np.trapezoid(np.abs(out), grid), np.trapezoid(np.abs(grid), grid), rel_tol=1e-12
    )


def test_s12_scale_and_cap_only_touches_ewmac4_and_ewmac64():
    grid = np.linspace(-25.0, 25.0, 501)
    df = pl.DataFrame({"f": grid})
    f4 = df.select(s12.adjust_speed(4, pl.col("f")).alias("a"))["a"].to_numpy()
    f8 = df.select(s12.adjust_speed(8, pl.col("f")).alias("a"))["a"].to_numpy()
    f64 = df.select(s12.adjust_speed(64, pl.col("f")).alias("a"))["a"].to_numpy()

    assert np.allclose(f4, np.clip(1.25 * grid, -15.0, 15.0))
    assert np.allclose(f64, np.clip(1.25 * grid, -15.0, 15.0))
    assert np.allclose(f8, np.clip(grid, -20.0, 20.0))


def test_s12_forecast_is_the_six_adjusted_speeds_times_the_fdm():
    panel = _panel({"AAA": _noisy(260, 0.0004, 0.006, seed=13)})
    feat = s12.pipeline.compute(panel, CONTRACT)
    speeds = []
    for fast, slow in s12.SPEEDS:
        trend, vol_bar = _feature_columns(panel, fast, slow)
        raw = trend / vol_bar * s12.FORECAST_SCALARS[fast]
        if fast == 2:
            size = np.abs(raw)
            speeds.append(np.where(size <= 10, 2 * raw, np.where(size < 20, np.sign(raw) * 2 * (20 - size), 0.0)))
        elif fast in (4, 64):
            speeds.append(np.clip(1.25 * raw, -15.0, 15.0))
        else:
            speeds.append(np.clip(raw, -20.0, 20.0))
    expected = np.clip(np.mean(speeds, axis=0) * 1.26, -20.0, 20.0)

    assert np.allclose(feat["forecast"].to_numpy()[5:], expected[5:], equal_nan=True)


def test_s14_synthetic_spot_follows_the_price_when_there_is_no_carry():
    panel = _panel({"AAA": _noisy(120, 0.0004, 0.006, seed=17)})
    feat = s14.pipeline.compute(panel, CONTRACT)
    closes = feat["close"].to_numpy()[1:]
    spots = feat["spot"].to_numpy()[1:]

    # identical total return path, only the arbitrary starting level differs (S_0 = 100)
    assert np.allclose(spots / spots[-1], closes / closes[-1], rtol=1e-12)


def test_s14_synthetic_spot_deducts_the_annual_carry():
    closes = _ramp(60, 0.01)
    panel = _panel({"AAA": closes}).with_columns(annual_carry=pl.lit(0.10))
    feat = s14.pipeline.compute(panel, CONTRACT)

    expected = 100.0 * np.cumprod(np.concatenate([[1.0], np.full(59, 1.0 + 0.01 - 0.10 / BPY)]))
    assert np.allclose(feat["spot"].to_numpy()[1:], expected[1:], rtol=1e-12)


def test_s14_positions_ignore_high_and_low():
    closes = _noisy(150, 0.0004, 0.006, seed=19)
    base = _panel({"AAA": closes})
    shocked = base.with_columns((pl.col("high") * 5.0).alias("high"), (pl.col("low") * 0.2).alias("low"))

    assert np.allclose(
        s14.generate_positions(base, CONTRACT)["positions"],
        s14.generate_positions(shocked, CONTRACT)["positions"],
    )


def test_s17_normalised_price_uses_risk_adjusted_increments():
    panel = _panel({"AAA": _noisy(200, 0.0005, 0.006, seed=23)})
    feat = s17.pipeline.compute(panel, CONTRACT)
    ret = panel.sort("timestamp", "asset")["close"].pct_change().to_numpy()
    vol_bar = feat["vol_bar"].to_numpy()

    # every increment is 100 risk-adjusted returns, and PN is their running sum
    assert np.allclose(feat["step"].to_numpy()[5:], (100.0 * ret / vol_bar)[5:], rtol=1e-12)
    assert np.allclose(
        feat["pn"].to_numpy()[5:], np.nancumsum(np.nan_to_num(feat["step"].to_numpy()))[5:],
        rtol=1e-12, atol=1e-9,
    )


# --- S18, S19 --------------------------------------------------------------------

s18 = load_module_from_path(Path("strategies/candidates/carver_daily/s18_trend_asset_classes.py"))
s19 = load_module_from_path(Path("strategies/candidates/carver_daily/s19_xs_momentum.py"))
CLASS_INDEX = common.class_index
NORMALISED_PRICE = common.normalised_price


def _fx_panel(tail_crash: bool = False) -> pl.DataFrame:
    # distinct drifts so per-instrument volatility, and hence sizing, differs
    closes = {sym: _ramp(300, step) for sym, step in
              (("AUDUSD", 0.002), ("EURUSD", 0.005), ("USDJPY", 0.008))}
    if tail_crash:
        for sym, series in closes.items():
            closes[sym] = series + [series[-1] * 0.9] + series[-1:] * 9
        closes["USDJPY"] = closes["USDJPY"][:-1] + [closes["USDJPY"][-2] * 0.35]
    return _panel(closes)


def test_s18_class_index_is_the_mean_of_member_normalised_prices():
    panel = _fx_panel()
    instr = s17.pipeline.compute(panel, CONTRACT).select("timestamp", "asset", "pn")
    index = CLASS_INDEX(instr.sort("timestamp", "asset"))

    pn = instr.pivot(index="timestamp", on="asset", values="pn").sort("timestamp")
    members = [c for c in pn.columns if c != "timestamp"]
    increments = np.nan_to_num(np.diff(pn.select(members).to_numpy(), axis=0))
    expected = np.nancumsum(increments.mean(axis=1))

    assert index["cls"].unique().to_list() == ["fx"]
    assert np.allclose(index["level"].to_numpy()[1:], expected, rtol=1e-12)


def test_s18_members_share_the_class_forecast():
    panel = _fx_panel()
    # small risk target so the per-instrument cap does not flatten the comparison
    feat = s18.TrendAssetClassesPipeline(vol_target=0.01).compute(panel, CONTRACT)
    last = feat.filter(pl.col("timestamp") == feat["timestamp"].max())

    assert last["forecast"].n_unique() == 1  # one latent class trend, shared forecast
    assert last["weight"].n_unique() > 1  # sizing still differs by instrument volatility


def test_s18_a_single_distressed_member_cannot_flip_a_trending_class():
    panel = _fx_panel(tail_crash=True)
    feat = s18.pipeline.compute(panel, CONTRACT)
    tail = feat.filter(pl.col("timestamp") >= feat["timestamp"].max() - pl.duration(days=1))
    crash = tail.filter(pl.col("asset") == "USDJPY")

    assert crash["close"].to_numpy()[-1] < crash["close"].to_numpy()[-2]  # the crash landed
    assert (crash["forecast"].to_numpy() > 0).all()


def test_s19_relative_price_removes_the_class_mean():
    panel = _fx_panel()
    feat = s19.pipeline.compute(panel, CONTRACT)

    totals = (
        feat.group_by("timestamp")
        .agg(pl.col("relative").sum().alias("total"))
        .filter(pl.col("total").is_not_null())
    )
    assert np.allclose(totals["total"].to_numpy()[5:], 0.0, atol=1e-9)


def test_s19_constants_match_tables_80_and_84():
    assert s19.HORIZONS == (5, 10, 20, 40, 80, 160)
    assert s19.SMOOTH_SPANS == {5: 2, 10: 3, 20: 5, 40: 10, 80: 20, 160: 40}
    assert s19.FORECAST_SCALARS == {5: 56.1, 10: 79.0, 20: 108.5, 40: 153.5, 80: 217.1, 160: 296.8}
    assert s19.FDM == 1.49


def test_s19_a_one_member_class_has_no_relative_signal():
    panel = _panel({"XAUUSD": _noisy(200, 0.0005, 0.006, seed=29)})
    feat = s19.pipeline.compute(panel, CONTRACT)

    assert np.allclose(feat["relative"].to_numpy()[1:], 0.0, atol=1e-9)
    assert np.allclose(feat["forecast"].to_numpy(), 0.0)


def test_s19_positions_are_invariant_to_row_order():
    panel = _fx_panel()
    shuffled = panel.sample(fraction=1.0, shuffle=True, seed=3)
    assert np.allclose(
        s19.generate_positions(panel, CONTRACT)["positions"],
        s19.generate_positions(shuffled, CONTRACT)["positions"],
    )
