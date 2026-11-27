#!/usr/bin/env python3
"""Metadata-only preflight for a declared trial; never execute candidate code.

Validate the frozen YAML, static entry source, pinned exploratory dataset,
execution schema and canonical selection clock against the read-only ledger.
Dynamic protocol/parameter checks, feasibility, and scientific evaluation
remain Warden's governed execution responsibilities. No registry snapshot,
strategy import, outcome calculation, or holdout access is performed here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from warden_ops.readonly import ReadOnlyResearchRegistry

DEFAULT_DB = Path("registry/registry.db")


def prior_stream_lengths(db_path: Path, contract_id: str, exclude_run_id: str = "") -> list[int]:
    """Historical read-only reporting projection; not the admission clock check."""
    ledger = ReadOnlyResearchRegistry(db_path)
    vectors = ledger.read_recorded_return_vectors(contract_id, exclude_run_id)
    return sorted({len(json.loads(vector)) for vector in vectors})


def preflight(spec_path: Path, db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    from warden.contracts import ENGINE_SIMULATION_EPOCH
    from warden.engine_sim import require_execution_columns
    from warden.market_data import ROLE_EXPLORATORY, MarketDataGateway
    from warden.provenance import resolve_dataset
    from warden.registry import normalise_module
    from warden.runner import ExperimentRunner
    from warden.schemas import load_experiment_spec

    root = Path.cwd()
    spec = load_experiment_spec(spec_path)
    if not spec.hypothesis_id:
        raise ValueError("Experiment spec must declare hypothesis_id")
    if not Path(db_path).is_file():
        raise ValueError("Registry does not exist; preflight cannot create it")
    ledger = ReadOnlyResearchRegistry(db_path)
    hyp = ledger.read_hypothesis(spec.hypothesis_id)
    if hyp is None:
        raise ValueError(f"Hypothesis '{spec.hypothesis_id}' not found")
    if hyp["contract_id"] != spec.contract_id or hyp["strategy_family"] != spec.strategy_family:
        raise ValueError("Experiment contract or family differs from registered hypothesis")
    contract = ExperimentRunner(registry=ledger, repo_root=root).resolve_active_contract(
        spec.contract_id
    )
    if ledger.read_contract_spec_hash(contract.contract_id) != contract.spec_hash():
        raise ValueError("Active contract differs from its registered immutable identity")

    # Do not follow candidate-controlled dependencies into data or other files.
    # Complete closure binding and dynamic protocol checks belong to admission.
    module = normalise_module(spec.strategy_module)
    source = (root / module).resolve()
    if not source.is_relative_to(root / "strategies") or source.suffix != ".py":
        raise ValueError("strategy_module must resolve to a Python source under strategies/")
    source_bytes = source.read_bytes()
    compile(source_bytes, module, "exec")

    dataset = resolve_dataset(contract, root)
    gateway = MarketDataGateway(root)
    market = gateway.load(
        contract, ROLE_EXPLORATORY, spec.parameters.get("target_symbols"), resolution=dataset
    )
    require_execution_columns(market.panel, contract)
    # The full contract grid is the selection clock, even for a symbol subset.
    timestamps = (gateway.contract_validation_grid(contract, resolution=dataset)
                  if market.symbols_filtered else market.ts[market.eval_slice])
    alignment = ledger.preflight_selection_alignment(
        contract, timestamps, expected_len=len(timestamps),
        simulation_epoch=ENGINE_SIMULATION_EPOCH,
    )
    return {
        "kind": "METADATA_ONLY",
        "contract_id": contract.contract_id,
        "hypothesis_id": spec.hypothesis_id,
        "strategy_source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "validation_bars": len(timestamps),
        "selection_alignment": alignment,
        "strategy_execution": "NOT_RUN",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate trial metadata without executing it.")
    parser.add_argument("spec", type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args(argv)
    try:
        summary = preflight(args.spec, args.db)
    except (ValueError, OSError, KeyError, SyntaxError, sqlite3.Error) as exc:
        print(f"PREFLIGHT_FAILED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2))
    print("PREFLIGHT_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
