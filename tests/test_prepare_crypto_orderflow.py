"""Order-flow market-source bridge: no historical feature invention or silent row changes."""
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from scripts import prepare_crypto_orderflow as flow
from warden.contracts import load_contract


@pytest.fixture
def source_pair() -> tuple[pl.DataFrame, pl.DataFrame]:
    t0 = datetime(2024, 3, 1, tzinfo=UTC)
    rows = []
    for i in range(3):
        rows.append({
            "timestamp": t0 + timedelta(hours=i),
            "asset": "BTCUSDT", "open": 100.0 + i,
            "high": 102.0 + i, "low": 99.0 + i,
            "close": 101.0 + i, "volume": 10.0,
            "funding_rate": 0.0001,
        })
    base = pl.DataFrame(rows)
    raw = base.drop("funding_rate").rename({"asset": "symbol"}).with_columns(
        pl.col("timestamp").cast(pl.Datetime("ms", "UTC")),
        pl.lit(1020.0).alias("quote_volume"),
        pl.lit(100).alias("trade_count"),
        pl.lit(6.0).alias("taker_buy_base_volume"),
        pl.lit(612.0).alias("taker_buy_quote_volume"),
    )
    return base, raw


def test_join_preserves_all_frozen_keys_and_values(source_pair) -> None:
    base, raw = source_pair
    enriched = flow.enrich(base, raw)
    assert enriched.height == base.height
    assert enriched["funding_rate"].to_list() == base["funding_rate"].to_list()
    assert enriched["trade_count"].to_list() == [100, 100, 100]
    assert enriched["taker_buy_quote_volume"].to_list() == [612.0] * 3


@pytest.mark.parametrize("issue,reason", [
    ("missing", "Missing raw"),
    ("duplicate", "Duplicate raw"),
    ("mismatch", "OHLCV disagreement"),
    ("bad_flow", "Invalid trade"),
    ("zero_count", "Invalid trade"),
])
def test_fail_closed_on_corruption(source_pair, issue, reason) -> None:
    base, raw = source_pair
    if issue == "missing":
        raw = raw.head(2)
    elif issue == "duplicate":
        raw = pl.concat([raw, raw.head(1)])
    elif issue == "mismatch":
        raw = raw.with_columns((pl.col("close") + 1.0).alias("close"))
    elif issue == "bad_flow":
        raw = raw.with_columns(pl.lit(1200.0).alias("taker_buy_quote_volume"))
    elif issue == "zero_count":
        raw = raw.with_columns(pl.lit(0).alias("trade_count"))
    with pytest.raises(ValueError, match=reason):
        flow.enrich(base, raw)


def test_genuine_no_trade_bar_is_explicitly_unusable_not_imputed(source_pair) -> None:
    base, raw = source_pair
    base = base.with_columns(
        pl.lit(100.0).alias("open"),
        pl.lit(100.0).alias("high"),
        pl.lit(100.0).alias("low"),
        pl.lit(100.0).alias("close"),
        pl.lit(0.0).alias("volume"),
    )
    raw = raw.with_columns(
        pl.lit(100.0).alias("open"),
        pl.lit(100.0).alias("high"),
        pl.lit(100.0).alias("low"),
        pl.lit(100.0).alias("close"),
        pl.lit(0.0).alias("volume"),
        pl.lit(0.0).alias("quote_volume"),
        pl.lit(0).alias("trade_count"),
        pl.lit(0.0).alias("taker_buy_base_volume"),
        pl.lit(0.0).alias("taker_buy_quote_volume"),
    )
    result = flow.enrich(base, raw)
    assert result["flow_valid"].to_list() == [False] * 3
    assert result["quote_volume"].to_list() == [0.0] * 3


def test_digest_pinned_audit_and_write_once(tmp_path, monkeypatch, source_pair) -> None:
    base, raw = source_pair
    panel = tmp_path / "baseline.parquet"
    base.write_parquet(panel)
    monkeypatch.setattr(flow, "BASE_SHA", flow.sha256(panel))
    root = tmp_path / "market"
    data_file = root / "symbol=BTCUSDT/year=2024/month=3/data.parquet"
    data_file.parent.mkdir(parents=True)
    raw.write_parquet(data_file)
    full, audit = flow.audit(panel, root)
    assert audit["row_count"] == 3
    assert audit["raw_source_file_count"] == 1
    assert audit["contract_approved"] is False
    target = tmp_path / "derived.parquet"
    flow.prepare_once(target, full, audit["derived_sha256"])
    flow.prepare_once(target, full, audit["derived_sha256"])
    assert flow.sha256(target) == audit["derived_sha256"]
    target.write_bytes(b"different content")
    with pytest.raises(FileExistsError, match="Refusing"):
        flow.prepare_once(target, full, audit["derived_sha256"])


def test_unapproved_proposal_matches_pinned_output_without_active_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    doc_root = root / "docs/contract-proposals"
    contract = load_contract(doc_root / "crypto_perps_top100_orderflow_1h_h72_v1.yaml")
    manifest = json.loads((doc_root / "crypto_perps_top100_orderflow_1h_h72_v1.json").read_text())
    assert contract.contract_id == manifest["contract_id"]
    assert contract.label_horizon_bars == contract.embargo_bars == 72
    assert contract.execution_delay_bars == 1
    assert contract.execution_mode == "NEXT_BAR"
    assert contract.data_snapshot_sha256 == manifest["sha256"]
    assert contract.val_end == datetime(2025, 7, 1, tzinfo=UTC)
    assert manifest["status"] == "UNAPPROVED_PROPOSAL_ONLY"
    assert not (root / "contracts/crypto_perps_top100_orderflow_1h_h72_v1.yaml").exists()


def test_changed_base_snapshot_fails(tmp_path, source_pair) -> None:
    base, _ = source_pair
    path = tmp_path / "base.parquet"
    base.write_parquet(path)
    with pytest.raises(ValueError, match="Frozen base SHA"):
        flow.audit(path, tmp_path)
