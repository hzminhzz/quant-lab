"""Spec #109 review B7 — packaging is *built and installed*, not asserted.

#110's acceptance criterion claimed "the installed CLI reaches ``advance`` without importing
unpackaged repository scripts", but the test that proved it ran the **editable** console
script, where the ``.pth`` file supplies the import; nothing in the suite built a wheel, so
``pyproject.toml``'s ``packages`` entry (adding ``warden_ops``, ``warden_evidence``) was
unverified.  This test builds the wheel, installs it into an isolated ``--target``
directory, and imports the modules #109 added from there with ``PYTHONPATH`` cleared and a
working directory outside the repository.

The build is skipped — with the reason printed — on a host without ``uv`` or without the
build backend available offline, rather than failing every developer machine.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The modules this spec added, plus the package #110 moved out of ``scripts/``.
NEW_MODULES = (
    "warden.resolve",      # #116 data requirements
    "warden.compile",      # #117 sandbox compile
    "warden.promote",      # #118 promotion
    "warden_ops",          # #110 advance no longer imports scripts/research_outcome_memory.py
    "warden_evidence",     # the evaluator half that cli.py imports at run time
)


def _uv() -> str:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is not installed: `uv build --wheel` cannot run on this host")
    assert uv is not None
    return uv


def test_the_built_wheel_imports_the_new_modules_outside_the_repo(tmp_path: Path) -> None:
    uv = _uv()
    dist = tmp_path / "dist"
    build = subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(dist), "--offline"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if build.returncode != 0:
        pytest.skip(f"`uv build --wheel --offline` is unavailable here: {build.stderr.strip()[-500:]}")
    wheels = sorted(dist.glob("*.whl"))
    assert len(wheels) == 1, f"expected one wheel, found {wheels}"

    target = tmp_path / "site"
    install = subprocess.run(
        [uv, "pip", "install", "--no-deps", "--offline", "--target", str(target), str(wheels[0])],
        check=False,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert install.returncode == 0, install.stderr
    for module in NEW_MODULES:
        assert (target / module.split(".")[0]).is_dir(), f"{module} is not packaged at all"

    # A fresh interpreter, a working directory outside the repo, no PYTHONPATH: the only
    # ``warden`` it can import is the installed wheel.
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    code = (
        "import sys;"
        f"sys.path.insert(0, {str(target)!r});"
        "import warden, warden_ops, warden_evidence, warden.resolve, warden.compile, warden.promote;"
        "print(warden.__file__)"
    )
    probe = subprocess.run(
        [sys.executable, "-I", "-c", code],
        cwd=str(tmp_path),
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert probe.returncode == 0, probe.stderr
    # The import came from the installed copy, not from an editable install of the checkout.
    imported = Path(probe.stdout.strip())
    assert target in imported.parents or imported.parent == target
    assert "site-packages" not in str(imported) or str(target) in str(imported)
