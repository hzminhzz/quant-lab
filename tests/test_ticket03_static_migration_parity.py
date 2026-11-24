"""Issue #89: the two migrated strategies must be static, and the vendored book must be faithful.

The strict AST ban (no `importlib`, no `__import__`/`eval`/`exec` in `strategies/`) has no
per-file exemptions. Two committed strategies used to violate it, so they were migrated:

* `s183_reference.py` (HYP-2026-127) now imports `trends_trading_book.py`, a verbatim static
  subset of the upstream MIT book, and declares it in `CODE_DEPENDENCIES`.
* `social_stoic_xsmom_10d21d.py` (HYP-SOC-STOIC-XSMOM19-20261030) now imports `timedelta`.

These tests pin three things that a future edit could quietly break: the vendored algebra is
byte- and value-identical to upstream, the strategy's code closure actually covers it, and
neither strategy gained a way to touch the registry, spend trial budget or re-run anything.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from warden.codeid import _declared_dependencies, code_closure
from warden_ops.integrity import check_ast_boundaries

REPO_ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = REPO_ROOT / "strategies" / "candidates"
S183 = "strategies/candidates/s183_reference.py"
STOIC = "strategies/candidates/social_stoic_xsmom_10d21d.py"
VENDORED = "strategies/candidates/trends_trading_book.py"

# sha256 of the vendored subset as reviewed for #89. Editing those bodies is a sourcing change,
# not a tuning knob, so it must be re-reviewed rather than silently absorbed.
VENDORED_SHA256 = "edeee90da4b0a1d49d8c89851defb442170167d8e523e2cfa63e122ed4bcb70c"
# Historical identities that must stay recorded even though the bytes moved on.
S183_ORIGINAL_SHA256 = "ef0dd455567df35908ba918d959e44daeb8db097b49dd10930081db535a63f0d"
S183_ORIGINAL_CLOSURE = "e8997c9e96ce0c9bdca32cf81a430a55afefffbc1b4f1e51ef9bc144eac81582"
STOIC_ORIGINAL_SHA256 = "538d27cc4437a7c8d5c6be81830d0241c7c4e1d5bcaef666280ce032ce0dd6df"
UPSTREAM_COMMIT = "0426baca40b148b9fed760740a7b7ac2e4cb3a70"
UPSTREAM_SHA256 = "f7bfac2916b53eb436fb360fe2a183629cd15d2fe14647df4aeba6f5aff7f0a5"

# Values computed from the upstream `Live/2_Engine/trading_book.py` at the commit above on the
# deterministic input in `_book_inputs()`, then confirmed identical from the vendored module.
GOLDEN = {
    "ts_last": 0.975715133035796,
    "ts_mean": 1.2505293827935298,
    "norm_last": 3.654620878644984,
    "norm_mean": 10.686702101600776,
    "vov_mean": -0.024260728473112624,
    "vov_last": 0.4266490695727223,
    "gvol_last": 0.7466391053019192,
    "corr_last_01": -0.03472326004531465,
    "z_last_a": 0.2927640318664566,
}

_BOOTSTRAPPED = False


def _bootstrap() -> None:
    """The strategy loader puts a strategy's own directory on sys.path; do the same here."""
    global _BOOTSTRAPPED
    if not _BOOTSTRAPPED:
        entry = str(CANDIDATES)
        if entry not in sys.path:
            sys.path.insert(0, entry)
        _BOOTSTRAPPED = True


def _load(rel: str, name: str):
    _bootstrap()
    path = REPO_ROOT / rel
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _module_calls(tree: ast.AST) -> set[str]:
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                called.add(func.id)
            elif isinstance(func, ast.Attribute):
                called.add(func.attr)
    return called


def _top_level_imports(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def _book_inputs():
    rng = np.random.default_rng(4242)
    n = 600
    close = pl.Series(50.0 * np.exp(np.cumsum(rng.normal(0, 0.012, n))))
    ret = (close - close.shift(1)) / close.shift(1)
    means = np.column_stack([rng.normal(size=n) for _ in range(3)])
    means[:300] = np.nan
    panel = {k: pl.Series(rng.normal(size=400)) for k in ("A", "B", "C")}
    return close, ret, means, panel


def _synthetic_hourly_panel(assets: int, bars: int) -> pl.DataFrame:
    gen = np.random.default_rng(11)
    common = gen.normal(0, 0.01, bars)
    stamps = pl.datetime_range(
        datetime(2024, 1, 1),
        datetime(2024, 1, 1) + timedelta(hours=bars - 1),
        "1h",
        eager=True,
    )
    return pl.concat(
        [
            pl.DataFrame(
                {
                    "timestamp": stamps,
                    "symbol": f"S{i}",
                    "close": 100.0
                    * np.exp(np.cumsum(0.7 * common + 0.3 * gen.normal(0, 0.01, bars))),
                }
            )
            for i in range(assets)
        ]
    ).sort("timestamp", "symbol")


# ---------------------------------------------------------------------------- vendored book


def test_vendored_book_is_byte_frozen_and_mit_attributed() -> None:
    path = REPO_ROOT / VENDORED
    assert hashlib.sha256(path.read_bytes()).hexdigest() == VENDORED_SHA256, (
        "trends_trading_book.py changed; a vendored-source edit must be re-reviewed against "
        "upstream instead of drifting silently"
    )
    head = path.read_text(encoding="utf-8")
    assert UPSTREAM_COMMIT in head and UPSTREAM_SHA256 in head
    assert "MIT" in head and "Copyright (c) 2026 Lucas Joly" in head
    licence = (CANDIDATES / "trends_trading_book.LICENSE.txt").read_text(encoding="utf-8")
    assert "MIT License" in licence and "Copyright (c) 2026 Lucas Joly" in licence
    assert "Permission is hereby granted" in licence


def test_vendored_book_is_pure_static_code() -> None:
    tree = ast.parse((REPO_ROOT / VENDORED).read_text(encoding="utf-8"))
    top_imports = _top_level_imports(tree)
    assert top_imports <= {"__future__", "numpy", "polars", "scipy.signal"}, top_imports
    called = _module_calls(tree)
    assert called.isdisjoint({"__import__", "eval", "exec"})
    assert called.isdisjoint({"open", "read_text", "read_bytes", "spec_from_file_location"})
    assert check_ast_boundaries(REPO_ROOT) == []


def test_vendored_book_matches_upstream_values() -> None:
    book = _load(VENDORED, "vendored_trading_book_for_parity")
    close, ret, means, panel = _book_inputs()

    ts = book.ewmac(close)["TS_trend"]
    assert ts.drop_nulls()[-1] == pytest.approx(GOLDEN["ts_last"], rel=1e-12)
    assert ts.drop_nulls().mean() == pytest.approx(GOLDEN["ts_mean"], rel=1e-12)

    norm = book.normalise_signal(ts)
    assert norm.drop_nulls()[-1] == pytest.approx(GOLDEN["norm_last"], rel=1e-12)
    assert norm.drop_nulls().mean() == pytest.approx(GOLDEN["norm_mean"], rel=1e-12)

    inner = book.vov_inner(ret)
    smooth = book.vov_smooth(inner)
    vov = book.vov_signal(smooth, book.vov_mean_ann(smooth), close)
    assert vov.drop_nulls().mean() == pytest.approx(GOLDEN["vov_mean"], rel=1e-12)
    assert vov.drop_nulls()[-1] == pytest.approx(GOLDEN["vov_last"], rel=1e-12)

    gvol = book.s_g_vol(close, book.daily_vol_abs(close))
    assert gvol.drop_nulls()[-1] == pytest.approx(GOLDEN["gvol_last"], rel=1e-12)

    corr = book.ewm_corr_4(means, book.FDM_CORR_SPAN, book.FDM_MIN_PERIODS)
    assert corr[-1, 0, 1] == pytest.approx(GOLDEN["corr_last_01"], rel=1e-12)
    assert corr[-1, 0, 0] == pytest.approx(1.0)

    z, _, good, total = book.cross_sectional_z(panel, pl.Series(range(400)))
    assert (good, total) == (400, 400)
    assert z["A"].drop_nulls()[-1] == pytest.approx(GOLDEN["z_last_a"], rel=1e-12)
    assert (book.TRADING_DAYS_YEAR, book.FDM_CORR_SPAN, book.FDM_MIN_PERIODS) == (256, 512, 256)


# ------------------------------------------------------------------------- s183 migration


def test_s183_imports_vendored_book_statically_and_declares_closure() -> None:
    source = (REPO_ROOT / S183).read_text(encoding="utf-8")
    tree = ast.parse(source)
    top_imports = _top_level_imports(tree)
    assert "trends_trading_book" in top_imports
    assert "importlib" not in top_imports and "importlib.util" not in top_imports
    called = _module_calls(tree)
    assert called.isdisjoint({"__import__", "eval", "exec", "spec_from_file_location"})
    assert _declared_dependencies(tree) == [VENDORED]
    # Historical identity stays recorded in the source even though the bytes moved on.
    assert S183_ORIGINAL_SHA256 in source
    assert S183_ORIGINAL_CLOSURE in source


def test_s183_code_closure_covers_the_vendored_book() -> None:
    digest, files = code_closure(REPO_ROOT, S183)
    assert VENDORED in files
    assert S183 in files
    assert digest != S183_ORIGINAL_CLOSURE, (
        "the closure must change when the executed code changes; the historical digest is "
        "recorded in the module docstring and in docs/migrations/issue-89-static-strategy-imports.md"
    )


def test_s183_positions_are_deterministic_and_non_degenerate() -> None:
    module = _load(S183, "s183_reference_under_test")
    panel = _synthetic_hourly_panel(assets=4, bars=1600)
    first = module.generate_positions(panel, None)["positions"]
    second = module.generate_positions(panel, None)["positions"]
    assert first.shape == (1600, 4)
    assert np.array_equal(first, second, equal_nan=True)
    assert np.isfinite(first).all()
    assert float(np.abs(first).max()) > 0.1


# --------------------------------------------------------------------- social_stoic migration


def test_social_stoic_uses_a_static_timedelta() -> None:
    source = (REPO_ROOT / STOIC).read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert "datetime" in _top_level_imports(tree)
    assert _module_calls(tree).isdisjoint({"__import__", "eval", "exec"})
    assert STOIC_ORIGINAL_SHA256 in source
    module = _load(STOIC, "social_stoic_under_test")
    assert module.timedelta is timedelta


def test_social_stoic_rebalance_still_steps_twenty_one_days() -> None:
    module = _load(STOIC, "social_stoic_rebalance_under_test")
    stamps = pl.datetime_range(
        datetime(2024, 1, 1),
        datetime(2024, 1, 1) + timedelta(hours=599),
        "1h",
        eager=True,
    )
    rng = np.random.default_rng(5)
    symbols = module.SOURCE_ADAPTATION_SYMBOLS
    panel = pl.concat(
        [
            pl.DataFrame(
                {
                    "timestamp": stamps,
                    "symbol": symbol,
                    "close": 100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, len(stamps)))),
                }
            )
            for symbol in symbols
        ]
    ).sort("timestamp", "symbol")
    contract = SimpleNamespace(contract_id=module.CONTRACT_ID)
    result = module.generate_positions(panel, contract, target_symbols=list(symbols))
    weights = result["positions"]
    assert weights.shape == (600, 19)
    nonzero_per_row = (weights != 0.0).sum(axis=1)
    assert set(nonzero_per_row.tolist()) <= {0, 10}
    assert nonzero_per_row.max() == 10
    live = nonzero_per_row == 10
    assert live.any()
    assert np.allclose(np.abs(weights[live]).max(axis=1), 0.1)
    assert np.allclose(weights.sum(axis=1), 0.0)  # dollar neutral


# ------------------------------------------------------------- no automatic re-run / no budget


@pytest.mark.parametrize("rel", [S183, STOIC])
def test_migrated_strategies_cannot_touch_registry_or_budget(rel: str) -> None:
    tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
    imports = _top_level_imports(tree)
    forbidden = {"warden.registry", "warden.qualify", "warden.runner", "sqlite3", "subprocess"}
    assert imports.isdisjoint(forbidden), imports & forbidden
    # A strategy must stay a pure function of (panel, contract): nothing may re-run trials.
    called = _module_calls(tree)
    assert called.isdisjoint({"record_trial", "qualify_candidate", "register_hypothesis"})
