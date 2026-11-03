"""PIT and missing-data regression tests for real Binance order-flow factors.

Fixtures are synthetic, never accepted as actual exchange evidence.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from scripts.screen_binance_orderflow_provisional import causal_labels
from strategies.candidates.recovery.binance_ats_h72 import pipeline as ats_pipeline
from strategies.candidates.recovery.binance_ntr_h72 import pipeline as ntr_pipeline
from strategies.candidates.recovery.binance_orderflow_factors import build_features
from warden.contracts import load_contract
from warden.leakage import verify_prefix_invariance


def fixtures(hours: int = 750, gap: int | None = None) -> pl.DataFrame:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    rows = []
    for i in range(hours):
        if i == gap:
            continue
        for j, asset in enumerate(("AUSDT", "BUSDT", "CUSDT")):
            q = 100.0 + j * 30.0 + i * (j + 1) * .02
            b = q * (0.35 + 0.12 * j)
            rows.append({
                "timestamp": start + timedelta(hours=i),
                "asset": asset,
                "open": 100.0 + j,
                "high": 101.0 + j,
                "low": 99.0 + j,
                "close": 100.5 + j,
                "volume": 20.0 + j,
                "quote_volume": q,
                "trade_count": 30 + j * 5,
                "taker_buy_base_volume": (20.0 + j) * (b / q),
                "taker_buy_quote_volume": b,
                "flow_valid": True,
            })
    return pl.DataFrame(rows).with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC"))
    )


@pytest.mark.parametrize("kind", ["ats", "ntr"])
def test_missing_real_exchange_fields_fail_closed(kind: str) -> None:
    frame = fixtures(30).drop("trade_count", "taker_buy_quote_volume")
    with pytest.raises(ValueError, match="Missing observed Binance"):
        build_features(frame, kind)


@pytest.mark.parametrize("kind", ["ats", "ntr"])
def test_future_shocks_do_not_modify_historical_factor(kind: str) -> None:
    frame = fixtures()
    cutoff = frame["timestamp"].unique().sort()[735]
    prefix = build_features(frame.filter(pl.col("timestamp") <= cutoff), kind)
    changed = frame.with_columns(
        pl.when(pl.col("timestamp") > cutoff)
        .then(pl.col("quote_volume") * 5)
        .otherwise(pl.col("quote_volume")).alias("quote_volume"),
        pl.when(pl.col("timestamp") > cutoff)
        .then(pl.col("taker_buy_quote_volume") * 3)
        .otherwise(pl.col("taker_buy_quote_volume")).alias("taker_buy_quote_volume"),
    )
    after = build_features(changed, kind).filter(pl.col("timestamp") <= cutoff)
    assert prefix.select("timestamp", "asset", "feat_ats_ratio_168" if kind == "ats" else "feat_ntr_168").equals(
        after.select("timestamp", "asset", "feat_ats_ratio_168" if kind == "ats" else "feat_ntr_168")
    )


def test_no_trade_hour_is_not_a_zero_ats_or_ntr_signal() -> None:
    frame = fixtures()
    date = datetime(2024, 1, 31, 1, tzinfo=UTC)
    frame = frame.with_columns(
        pl.when(pl.col("timestamp") == date).then(0).otherwise(pl.col("volume")).alias("volume"),
        pl.when(pl.col("timestamp") == date).then(0).otherwise(pl.col("quote_volume")).alias("quote_volume"),
        pl.when(pl.col("timestamp") == date).then(0).otherwise(pl.col("trade_count")).alias("trade_count"),
        pl.when(pl.col("timestamp") == date).then(0).otherwise(pl.col("taker_buy_base_volume")).alias("taker_buy_base_volume"),
        pl.when(pl.col("timestamp") == date).then(0).otherwise(pl.col("taker_buy_quote_volume")).alias("taker_buy_quote_volume"),
        (pl.col("timestamp") != date).alias("flow_valid"),
        pl.when(pl.col("timestamp") == date).then(100.0)
          .otherwise(pl.col("open")).alias("open"),
        pl.when(pl.col("timestamp") == date).then(100.0)
          .otherwise(pl.col("high")).alias("high"),
        pl.when(pl.col("timestamp") == date).then(100.0)
          .otherwise(pl.col("low")).alias("low"),
        pl.when(pl.col("timestamp") == date).then(100.0)
          .otherwise(pl.col("close")).alias("close"),
    )
    for kind, name in (("ats", "feat_ats_ratio_168"), ("ntr", "feat_ntr_168")):
        result = build_features(frame, kind)
        bad = result.filter(pl.col("timestamp") == date)
        assert bad[name].null_count() == 3
        assert result.filter(pl.col("timestamp") == date + timedelta(hours=1))[name].null_count() == 0


def test_false_flow_valid_mask_cannot_hide_real_trades() -> None:
    frame = fixtures(750).with_columns(pl.lit(False).alias("flow_valid"))
    with pytest.raises(ValueError, match="mask conflicts"):
        build_features(frame, "ats")


def test_720h_continuity_fails_closed_after_missing_hour() -> None:
    complete = fixtures(820)
    broken = fixtures(820, gap=400)
    clean = build_features(complete, "ats")
    changed = build_features(broken, "ats")
    last_time = complete["timestamp"].max()
    assert clean.filter(pl.col("timestamp") == last_time)["feat_ats_ratio_168"].null_count() == 0
    assert changed.filter(pl.col("timestamp") == last_time)["feat_ats_ratio_168"].null_count() == 3


def test_taker_factor_uses_signed_observed_taker_quote_not_candle_direction() -> None:
    data = fixtures(220)
    a = build_features(data, "ntr")
    assert a.filter(pl.col("asset") == "AUSDT")["raw_ntr_168"].drop_nulls().tail(1).item() < 0
    assert a.filter(pl.col("asset") == "CUSDT")["raw_ntr_168"].drop_nulls().tail(1).item() > 0
    inverted_candles = data.with_columns(
        pl.col("close").alias("open"),
        pl.col("open").alias("close"),
    )
    b = build_features(inverted_candles, "ntr")
    assert np.allclose(
        a["raw_ntr_168"].fill_null(0).to_numpy(),
        b["raw_ntr_168"].fill_null(0).to_numpy(),
    )


@pytest.mark.parametrize("pipeline", [ats_pipeline, ntr_pipeline])
def test_warden_native_prefix_invariance_for_recovered_factors(pipeline) -> None:
    contract = load_contract(
        "docs/contract-proposals/crypto_perps_top100_orderflow_1h_h72_v1.yaml"
    )
    frame = fixtures(1600).with_columns(pl.lit(0.0).alias("funding_rate"))
    verify_prefix_invariance(pipeline, frame, contract)


def test_label_entry_is_one_hour_later_not_same_bar() -> None:
    t = [datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=i) for i in range(7)]
    opens = np.array([[100.0], [102.0], [103.0], [105.0], [106.0], [108.0], [110.0]])
    valid = np.ones_like(opens, dtype=bool)
    y = causal_labels(t, opens, valid, horizon=2, delay=1)
    assert y[0, 0] == pytest.approx(105.0 / 102.0 - 1.0)
    assert y[1, 0] == pytest.approx(106.0 / 103.0 - 1.0)
    assert np.isnan(y[-3:]).all()


def test_label_does_not_forward_fill_missing_future_observation() -> None:
    t = [datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=i)
         for i in (0, 1, 2, 4, 5, 6)]
    opens = np.array([100.0 + i for i in (0, 1, 2, 4, 5, 6)]).reshape(-1, 1)
    valid = np.ones_like(opens, dtype=bool)
    y = causal_labels(t, opens, valid, horizon=2, delay=1)
    assert np.isnan(y[0, 0])  # t+3h missing
    assert y[1, 0] == pytest.approx(104.0 / 102.0 - 1.0)
    valid[2] = False
    y = causal_labels(t, opens, valid, horizon=2, delay=1)
    assert np.isnan(y[1, 0])  # entry cannot trade
