"""Perf-core fixes 2/3a/3b/3c/5: bit-identity, cache-first verify, lean child, position reuse.

Everything here is fixture-isolated: `tiny_repo` registries under tmp_path, never the live
registry, and no holdout access.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest
import yaml

import warden.cli as cli
import warden.leakage as leakage
import warden.runner as runner_mod
from warden.leakage import compute_open_forward_label
from warden.registry import CORR_DUPLICATE, _eigen_rank, distinct_stream_indices
from warden.runner import ExperimentRunner, load_module_from_path
from warden.strategy_loader import load_module_from_path as lean_load_module_from_path

SCALED_MODULE = """
import numpy as np


def generate_positions(panel_df, contract, **kwargs):
    ts = sorted(panel_df["timestamp"].unique().to_list())
    assets = sorted(panel_df["asset"].unique().to_list())
    pos = np.zeros((len(ts), len(assets)))
    pos[::4] = 0.1 * float(kwargs.get("scale", 1.0))
    return {"positions": pos, "ic_series": np.full(max(len(ts) - 8, 0), 0.02)}
"""

CAUSAL_PIPELINE_MODULE = """
import polars as pl


class Pipeline:
    def compute(self, bars_df, contract):
        return (bars_df.sort("timestamp", "asset")
                .with_columns(feat=(pl.col("high") - pl.col("low")) / pl.col("close"),
                              vol_rel=pl.col("volume") / pl.col("volume").rolling_mean(5).over("asset"))
                .select("timestamp", "asset", "feat", "vol_rel"))


pipeline = Pipeline()
"""


# ----------------------------------------------------------------- HEAD references (29ccb57)
def head_open_forward_label(open_mat: np.ndarray, delay: int, horizon: int) -> np.ndarray:
    n_bars, n_assets = open_mat.shape
    forward_label = np.zeros((n_bars, n_assets), dtype=float)
    max_t = n_bars - delay - horizon
    for t in range(max_t):
        entry_open = open_mat[t + delay]
        exit_open = open_mat[t + delay + horizon]
        fwd = np.divide(exit_open - entry_open, entry_open, out=np.zeros_like(entry_open), where=entry_open > 0)
        forward_label[t] = fwd - np.mean(fwd)
    return forward_label


def head_distinct_stream_indices(matrix: np.ndarray, tol: float = CORR_DUPLICATE) -> list[int]:
    keep: list[int] = []
    for j in range(matrix.shape[1]):
        col = matrix[:, j]
        if any(abs(float(np.corrcoef(col, matrix[:, k])[0, 1])) >= tol for k in keep):
            continue
        keep.append(j)
    return keep


@pytest.fixture
def child_calls(monkeypatch) -> dict:
    calls = {"n": 0}
    original = runner_mod._run_strategy_isolated

    def counted(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(runner_mod, "_run_strategy_isolated", counted)
    return calls


def _trial_row(registry, run_id: str):
    with registry._connect() as conn:
        return conn.execute(
            "SELECT returns_json, verdict, net_sharpe, deflated_sharpe_prob, effective_trials_keff, "
            "diagnostics_json FROM trials WHERE run_id=?", (run_id,)).fetchone()


def _spec_with(case: SimpleNamespace, name: str, **overrides) -> Path:
    cfg = yaml.safe_load(case.spec.read_text(encoding="utf-8"))
    cfg.update(overrides)
    path = Path(case.spec).parent / name
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return path


def _run_spec(case: SimpleNamespace, spec: Path, run_id: str) -> object:
    runner = ExperimentRunner(registry=case.registry, repo_root=case.repo)
    return runner.run_spec(spec, override_run_id=run_id)


# ----------------------------------------------------------------- fix 3b: open label
@pytest.mark.parametrize("shape,delay,horizon", [
    ((50, 1), 1, 1), ((50, 4), 0, 0), ((300, 7), 3, 21), ((5, 3), 6, 1),
    ((1, 2), 0, 0), ((257, 5), 0, 24), ((129, 9), 2, 3), ((140, 131), 1, 24), ((60, 0), 1, 4),
])
def test_open_forward_label_is_bit_identical_to_head_loop(shape, delay, horizon) -> None:
    rng = np.random.default_rng(20261119 + shape[1])
    opens = rng.normal(100.0, 5.0, size=shape)
    mask = rng.random(shape)
    opens = np.where(mask < 0.10, 0.0, opens)          # zero open: where=entry>0 must stay 0
    opens = np.where(mask > 0.93, -np.abs(opens), opens)  # negative open: excluded from the ratio
    if shape[0] > 3:
        opens[1] = opens[0]
    old, new = head_open_forward_label(opens, delay, horizon), compute_open_forward_label(opens, delay, horizon)
    assert np.array_equal(old, new)
    assert old.tobytes() == new.tobytes()


def test_open_forward_label_keeps_shape_when_no_full_label_fits() -> None:
    opens = np.abs(np.random.default_rng(3).normal(10.0, 1.0, size=(6, 3)))
    old, new = head_open_forward_label(opens, 4, 4), compute_open_forward_label(opens, 4, 4)
    assert new.shape == (6, 3)
    assert np.array_equal(old, new)
    assert float(np.abs(new).sum()) == 0.0


# ----------------------------------------------------------------- fix 5: distinct streams
def test_distinct_stream_indices_matches_pairwise_reference_on_near_duplicates() -> None:
    rng = np.random.default_rng(11)
    base = rng.normal(0.0, 1.0, size=600)
    dup = base + 0.3 * rng.normal(0.0, 1.0, size=600)
    matrix = np.column_stack([base, dup, -dup, base.copy(), rng.normal(0.0, 1.0, 600),
                              np.ones(600), np.zeros(600), base + 0.02 * rng.normal(0.0, 1.0, 600)])
    old, new = head_distinct_stream_indices(matrix), distinct_stream_indices(matrix)
    assert old == new
    assert _eigen_rank(matrix[:, old]) == _eigen_rank(matrix[:, new])


@pytest.mark.parametrize("eps", [0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0])
def test_distinct_stream_indices_agrees_at_the_09995_boundary(eps) -> None:
    rng = np.random.default_rng(int(eps * 1000))
    base = rng.normal(0.0, 1.0, size=500)
    matrix = np.column_stack([base, base + eps * rng.normal(0.0, 1.0, 500),
                              base + 0.5 * eps * rng.normal(0.0, 1.0, 500)])
    corr = float(np.corrcoef(matrix[:, 0], matrix[:, 1])[0, 1])
    assert abs(corr - CORR_DUPLICATE) > 1e-12  # not a last-bit coin flip
    assert head_distinct_stream_indices(matrix) == distinct_stream_indices(matrix)


def test_distinct_stream_indices_edge_shapes() -> None:
    assert distinct_stream_indices(np.zeros((50, 0))) == []
    assert distinct_stream_indices(np.zeros((50, 1))) == [0]
    degenerate = np.column_stack([np.ones(50), np.zeros(50), np.ones(50)])
    assert distinct_stream_indices(degenerate) == head_distinct_stream_indices(degenerate)


# ----------------------------------------------------------------- fix 3a: lean loader
def test_runner_reexports_lean_loader_and_child_import_is_lean(tmp_path) -> None:
    assert load_module_from_path is lean_load_module_from_path
    src = str(Path(__file__).resolve().parents[1] / "src")
    env = {"PYTHONPATH": src, "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    probe = ("import json, sys; import warden.strategy_loader; "
             "print(json.dumps(sorted(m for m in ('scipy', 'ml4t', 'warden.runner', 'warden.registry', "
             "'pandas', 'warden.contracts') if m in sys.modules)))")
    proc = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True, env=env)
    assert json.loads(proc.stdout) == []


def test_isolated_child_uses_the_lean_loader_for_identical_positions(tiny_repo) -> None:
    case = tiny_repo(module_source=SCALED_MODULE, module_name="scaled")
    contract = case.contract
    panel = pl.read_parquet(case.repo / contract.exploratory_data_path)
    direct = load_module_from_path(case.repo / "strategies" / "scaled.py").generate_positions(panel, contract, scale=1.5)
    child = runner_mod._run_strategy_isolated(case.repo / "strategies" / "scaled.py", panel, contract, {"scale": 1.5})
    assert np.array_equal(np.asarray(direct["positions"]), np.asarray(child["positions"]))


# ----------------------------------------------------------------- fix 2: cache-first verify
def test_cmd_verify_payload_keys_are_unchanged(tiny_repo, monkeypatch, capsys) -> None:
    case = tiny_repo(module_source=CAUSAL_PIPELINE_MODULE, module_name="pipeline")
    monkeypatch.setattr(cli, "check_worktree", lambda *a, **k: True)
    seen = {}

    def stub(pipeline, panel, contract, cache_dir=Path("cache/features"), registry=None):
        seen["called"] = True
        return panel.select("timestamp", "asset")

    monkeypatch.setattr(cli, "compute_and_cache_features", stub)
    args = SimpleNamespace(spec_yaml=str(case.spec), json=True)
    runner = ExperimentRunner(registry=case.registry, repo_root=case.repo)
    assert cli.cmd_verify(args, runner) == 0
    payload = json.loads(capsys.readouterr().out)
    assert seen["called"] is True
    assert set(payload) == {"status", "prefix_invariance", "cached", "rows", "data_provenance",
                            "market_eligible", "provenance_reason"}
    assert payload["status"] == "PASS" and payload["prefix_invariance"] is True
    assert payload["rows"] == case.panel.height
    assert not hasattr(cli, "verify_prefix_invariance")  # no standalone sampling pass remains


def test_verify_end_to_end_proves_on_miss_and_hits_the_cache(tiny_repo, monkeypatch, capsys) -> None:
    case = tiny_repo(module_source=CAUSAL_PIPELINE_MODULE, module_name="pipeline")
    monkeypatch.setattr(cli, "check_worktree", lambda *a, **k: True)
    cache = Path(case.repo).parent / "cache" / "features"
    monkeypatch.chdir(Path(case.repo).parent)
    args = SimpleNamespace(spec_yaml=str(case.spec), json=True)
    runner = ExperimentRunner(registry=case.registry, repo_root=case.repo)

    assert cli.cmd_verify(args, runner) == 0
    cold = json.loads(capsys.readouterr().out)
    cached_files = sorted(cache.glob("*.parquet"))
    assert len(cached_files) == 1
    stamp = cached_files[0].stat().st_mtime_ns

    assert cli.cmd_verify(args, runner) == 0
    warm = json.loads(capsys.readouterr().out)
    assert warm == cold
    assert cached_files[0].stat().st_mtime_ns == stamp  # hit path rewrote nothing

    with case.registry._connect() as conn:
        rows = [tuple(r) for r in conn.execute(
            "SELECT prefix_invariance_passed, contract_id FROM feature_sets").fetchall()]
    assert rows == [(1, case.contract.contract_id)]


def test_compute_and_cache_features_skips_the_proof_on_a_cache_hit(tiny_repo, monkeypatch) -> None:
    case = tiny_repo(module_source=CAUSAL_PIPELINE_MODULE, module_name="pipeline")
    panel = pl.read_parquet(case.repo / case.contract.exploratory_data_path)
    pipeline = load_module_from_path(case.repo / "strategies" / "pipeline.py").pipeline
    verified, computed = {"n": 0}, {"n": 0}
    original_verify = leakage.verify_prefix_invariance
    original_compute = pipeline.compute

    def counting_verify(*args, **kwargs):
        verified["n"] += 1
        return original_verify(*args, **kwargs)

    def counting_compute(bars_df, contract):
        computed["n"] += 1
        return original_compute(bars_df, contract)

    monkeypatch.setattr(leakage, "verify_prefix_invariance", counting_verify)
    monkeypatch.setattr(pipeline, "compute", counting_compute)
    cache = Path(case.repo).parent / "cache" / "features"
    cold = leakage.compute_and_cache_features(pipeline, panel, case.contract, cache_dir=cache, registry=case.registry)
    assert verified["n"] == 1 and computed["n"] == 10  # 9 sampling computes + 1 final frame
    computed["n"] = 0
    warm = leakage.compute_and_cache_features(pipeline, panel, case.contract, cache_dir=cache, registry=case.registry)
    assert verified["n"] == 1 and computed["n"] == 0
    assert warm.equals(cold)


# ----------------------------------------------------------------- fix 3c: position reuse
def test_screen_then_backtest_reuse_is_bit_identical_to_a_fresh_runner(tiny_repo, child_calls) -> None:
    reuse_case, fresh_case = tiny_repo(module_source=SCALED_MODULE, module_name="scaled"), \
        tiny_repo(module_source=SCALED_MODULE, module_name="scaled")
    reusing = ExperimentRunner(registry=reuse_case.registry, repo_root=reuse_case.repo)
    screening = ExperimentRunner(registry=fresh_case.registry, repo_root=fresh_case.repo)

    assert reusing.run_stage1_screen(reuse_case.spec).passed == screening.run_stage1_screen(fresh_case.spec).passed
    cache_key = next(iter(reusing._strategy_output_cache))
    cached_arrays = {k: v.copy() for k, v in reusing._strategy_output_cache[cache_key].items()
                     if isinstance(v, np.ndarray)}
    child_calls["n"] = 0
    reused = reusing.run_spec(reuse_case.spec, override_run_id="RUN-REUSE")
    assert child_calls["n"] == 0  # the child was skipped: screen already produced these positions
    for name, arr in cached_arrays.items():  # the reuse hands out copies; the cache stays intact
        assert np.array_equal(arr, reusing._strategy_output_cache[cache_key][name])

    fresh = ExperimentRunner(registry=fresh_case.registry, repo_root=fresh_case.repo)  # empty cache
    fresh_res = fresh.run_spec(fresh_case.spec, override_run_id="RUN-REUSE")
    assert child_calls["n"] == 1

    row_reuse = _trial_row(reuse_case.registry, "RUN-REUSE")
    row_fresh = _trial_row(fresh_case.registry, "RUN-REUSE")
    assert row_reuse == row_fresh
    assert reused.verdict == fresh_res.verdict and reused.net_sharpe == fresh_res.net_sharpe
    assert len(json.loads(row_reuse[0])) == len(json.loads(row_fresh[0])) > 0


def test_reuse_does_not_leak_across_parameters(tiny_repo, child_calls) -> None:
    case, twin = tiny_repo(module_source=SCALED_MODULE, module_name="scaled"), \
        tiny_repo(module_source=SCALED_MODULE, module_name="scaled")
    runner = ExperimentRunner(registry=case.registry, repo_root=case.repo)
    runner.run_stage1_screen(case.spec)                       # cache holds scale=1.0
    twin_screen = ExperimentRunner(registry=twin.registry, repo_root=twin.repo)
    twin_screen.run_stage1_screen(twin.spec)

    spec = _spec_with(case, "spec_scale2.yaml", parameters={"scale": 4.5})
    child_calls["n"] = 0
    scaled = runner.run_spec(spec, override_run_id="RUN-P-SCALED")
    assert child_calls["n"] == 1                              # params are part of the key: miss

    twin_spec = _spec_with(twin, "spec_scale2.yaml", parameters={"scale": 4.5})
    fresh = ExperimentRunner(registry=twin.registry, repo_root=twin.repo)  # empty cache
    fresh_res = fresh.run_spec(twin_spec, override_run_id="RUN-P-SCALED")
    assert child_calls["n"] == 2

    row_scaled = _trial_row(case.registry, "RUN-P-SCALED")
    assert row_scaled == _trial_row(twin.registry, "RUN-P-SCALED")
    assert scaled.verdict == fresh_res.verdict
    assert len(json.loads(row_scaled[0])) > 0                  # observed returns, not a placeholder
    assert len(runner._strategy_output_cache) == 1             # the screen's entry, not the spec's


def test_reuse_does_not_leak_across_data_bytes(tiny_repo, child_calls) -> None:
    case = tiny_repo(module_source=SCALED_MODULE, module_name="scaled")
    runner = ExperimentRunner(registry=case.registry, repo_root=case.repo)
    runner.run_stage1_screen(case.spec)
    panel = pl.read_parquet(case.repo / case.contract.exploratory_data_path)
    shifted = panel.with_columns((pl.col("open") * 1.05).alias("open"))
    shifted.write_parquet(case.repo / case.contract.exploratory_data_path)
    assert case.contract.data_snapshot_sha256 != hashlib.sha256(
        (case.repo / case.contract.exploratory_data_path).read_bytes()).hexdigest()

    child_calls["n"] = 0
    result = runner.run_spec(case.spec, override_run_id="RUN-D-SHIFTED")
    assert child_calls["n"] == 1  # data digest is part of the key
    assert len(runner._strategy_output_cache) == 1  # only the screen populates the cache
    assert result.verdict == "NON_PROMOTABLE"  # digest no longer matches the declared snapshot


def test_reuse_does_not_leak_across_contract_spec(tiny_repo, child_calls) -> None:
    case = tiny_repo(module_source=SCALED_MODULE, module_name="scaled")
    runner = ExperimentRunner(registry=case.registry, repo_root=case.repo)
    runner.run_stage1_screen(case.spec)
    contract_path = case.repo / "contracts" / "tiny_v1.yaml"
    fields = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    fields["execution_delay_bars"] = 3
    contract_path.write_text(yaml.safe_dump(fields), encoding="utf-8")

    child_calls["n"] = 0
    delayed = runner.run_spec(case.spec, override_run_id="RUN-C-DELAY3")
    assert child_calls["n"] == 1  # contract spec hash is part of the key
    assert len(runner._strategy_output_cache) == 1  # only the screen populates the cache
    assert delayed.stage_reached == "STAGE_3_STRESS"


def test_param_grid_still_spawns_one_child_per_variant(tiny_repo, child_calls) -> None:
    case = tiny_repo(module_source=SCALED_MODULE, module_name="scaled")
    runner = ExperimentRunner(registry=case.registry, repo_root=case.repo)
    runner.run_stage1_screen(case.spec)
    spec = _spec_with(case, "spec_grid.yaml", param_grid=[{"scale": 1.0}, {"scale": 2.0}])
    child_calls["n"] = 0
    runner.run_spec(spec, override_run_id="RUN-GRID")
    assert child_calls["n"] == 2
    with case.registry._connect() as conn:
        grid = conn.execute("SELECT parameters_json, returns_json FROM selection_attempts "
                            "WHERE kind='param_grid' ORDER BY parameters_json").fetchall()
    assert len(grid) == 2 and grid[0][1] != grid[1][1]
