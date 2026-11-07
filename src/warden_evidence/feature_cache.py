"""Causal feature-cache evidence: artifact presence never grants verification.

The proof binds the input panel, contract, feature implementation and Parquet
bytes. Registry-backed callers additionally require matching SQLite evidence.
This module is Warden-owned and covered by its protected source manifest.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl

from warden.contracts import canonical_json

if TYPE_CHECKING:
    from warden.contracts import EvaluationContract
    from warden.leakage import FeaturePipelineProtocol
    from warden.registry import ResearchRegistry


class UnverifiedFeatureCacheError(ValueError):
    """A reused artifact lacks an attributable, matching causality proof."""


def _hash_panel(panel: pl.DataFrame) -> str:
    values = panel.hash_rows(seed=0).to_numpy()
    digest = hashlib.sha256()
    digest.update(str(panel.schema).encode("utf-8"))
    digest.update(str(panel.shape).encode("utf-8"))
    digest.update(values.tobytes())
    return digest.hexdigest()


def _read_proof(path: Path) -> dict | None:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else None
    except (FileNotFoundError, OSError, ValueError):
        return None


def _registry_proof(registry: ResearchRegistry, identity: str, cid: str, path: Path) -> bool:
    with registry._connect() as conn:
        row = conn.execute("SELECT contract_id,prefix_invariance_passed,parquet_cache_path FROM feature_sets WHERE feature_hash=?",
                           (identity,)).fetchone()
    return row is not None and row[0] == cid and row[1] == 1 and Path(row[2]).resolve() == path.resolve()


def _register_proof(registry: ResearchRegistry, identity: str, cid: str, path: Path) -> None:
    with registry._connect() as conn:
        conn.execute("""INSERT OR IGNORE INTO feature_sets
                        (feature_hash,contract_id,feature_names_json,ml4t_engineer_version,
                         prefix_invariance_passed,parquet_cache_path,created_at)
                        VALUES (?,?,?, ?,1,?,?)""",
                     (identity, cid, "[]", "0.2.0", str(path), time.time()))
    if not _registry_proof(registry, identity, cid, path):
        raise UnverifiedFeatureCacheError("existing contradictory SQLite causal proof cannot be overwritten")


def feature_identity(pipeline: FeaturePipelineProtocol, bars_df: pl.DataFrame,
                     contract: EvaluationContract) -> tuple[str, dict]:
    """Exactly one identity formula for cache writes, audit and run-bound verification."""
    try:
        source = inspect.getsource(pipeline.__class__)
    except (TypeError, OSError):
        source = pipeline.__class__.__qualname__
    params = json.dumps(vars(pipeline), sort_keys=True, default=str)
    config = {"contract": contract.spec_hash(), "source": hashlib.sha256(source.encode()).hexdigest(),
              "params": params, "polars": pl.__version__, "engine": "0.2.0",
              "input": _hash_panel(bars_df)}
    return hashlib.sha256(canonical_json(config).encode()).hexdigest()[:20], config


def verified_feature_evidence(pipeline: FeaturePipelineProtocol, bars_df: pl.DataFrame,
                              contract: EvaluationContract, registry: ResearchRegistry,
                              cache_dir: Path) -> dict:
    """Read-only proof projection. Contract-only legacy feature rows are not sufficient."""
    identity, config = feature_identity(pipeline, bars_df, contract)
    path = Path(cache_dir) / f"{identity}.parquet"
    proof = _read_proof(Path(cache_dir) / f"{identity}.proof.json")
    valid = (isinstance(proof, dict) and proof.get("config") == config and
             proof.get("causality") is True and proof.get("artifact_path") == str(path.resolve())
             and path.is_file() and _registry_proof(registry, identity, contract.contract_id, path))
    if valid:
        valid = hashlib.sha256(path.read_bytes()).hexdigest() == proof.get("artifact_sha256")
    return {"verified": bool(valid), "feature_identity": identity,
            "artifact_sha256": proof.get("artifact_sha256") if valid else "",
            "reason": "matching contract/source/input/cache+SQLite proof" if valid else
                      "no matching verified feature evidence for this exact strategy/data"}


def compute_and_cache_features(
    pipeline: FeaturePipelineProtocol, bars_df: pl.DataFrame, contract: EvaluationContract,
    cache_dir: Path = Path("cache/features"), registry: ResearchRegistry | None = None,
) -> pl.DataFrame:
    """Reuse only identity-bound proof; absent/mismatched proof triggers revalidation."""
    from warden.leakage import verify_prefix_invariance

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    identity, config = feature_identity(pipeline, bars_df, contract)
    path = cache_dir / f"{identity}.parquet"
    proof_path = cache_dir / f"{identity}.proof.json"
    proof = _read_proof(proof_path)
    valid = (proof is not None and proof.get("config") == config and proof.get("causality") is True
             and proof.get("artifact_path") == str(path.resolve()) and path.is_file())
    if valid:
        valid = hashlib.sha256(path.read_bytes()).hexdigest() == proof.get("artifact_sha256")
    if valid and registry is not None:
        valid = _registry_proof(registry, identity, contract.contract_id, path)
    if valid:
        return pl.read_parquet(path)

    # Incomplete cached bytes never stand in for a committed causality proof.
    verify_prefix_invariance(pipeline, bars_df, contract)
    feats = pipeline.compute(bars_df, contract).sort("timestamp", "asset")
    if path.exists():
        # We may recover after SQLite/proof commit failure only if rerendering
        # is byte-identical. Never overwrite an ambiguous previous artifact.
        with tempfile.NamedTemporaryFile(dir=cache_dir, suffix=".pending") as handle:
            feats.write_parquet(handle)
            handle.flush()
            os.fsync(handle.fileno())
            expected = hashlib.sha256(Path(handle.name).read_bytes()).hexdigest()
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise UnverifiedFeatureCacheError("orphaned/mismatched feature Parquet requires operator review")
    else:
        with tempfile.NamedTemporaryFile(dir=cache_dir, suffix=".pending") as handle:
            feats.write_parquet(handle)
            handle.flush()
            os.fsync(handle.fileno())
            os.link(handle.name, path)
        dir_fd = os.open(cache_dir, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    if registry is not None:
        _register_proof(registry, identity, contract.contract_id, path)
    # Proof is last: a crash before this point leaves a revalidation-only artifact.
    receipt = {"schema": 1, "config": config, "causality": True,
               "artifact_path": str(path.resolve()), "artifact_sha256": sha}
    with tempfile.NamedTemporaryFile("w", dir=cache_dir, suffix=".pending", delete=False,
                                     encoding="utf-8") as handle:
        temporary = Path(handle.name)
        handle.write(json.dumps(receipt, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, proof_path)
    dir_fd = os.open(cache_dir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    return feats
