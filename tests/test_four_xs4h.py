"""Regression and causality controls for four separately preregistered XS4H studies."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest
import yaml

from strategies.candidates.four_xs4h import common
from warden.contracts import load_contract
from warden.leakage import verify_prefix_invariance


def sample_frame(
    hours: int = 220, assets: int = 51, gap: int | None = None,
    first: datetime = datetime(2024, 1, 1, tzinfo=UTC),
) -> pl.DataFrame:
    symbols = ["BTCUSDT"] + [f"ALT{i:03d}USDT" for i in range(assets - 1)]
    rows = []
    for i in range(hours):
        for idx, asset in enumerate(symbols):
            if i == gap and idx == 1:
                continue
            base = 50.0 + idx + 0.003 * i * (1 + idx / assets)
            close = base + 0.11 * np.sin((i + idx * 3) / 9)
            rows.append({
                "timestamp": first + timedelta(hours=i),
                "asset": asset,
                "open": base,
                "high": max(base, close) + 0.5,
                "low": min(base, close) - 0.5,
                "close": close,
                "volume": 500.0 + 7.0 * idx + (i % 29),
                "funding_rate": 0.0001,
            })
    return pl.DataFrame(rows)


@pytest.mark.parametrize("kind", list(common.HYPOTHESES))
def test_frozen_source_fields_fail_closed(kind: str) -> None:
    with pytest.raises(ValueError, match="Missing pinned Binance"):
        common.source_features(sample_frame(5, 3).drop("volume"), kind)


@pytest.mark.parametrize("kind", list(common.HYPOTHESES))
def test_past_signal_immune_to_future_price_and_volume_shock(kind: str) -> None:
    frame = sample_frame(220, 41)
    cut = datetime(2024, 1, 8, tzinfo=UTC)
    before = common.source_features(frame.filter(pl.col("timestamp") <= cut), kind)
    perturbed = frame.with_columns(
        pl.when(pl.col("timestamp") > cut).then(pl.col("close") * 3)
        .otherwise(pl.col("close")).alias("close"),
        pl.when(pl.col("timestamp") > cut).then(pl.col("volume") * 5)
        .otherwise(pl.col("volume")).alias("volume"),
    )
    after = common.source_features(perturbed, kind).filter(pl.col("timestamp") <= cut)
    assert before.equals(after, null_equal=True)


def test_sign_only_ignores_candle_return_magnitude() -> None:
    frame = sample_frame(7, 4)
    before = common.source_features(frame, "sign_reversal")
    frame = frame.with_columns(
        (pl.col("open") + (pl.col("close") - pl.col("open")) * 3)
        .alias("close")
    )
    after = common.source_features(frame, "sign_reversal")
    assert before.equals(after, null_equal=True)


@pytest.mark.parametrize("kind", ["btc_spillover", "liquid_weekly_momentum", "price_volume_confirm"])
def test_incomplete_hourly_windows_not_repaired_with_shifted_rows(kind: str) -> None:
    full = sample_frame(240, 51)
    gap = sample_frame(240, 51, gap=100)
    clean = common.source_features(full, kind)
    broken = common.source_features(gap, kind)
    after_gap = datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=180)
    clean_value = clean.filter(
        (pl.col("asset") == "ALT000USDT") & (pl.col("timestamp") == after_gap)
    )["score"].to_list()[0]
    corrupt_value = broken.filter(
        (pl.col("asset") == "ALT000USDT") & (pl.col("timestamp") == after_gap)
    )["score"].to_list()[0]
    assert clean_value is not None
    if kind in {"btc_spillover", "liquid_weekly_momentum"}:
        assert corrupt_value is None
    else:
        # Its 48h input window has healed by hour 180.
        assert corrupt_value is not None


def test_existing_frozen_contract_and_all_spec_links() -> None:
    c = load_contract("contracts/crypto_perps_top100_1h_v1.yaml")
    assert c.label_horizon_bars == 4 and c.execution_delay_bars == 1
    assert c.data_provenance == "verified_market"
    root = Path(__file__).resolve().parents[1]
    for kind, hid in common.HYPOTHESES.items():
        spec = yaml.safe_load(
            (root / f"experiments/active/four_xs4h_{kind}.yaml").read_text()
        )
        hyp = yaml.safe_load((root / f".workspace/hypotheses/{hid}.yaml").read_text())
        assert spec["hypothesis_id"] == hid == hyp["hypothesis_id"]
        assert spec["contract_id"] == hyp["contract_id"] == c.contract_id
        assert spec["strategy_module"] == hyp["strategy_module"]
        assert spec["parameters"] == {"rebalance_every": 4, "top_k": 5}
        assert hyp["expected_holding_bars"] == 4


def test_portfolio_targets_do_not_depend_on_future_prices() -> None:
    panel = sample_frame(
        hours=1100,
        assets=51,
        first=datetime(2024, 6, 1, tzinfo=UTC),
    )
    cutoff = datetime(2024, 7, 10, tzinfo=UTC)
    contract = load_contract("contracts/crypto_perps_top100_1h_v1.yaml")
    original = common.run_factor(panel, contract, "sign_reversal")
    perturbed = panel.with_columns(
        pl.when(pl.col("timestamp") > cutoff)
        .then(pl.col("open") * 2.0)
        .otherwise(pl.col("open")).alias("open"),
        pl.when(pl.col("timestamp") > cutoff)
        .then(pl.col("close") * 2.0)
        .otherwise(pl.col("close")).alias("close"),
    )
    changed = common.run_factor(perturbed, contract, "sign_reversal")
    times = sorted(panel["timestamp"].unique().to_list())
    before = sum(t <= cutoff for t in times)
    assert np.array_equal(
        original["positions"][:before],
        changed["positions"][:before],
    )
    assert np.max(np.sum(np.abs(original["positions"]), axis=1)) <= 1.0000001
    assert np.max(np.abs(original["positions"])) <= 0.5


def test_warden_prefix_invariance_on_btc_spillover_small_fixture() -> None:
    panel = sample_frame(220, 41)
    contract = load_contract("contracts/crypto_perps_top100_1h_v1.yaml")
    class TestPipeline:
        def compute(self, bars_df, contract):
            return common.source_features(bars_df, "btc_spillover")
    verify_prefix_invariance(TestPipeline(), panel, contract)
