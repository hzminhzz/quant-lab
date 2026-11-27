"""Early engineering gate failures, without a canonical state mount."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from warden_ops.integrity import generate_manifest
from warden_ops.verification import (
    clean_test_environment,
    environment_preflight,
    fast_source_checks,
)


def test_inherited_filters_and_plugins_are_removed():
    env, removed = clean_test_environment({
        "PYTEST_ADDOPTS": "-k subset", "PYTEST_PLUGINS": "unreviewed",
        "PYTEST_XDIST_WORKER": "gw0", "PATH": "/usr/bin",
        "WARDEN_HUMAN_SIGNING_KEY": "fixture-only",
        "WARDEN_ADMISSION_HMAC_KEY": "fixture-only",
        "QUANT_LAB_APPLIANCE_HOME": "/fixture",
        "WARDEN_TEST_FIXTURE_ROOT": "/fixture",
    })
    assert removed == ["PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTEST_XDIST_WORKER",
                       "QUANT_LAB_APPLIANCE_HOME", "WARDEN_ADMISSION_HMAC_KEY",
                       "WARDEN_HUMAN_SIGNING_KEY", "WARDEN_TEST_FIXTURE_ROOT"]
    assert env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert env["PATH"] == "/usr/bin"
    assert not set(removed) & set(env)


def test_missing_fixture_setup_and_missing_installed_package_refuse(tmp_path):
    env, _ = clean_test_environment(os.environ)
    with pytest.raises(ValueError, match="fixture setup"):
        environment_preflight(sys.executable, tmp_path, env)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/conftest.py").touch()
    venv = tmp_path / "empty"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True)
    with pytest.raises(ValueError, match="installed project/dependency import"):
        environment_preflight(venv / "bin/python", tmp_path, env)


def test_linked_scratch_state_refused_before_imports(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/conftest.py").touch()
    linked = tmp_path / "elsewhere"
    linked.mkdir()
    (tmp_path / "registry").symlink_to(linked, target_is_directory=True)
    with pytest.raises(ValueError, match="canonical state bindings"):
        environment_preflight(sys.executable, tmp_path, os.environ)


def test_fast_gate_refuses_fatal_lint_and_inventory_drift(tmp_path):
    root = tmp_path / "src/warden"
    root.mkdir(parents=True)
    path = root / "fixture.py"
    path.write_text("value = absent_name\n")
    generate_manifest(tmp_path)
    with pytest.raises(ValueError, match="fast lint failed"):
        fast_source_checks(sys.executable, tmp_path, os.environ)
    path.write_text("value = 1\n")
    with pytest.raises(ValueError, match="protected inventory"):
        fast_source_checks(sys.executable, tmp_path, os.environ)
    generate_manifest(tmp_path)
    assert fast_source_checks(sys.executable, tmp_path, os.environ)["fatal_lint"]


def test_fast_gate_refuses_forbidden_evaluator_import(tmp_path):
    # Existing AST checker treats direct evaluator dependencies as protected.
    root = tmp_path / "src/warden"
    root.mkdir(parents=True)
    path = root / "fixture.py"
    path.write_text("import polars as pl\npl.read_parquet('fixture.parquet')\n")
    generate_manifest(tmp_path)
    with pytest.raises(ValueError, match="AST boundary"):
        fast_source_checks(sys.executable, tmp_path, os.environ)
