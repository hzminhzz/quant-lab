import hashlib
import runpy
from pathlib import Path

import polars as pl
import pytest

from warden.contracts import load_contract
from warden.synthetic import generate_canonical_market_panel

API = runpy.run_path("scripts/salvage_crypto_perps.py")
CONTRACT = Path("contracts/crypto_perps_1h_v1.yaml")


def test_deterministic_chronological_parquet_split(tmp_path):
    before = CONTRACT.read_bytes()
    first = API["salvage"](CONTRACT, tmp_path / "one", synthetic=True)
    second = API["salvage"](CONTRACT, tmp_path / "two", synthetic=True)
    contract = load_contract(CONTRACT)
    for name, count in [("exploratory", 24000), ("holdout", 4800)]:
        assert first[name]["sha256"] == second[name]["sha256"]
        panel = pl.read_parquet(first[name]["path"])
        assert panel.height == count
        assert panel.schema["timestamp"] == pl.Datetime("us", "UTC")
        assert panel.null_count().row(0) == (0,) * 8
        assert panel.select(pl.col("close").min()).item() > 0
        assert panel.equals(panel.sort("timestamp", "asset"))
    exp = pl.read_parquet(first["exploratory"]["path"])
    hold = pl.read_parquet(first["holdout"]["path"])
    assert exp["timestamp"].max() < contract.holdout_start == hold["timestamp"].min()
    assert CONTRACT.read_bytes() == before


def test_real_source_is_read_only_and_invalid_keys_fail(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    path = source / "bars.parquet"
    panel = generate_canonical_market_panel()
    panel.write_parquet(path)
    original = hashlib.sha256(path.read_bytes()).hexdigest()
    API["salvage"](CONTRACT, tmp_path / "out", source_dir=source)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original
    with pytest.raises(ValueError, match="read-only source"):
        API["salvage"](CONTRACT, source, source_dir=source)
    with pytest.raises(ValueError, match="Duplicate"):
        API["normalize"](pl.concat([panel, panel[:1]]))
    with pytest.raises(ValueError, match="Invalid"):
        API["normalize"](panel.with_columns(pl.lit(-1.0).alias("close")))
    with pytest.raises(ValueError, match="non-finite"):
        API["normalize"](panel.with_columns(pl.lit(float("nan")).alias("funding_rate")))
