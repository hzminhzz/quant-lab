"""Synthetic producer wire fixtures, never installed or used for real acceptance.

Compiler tests use committed inventory rather than a legacy mutable catalog.
Real producer interoperability is independently exercised by DG's native
cross-repository harness.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import polars as pl


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def commit_fixture(root: Path) -> None:
    catalog_path = root / ".catalog/research-dataset-catalog.json"
    catalog = json.loads(catalog_path.read_text())
    for entry in catalog["entries"]:
        key = entry["dataset_key"]
        metadata = json.loads((root / ".metadata" / f"{key}.json").read_text())
        leaves = []
        for path in sorted((root / key).rglob("*.parquet")):
            leaves.append({
                "path": path.relative_to(root / key).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "schema": {k: str(v) for k, v in pl.read_parquet_schema(path).items()},
            })
        if not leaves:
            continue
        domain = entry["asset_class"]
        symbol = entry.get("symbol") or key.split("_")[-1]
        if symbol == "PERP":
            symbol = key.split("_")[-2]
        entry.update(
            asset_class="crypto_perp" if domain in {"crypto_perps", "crypto_perp"} else domain,
            symbol=symbol, provider="hyperliquid" if domain in {"crypto_perps", "crypto_perp"} else "dukascopy",
            data_kind="ohlcv", instrument_semantics="perpetual" if domain in {"crypto_perps", "crypto_perp"} else "spot_fx",
            authority="canonical", qualification="accepted", store_present=True,
            evidence_errors=[], coverage={
                "first_timestamp": metadata["first_timestamp"], "last_timestamp": metadata["last_timestamp"],
                "row_count": sum(pl.scan_parquet(root / key / leaf["path"]).select(pl.len()).collect().item()
                                 for leaf in leaves),
                "partition_count": len(leaves),
            },
        )
        # These are synthetic writer receipts deliberately used by the existing
        # promotion tests, not evidence about a live provider.
        acceptance = entry.get("acceptance", {"present": False})
        generation = {
            "schema_version": "committed-dataset-v1", "dataset_key": key,
            "partitions": leaves, "data_identity": _digest(leaves),
            "metadata": metadata, "quality": {"status": "ok", "fixture": True},
            "ohlcv_auxiliary_columns": [],
            "acceptance": acceptance,
            "semantics": {k: entry.get(k) for k in (
                "provider", "symbol", "asset_class", "sub_family", "frequency", "data_kind",
                "instrument_semantics", "price_type", "source_symbol", "qualification", "coverage")},
        }
        identity = _digest(generation)
        generation["generation_identity"] = identity
        directory = root / ".generations" / key / identity
        directory.mkdir(parents=True, exist_ok=True)
        manifest = directory / "generation.json"
        manifest.write_text(json.dumps(generation, sort_keys=True))
        bindings = {}
        for leaf in leaves:
            path = directory / leaf["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                shutil.copyfile(root / key / leaf["path"], path)
            stat = path.stat()
            bindings[leaf["path"]] = [stat.st_size, stat.st_mtime_ns, stat.st_dev, stat.st_ino]
        entry.update(generation=generation, observed_leaf_stats=bindings,
                     evidence={"metadata": {
                         "present": True, "relative_path": manifest.relative_to(root).as_posix(),
                         "sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                     }, "acceptance": acceptance})
        entry["entry_identity"] = _digest({k: v for k, v in entry.items() if k != "entry_identity"})
    catalog["schema_version"] = "research-dataset-catalog-v2"
    catalog["catalog_identity"] = _digest({k: v for k, v in catalog.items() if k != "catalog_identity"})
    catalog_path.write_text(json.dumps(catalog, sort_keys=True))
