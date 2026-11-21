"""Inspect VPS Python environment and record ML4T package signatures.

Generates .workspace/env_manifest.json as required by TKT-01.
"""

from __future__ import annotations

import dataclasses
import importlib
import importlib.metadata
import inspect
import json
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = REPO_ROOT / ".workspace" / "env_manifest.json"

PACKAGES: dict[str, str] = {
    "polars": "polars",
    "duckdb": "duckdb",
    "pyarrow": "pyarrow",
    "numpy": "numpy",
    "scipy": "scipy",
    "scikit-learn": "sklearn",
    "lightgbm": "lightgbm",
    "pyyaml": "yaml",
    "pydantic": "pydantic",
    "ml4t-specs": "ml4t.specs",
    "ml4t-data": "ml4t.data",
    "ml4t-engineer": "ml4t.engineer",
    "ml4t-diagnostic": "ml4t.diagnostic",
    "ml4t-backtest": "ml4t.backtest",
}

OPTIONAL_PACKAGES: dict[str, str] = {
    "ml4t-models": "ml4t.models",
}

SIGNATURE_PROBES: tuple[tuple[str, str], ...] = (
    ("ml4t.specs", "FeedSpec"),
    ("ml4t.specs", "MarketDataSpec"),
    ("ml4t.data.validation", "OHLCVValidator"),
    ("ml4t.data.validation.rules", "ValidationRulePresets"),
    ("ml4t.data.anomaly", "AnomalyManager"),
    ("ml4t.data.sessions", "SessionAssigner"),
    ("ml4t.data.sessions", "SessionCompleter"),
    ("ml4t.engineer", "compute_features"),
    ("ml4t.engineer", "MLDatasetBuilder"),
    ("ml4t.engineer.labeling", "meta_labels"),
    ("ml4t.diagnostic.metrics", "compute_ic_hac_stats"),
    ("ml4t.diagnostic.splitters", "WalkForwardCV"),
    ("ml4t.diagnostic.evaluation.stats", "deflated_sharpe_ratio"),
    ("ml4t.diagnostic.evaluation.stats", "deflated_sharpe_ratio_from_statistics"),
    ("ml4t.diagnostic.evaluation.stats", "effective_number_of_trials"),
    ("ml4t.diagnostic.evaluation.stats", "compute_pbo"),
    ("ml4t.diagnostic.evaluation.stats", "ras_sharpe_adjustment"),
    ("ml4t.diagnostic.evaluation", "TradeAnalysis"),
    ("ml4t.diagnostic.evaluation", "PortfolioAnalysis"),
    ("ml4t.backtest", "Engine"),
    ("ml4t.backtest", "DataFeed"),
    ("ml4t.backtest", "BacktestConfig"),
    ("ml4t.backtest", "ContractSpec"),
    ("ml4t.backtest.execution.rebalancer", "TargetWeightExecutor"),
    ("ml4t.backtest.execution.rebalancer", "RebalanceConfig"),
    ("ml4t.backtest.risk", "StopLoss"),
    ("ml4t.backtest.risk", "TrailingStop"),
)


def _get_version(distribution: str, module: Any) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return getattr(module, "__version__", None)


def _inspect_callable(module_name: str, attr_name: str) -> dict[str, Any]:
    full_target = f"{module_name}.{attr_name}"
    record: dict[str, Any] = {
        "target": full_target,
        "module": module_name,
        "attribute": attr_name,
    }
    try:
        module = importlib.import_module(module_name)
        target = getattr(module, attr_name)
    except (ImportError, AttributeError) as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
        return record

    record["object_kind"] = (
        "class"
        if inspect.isclass(target)
        else "function"
        if inspect.isfunction(target)
        else type(target).__name__
    )

    try:
        sig = inspect.signature(target)
    except (TypeError, ValueError) as exc:
        record["error"] = f"signature_unavailable: {type(exc).__name__}: {exc}"
        return record

    record["signature"] = str(sig)
    params_info: list[dict[str, Any]] = []
    has_calendar = False
    calendar_default: str | None = None

    for name, param in sig.parameters.items():
        default_repr = (
            None
            if param.default is inspect.Parameter.empty
            else repr(param.default)
        )
        annotation_repr = (
            None
            if param.annotation is inspect.Parameter.empty
            else str(param.annotation)
        )
        params_info.append(
            {
                "name": name,
                "kind": param.kind.name,
                "default": default_repr,
                "annotation": annotation_repr,
            }
        )
        if name == "calendar":
            has_calendar = True
            calendar_default = default_repr

    record["parameters"] = params_info
    field_names: list[str] | None = None
    if dataclasses.is_dataclass(target):
        field_names = [f.name for f in dataclasses.fields(target)]
    elif hasattr(target, "model_fields"):
        field_names = list(target.model_fields.keys())
    if field_names is not None:
        record["fields"] = field_names

    if attr_name == "compute_features":
        record["has_calendar_parameter"] = has_calendar
        record["calendar_default"] = calendar_default
        record["accepts_calendar_none"] = has_calendar
    elif module_name == "ml4t.specs" and attr_name == "FeedSpec":
        supports_v2 = False
        v1_fields: list[str] = []
        v2_only_fields: list[str] = []
        if field_names and "time_contract_version" in field_names:
            try:
                v1_spec = target()
                supports_v2 = target(time_contract_version=2).time_contract_version == 2
                v1_set = set(v1_spec.to_dict()) if hasattr(v1_spec, "to_dict") else set(field_names)
                v1_fields = [f for f in field_names if f in v1_set]
                v2_only_fields = [f for f in field_names if f not in v1_set]
            except Exception:  # noqa: BLE001
                supports_v2 = False
        elif field_names:
            v1_fields = list(field_names)
        record["supports_time_contract_version_2"] = supports_v2
        record["v1_fields"] = v1_fields
        record["v2_only_fields"] = v2_only_fields
        if (sem_fields := getattr(target, "_SEMANTICS_FIELDS", None)) is not None:
            record["semantics_fields"] = sorted(sem_fields)
    elif module_name == "ml4t.backtest" and attr_name == "Engine":
        record["has_funding_df"] = "funding_df" in sig.parameters
    elif module_name == "ml4t.backtest" and attr_name == "DataFeed":
        record["has_feed_spec"] = "feed_spec" in sig.parameters
        record["has_session_col"] = "session_col" in sig.parameters

    return record


def _probe_packages(packages: dict[str, str]) -> tuple[dict[str, Any], list[str]]:
    result: dict[str, Any] = {}
    errors: list[str] = []
    for dist_name, mod_name in packages.items():
        entry: dict[str, Any] = {"distribution": dist_name, "module": mod_name}
        try:
            mod = importlib.import_module(mod_name)
            entry["import_ok"] = True
            entry["version"] = _get_version(dist_name, mod)
            entry["error"] = None
        except Exception as exc:  # noqa: BLE001 - manifest must record any import failure
            entry["import_ok"] = False
            entry["version"] = None
            entry["error"] = f"{type(exc).__name__}: {exc}"
            errors.append(f"{mod_name}: {type(exc).__name__}: {exc}")
        result[dist_name] = entry
    return result, errors


def inspect_environment() -> dict[str, Any]:
    packages_result, import_errors = _probe_packages(PACKAGES)
    optional_result, _ = _probe_packages(OPTIONAL_PACKAGES)

    probes_result: dict[str, Any] = {}
    probe_errors: list[str] = []
    for mod_name, attr_name in SIGNATURE_PROBES:
        probe_rec = _inspect_callable(mod_name, attr_name)
        key = f"{mod_name}:{attr_name}"
        probes_result[key] = probe_rec
        if "error" in probe_rec:
            probe_errors.append(f"{key}: {probe_rec['error']}")

    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable": sys.executable,
        },
        "packages": packages_result,
        "optional_packages": optional_result,
        "api_signatures": probes_result,
        "import_errors": import_errors,
        "probe_errors": probe_errors,
    }


def main() -> int:
    manifest = inspect_environment()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _ = OUTPUT_PATH.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )

    print(f"Manifest written to: {OUTPUT_PATH}")
    print(f"Python: {manifest['python']['version']} ({manifest['python']['executable']})")
    print(f"Platform: {manifest['platform']['system']} {manifest['platform']['machine']}")
    print(f"Packages checked: {len(manifest['packages'])}")
    print(f"Import errors: {len(manifest['import_errors'])}")
    print(f"Probe errors: {len(manifest['probe_errors'])}")

    cf = manifest["api_signatures"].get("ml4t.engineer:compute_features", {})
    print(f"ml4t.engineer.compute_features accepts calendar=None: {cf.get('accepts_calendar_none')}")

    if manifest["import_errors"]:
        print("IMPORT ERRORS DETECTED:")
        for err in manifest["import_errors"]:
            print(f"  - {err}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
