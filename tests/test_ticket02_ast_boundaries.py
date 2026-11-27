"""Tests for AST import boundaries, pruned surfaces, centralized gates, and dynamic engineer version."""

from __future__ import annotations

import ast
import hashlib
import hmac
import io
import os
import sqlite3
import subprocess
import sys
import tokenize
from pathlib import Path

import ml4t.engineer
import polars as pl
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _make_ast_mini_repo(tmp_path: Path, rel_path: str | None = None, content: str = "") -> Path:
    from warden_ops.integrity import generate_manifest

    repo = tmp_path / "mini_repo"
    (repo / "contracts" / "datasets").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "pkg").mkdir(parents=True, exist_ok=True)
    (repo / "scripts").mkdir(parents=True, exist_ok=True)
    (repo / "strategies" / "candidates").mkdir(parents=True, exist_ok=True)
    (repo / "strategies" / "books").mkdir(parents=True, exist_ok=True)
    (repo / "experiments" / "active").mkdir(parents=True, exist_ok=True)

    (repo / "AGENTS.md").write_text("# test agents\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname = 'mini'\n", encoding="utf-8")
    (repo / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (repo / "contracts" / "tiny.yaml").write_text("contract_id: TINY_V1\n", encoding="utf-8")
    (repo / "src" / "pkg" / "__init__.py").write_text("", encoding="utf-8")

    if rel_path is not None:
        target = repo / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    generate_manifest(repo)
    return repo


def _run_cli_check_worktree(repo: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
    return subprocess.run(
        [sys.executable, "-m", "warden.cli", "check-worktree"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_check_ast_boundaries_passes_on_real_repo() -> None:
    from warden_ops.integrity import check_ast_boundaries

    assert check_ast_boundaries(REPO_ROOT) == []


def test_clean_strategy_with_non_engine_ml4t_backtest_passes(tmp_path: Path) -> None:
    from warden_ops.integrity import check_ast_boundaries, check_worktree

    clean_src = (
        "from ml4t.backtest import RuleChain, StopLoss, Strategy, TrailingStop\n"
        "from ml4t.backtest.risk import StopLoss as RiskStopLoss\n"
        "\n"
        "def generate_positions(panel_df, contract, **kwargs):\n"
        "    return {'positions': []}\n"
    )
    repo = _make_ast_mini_repo(tmp_path, "strategies/candidates/clean_bt.py", clean_src)
    assert check_ast_boundaries(repo) == []
    assert check_worktree(repo) is True
    proc = _run_cli_check_worktree(repo)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize(
    ("rel_path", "source"),
    [
        ("src/pkg/live_mod.py", "import ml4t.live\n"),
        ("scripts/live_runner.py", "import ml4t.live as ml\n"),
        ("strategies/candidates/live_strat.py", "from ml4t.live import Broker\n"),
        ("experiments/active/live_exp.py", "from ml4t import live\n"),
        ("strategies/books/local_live.py", "def f():\n    from ml4t.live import x\n    return x\n"),
        ("src/pkg/underscore_live.py", "import ml4t_live\n"),
        ("scripts/underscore_from.py", "from ml4t_live.broker import Broker as B\n"),
        ("strategies/candidates/aliased_from.py", "from ml4t import live as lv\n"),
    ],
)
def test_ml4t_live_banned_repo_wide(tmp_path: Path, rel_path: str, source: str) -> None:
    from warden_ops.integrity import check_ast_boundaries, check_worktree

    repo = _make_ast_mini_repo(tmp_path, rel_path, source)
    violations = check_ast_boundaries(repo)
    assert violations, f"Expected AST violation for {rel_path}"
    assert any(rel_path in v and ("ml4t.live" in v or "ml4t_live" in v) for v in violations)
    assert check_worktree(repo) is False
    proc = _run_cli_check_worktree(repo)
    assert proc.returncode == 2


@pytest.mark.parametrize(
    ("rel_path", "source"),
    [
        ("top_level_probe.py", "import ml4t.live\n"),
        ("perf_audit/probe.py", "from ml4t.live import Broker\n"),
        ("extra_pkg/foo.py", "from ml4t import live\n"),
        ("extra_pkg/bar.py", "import ml4t_live\n"),
    ],
)
def test_ml4t_live_banned_outside_primary_dirs(tmp_path: Path, rel_path: str, source: str) -> None:
    from warden_ops.integrity import check_ast_boundaries, check_worktree

    repo = _make_ast_mini_repo(tmp_path, rel_path, source)
    violations = check_ast_boundaries(repo)
    assert violations, f"Expected AST violation for {rel_path}"
    assert any(rel_path in v and ("ml4t.live" in v or "ml4t_live" in v) for v in violations)
    assert check_worktree(repo) is False
    assert _run_cli_check_worktree(repo).returncode == 2


def test_ast_scan_skips_ignored_dirs_and_scopes_strategy_bans(tmp_path: Path) -> None:
    from warden_ops.integrity import check_ast_boundaries, check_worktree

    repo = _make_ast_mini_repo(tmp_path, "extra_pkg/ok_db.py", "import sqlite3\n")
    for skipped in (
        ".venv/lib/site.py",
        "venv/lib/site.py",
        "build/lib/mod.py",
        "dist/mod.py",
        "node_modules/pkg/mod.py",
        "pkg.egg-info/top_level.py",
        ".pytest_cache/mod.py",
    ):
        target = repo / skipped
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("import ml4t.live\n", encoding="utf-8")

    assert check_ast_boundaries(repo) == []
    assert check_worktree(repo) is True


@pytest.mark.parametrize(
    ("rel_path", "source", "check_cli"),
    [
        ("strategies/candidates/bad_sqlite_top.py", "import sqlite3\n", True),
        ("strategies/candidates/bad_sqlite_local.py", "def f():\n    import sqlite3 as db\n    return db\n", False),
        ("strategies/books/bad_aiosqlite.py", "from aiosqlite import connect\n", False),
        ("strategies/candidates/bad_subprocess_top.py", "import subprocess\n", True),
        ("strategies/books/bad_subprocess_from.py", "def f():\n    from subprocess import run\n    return run\n", False),
        ("strategies/candidates/bad_httpx.py", "import httpx\n", False),
        ("strategies/candidates/bad_requests.py", "def f():\n    import requests as req\n    return req\n", True),
        ("strategies/candidates/bad_urllib_req.py", "import urllib.request\n", False),
        ("strategies/books/bad_from_urllib.py", "from urllib import request\n", False),
        ("strategies/candidates/bad_urllib3.py", "import urllib3\n", False),
        ("strategies/candidates/bad_socket.py", "import socket\n", False),
        ("strategies/candidates/bad_aiohttp.py", "import aiohttp\n", False),
        ("strategies/candidates/bad_http_client.py", "import http.client\n", False),
        ("strategies/books/bad_from_http_client.py", "from http import client as hc\n", False),
        ("strategies/candidates/bad_engine_from.py", "from ml4t.backtest import Engine\n", True),
        ("strategies/candidates/bad_engine_alias.py", "def g():\n    from ml4t.backtest import Engine as E\n    return E\n", False),
        ("strategies/books/bad_engine_sub.py", "from ml4t.backtest.engine import Engine\n", False),
        ("strategies/candidates/bad_engine_mod.py", "import ml4t.backtest.engine\n", False),
    ],
)
def test_strategies_banned_imports_fail_closed(
    tmp_path: Path, rel_path: str, source: str, check_cli: bool
) -> None:
    from warden_ops.integrity import check_ast_boundaries, check_worktree

    repo = _make_ast_mini_repo(tmp_path, rel_path, source)
    violations = check_ast_boundaries(repo)
    assert violations, f"Expected AST violation for {rel_path}"
    assert any(rel_path in v for v in violations)
    assert check_worktree(repo) is False
    if check_cli:
        assert _run_cli_check_worktree(repo).returncode == 2


def test_syntax_error_in_scanned_dir_fails_closed(tmp_path: Path) -> None:
    from warden_ops.integrity import check_ast_boundaries, check_worktree

    repo = _make_ast_mini_repo(tmp_path, "strategies/candidates/broken.py", "def broken(:\n    pass\n")
    violations = check_ast_boundaries(repo)
    assert violations
    assert any("strategies/candidates/broken.py" in v and "SyntaxError" in v for v in violations)
    assert check_worktree(repo) is False


def test_deleted_dead_surfaces_and_centralized_gate_constants() -> None:
    from warden import cli, gates, leakage, qualify, runner

    for deleted in (
        REPO_ROOT / "src" / "warden" / "causality.py",
        REPO_ROOT / "scripts" / "mcp_warden" / "server.py",
        REPO_ROOT / "scripts" / "check_loc_budget.py",
    ):
        assert not deleted.exists(), f"{deleted} must be deleted"

    assert not hasattr(leakage, "call_ml4t_compute_features")
    assert not hasattr(leakage, "executable_label_span")
    assert not hasattr(runner, "load_module_from_path")
    for const in ("STATE_FILE", "HISTORY_FILE", "TRACE_OPS", "REGISTRY_TABLES", "LEGACY_TRACE"):
        assert not hasattr(cli, const), f"Unused constant {const} remains on warden.cli"

    assert not hasattr(gates, "STAGE0_VOL_PROXY")
    assert not hasattr(gates, "stage0_holding_volatility")
    required_gates = (
        "MIN_SR",
        "SR2_FLOOR",
        "SRD_FLOOR",
        "DSR_FLOOR",
        "IC_T_FLOOR",
        "AUDIT_DSR_FLOOR",
        "TRIMMED_SHARPE_FLOOR",
        "NEIGHBOR_STABILITY_RATIO",
        "NEIGHBOR_MAX_SR_FLOOR",
        "HOLDOUT_SR_FLOOR",
        "HOLDOUT_RATIO_FLOOR",
        "REVISE_SR_FLOOR",
    )
    for name in required_gates:
        assert hasattr(gates, name), f"Missing gate constant {name} in warden.gates"

    # Verify zero remaining references to deleted symbols/surfaces across src/, scripts/, tests/, AGENTS.md
    banned_tokens = (
        "check_loc_budget",
        "WARDEN_MAX_LOC",
        "EXPERIMENT_MAX_LOC",
        "warden.causality",
        "causality.py",
        "mcp_warden.server",
        "mcp_warden/server",
        "call_ml4t_compute_features",
        "executable_label_span",
        "STAGE0_VOL_PROXY",
        "stage0_holding_volatility",
        "warden.runner import load_module_from_path",
        "TRIM_MEAN_FLOOR",
    )
    self_path = Path(__file__).resolve()

    def _code_without_comments_or_docstrings(path: Path) -> str:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(path))
        doc_lines: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
                first = node.body[0]
                if (
                    isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)
                ):
                    doc_lines.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
        tokens = [
            tok.string
            for tok in tokenize.generate_tokens(io.StringIO(src).readline)
            if tok.type != tokenize.COMMENT and tok.start[0] not in doc_lines
        ]
        return " ".join(tokens)

    for root_dir in ("src", "scripts"):
        for p in sorted((REPO_ROOT / root_dir).rglob("*.py")):
            text = p.read_text(encoding="utf-8")
            for token in banned_tokens:
                assert token not in text, f"Stale reference {token!r} found in {p.relative_to(REPO_ROOT)}"
            if "load_module_from_path" in text and "warden.runner" in text:
                tree = ast.parse(text, filename=str(p))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.module == "warden.runner":
                        assert all(a.name != "load_module_from_path" for a in node.names), (
                            f"Stale import warden.runner.load_module_from_path in {p.relative_to(REPO_ROOT)}"
                        )

    agents_text = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for token in ("check_loc_budget", "WARDEN_MAX_LOC", "EXPERIMENT_MAX_LOC"):
        assert token not in agents_text, f"Stale reference {token!r} found in AGENTS.md"

    for p in sorted((REPO_ROOT / "tests").rglob("*.py")):
        if p.resolve() == self_path:
            continue
        code_text = _code_without_comments_or_docstrings(p)
        for token in banned_tokens:
            assert token not in code_text, f"Stale reference {token!r} found in {p.relative_to(REPO_ROOT)}"
        raw = p.read_text(encoding="utf-8")
        if "load_module_from_path" in raw and "warden.runner" in raw:
            tree = ast.parse(raw, filename=str(p))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "warden.runner":
                    assert all(a.name != "load_module_from_path" for a in node.names), (
                        f"Stale import warden.runner.load_module_from_path in {p.relative_to(REPO_ROOT)}"
                    )

    # Scientific verification must never invent a fallback feature pipeline.
    runner_tree = ast.parse(Path(runner.__file__).read_text(encoding="utf-8"))
    verify_fn = next(
        n for n in ast.walk(runner_tree) if isinstance(n, ast.FunctionDef) and n.name == "verify_spec"
    )
    verify_consts = {
        n.value for n in ast.walk(verify_fn) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
    assert "FeaturePipeline" not in verify_consts

    # Scientific owners use central gate constants, never local threshold copies.
    def _func_names_and_compare_floats(mod_path: Path, fn_name: str) -> tuple[set[str], set[float]]:
        tree = ast.parse(mod_path.read_text(encoding="utf-8"))
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == fn_name)
        names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
        cmp_floats: set[float] = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Compare):
                for comp in node.comparators:
                    if isinstance(comp, ast.Constant) and isinstance(comp.value, float):
                        cmp_floats.add(comp.value)
                    elif (
                        isinstance(comp, ast.UnaryOp)
                        and isinstance(comp.op, ast.USub)
                        and isinstance(comp.operand, ast.Constant)
                        and isinstance(comp.operand.value, float)
                    ):
                        cmp_floats.add(-comp.operand.value)
        return names, cmp_floats

    runner_names, runner_floats = _func_names_and_compare_floats(Path(runner.__file__), "run_spec")
    assert {"MIN_SR", "REVISE_SR_FLOOR", "SR2_FLOOR", "SRD_FLOOR", "DSR_FLOOR", "IC_T_FLOOR", "TRIMMED_SHARPE_FLOOR"} <= runner_names
    assert runner_floats.isdisjoint({0.5, 0.7, 0.8, 0.9, 1.0, 1.2, 2.0, -0.001, 0.0})

    audit_names, audit_floats = _func_names_and_compare_floats(Path(runner.__file__), "audit_run")
    assert {"TRIMMED_SHARPE_FLOOR", "SR2_FLOOR", "SRD_FLOOR", "AUDIT_DSR_FLOOR"} <= audit_names
    assert audit_floats.isdisjoint({0.5, 0.7, 0.8, 0.9, 1.0, 1.2, 2.0, -0.001, 0.0})

    qual_names, qual_floats = _func_names_and_compare_floats(Path(qualify.__file__), "qualify_candidate")
    assert {"HOLDOUT_SR_FLOOR", "HOLDOUT_RATIO_FLOOR"} <= qual_names
    assert qual_floats.isdisjoint({0.5, 0.8, 0.9, 1.0, 1.2, 2.0, -0.001})


def test_feature_cache_records_dynamic_ml4t_engineer_version(
    tiny_repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from warden_evidence.feature_cache import compute_and_cache_features, feature_identity

    class LagClose:
        def compute(self, frame: pl.DataFrame, contract) -> pl.DataFrame:
            return frame.sort("asset", "timestamp").with_columns(
                pl.col("close").shift(1).over("asset").alias("lag_close")
            )

    fixture = tiny_repo()
    cache_dir = tmp_path / "cache"
    pipe = LagClose()
    expected_ver = str(ml4t.engineer.__version__)
    _, config = feature_identity(pipe, fixture.panel, fixture.contract)
    assert config["engine"] == expected_ver

    compute_and_cache_features(pipe, fixture.panel, fixture.contract, cache_dir, fixture.registry)
    with sqlite3.connect(fixture.registry.db_path) as conn:
        row = conn.execute("SELECT ml4t_engineer_version FROM feature_sets").fetchone()
    assert row is not None and row[0] == expected_ver

    monkeypatch.setattr(ml4t.engineer, "__version__", "9.9.9-test")
    _, patched_cfg = feature_identity(pipe, fixture.panel, fixture.contract)
    assert patched_cfg["engine"] == "9.9.9-test"
    compute_and_cache_features(pipe, fixture.panel, fixture.contract, cache_dir, fixture.registry)
    with sqlite3.connect(fixture.registry.db_path) as conn:
        versions = {r[0] for r in conn.execute("SELECT ml4t_engineer_version FROM feature_sets")}
    assert "9.9.9-test" in versions


# --------------------------------------------------------------------------------------
# Protected-set coverage: the manifest must be exactly the enforced protected set.
# --------------------------------------------------------------------------------------


def _make_protected_mini_repo(tmp_path: Path) -> Path:
    """Mini repo whose protected set spans every enforced protected directory."""
    from warden_ops.integrity import generate_manifest

    repo = tmp_path / "protected_repo"
    for rel in (
        "contracts/datasets",
        "src/warden",
        "src/warden_ops",
        "src/warden_evidence",
        "scripts",
        "strategies/candidates",
        "experiments/active",
    ):
        (repo / rel).mkdir(parents=True, exist_ok=True)

    (repo / "AGENTS.md").write_text("# test agents\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname = 'mini'\n", encoding="utf-8")
    (repo / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (repo / "contracts" / "tiny.yaml").write_text("contract_id: TINY_V1\n", encoding="utf-8")
    (repo / "contracts" / "tiny.yml").write_text("contract_id: TINY_YML_V1\n", encoding="utf-8")
    (repo / "contracts" / "datasets" / "tiny.json").write_text('{"dataset": "TINY"}\n', encoding="utf-8")
    (repo / "src" / "warden" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "warden" / "evaluator.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "src" / "warden_ops" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "warden_ops" / "integrity.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "src" / "warden_evidence" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "warden_evidence" / "market_validation.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "scripts" / "preflight_trust_boundary.py").write_text("# preflight\n", encoding="utf-8")

    generate_manifest(repo)
    return repo


def _protected_rels(repo: Path) -> set[str]:
    from warden_ops.integrity import protected_manifest_entries

    return set(protected_manifest_entries(repo))


def test_protected_set_covers_every_evaluator_contract_and_dataset_entry(tmp_path: Path) -> None:
    from warden_ops.integrity import MANIFEST_FILE, check_worktree

    repo = _make_protected_mini_repo(tmp_path)
    rels = _protected_rels(repo)
    required = {
        "src/warden/evaluator.py",
        "src/warden_ops/integrity.py",
        "src/warden_evidence/market_validation.py",
        "contracts/tiny.yaml",
        "contracts/tiny.yml",
        "contracts/datasets/tiny.json",
        "AGENTS.md",
        "pyproject.toml",
        "uv.lock",
        "scripts/preflight_trust_boundary.py",
    }
    assert required <= rels, sorted(required - rels)

    manifest_rels = {
        line.strip().split(maxsplit=1)[1]
        for line in (repo / MANIFEST_FILE).read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    assert manifest_rels == rels

    assert check_worktree(repo) is True
    assert _run_cli_check_worktree(repo).returncode == 0


@pytest.mark.parametrize(
    "rel_path",
    [
        "contracts/late_contract.yaml",
        "contracts/late_contract.yml",
        "contracts/datasets/late_dataset.json",
        "src/warden/late_evaluator.py",
        "src/warden/nested/late_evaluator.py",
        "src/warden_ops/late_ops.py",
        "src/warden_evidence/late_evidence.py",
    ],
)
def test_unlisted_protected_file_fails_closed(tmp_path: Path, rel_path: str) -> None:
    from warden_ops.integrity import check_ast_boundaries, check_worktree

    repo = _make_protected_mini_repo(tmp_path)
    target = repo / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("VALUE = 1\n", encoding="utf-8")

    # The file is innocuous: the failure must come from set coverage, not syntax or imports.
    assert check_ast_boundaries(repo) == []
    assert check_worktree(repo) is False
    assert _run_cli_check_worktree(repo).returncode == 2


def test_manifest_omitting_a_protected_file_fails_closed(tmp_path: Path) -> None:
    from warden_ops.integrity import MANIFEST_FILE, check_worktree

    repo = _make_protected_mini_repo(tmp_path)
    manifest = repo / MANIFEST_FILE
    lines = manifest.read_text(encoding="utf-8").splitlines()
    kept = [line for line in lines if not line.endswith("  src/warden/evaluator.py")]
    assert len(kept) == len(lines) - 1, "fixture must contain the omitted entry"
    manifest.write_text("\n".join(kept) + "\n", encoding="utf-8")

    assert check_worktree(repo) is False
    assert _run_cli_check_worktree(repo).returncode == 2


def test_manifest_entry_without_a_file_fails_closed(tmp_path: Path) -> None:
    from warden_ops.integrity import MANIFEST_FILE, check_worktree

    repo = _make_protected_mini_repo(tmp_path)
    manifest = repo / MANIFEST_FILE
    ghost = "0" * 64 + "  src/warden/ghost.py\n"
    manifest.write_text(manifest.read_text(encoding="utf-8") + ghost, encoding="utf-8")

    assert check_worktree(repo) is False


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda text: text + text.splitlines()[0] + "\n", id="duplicate-entry"),
        pytest.param(lambda text: text + "deadbeef  src/warden/evaluator.py\n", id="short-digest"),
        pytest.param(lambda text: text + "not-a-digest-line\n", id="malformed-line"),
        pytest.param(lambda text: "\n\n", id="empty-manifest"),
    ],
)
def test_malformed_or_duplicate_manifest_lines_fail_closed(tmp_path: Path, mutate) -> None:
    from warden_ops.integrity import MANIFEST_FILE, check_worktree

    repo = _make_protected_mini_repo(tmp_path)
    manifest = repo / MANIFEST_FILE
    manifest.write_text(mutate(manifest.read_text(encoding="utf-8")), encoding="utf-8")

    assert check_worktree(repo) is False


def test_worktree_signature_is_verified_only_with_the_matching_human_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from warden_ops.integrity import MANIFEST_FILE, check_worktree

    repo = _make_protected_mini_repo(tmp_path)
    manifest = repo / MANIFEST_FILE
    human_key = "human-held-key"
    sig = repo / (MANIFEST_FILE + ".sig")
    sig.write_text(hmac.new(human_key.encode(), manifest.read_bytes(), hashlib.sha256).hexdigest() + "\n",
                   encoding="utf-8")

    monkeypatch.delenv("WARDEN_HUMAN_SIGNING_KEY", raising=False)
    assert check_worktree(repo) is True  # no key available: hash integrity only

    monkeypatch.setenv("WARDEN_HUMAN_SIGNING_KEY", human_key)
    assert check_worktree(repo) is True  # matching HMAC

    monkeypatch.setenv("WARDEN_HUMAN_SIGNING_KEY", "guessed-key")
    assert check_worktree(repo) is False  # a foreign key cannot certify the manifest

    monkeypatch.setenv("WARDEN_HUMAN_SIGNING_KEY", human_key)
    manifest.write_text(manifest.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert check_worktree(repo) is False  # HMAC covers the manifest bytes, not only the hashes

    monkeypatch.delenv("WARDEN_HUMAN_SIGNING_KEY", raising=False)
    assert check_worktree(repo) is True  # bytes still self-consistent, approval state reports the rest


def test_missing_signature_is_not_a_hash_integrity_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from warden_ops.integrity import MANIFEST_FILE, check_worktree

    repo = _make_protected_mini_repo(tmp_path)
    assert not (repo / (MANIFEST_FILE + ".sig")).exists()
    monkeypatch.setenv("WARDEN_HUMAN_SIGNING_KEY", "human-held-key")
    assert check_worktree(repo) is True


# --------------------------------------------------------------------------------------
# Strategy-module AST boundaries: duckdb and dynamic import/exec primitives.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rel_path", "source"),
    [
        ("strategies/candidates/bad_duckdb.py", "import duckdb\n"),
        ("strategies/books/bad_duckdb_from.py", "from duckdb import connect\n"),
        ("strategies/candidates/bad_duckdb_sub.py", "def f():\n    import duckdb.connect\n"),
        ("strategies/candidates/bad_importlib.py", "import importlib\n"),
        ("strategies/books/bad_importlib_util.py", "import importlib.util\n"),
        ("strategies/candidates/bad_from_importlib.py", "from importlib import import_module\n"),
        ("strategies/candidates/bad_dunder_import.py",
         "def f():\n    return __import__('datetime')\n"),
        ("strategies/books/bad_dunder_import_attr.py",
         "import builtins\n\ndef f():\n    return builtins.__import__('os')\n"),
        ("strategies/candidates/bad_exec.py", "def f(payload):\n    exec(payload)\n"),
        ("strategies/candidates/bad_eval.py", "def f(expr):\n    return eval(expr)\n"),
        ("strategies/books/bad_eval_builtins.py",
         "import builtins\n\ndef f(expr):\n    return builtins.eval(expr)\n"),
    ],
)
def test_strategy_dynamic_import_and_exec_primitives_fail_closed(
    tmp_path: Path, rel_path: str, source: str
) -> None:
    from warden_ops.integrity import check_ast_boundaries, check_worktree

    repo = _make_ast_mini_repo(tmp_path, rel_path, source)
    violations = check_ast_boundaries(repo)
    assert violations, f"Expected AST violation for {rel_path}"
    assert any(rel_path in v for v in violations)
    assert check_worktree(repo) is False
    assert _run_cli_check_worktree(repo).returncode == 2


def test_dynamic_primitive_bans_are_scoped_to_strategy_modules(tmp_path: Path) -> None:
    from warden_ops.integrity import check_ast_boundaries, check_worktree

    source = (
        "import importlib.util\n"
        "import duckdb\n"
        "\n"
        "def load():\n"
        "    return __import__('polars'), importlib.util, eval('1')\n"
    )
    repo = _make_ast_mini_repo(tmp_path, "scripts/dynamic_probe.py", source)
    assert check_ast_boundaries(repo) == []
    assert check_worktree(repo) is True


def test_valid_causal_strategy_imports_remain_green(tmp_path: Path) -> None:
    from warden_ops.integrity import check_ast_boundaries, check_worktree

    source = (
        "from __future__ import annotations\n"
        "\n"
        "import math\n"
        "import numpy as np\n"
        "import polars as pl\n"
        "from ml4t.backtest import RuleChain, StopLoss, Strategy\n"
        "from ml4t.engineer import compute_features\n"
        "\n"
        "def generate_positions(panel_df, contract, **kwargs):\n"
        "    frame = panel_df.with_columns(\n"
        "        pl.col('close').rolling_mean(20).alias('sma'),\n"
        "        pl.col('close').shift(1).alias('lag_close'),\n"
        "    )\n"
        "    weight = float(np.clip(math.tanh(0.1), -1.0, 1.0))\n"
        "    return {'positions': [{'asset': a, 'weight': weight} for a in frame['asset'].unique()]}\n"
    )
    repo = _make_ast_mini_repo(tmp_path, "strategies/candidates/valid_causal.py", source)
    assert check_ast_boundaries(repo) == []
    assert check_worktree(repo) is True
    assert _run_cli_check_worktree(repo).returncode == 0


def test_strategy_scan_carries_no_exemption_machinery() -> None:
    """#89 is satisfied only with zero per-file waivers, so the scanner must not ship any."""
    from warden_ops import integrity

    names = set(dir(integrity))
    assert {n for n in names if "EXEMPT" in n.upper() or "WAIVER" in n.upper()} == set()
    assert "_STRATEGY_ROOTS_ACTIVE" not in names
    # The shipped strategy root set is the full ban: nothing is subtracted at scan time.
    assert {"importlib", "duckdb"} <= integrity._BANNED_STRATEGY_ROOTS
    assert integrity._BANNED_STRATEGY_CALLS == frozenset({"__import__", "eval", "exec"})


def test_pre_migration_strategy_bytes_now_fail_closed(tmp_path: Path) -> None:
    """The two historical dynamic-primitive lines must violate even at their old paths."""
    from warden_ops.integrity import check_ast_boundaries, check_worktree

    historical = {
        # What s183_reference.py did before the #89 migration.
        "strategies/candidates/s183_reference.py": (
            "import importlib.util\n"
            "from pathlib import Path\n"
            "\n"
            "TB_PATH = Path('data/external/trends-research/Live/2_Engine/trading_book.py')\n"
            "\n"
            "\n"
            "def _tb():\n"
            "    spec = importlib.util.spec_from_file_location('trends_trading_book', TB_PATH)\n"
            "    module = importlib.util.module_from_spec(spec)\n"
            "    spec.loader.exec_module(module)\n"
            "    return module\n"
        ),
        # What social_stoic_xsmom_10d21d.py did before the #89 migration.
        "strategies/candidates/social_stoic_xsmom_10d21d.py": (
            "from __future__ import annotations\n"
            "\n"
            "\n"
            "def generate_positions(ts, hours):\n"
            "    return ts + __import__('datetime').timedelta(hours=hours)\n"
        ),
    }
    for rel, source in historical.items():
        repo = _make_ast_mini_repo(tmp_path / Path(rel).stem, rel, source)
        violations = check_ast_boundaries(repo)
        assert violations, f"{rel}: pre-migration bytes must not pass the strict scan"
        assert any(rel in v for v in violations)
        assert check_worktree(repo) is False
