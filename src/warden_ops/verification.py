"""Fixed release test inventory, environment preflight and isolated performance witness."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

WITNESSES = (
    "tests/test_ticket07_engine_cutover.py::"
    "test_falsifier_2_stage1_100_assets_20k_bars_sub5s_and_engine_free",
    "tests/test_warden_leakage.py::test_content_addressed_feature_cache_hit_speed",
)


def clean_test_environment(env):
    """Remove inherited selectors and external plugin autoload; pin worker resources."""
    cleared = sorted(key for key in env if key.startswith("PYTEST_") or key in {
        "WARDEN_HUMAN_SIGNING_KEY", "WARDEN_ADMISSION_HMAC_KEY",
        "QUANT_LAB_APPLIANCE_HOME", "QUANT_LAB_CALLER_ROOT", "WARDEN_TEST_FIXTURE_ROOT",
    })
    clean = {key: value for key, value in env.items() if key not in cleared}
    clean.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", POLARS_MAX_THREADS="2",
                 OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
    return clean, cleared


def environment_preflight(python, source, env):
    """Check the installed project without source PYTHONPATH and required fixture assets."""
    if not (source / "tests/conftest.py").is_file():
        raise ValueError("verification fixture setup is missing tests/conftest.py")
    # Git tracks historical return artifacts and empty cache placeholders. They
    # are fixture assets, not live state. Reject mounted directories and databases.
    if (any((source / name).is_symlink() for name in
            ("registry", "data", "cache", ".workspace/hypotheses"))
            or (source / "data").exists()
            or any((source / "registry" / name).exists() or
                   (source / "registry" / name).is_symlink()
                   for name in ("registry.db", "registry.db-wal", "registry.db-shm"))):
        raise ValueError("verification must start without canonical state bindings")
    code = (
        "import importlib,importlib.metadata as m,json,sys;"
        "from pathlib import Path;"
        "modules=['warden.cli','warden_ops.appliance','warden_evidence','pytest','xdist','ruff'];"
        "[importlib.import_module(name) for name in modules];"
        "names={d.metadata['Name'].lower() for d in m.distributions()};"
        "source=Path(sys.argv[1])/'src';"
        "packages=('warden','warden_ops','warden_evidence');"
        "roots={name:Path(importlib.import_module(name).__file__).parent for name in packages};"
        "mismatch=[str(p.relative_to(source)) for name in packages "
        "for p in (source/name).rglob('*.py') "
        "if not (roots[name]/p.relative_to(source/name)).is_file() "
        "or p.read_bytes()!=(roots[name]/p.relative_to(source/name)).read_bytes()];"
        "print(json.dumps({'versions':{name:m.version(name) for name in names},"
        "'mismatch':mismatch}))"
    )
    proc = subprocess.run([str(python), "-I", "-c", code, str(source)], cwd=source, env=env,
                          capture_output=True, text=True, check=False, timeout=30)
    if proc.returncode:
        raise ValueError("installed project/dependency import preflight failed: " + proc.stderr[-1000:])
    installed = json.loads(proc.stdout)
    if installed["mismatch"]:
        raise ValueError("installed project source differs: " + ", ".join(installed["mismatch"][:5]))
    versions = installed["versions"]
    lock = tomllib.loads((source / "uv.lock").read_text())
    locked = {}
    for package in lock["package"]:
        if "version" in package:
            locked.setdefault(package["name"].lower(), set()).add(package["version"])
    # uv represents the dynamic-version local project without a locked version.
    version = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']',
                        (source / "src/warden/__init__.py").read_text())
    if not version:
        raise ValueError("cannot identify the candidate's installed project version")
    locked["quant-lab"] = {version.group(1)}
    project = tomllib.loads((source / "pyproject.toml").read_text())
    from packaging.requirements import Requirement

    requirements = [*project["project"].get("dependencies", []),
                    *project.get("dependency-groups", {}).get("dev", [])]
    required = {"quant-lab", "pytest", "pytest-xdist", "ruff"}
    for requirement in map(Requirement, requirements):
        if requirement.marker is None or requirement.marker.evaluate():
            required.add(requirement.name.lower())
    mismatches = sorted(name for name in required
                        if versions.get(name) not in locked.get(name, set()))
    if mismatches:
        raise ValueError("installed required packages differ from uv.lock: " + ", ".join(mismatches))
    return {"required_packages": {name: versions[name] for name in sorted(required)},
            "installed_imports": True, "installed_source_bytes": True, "fixture_setup": True}


def pytest_addoption(parser):
    parser.addoption("--warden-selection", default=None)


def pytest_collection_finish(session):
    """Record actual selected node IDs from each worker (or serial collector)."""
    prefix = session.config.getoption("--warden-selection")
    if prefix:
        worker = os.environ.get("PYTEST_XDIST_WORKER", "controller")
        path = Path(f"{prefix}-{worker}.json")
        path.write_text(json.dumps([item.nodeid for item in session.items]))


def verification_command(python, source, attempt):
    return [str(python), "-m", "pytest", "-q", "-p", "xdist.plugin",
            "-p", "warden_ops.verification", "-o", "addopts=",
            "-o", f"cache_dir={attempt / 'pytest-cache'}",
            "--rootdir", str(source), "--confcutdir", str(source), "--tb=short"]


def collect_inventory(base, source, env, attempt):
    """Collect the complete fixed tests directory before executing expensive work."""
    proc = subprocess.run([*base, "--warden-selection", str(attempt / "collect-selection"),
                           "--collect-only", "tests"], cwd=source, env=env,
                          text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          check=False, timeout=120)
    (attempt / "collection.log").write_text(proc.stdout)
    selection = attempt / "collect-selection-controller.json"
    nodes = json.loads(selection.read_text()) if selection.is_file() else []
    total = re.search(r"(\d+) tests? collected", proc.stdout)
    if (proc.returncode or not total or len(nodes) != int(total.group(1))
            or len(set(nodes)) != len(nodes) or not set(WITNESSES) <= set(nodes)
            or any(not node.startswith("tests/") for node in nodes)):
        raise ValueError("full test collection is empty, narrowed, invalid, or missing required performance witnesses")
    return nodes


def fast_source_checks(python, source, env):
    """Fail before costly tests on syntax/undefined-name or protected inventory faults."""
    from warden_ops.integrity import check_worktree

    if not check_worktree(source):
        raise ValueError("protected inventory or AST boundary check failed")
    paths = [str(source / name) for name in ("src", "scripts", "tests")
             if (source / name).is_dir()]
    proc = subprocess.run([str(python), "-m", "ruff", "check",
                           "--select", "E9,F63,F7,F82", *paths], cwd=source, env=env,
                          capture_output=True, text=True, check=False, timeout=60)
    if proc.returncode:
        raise ValueError("fast lint failed: " + (proc.stdout + proc.stderr)[-2000:])
    return {"protected_inventory_and_ast": True, "fatal_lint": True}


def selection_matches(attempt, nodes):
    expected = set(nodes) - set(WITNESSES)
    selected = list(attempt.glob("suite-selection-gw*.json"))
    witness = attempt / "witness-selection-controller.json"
    return (len(selected) == 4
            and all(sorted(json.loads(path.read_text())) == sorted(expected)
                    for path in selected)
            and witness.is_file() and json.loads(witness.read_text()) == list(WITNESSES))


def run_suite(argv, source, env, log_path):
    with log_path.open("x") as log:
        return subprocess.run(argv, cwd=source, env=env, stdout=log,
                              stderr=subprocess.STDOUT, check=False, timeout=1800).returncode


def junit_report(path):
    counts = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "xfail": 0}
    reasons = []
    if path.is_file():
        report = ET.parse(path).getroot()
        for suite in report.iter("testsuite"):
            for name in ("tests", "failures", "errors", "skipped"):
                counts[name] += int(suite.get(name, "0"))
        for case in report.iter("testcase"):
            for skip in case.findall("skipped"):
                kind = skip.get("type", "pytest.skip")
                counts["xfail"] += int(kind == "pytest.xfail")
                reasons.append({"case": case.get("classname", "") + "::" + case.get("name", ""),
                                "type": kind, "reason": skip.get("message", "")})
    return counts, reasons


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fast", action="store_true", required=True)
    parser.parse_args(argv)
    source = Path.cwd()
    env, cleared = clean_test_environment(os.environ)
    env.pop("WARDEN_HUMAN_SIGNING_KEY", None)
    env.pop("WARDEN_NON_INTERACTIVE", None)
    env.pop("QUANT_LAB_APPLIANCE_HOME", None)
    env.update(PYTHONPATH=f"{source / 'src'}:{source}", PYTHONDONTWRITEBYTECODE="1",
               PYTHONNOUSERSITE="1")
    try:
        preflight = environment_preflight(sys.executable, source, env)
        checks = fast_source_checks(sys.executable, source, env)
        with tempfile.TemporaryDirectory(prefix="warden-fast-") as directory:
            attempt = Path(directory)
            nodes = collect_inventory(verification_command(sys.executable, source, attempt),
                                      source, env, attempt)
        print(json.dumps({"ok": True, "checks": checks, "preflight": preflight,
                          "collection_count": len(nodes), "cleared": cleared,
                          "full_release_gate": False}, sort_keys=True))
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "reason": str(exc), "full_release_gate": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
