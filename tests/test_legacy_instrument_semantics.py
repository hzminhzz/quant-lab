"""Explicit semantics cannot be satisfied by a pre-typed lake catalog."""
import json

from warden.resolve import resolve_data_requirement


def test_legacy_lake_refuses_explicit_instrument_semantics(tmp_path):
    lake = tmp_path / "lake"
    repo = tmp_path / "repo"
    repo.mkdir()
    (lake / ".catalog").mkdir(parents=True)
    (lake / ".metadata").mkdir()
    key = "crypto_futures_minute_BTCUSDT_PERP"
    (lake / ".metadata" / f"{key}.json").write_text(json.dumps({
        "first_timestamp": "2019-01-01T00:00:00Z",
        "last_timestamp": "2025-01-01T00:00:00Z",
    }))
    (lake / ".catalog/research-dataset-catalog.json").write_text(json.dumps({
        "entries": [{"dataset_key": key, "asset_class": "crypto_perps", "frequency": "minute"}],
    }))
    report = resolve_data_requirement({
        "asset_class": "crypto_perps", "symbols": ["BTCUSDT"], "frequency": "1m",
        "min_start": "2021-01-01T00:00:00Z", "price_basis": "ohlcv",
        "execution_mode": "NEXT_BAR", "instrument_semantics": "individual_futures",
    }, repo_root=repo, lake_root=lake)
    assert report.decision == "fail_closed"
    assert report.reason == "unsupported_instrument_semantics"
    assert not report.missing_symbols
