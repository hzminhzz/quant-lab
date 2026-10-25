"""Bootstrap acceptance tests for Ticket TKT-01."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

CANONICAL_DIRECTORIES: tuple[str, ...] = (
    "contracts",
    "src/warden",
    "strategies/baselines",
    "strategies/candidates",
    "experiments/active",
    "registry/returns",
    "registry/predictions",
    "cache/features",
    "tests/acceptance",
    "tests/experiments",
    "scripts",
    "adapters/omp",
    "adapters/opencode",
    "adapters/claude",
    "adapters/codex",
    ".workspace/memory",
    ".workspace/hypotheses",
    ".workspace/literature",
    ".workspace/plans",
    ".workspace/transitions",
)

REQUIRED_PACKAGES: tuple[str, ...] = (
    "polars",
    "duckdb",
    "pyarrow",
    "numpy",
    "scipy",
    "scikit-learn",
    "lightgbm",
    "pyyaml",
    "pydantic",
    "ml4t-specs",
    "ml4t-data",
    "ml4t-engineer",
    "ml4t-diagnostic",
    "ml4t-backtest",
    "ml4t-live",
)


def test_canonical_directory_skeleton_exists() -> None:
    """Verify all 20 required canonical directories exist in quant-lab."""
    missing = [
        rel_path
        for rel_path in CANONICAL_DIRECTORIES
        if not (REPO_ROOT / rel_path).is_dir()
    ]
    assert not missing, f"Missing canonical directories: {missing}"


def test_env_manifest_validity_and_imports() -> None:
    """Verify .workspace/env_manifest.json is valid and has 0 import errors."""
    manifest_path = REPO_ROOT / ".workspace" / "env_manifest.json"
    err_msg = (
        f"Missing manifest file at {manifest_path}; "
        "run scripts/inspect_env.py first"
    )
    assert manifest_path.is_file(), err_msg

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest.get("import_errors") == [], (
        f"Import errors detected in manifest: {manifest.get('import_errors')}"
    )
    assert manifest.get("probe_errors") == [], (
        f"Probe errors detected in manifest: {manifest.get('probe_errors')}"
    )

    packages = manifest.get("packages", {})
    for pkg in REQUIRED_PACKAGES:
        assert pkg in packages, f"Package {pkg} missing from manifest"
        entry = packages[pkg]
        assert entry.get("import_ok") is True, (
            f"Import failed for {pkg}: {entry.get('error')}"
        )
        assert entry.get("version"), f"Version missing for {pkg}"

    probes = manifest.get("api_signatures", {})
    cf_probe = probes.get("ml4t.engineer:compute_features")
    assert cf_probe is not None, "ml4t.engineer:compute_features probe missing"
    assert cf_probe.get("has_calendar_parameter") is True, (
        "compute_features must declare calendar parameter"
    )
    assert cf_probe.get("accepts_calendar_none") is True, (
        "compute_features must accept calendar=None"
    )

    engine_probe = probes.get("ml4t.backtest:Engine")
    assert engine_probe is not None, "ml4t.backtest:Engine probe missing"
    assert engine_probe.get("object_kind") == "class"

    dsr_key = "ml4t.diagnostic.evaluation.stats:deflated_sharpe_ratio"
    dsr_probe = probes.get(dsr_key)
    assert dsr_probe is not None, "deflated_sharpe_ratio probe missing"


def test_warden_cli_version_entrypoint() -> None:
    """Verify uv run warden --version prints 'warden 0.1.0' and exits 0."""
    result = subprocess.run(
        ["uv", "run", "warden", "--version"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, f"warden --version failed: {result.stderr}"
    assert result.stdout.strip() == "warden 0.1.0", (
        f"Unexpected warden --version output: {result.stdout.strip()!r}"
    )


def test_warden_cli_help_entrypoint() -> None:
    """Verify uv run warden --help displays usage and exits 0."""
    result = subprocess.run(
        ["uv", "run", "warden", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, f"warden --help failed: {result.stderr}"
    assert "warden" in result.stdout.lower()
