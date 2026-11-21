"""Ticket 01 tests: code_closure binding, pre-reservation target_symbols & Stage-0 guards, and env probe."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest
import yaml

from warden.cli import main as cli_main
from warden.codeid import code_closure
from warden.contracts import EvaluationContract, HypothesisSpec
from warden.diagnostics import compute_trade_metrics
from warden.gates import GateFalsificationError, empirical_holding_volatility
from warden.qualify import qualify_candidate
from warden.registry import ResearchRegistry
from warden.runner import ExperimentRunner
from warden_ops.integrity import generate_manifest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _backup_sqlite(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f"file:{src.resolve()}?mode=ro", uri=True) as s, sqlite3.connect(dst) as d:
        s.backup(d)


def _write_panel(path: Path, *, assets: tuple[str, ...], n_bars: int, freq_minutes: int,
                 drift: float, vol: float, seed: int = 7, stagger_minutes: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    start = datetime(2025, 1, 1, tzinfo=UTC)
    for idx, asset in enumerate(assets):
        price = 100.0 + 10.0 * idx
        offset = timedelta(minutes=idx * stagger_minutes)
        for t in range(n_bars):
            ts = start + timedelta(minutes=t * freq_minutes) + offset
            ret = drift + float(rng.normal(0.0, vol))
            nxt = price * (1.0 + ret)
            rows.append({
                "timestamp": ts,
                "asset": asset,
                "open": price,
                "high": max(price, nxt) * (1.0 + abs(vol) * 0.1),
                "low": min(price, nxt) * (1.0 - abs(vol) * 0.1),
                "close": nxt,
                "volume": 100.0,
                "funding_rate": 0.0,
            })
            price = nxt
    df = pl.DataFrame(rows).sort("timestamp", "asset").with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path)
    return df


def _hash_file(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _setup_isolated_repo(tmp_path: Path, *, name: str = "iso") -> Path:
    import shutil
    repo = tmp_path / name
    for rel in ("src", "scripts/check_loc_budget.py", "pyproject.toml", "uv.lock", "AGENTS.md"):
        src = REPO_ROOT / rel
        dst = repo / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst) if src.is_dir() else shutil.copy2(src, dst)
    live_db = REPO_ROOT / "registry" / "registry.db"
    if live_db.is_file():
        _backup_sqlite(live_db, repo / "registry" / "registry.db")
    else:
        ResearchRegistry(repo / "registry" / "registry.db")
    return repo


def _register_funnel_contract(repo: Path, *, cid: str = "T01_FUNNEL_V1", n_bars: int = 320) -> tuple[EvaluationContract, Path]:
    exp_path = repo / "data" / "exploratory" / f"{cid.lower()}.parquet"
    _write_panel(exp_path, assets=("AAA", "BBB"), n_bars=n_bars, freq_minutes=60, drift=0.003, vol=0.001, seed=11)
    sha = _hash_file(exp_path)
    start = datetime(2025, 1, 1, tzinfo=UTC)
    val_start = start + timedelta(hours=60)
    val_end = start + timedelta(hours=n_bars)
    holdout_start = val_end
    holdout_end = holdout_start + timedelta(hours=120)
    c = EvaluationContract(
        contract_id=cid, asset_class="crypto_perps", frequency="1h", bars_per_year=8760,
        exploratory_data_path=f"data/exploratory/{cid.lower()}.parquet",
        holdout_data_path=f"data/holdout/{cid.lower()}.parquet",
        train_start=start, val_start=val_start, val_end=val_end,
        holdout_start=holdout_start, holdout_end=holdout_end,
        n_folds=2, label_horizon_bars=2, embargo_bars=1, execution_delay_bars=1,
        taker_fee_bps=1.0, slippage_bps=1.0, funding_bps_per_8h=0.0,
        data_snapshot_sha256=sha, data_provenance="verified_market",
    )
    (repo / "contracts" / "datasets").mkdir(parents=True, exist_ok=True)
    (repo / "contracts" / f"{cid.lower()}.yaml").write_text(yaml.safe_dump(c.__dict__), encoding="utf-8")
    (repo / "contracts" / "datasets" / f"{cid}.json").write_text(json.dumps({
        "contract_id": cid, "path": c.exploratory_data_path,
        "sha256": sha, "provenance": "verified_market", "source": "synthetic_t01_fixture",
    }), encoding="utf-8")

    holdout_override = repo.parent / f"{cid.lower()}_holdout_override.parquet"
    rows = []
    rng = np.random.default_rng(19)
    for idx, asset in enumerate(("AAA", "BBB")):
        price = 100.0 + 10.0 * idx
        for t in range(120):
            ts = holdout_start + timedelta(hours=t)
            nxt = price * (1.0 + 0.003 + float(rng.normal(0.0, 0.001)))
            rows.append({
                "timestamp": ts, "asset": asset, "open": price,
                "high": max(price, nxt) * 1.001, "low": min(price, nxt) * 0.999,
                "close": nxt, "volume": 100.0, "funding_rate": 0.0,
            })
            price = nxt
    pl.DataFrame(rows).sort("timestamp", "asset").with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC"))
    ).write_parquet(holdout_override)
    return c, holdout_override


def _write_closure_strategy(repo: Path, main_name: str, sibling_name: str, runtime_name: str) -> str:
    strat_dir = repo / "strategies" / "candidates"
    strat_dir.mkdir(parents=True, exist_ok=True)
    (strat_dir / f"{sibling_name}.py").write_text("SIBLING_WEIGHT = 0.5\n", encoding="utf-8")
    (strat_dir / f"{runtime_name}.py").write_text("RUNTIME_SCALE = 1.0\n", encoding="utf-8")
    rel = f"strategies/candidates/{main_name}.py"
    (repo / rel).write_text(f"""from pathlib import Path
import numpy as np
import polars as pl
from {sibling_name} import SIBLING_WEIGHT
from warden.runner import load_module_from_path

CODE_DEPENDENCIES = ("strategies/candidates/{runtime_name}.py",)


class CausalClosurePipeline:
    def compute(self, bars_df, contract):
        return bars_df.sort("timestamp", "asset").with_columns(
            pl.col("close").pct_change().shift(1).over("asset").fill_null(0.0).alias("sig")
        )


pipeline = CausalClosurePipeline()


def generate_positions(panel_df, contract, **kwargs):
    dep = load_module_from_path(Path(CODE_DEPENDENCIES[0]))
    ts = sorted(panel_df["timestamp"].unique().to_list())
    assets = sorted(panel_df["asset"].unique().to_list())
    pos = np.full((len(ts), len(assets)), SIBLING_WEIGHT * dep.RUNTIME_SCALE, dtype=float)
    pos[0] = 0.0
    return {{"positions": pos}}
""", encoding="utf-8")
    return rel


def test_full_funnel_code_closure_binding_and_drift_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _setup_isolated_repo(tmp_path, name="funnel_repo")
    contract, holdout_override = _register_funnel_contract(repo)

    strat_a = _write_closure_strategy(repo, "t01_a", "sib_a", "rt_a")
    strat_b = _write_closure_strategy(repo, "t01_b", "sib_b", "rt_b")
    strat_c = _write_closure_strategy(repo, "t01_c", "sib_c", "rt_c")
    strat_d = _write_closure_strategy(repo, "t01_d", "sib_d", "rt_d")
    generate_manifest(repo)

    monkeypatch.chdir(repo)
    monkeypatch.delenv("WARDEN_NON_INTERACTIVE", raising=False)

    def _run_funnel(hyp_id: str, run_id: str, strat_rel: str) -> None:
        hyp_md = tmp_path / f"{hyp_id}.md"
        hyp_md.write_text(
            f"---\nhypothesis_id: {hyp_id}\ncontract_id: {contract.contract_id}\n"
            f"title: Ticket 01 binding {hyp_id}\nstrategy_family: rules_ts\n"
            "economic_mechanism: Positive drift captured via causal lagged positioning\n"
            "counterparty_or_friction: Taker fee and slippage paid on position entry/exit\n"
            "expected_holding_bars: 8\nfalsification_criteria: Reject if net Sharpe <= 1.0\n---\n",
            encoding="utf-8",
        )
        spec_yaml = tmp_path / f"{hyp_id}_spec.yaml"
        spec_yaml.write_text(yaml.safe_dump({
            "contract_id": contract.contract_id,
            "hypothesis_id": hyp_id,
            "run_id": run_id,
            "strategy_name": f"Strat_{hyp_id}",
            "strategy_family": "rules_ts",
            "model_type": "rules",
            "strategy_module": strat_rel,
            "parameters": {},
        }), encoding="utf-8")
        assert cli_main(["register-hypothesis", str(hyp_md), "--json"]) == 0
        assert cli_main(["verify", str(spec_yaml), "--json"]) == 0
        assert cli_main(["backtest", str(spec_yaml), "--json"]) == 0
        assert cli_main(["verify-causality", "--run-id", run_id, "--json"]) == 0
        assert cli_main(["audit-check", "--run-id", run_id, "--json"]) == 0

    _run_funnel("HYP-T01-FUNNEL-A", "RUN-T01-A", strat_a)
    _run_funnel("HYP-T01-FUNNEL-B", "RUN-T01-B", strat_b)
    _run_funnel("HYP-T01-FUNNEL-C", "RUN-T01-C", strat_c)

    expected_sha_a, closure_files_a = code_closure(repo, strat_a)
    assert "strategies/candidates/sib_a.py" in closure_files_a
    assert "strategies/candidates/rt_a.py" in closure_files_a
    with sqlite3.connect(repo / "registry" / "registry.db") as conn:
        recorded_sha_a = conn.execute(
            "SELECT strategy_code_sha256 FROM trials WHERE run_id='RUN-T01-A'"
        ).fetchone()[0]
    assert recorded_sha_a and recorded_sha_a == expected_sha_a

    runner = ExperimentRunner(repo_root=repo)
    q_a = qualify_candidate("RUN-T01-A", "YES_UNSEAL_HOLDOUT", runner, holdout_override_path=holdout_override)
    assert q_a["verdict"] == "PROMOTED" and q_a["promoted"] is True

    # End-to-end 5-step CLI qualification inside tmp_path (synthetic holdout under repo, never live data/holdout/)
    holdout_repo_path = repo / contract.holdout_data_path
    assert holdout_repo_path.resolve().is_relative_to(tmp_path.resolve())
    holdout_repo_path.parent.mkdir(parents=True, exist_ok=True)
    holdout_repo_path.write_bytes(holdout_override.read_bytes())
    ds_manifest_path = repo / "contracts" / "datasets" / f"{contract.contract_id}.json"
    ds_manifest = json.loads(ds_manifest_path.read_text(encoding="utf-8"))
    ds_manifest["holdout_path"] = contract.holdout_data_path
    ds_manifest["holdout_sha256"] = _hash_file(holdout_repo_path)
    ds_manifest_path.write_text(json.dumps(ds_manifest), encoding="utf-8")
    generate_manifest(repo)

    _run_funnel("HYP-T01-FUNNEL-D", "RUN-T01-D", f"./{strat_d}")
    assert runner.registry.causal_proof_for_run("RUN-T01-D", repo)["verified"] is True
    with sqlite3.connect(repo / "registry" / "registry.db") as conn:
        assert conn.execute(
            "SELECT verdict FROM audit_evidence WHERE run_id='RUN-T01-D' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()[0] == "APPROVED"
    capsys.readouterr()
    assert cli_main([
        "qualify", "--run-id", "RUN-T01-D",
        "--unseal-holdout", "--human-confirm-unseal", "YES_UNSEAL_HOLDOUT", "--json",
    ]) == 0
    q_d = json.loads(capsys.readouterr().out)
    assert q_d["verdict"] == "PROMOTED" and q_d["promoted"] is True and q_d["holdout_override"] is False
    with sqlite3.connect(repo / "registry" / "registry.db") as conn:
        assert conn.execute("SELECT state FROM holdout_claims WHERE run_id='RUN-T01-D'").fetchone()[0] == "PROMOTED"
        assert conn.execute("SELECT qual_id FROM qualifications WHERE run_id='RUN-T01-D'").fetchone()[0] == "QUAL-RUN-T01-D"

    # Mutate ONLY the CODE_DEPENDENCIES helper on Run B (main strategy file untouched)
    rt_b = repo / "strategies" / "candidates" / "rt_b.py"
    rt_b.write_text("RUNTIME_SCALE = 0.25\n", encoding="utf-8")
    assert cli_main(["verify-causality", "--run-id", "RUN-T01-B", "--json"]) == 1
    assert cli_main(["audit-check", "--run-id", "RUN-T01-B", "--json"]) == 1
    with pytest.raises(ValueError, match="changed since the trial"):
        qualify_candidate("RUN-T01-B", "YES_UNSEAL_HOLDOUT", runner, holdout_override_path=holdout_override)

    # Mutate ONLY the imported sibling helper on Run C (main strategy file untouched)
    sib_c = repo / "strategies" / "candidates" / "sib_c.py"
    sib_c.write_text("SIBLING_WEIGHT = 0.25\n", encoding="utf-8")
    assert cli_main(["verify-causality", "--run-id", "RUN-T01-C", "--json"]) == 1
    with pytest.raises(ValueError, match="changed since the trial"):
        qualify_candidate("RUN-T01-C", "YES_UNSEAL_HOLDOUT", runner, holdout_override_path=holdout_override)


def test_target_symbols_pre_reservation_guard_and_calendar_filtering(tmp_path: Path) -> None:
    # 1. Replay RUN-HYP068-TYPO-PROBE on isolated SQLite .backup() of live registry (if present)
    live_db = REPO_ROOT / "registry" / "registry.db"
    if live_db.is_file() and (REPO_ROOT / "data" / "exploratory" / "indices_1h.parquet").is_file():
        snap_db = tmp_path / "snap.db"
        _backup_sqlite(live_db, snap_db)
        with sqlite3.connect(snap_db) as conn:
            before_used = conn.execute(
                "SELECT trials_used FROM hypotheses WHERE hypothesis_id='HYP-2026-068'"
            ).fetchone()[0]
            before_count = conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0]
        typo_spec = tmp_path / "typo_snap.yaml"
        typo_spec.write_text(yaml.safe_dump({
            "contract_id": "INDICES_1H_V2",
            "hypothesis_id": "HYP-2026-068",
            "run_id": "RUN-HYP068-TYPO-REPLAY",
            "strategy_name": "TypoProbeReplay",
            "strategy_family": "rules_ts",
            "model_type": "rules",
            "strategy_module": "strategies/candidates/ts_breakout_20_1h.py",
            "parameters": {"target_symbols": ["NOT_REAL"]},
        }), encoding="utf-8")
        snap_runner = ExperimentRunner(registry=ResearchRegistry(snap_db), repo_root=REPO_ROOT)
        with pytest.raises(ValueError, match="unknown symbols"):
            snap_runner.run_spec(typo_spec)
        with sqlite3.connect(snap_db) as conn:
            assert conn.execute(
                "SELECT trials_used FROM hypotheses WHERE hypothesis_id='HYP-2026-068'"
            ).fetchone()[0] == before_used
            assert conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0] == before_count

    # 2. Fresh hypothesis typo probe + non-overlapping union-calendar multi-asset filtering
    repo = tmp_path / "multi_repo"
    (repo / "contracts" / "datasets").mkdir(parents=True, exist_ok=True)
    (repo / "strategies").mkdir(parents=True, exist_ok=True)
    panel_path = repo / "data" / "exploratory" / "multi_stagger.parquet"
    # AAA at :00, BBB at :30 -> completely disjoint timestamps (union-calendar dead bars)
    _write_panel(panel_path, assets=("AAA", "BBB"), n_bars=80, freq_minutes=60,
                 drift=0.003, vol=0.001, seed=21, stagger_minutes=30)
    sha = _hash_file(panel_path)
    start = datetime(2025, 1, 1, tzinfo=UTC)
    c = EvaluationContract(
        contract_id="MULTI_STAG_V1", asset_class="indices", frequency="1h", bars_per_year=5850,
        exploratory_data_path="data/exploratory/multi_stagger.parquet",
        holdout_data_path="data/holdout/multi_stagger.parquet",
        train_start=start, val_start=start + timedelta(hours=10), val_end=start + timedelta(hours=80),
        holdout_start=start + timedelta(hours=80), holdout_end=start + timedelta(hours=120),
        n_folds=2, label_horizon_bars=2, embargo_bars=1, execution_delay_bars=1,
        taker_fee_bps=0.5, slippage_bps=0.5, funding_bps_per_8h=0.0,
        data_snapshot_sha256=sha, data_provenance="verified_market",
    )
    (repo / "contracts" / "multi_stag_v1.yaml").write_text(yaml.safe_dump(c.__dict__), encoding="utf-8")
    (repo / "contracts" / "datasets" / "MULTI_STAG_V1.json").write_text(json.dumps({
        "contract_id": "MULTI_STAG_V1", "path": c.exploratory_data_path,
        "sha256": sha, "provenance": "verified_market", "source": "fixture",
    }), encoding="utf-8")
    (repo / "strategies" / "aaa_only.py").write_text("""import numpy as np
def generate_positions(panel_df, contract, **kwargs):
    ts = sorted(panel_df["timestamp"].unique().to_list())
    assets = sorted(panel_df["asset"].unique().to_list())
    assert assets == ["AAA"], f"expected filtered AAA only, got {assets}"
    assert len(ts) == 80, f"expected 80 bars without BBB dead timestamps, got {len(ts)}"
    pos = np.full((len(ts), len(assets)), 0.5, dtype=float)
    pos[0] = 0.0
    return {"positions": pos}
""", encoding="utf-8")

    reg = ResearchRegistry(repo / "registry" / "registry.db")
    reg.register_contract(c)
    reg.register_hypothesis(HypothesisSpec(
        "HYP-TYPO-FRESH", c.contract_id, "Typo guard", "rules_ts",
        "Testing pre-reservation target_symbols guard",
        "Friction explanation of twenty chars", 4, "Reject on unknown symbol",
    ))
    spec_bad = tmp_path / "bad_sym.yaml"
    spec_bad.write_text(yaml.safe_dump({
        "contract_id": c.contract_id, "hypothesis_id": "HYP-TYPO-FRESH",
        "strategy_family": "rules_ts", "model_type": "rules",
        "strategy_module": "strategies/aaa_only.py",
        "parameters": {"target_symbols": ["NOT_REAL"]},
    }), encoding="utf-8")
    runner = ExperimentRunner(registry=reg, repo_root=repo)
    with pytest.raises(ValueError, match="unknown symbols"):
        runner.run_spec(spec_bad)
    with reg._connect() as conn:
        assert conn.execute("SELECT trials_used FROM hypotheses WHERE hypothesis_id='HYP-TYPO-FRESH'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0] == 0

    # Valid target_symbols filtering drops BBB & its disjoint :30 timestamps before execution_returns
    from warden.runner import filter_target_symbols
    raw_df = pl.read_parquet(panel_path)
    filtered = filter_target_symbols(raw_df, {"target_symbols": ["AAA"]})
    assert filtered["asset"].unique().to_list() == ["AAA"]
    assert filtered["timestamp"].n_unique() == 80

    spec_good = tmp_path / "good_sym.yaml"
    spec_good.write_text(yaml.safe_dump({
        "contract_id": c.contract_id, "hypothesis_id": "HYP-TYPO-FRESH",
        "run_id": "RUN-STAG-OK", "strategy_family": "rules_ts", "model_type": "rules",
        "strategy_module": "strategies/aaa_only.py",
        "parameters": {"target_symbols": ["AAA"]},
    }), encoding="utf-8")
    res = runner.run_spec(spec_good)
    assert res.stage_reached == "STAGE_3_STRESS"
    with reg._connect() as conn:
        returns_len = len(json.loads(
            conn.execute("SELECT returns_json FROM trials WHERE run_id='RUN-STAG-OK'").fetchone()[0]
        ))
    assert returns_len == 70  # val_start is +10h -> 70 AAA bars, not 140 union bars

    # compute_trade_metrics restricts equal-weight benchmark to active columns when pos.shape == asset_returns.shape
    rng = np.random.default_rng(31)
    asset_rets = np.column_stack([rng.normal(0.001, 0.005, 100), rng.normal(-0.02, 0.05, 100)])
    pos_only_col0 = np.column_stack([np.ones(100), np.zeros(100)])
    tm = compute_trade_metrics(pos_only_col0, asset_rets[:, 0], asset_rets, bars_per_year=5850)
    assert tm["information_ratio"] == pytest.approx(0.0, abs=1e-9)

    # qualify_candidate also applies target_symbols filtering on holdout
    holdout_multi = tmp_path / "holdout_multi.parquet"
    _write_panel(holdout_multi, assets=("AAA", "BBB"), n_bars=40, freq_minutes=60,
                 drift=0.003, vol=0.001, seed=99, stagger_minutes=30)
    # Build a clean CANDIDATE trial with target_symbols=["AAA"] and qualify against holdout_multi
    reg.register_hypothesis(HypothesisSpec(
        "HYP-QUAL-FILTER", c.contract_id, "Qual filter", "rules_ts",
        "Testing holdout target_symbols filtering",
        "Friction explanation of twenty chars", 4, "Reject if holdout unfiltered",
    ))
    (repo / "strategies" / "aaa_holdout.py").write_text("""import numpy as np
def generate_positions(panel_df, contract, **kwargs):
    ts = sorted(panel_df["timestamp"].unique().to_list())
    assets = sorted(panel_df["asset"].unique().to_list())
    assert assets == ["AAA"], f"expected AAA only on holdout, got {assets}"
    pos = np.full((len(ts), len(assets)), 0.5, dtype=float)
    pos[0] = 0.0
    return {"positions": pos}
""", encoding="utf-8")
    spec_q = tmp_path / "spec_q.yaml"
    spec_q.write_text(yaml.safe_dump({
        "contract_id": c.contract_id, "hypothesis_id": "HYP-QUAL-FILTER",
        "run_id": "RUN-QUAL-FILTER", "strategy_family": "rules_ts", "model_type": "rules",
        "strategy_module": "strategies/aaa_holdout.py",
        "parameters": {"target_symbols": ["AAA"]},
    }), encoding="utf-8")
    res_q = runner.run_spec(spec_q)
    assert res_q.verdict == "CANDIDATE_FOR_QUALIFICATION"
    q_res = qualify_candidate("RUN-QUAL-FILTER", "YES_UNSEAL_HOLDOUT", runner, holdout_override_path=holdout_multi)
    assert q_res["promoted"] is True


def test_1m_low_volatility_fails_stage0_empirical_guard(tmp_path: Path) -> None:
    repo = tmp_path / "lowvol_repo"
    (repo / "contracts" / "datasets").mkdir(parents=True, exist_ok=True)
    (repo / "strategies").mkdir(parents=True, exist_ok=True)
    panel_path = repo / "data" / "exploratory" / "lowvol_1m.parquet"
    _write_panel(panel_path, assets=("BTCUSDT", "ETHUSDT"), n_bars=200, freq_minutes=1,
                 drift=0.0, vol=5e-5, seed=5)
    sha = _hash_file(panel_path)
    start = datetime(2025, 1, 1, tzinfo=UTC)
    c = EvaluationContract(
        contract_id="LOWVOL_1M_V1", asset_class="crypto_perps", frequency="1m", bars_per_year=525600,
        exploratory_data_path="data/exploratory/lowvol_1m.parquet",
        holdout_data_path="data/holdout/lowvol_1m.parquet",
        train_start=start, val_start=start + timedelta(minutes=40), val_end=start + timedelta(minutes=200),
        holdout_start=start + timedelta(minutes=200), holdout_end=start + timedelta(minutes=260),
        n_folds=2, label_horizon_bars=5, embargo_bars=1, execution_delay_bars=1,
        taker_fee_bps=4.0, slippage_bps=2.0, funding_bps_per_8h=0.0,
        min_expected_move_to_cost_ratio=2.5, data_snapshot_sha256=sha, data_provenance="verified_market",
    )
    (repo / "contracts" / "lowvol_1m_v1.yaml").write_text(yaml.safe_dump(c.__dict__), encoding="utf-8")
    (repo / "contracts" / "datasets" / "LOWVOL_1M_V1.json").write_text(json.dumps({
        "contract_id": c.contract_id, "path": c.exploratory_data_path,
        "sha256": sha, "provenance": "verified_market", "source": "fixture",
    }), encoding="utf-8")
    (repo / "strategies" / "scalper.py").write_text("def generate_positions(*a, **k): raise AssertionError('should not run')\n", encoding="utf-8")

    reg = ResearchRegistry(repo / "registry" / "registry.db")
    reg.register_contract(c)
    reg.register_hypothesis(HypothesisSpec(
        "HYP-LOWVOL-1M", c.contract_id, "Low-vol 1m scalper", "rules_ts",
        "Scalping tiny 1m mean-reversion oscillations",
        "Fees and slippage dominate tiny 1m variance", 5, "Reject when vol < hurdle",
    ))
    spec = tmp_path / "lowvol_spec.yaml"
    spec.write_text(yaml.safe_dump({
        "contract_id": c.contract_id, "hypothesis_id": "HYP-LOWVOL-1M",
        "strategy_family": "rules_ts", "model_type": "rules",
        "strategy_module": "strategies/scalper.py", "parameters": {},
    }), encoding="utf-8")
    runner = ExperimentRunner(registry=reg, repo_root=repo)

    with pytest.raises(GateFalsificationError):
        runner.run_stage1_screen(spec)
    with pytest.raises(GateFalsificationError):
        runner.run_spec(spec)
    with reg._connect() as conn:
        assert conn.execute("SELECT trials_used FROM hypotheses WHERE hypothesis_id='HYP-LOWVOL-1M'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0] == 0

    from time import perf_counter
    short_panel = pl.read_parquet(panel_path).head(8)  # 4 bars per asset < 5 + 2 -> 1-bar * sqrt(h) fallback
    t0 = perf_counter()
    vol_fb = empirical_holding_volatility(short_panel, 5)
    assert (perf_counter() - t0) < 0.1
    assert 0.0 < vol_fb < 0.0030


def test_empirical_holding_volatility_quote_only_and_price_fallbacks() -> None:
    rng = np.random.default_rng(13)
    start = datetime(2025, 1, 1, tzinfo=UTC)
    rows = []
    for asset in ("EURUSD", "USDJPY"):
        mid = 1.10 if asset == "EURUSD" else 150.0
        for t in range(40):
            mid *= 1.0 + float(rng.normal(0.0, 0.002))
            half_spread = mid * 0.0002
            rows.append({
                "timestamp": start + timedelta(minutes=15 * t),
                "symbol": asset,
                "bid": mid - half_spread,
                "ask": mid + half_spread,
            })
    quote_df = pl.DataFrame(rows).with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))
    vol_quote = empirical_holding_volatility(quote_df, 4)
    assert vol_quote > 0.0

    mid_df = quote_df.with_columns(((pl.col("bid") + pl.col("ask")) / 2.0).alias("mid")).drop("bid", "ask")
    close_df = mid_df.rename({"mid": "close"})
    open_df = mid_df.rename({"mid": "open"})
    assert empirical_holding_volatility(mid_df, 4) == pytest.approx(vol_quote, rel=1e-12)
    assert empirical_holding_volatility(close_df, 4) == pytest.approx(vol_quote, rel=1e-12)
    assert empirical_holding_volatility(open_df, 4) == pytest.approx(vol_quote, rel=1e-12)
    assert empirical_holding_volatility(quote_df.drop("bid", "ask"), 4) == 0.0


def test_xs_panel_diagnostics_and_inspect_env_capability_probe() -> None:
    xs = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "xs_panel_diagnostics.py"), "--help"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    assert xs.returncode == 0, xs.stderr

    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, scripts.inspect_env as ie; assert ie.main() == 0; assert not any(m.startswith('ml4t.live') for m in sys.modules)",
        ],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    assert probe.returncode == 0, probe.stderr

    manifest = json.loads((REPO_ROOT / ".workspace" / "env_manifest.json").read_text(encoding="utf-8"))
    assert manifest["import_errors"] == []
    assert manifest["probe_errors"] == []
    assert "ml4t-live" not in manifest["packages"]
    assert "ml4t-models" in manifest.get("optional_packages", {})

    sigs = manifest["api_signatures"]
    expected_probes = {
        "ml4t.specs:FeedSpec",
        "ml4t.specs:MarketDataSpec",
        "ml4t.data.validation:OHLCVValidator",
        "ml4t.data.validation.rules:ValidationRulePresets",
        "ml4t.data.anomaly:AnomalyManager",
        "ml4t.data.sessions:SessionAssigner",
        "ml4t.data.sessions:SessionCompleter",
        "ml4t.engineer:compute_features",
        "ml4t.engineer:MLDatasetBuilder",
        "ml4t.engineer.labeling:meta_labels",
        "ml4t.diagnostic.metrics:compute_ic_hac_stats",
        "ml4t.diagnostic.splitters:WalkForwardCV",
        "ml4t.diagnostic.evaluation.stats:deflated_sharpe_ratio",
        "ml4t.diagnostic.evaluation.stats:deflated_sharpe_ratio_from_statistics",
        "ml4t.diagnostic.evaluation.stats:effective_number_of_trials",
        "ml4t.diagnostic.evaluation.stats:compute_pbo",
        "ml4t.diagnostic.evaluation.stats:ras_sharpe_adjustment",
        "ml4t.diagnostic.evaluation:TradeAnalysis",
        "ml4t.diagnostic.evaluation:PortfolioAnalysis",
        "ml4t.backtest:Engine",
        "ml4t.backtest:DataFeed",
        "ml4t.backtest:BacktestConfig",
        "ml4t.backtest:ContractSpec",
        "ml4t.backtest.execution.rebalancer:TargetWeightExecutor",
        "ml4t.backtest.execution.rebalancer:RebalanceConfig",
        "ml4t.backtest.risk:StopLoss",
        "ml4t.backtest.risk:TrailingStop",
    }
    assert expected_probes <= set(sigs)
    feed_sig = sigs["ml4t.specs:FeedSpec"]
    assert feed_sig["supports_time_contract_version_2"] is True
    assert "time_contract_version" in feed_sig["fields"]
    assert set(feed_sig["v2_only_fields"]) == {
        "time_contract_version",
        "bar_grid_anchor",
        "bar_label",
        "partial_bar_policy",
    }
    assert "close_col" in feed_sig["v1_fields"]
    assert "timestamp_semantics" in feed_sig["v1_fields"]
    assert set(feed_sig["v1_fields"]).isdisjoint(set(feed_sig["v2_only_fields"]))
    assert set(feed_sig["v1_fields"]) | set(feed_sig["v2_only_fields"]) == set(feed_sig["fields"])
    assert sigs["ml4t.backtest:Engine"]["has_funding_df"] is True
    assert sigs["ml4t.backtest:DataFeed"]["has_feed_spec"] is True
    assert sigs["ml4t.backtest:DataFeed"]["has_session_col"] is True
