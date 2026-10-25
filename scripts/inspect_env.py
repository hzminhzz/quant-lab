"""Inspect VPS Python environment and record ML4T package signatures.

Generates .workspace/env_manifest.json as required by TKT-01.
"""

from __future__ import annotations

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
    "ml4t-live": "ml4t.live",
}

SIGNATURE_PROBES: tuple[tuple[str, str], ...] = (
    ("ml4t.engineer", "compute_features"),
    ("ml4t.diagnostic.evaluation.stats", "deflated_sharpe_ratio"),
    ("ml4t.backtest", "Engine"),
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
    if attr_name == "compute_features":
        record["has_calendar_parameter"] = has_calendar
        record["calendar_default"] = calendar_default
        # accepts calendar=None if parameter is present
        record["accepts_calendar_none"] = has_calendar

    return record


def inspect_environment() -> dict[str, Any]:
    packages_result: dict[str, Any] = {}
    import_errors: list[str] = []

    for dist_name, mod_name in PACKAGES.items():
        entry: dict[str, Any] = {
            "distribution": dist_name,
            "module": mod_name,
        }
        try:
            mod = importlib.import_module(mod_name)
            entry["import_ok"] = True
            entry["version"] = _get_version(dist_name, mod)
            entry["error"] = None
        except Exception as exc:  # noqa: BLE001 - manifest must record any import failure
            entry["import_ok"] = False
            entry["version"] = None
            entry["error"] = f"{type(exc).__name__}: {exc}"
            import_errors.append(f"{mod_name}: {type(exc).__name__}: {exc}")
        packages_result[dist_name] = entry

    probes_result: dict[str, Any] = {}
    probe_errors: list[str] = []
    for mod_name, attr_name in SIGNATURE_PROBES:
        probe_rec = _inspect_callable(mod_name, attr_name)
        key = f"{mod_name}:{attr_name}"
        probes_result[key] = probe_rec
        if "error" in probe_rec:
            probe_errors.append(f"{key}: {probe_rec['error']}")

    manifest: dict[str, Any] = {
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
        "api_signatures": probes_result,
        "import_errors": import_errors,
        "probe_errors": probe_errors,
    }
    return manifest


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
