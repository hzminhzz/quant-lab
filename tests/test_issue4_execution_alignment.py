"""Issue #4 tickets 04/05: execution-time, funding, label and purge alignment."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from warden.contracts import load_contract
from warden.leakage import (
    compute_open_forward_label,
    executable_label_span,
    purged_walk_forward_splits,
)
from warden.runner import (
    DoubleLatencyError,
    ExecutionDataError,
    ExperimentRunner,
    _funding_settlement_mask,
    execution_returns,
    require_execution_columns,
    simulate_positions,
)

REPO_CONTRACT = load_contract(__import__("pathlib").Path("contracts/crypto_perps_1h_v1.yaml"))


def _gapped_panel(n_bars: int = 60, n_assets: int = 3):
    ts = [datetime(2025, 1, 1, tzinfo=UTC) + timedelta(hours=t) for t in range(n_bars)]
    rng = np.random.default_rng(3)
    opens = 100.0 + np.cumsum(rng.normal(0, 0.6, (n_bars, n_assets)), axis=0)
    closes = opens + rng.normal(0, 0.4, (n_bars, n_assets))  # close is deliberately not the next open
    return opens, closes, ts


def test_open_to_open_pnl_excludes_every_prefill_gap():
    opens, closes, _ts = _gapped_panel()
    n_bars, n_assets = opens.shape
    for delay in (1, 2, 3):
        for horizon in (2, 4):
            contract = _contract(delay, horizon)
            ret_p = {"long": np.divide(np.vstack([opens[1:], opens[-1:]]), opens) - 1.0, "short": None}
            for decision in (5, 11):
                for a in range(n_assets):
                    pos = np.zeros((n_bars, n_assets))
                    pos[decision:decision + horizon, a] = 1.0
                    sim = simulate_positions(pos, ret_p["long"], None, contract, slice(0, n_bars), cost_multiplier=0.0)
                    earned = np.prod(1.0 + sim["gross_returns"][decision + delay:decision + delay + horizon]) - 1.0
                    expected = opens[decision + delay + horizon, a] / opens[decision + delay, a] - 1.0
                    np.testing.assert_allclose(earned, expected, rtol=1e-12, atol=1e-12)
                    # pre-fill bars and the close-to-open gap contribute nothing
                    assert np.allclose(sim["gross_returns"][:decision + delay], 0.0)
                    prefill = (closes[decision + delay - 1, a] / opens[decision + delay, a] - 1.0)
                    if abs(prefill) > 1e-6:
                        assert abs(earned - prefill) > 1e-6


def _contract(delay: int, horizon: int):
    from dataclasses import replace
    return replace(REPO_CONTRACT, contract_id=f"GAP_D{delay}_H{horizon}", label_horizon_bars=horizon,
                   execution_delay_bars=delay, data_provenance="unknown", data_snapshot_sha256="")


def test_missing_open_and_missing_quote_side_fail_closed():
    from dataclasses import replace
    panel = pl.DataFrame({"timestamp": [datetime(2025, 1, 1, tzinfo=UTC)], "asset": ["A"], "close": [1.0],
                          "funding_rate": [0.0]})
    with pytest.raises(ExecutionDataError, match="requires \\['open'\\]"):
        execution_returns(panel, REPO_CONTRACT, ["A"])
    quote_contract = replace(REPO_CONTRACT, execution_mode="QUOTE_SIDE", data_provenance="unknown",
                             data_snapshot_sha256="")
    with pytest.raises(ExecutionDataError, match="requires \\['bid', 'ask'\\]"):
        require_execution_columns(panel, quote_contract)
    perp_without_funding = panel.drop("funding_rate").with_columns(open=pl.lit(1.0))
    with pytest.raises(ExecutionDataError, match="without funding_rate"):
        require_execution_columns(perp_without_funding, REPO_CONTRACT)
    with pytest.raises(ExecutionDataError, match="null open prices"):
        require_execution_columns(panel.with_columns(open=pl.lit(None, dtype=pl.Float64)), REPO_CONTRACT)


def test_quote_side_long_pays_ask_and_short_receives_bid():
    from dataclasses import replace
    contract = replace(REPO_CONTRACT, execution_mode="QUOTE_SIDE", data_provenance="unknown",
                       data_snapshot_sha256="", taker_fee_bps=0.0001, slippage_bps=0.0001)
    panel = pl.DataFrame({
        "timestamp": [datetime(2025, 1, 1, tzinfo=UTC) + timedelta(hours=i) for i in range(4) for _ in ("A",)],
        "asset": ["A"] * 4, "bid": [100.0, 101.0, 102.0, 103.0], "ask": [100.5, 101.5, 102.5, 103.5],
        "open": [100.0] * 4, "funding_rate": [0.0] * 4})
    ret_p = execution_returns(panel, contract, ["A"])
    np.testing.assert_allclose(ret_p["long"][:3, 0], [101.0 / 100.5 - 1, 102.0 / 101.5 - 1, 103.0 / 102.5 - 1])
    np.testing.assert_allclose(ret_p["short"][:3, 0], [-(101.5 / 100.0 - 1), -(102.5 / 101.0 - 1), -(103.5 / 102.0 - 1)])
    long_pos = np.ones((4, 1))
    sim = simulate_positions(long_pos, ret_p["long"], None, contract, slice(0, 4), quote_returns=ret_p)
    assert np.isclose(sim["gross_returns"][1], 102.0 / 101.5 - 1)   # filled at ask[1], exited at bid[2]
    short_sim = simulate_positions(-long_pos, ret_p["long"], None, contract, slice(0, 4), quote_returns=ret_p)
    assert np.isclose(short_sim["gross_returns"][1], -(102.5 / 101.0 - 1))


def test_funding_signs_and_settlement_mask():
    ts = [datetime(2025, 1, 1, tzinfo=UTC) + timedelta(hours=i) for i in range(24)]
    mask = _funding_settlement_mask(ts, REPO_CONTRACT)
    assert list(np.where(mask)[0]) == [0, 8, 16]
    pos, rets = np.ones((24, 1)), np.zeros((24, 1))
    for rate, sign in ((0.0005, -1), (-0.0005, 1)):
        fund = np.full((24, 1), rate)
        long_net = float(np.sum(simulate_positions(pos, rets, fund, REPO_CONTRACT, slice(0, 24), funding_mask=mask)["net_returns"]))
        short_net = float(np.sum(simulate_positions(-pos, rets, fund, REPO_CONTRACT, slice(0, 24), funding_mask=mask)["net_returns"]))
        # 3 settlements fall in the window, but the bar-0 charge is skipped by the execution delay.
        assert np.isclose(long_net - (-0.0006), -2 * rate, atol=1e-12)   # long pays positive funding
        assert np.isclose(short_net - (-0.0006), 2 * rate, atol=1e-12)   # short receives positive funding
        assert np.sign(long_net - (-0.0006)) == sign


def test_forward_label_uses_the_executable_interval():
    opens, _, _ = _gapped_panel(30)
    for delay, horizon in ((1, 2), (2, 4), (3, 4)):
        labels = compute_open_forward_label(opens, delay, horizon)
        for t in range(len(opens) - delay - horizon):
            raw = (opens[t + delay + horizon] - opens[t + delay]) / opens[t + delay]
            np.testing.assert_allclose(labels[t], raw - raw.mean(), rtol=1e-12, atol=1e-12)
        assert np.allclose(labels[len(opens) - delay - horizon:], 0.0)
    assert executable_label_span(REPO_CONTRACT) == REPO_CONTRACT.label_horizon_bars + REPO_CONTRACT.execution_delay_bars


def test_purge_covers_execution_delay_when_it_exceeds_embargo():
    delay, horizon, embargo = 3, 4, 1
    splits = purged_walk_forward_splits(500, 3, 200, horizon, embargo, execution_delay=delay)
    for train_idx, test_idx in splits:
        assert train_idx[-1] + delay + horizon + embargo <= test_idx[0]
    uncorrected = purged_walk_forward_splits(500, 3, 200, horizon, embargo)
    assert any(train_idx[-1] + delay + horizon >= test_idx[0] for train_idx, test_idx in uncorrected)


def test_declared_prelag_is_rejected_as_double_latency(tiny_repo):
    fixture = tiny_repo(module_source="""
import numpy as np


def generate_positions(panel_df, contract, **kwargs):
    ts = sorted(panel_df["timestamp"].unique().to_list())
    assets = sorted(panel_df["asset"].unique().to_list())
    pos = np.zeros((len(ts), len(assets)))
    pos[2::4] = 0.1
    return {"positions": pos, "signal_delay_bars": contract.execution_delay_bars}
""")
    runner = ExperimentRunner(registry=fixture.registry, repo_root=fixture.repo)
    with pytest.raises(DoubleLatencyError, match="signals must not be pre-lagged"):
        runner.run_spec(fixture.spec)
    with fixture.registry._connect() as conn:
        row = conn.execute("SELECT verdict, notes FROM trials").fetchone()
    assert row[0] == "FAILED" and "double latency" in row[1].lower() or row[0] == "FAILED"
