"""Producer v2 compatibility: typed availability, identity and price basis."""
import hashlib
import json

import pytest

from warden.resolve import resolve_data_requirement


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def publish(lake, entries):
    entries = [{**entry, "entry_identity": digest(entry)} for entry in entries]
    doc = {"schema_version": "research-dataset-catalog-v2", "entries": entries}
    doc["catalog_identity"] = digest(doc)
    (lake / ".catalog" / "research-dataset-catalog.json").write_text(json.dumps(doc))
    (lake / ".catalog" / "research-readiness.json").write_text(
        json.dumps({"catalog_identity": doc["catalog_identity"]})
    )


@pytest.mark.parametrize(
    "domain,catalog_domain,symbol,key,sub_family,provider,semantics",
    [
        ("crypto_perps", "crypto_perp", "BTCUSDT", "crypto_futures_minute_BTCUSDT_PERP",
         "crypto_perp", "binance", "perpetual"),
        ("indices", "index", "ES", "index_minute_ES", "equity_index", "dukascopy", "index_cfd"),
        ("fx", "fx", "EURUSD", "forex_minute_EURUSD", "major", "dukascopy", "spot_fx"),
    ],
)
def test_current_catalog_v2_taxonomy_and_provenance(
    tmp_path, domain, catalog_domain, symbol, key, sub_family, provider, semantics,
):
    lake = tmp_path / "lake"
    repo = tmp_path / "repo"
    repo.mkdir()
    (lake / ".catalog").mkdir(parents=True)
    (lake / ".metadata").mkdir()
    (lake / key).mkdir()
    metadata = {"first_timestamp": "2019-01-01T00:00:00Z", "last_timestamp": "2025-01-01T00:00:00Z"}
    evidence = {}
    if domain == "fx":
        metadata = {"schema_version": "dukascopy-wave1-store-provenance-v1",
                    "observed_start": "2009-01-01T00:00:00Z",
                    "observed_end": "2025-01-01T00:00:00Z", "price_type": "bid"}
        rel = f".acceptance/{key}/dataset.json"
        (lake / rel).parent.mkdir(parents=True)
        raw = b'{"accepted":true}'
        (lake / rel).write_bytes(raw)
        evidence["acceptance"] = {"relative_path": rel, "present": True,
                                  "sha256": hashlib.sha256(raw).hexdigest()}
    rel = f".metadata/{key}.json"
    raw = json.dumps(metadata).encode()
    (lake / rel).write_bytes(raw)
    evidence["metadata"] = {"relative_path": rel, "sha256": hashlib.sha256(raw).hexdigest()}
    entry = {
        "asset_class": catalog_domain, "frequency": "minute", "sub_family": sub_family,
        "dataset_key": key, "symbol": symbol, "data_kind": "ohlcv",
        "instrument_semantics": semantics, "authority": "canonical", "qualification": "accepted",
        "store_present": True, "provider": provider, "expected_provider": provider,
        "evidence_errors": [], "evidence": evidence,
        "coverage": {"first_timestamp": metadata.get("observed_start", metadata.get("first_timestamp")),
                     "last_timestamp": metadata.get("observed_end", metadata.get("last_timestamp"))},
    }
    distractors = [
        {**entry, "dataset_key": "crypto_futures_open_interest_1m_HYPERLIQUID_BTCUSDT_PERP",
         "data_kind": "open_interest"},
        {**entry, "dataset_key": "crypto_futures_mark_price_1m_BINANCE_UM_PERP", "data_kind": "mark"},
        {**entry, "dataset_key": "crypto_futures_funding_BTCUSDT_PERP", "data_kind": "funding_events"},
    ]
    publish(lake, [entry, *distractors])
    requirement = {
        "asset_class": domain, "symbols": [symbol], "frequency": "1m",
        "min_start": "2021-01-01T00:00:00Z", "price_basis": "ohlcv",
        "execution_mode": "NEXT_BAR",
    }
    def resolve(req=requirement):
        return resolve_data_requirement(req, repo_root=repo, lake_root=lake).to_dict()
    report = resolve()
    assert report["decision"] == "compile_required"
    assert report["lake"]["symbols"][symbol]["dataset_key"] == key
    assert set(report["lake"]["symbols"]) == {symbol}
    assert report["policy"]["trusted"] is False
    assert report["matched_contract"] is None
    assert resolve({**requirement, "price_basis": "quote_ohlcv", "execution_mode": "QUOTE_SIDE"})["reason"] == "incompatible_price_basis"
    assert resolve({**requirement, "instrument_semantics": "individual_futures"})["reason"] == "unsupported_instrument_semantics"
    for field, value in (
        ("qualification", "unqualified"), ("authority", "secondary"),
        ("store_present", False), ("evidence_errors", ["bad identity"]),
    ):
        publish(lake, [{**entry, field: value}, *distractors])
        refused = resolve()
        assert refused["decision"] == "fail_closed"
        assert refused["reason"] == "untrusted_source"
        assert refused["missing_symbols"] == []
    publish(lake, [entry, *distractors])
    other_key = key + "_SECOND"
    other = {**entry, "symbol": "OTHER", "dataset_key": other_key,
             "qualification": "unqualified"}
    family = {**requirement, "symbols": [], "all_in_family": True}
    publish(lake, [entry, other, *distractors])
    assert resolve(family)["reason"] == "untrusted_source"
    publish(lake, [entry, {k: v for k, v in other.items() if k != "data_kind"}, *distractors])
    assert resolve(family)["reason"] == "unsupported_metadata"
    publish(lake, [entry, *distractors])
    readiness = lake / ".catalog" / "research-readiness.json"
    readiness.write_text('{"catalog_identity":"stale"}')
    assert resolve()["reason"] == "lake_unavailable"
    publish(lake, [entry, *distractors])
    (lake / rel).write_bytes(b"{}")
    assert resolve()["reason"] == "unsupported_metadata"
    assert list(repo.iterdir()) == []
