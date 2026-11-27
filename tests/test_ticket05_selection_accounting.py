"""Ticket 05 (#76): DSR non-ratchet, simulation_epoch isolation, source_published_at & shadow calibration."""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pytest
import yaml
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from warden.cli import main as cli_main
from warden.contract_source import load_recorded_contract
from warden.contracts import (
    DEFAULT_SIMULATION_EPOCH,
    ENGINE_SIMULATION_EPOCH,
    ContractValidationError,
    ExperimentResult,
    HypothesisSpec,
    load_contract,
)
from warden.qualify import qualify_candidate
from warden.registry import ResearchRegistry, SelectionHistoryError, compute_selection_report
from warden.runner import ExperimentRunner
from warden_ops.integrity import generate_manifest


def _snapshot_sqlite(src_path: Path, dst_path: Path) -> Path:
    """Copy ``src_path`` to ``dst_path`` through SQLite's backup API (source read-only)."""
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"file:{src_path.resolve()}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(dst_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return dst_path


def _seed_result(run_id: str, hyp_id: str, contract, family: str) -> ExperimentResult:
    """One recorded trial row, enough for the shadow calibration to evaluate it."""
    return ExperimentResult(
        run_id=run_id,
        hypothesis_id=hyp_id,
        contract_id=contract.contract_id,
        strategy_name="Seeded",
        strategy_family=family,
        config_hash=f"cfg-{run_id}",
        stage_reached="STAGE_3_STRESS",
        verdict="REVISE",
        gross_sharpe=1.1,
        net_sharpe=0.9,
        deflated_sharpe_prob=0.9,
        expected_max_sharpe_noise=0.2,
        total_trials_n=3,
        effective_trials_keff=11.0,
        mean_ic=0.03 if family == "cross_sectional_ml" else None,
        ic_hac_tstat=2.4 if family == "cross_sectional_ml" else None,
        hit_rate=0.52,
        profit_factor=1.1,
        max_drawdown_pct=4.0,
        annualized_turnover=12.0,
        breakeven_cost_bps=10.0,
        stress_2x_cost_sharpe=0.4,
        param_neighbor_stability=None,
        notes="seeded shadow-calibration evidence",
        data_snapshot_sha256=contract.data_snapshot_sha256,
        strategy_module="strategies/candidates/ts_breakout_20_1h.py",
        model_type="rules" if family == "rules_ts" else "ml",
        data_provenance="verified_market",
    )


def _seed_ledger(root: Path) -> Path:
    """Build, inside ``tmp_path``, the ledger evidence the snapshot tests replay.

    ``root/registry/registry.db`` is synthetic, never the checkout's ledger.  These tests used
    to copy whatever ``registry/registry.db`` happened to exist: on a clean tree the file is
    absent (both tests skipped), and once a sibling test created an empty one they failed on
    history that was never there.  The seed states the evidence explicitly -- a stored search
    floor above the 1.0 default, and non-degenerate return/IC streams -- so every assertion
    below is exercised on any checkout, in any test order.
    """
    reg = ResearchRegistry(root / "registry" / "registry.db")
    indices = load_contract(REPO_ROOT / "contracts" / "indices_1d_v1.yaml")
    reg.register_contract(indices)
    # The epoch falsifier asserts an inherited floor, so one must actually be recorded.
    reg.update_search_floor(indices.contract_id, 11.0, 0.4213)

    crypto = load_recorded_contract(REPO_ROOT, "CRYPTO_PERPS_1H_V1")
    reg.register_contract(crypto)
    rng = np.random.default_rng(4242)
    for i in range(2):
        hyp_id = f"HYP-SEED-RULES-{i}"
        reg.register_hypothesis(
            HypothesisSpec(
                hyp_id,
                crypto.contract_id,
                f"Seeded DSR stream {i}",
                "rules_ts",
                "Mechanism explanation of twenty chars",
                "Friction explanation of twenty chars",
                8,
                "Reject net Sharpe below one",
            )
        )
        reg.record_trial(
            _seed_result(f"RUN-SEED-RULES-{i}", hyp_id, crypto, "rules_ts"),
            {},
            rng.normal(0.0004, 0.01, 120),
        )

    ml_hyp = "HYP-SEED-ML"
    reg.register_hypothesis(
        HypothesisSpec(
            ml_hyp,
            crypto.contract_id,
            "Seeded IC stream",
            "cross_sectional_ml",
            "Mechanism explanation of twenty chars",
            "Friction explanation of twenty chars",
            8,
            "Reject net Sharpe below one",
        )
    )
    reg.record_trial(
        _seed_result("RUN-SEED-ML", ml_hyp, crypto, "cross_sectional_ml"),
        {},
        rng.normal(0.0006, 0.012, 120),
        diagnostics={"stage1_signal": {"vector": [float(v) for v in rng.normal(0.03, 0.02, 30)]}},
    )
    return reg.db_path


def test_hypothesis_source_published_at_normalization_and_retrospective_cutoff() -> None:
    contract = load_recorded_contract(REPO_ROOT, "CRYPTO_PERPS_1H_V1")
    hyp_date = HypothesisSpec(
        "HYP-PUB-DATE",
        contract.contract_id,
        "Published post-val",
        "rules_ts",
        "Mechanism explanation of twenty chars",
        "Friction explanation of twenty chars",
        8,
        "Reject net Sharpe below one",
        source_published_at="2025-02-01",
    )
    assert hyp_date.source_published_at == "2025-02-01T00:00:00+00:00"
    assert hyp_date.source_published_utc() == datetime(2025, 2, 1, tzinfo=UTC)

    hyp_dt_obj = HypothesisSpec(
        "HYP-PUB-OBJ",
        contract.contract_id,
        "Published date object",
        "rules_ts",
        "Mechanism explanation of twenty chars",
        "Friction explanation of twenty chars",
        8,
        "Reject net Sharpe below one",
        source_published_at=date(2020, 1, 1),
    )
    assert hyp_dt_obj.source_published_at == "2020-01-01T00:00:00+00:00"
    assert hyp_dt_obj.is_retrospective_for(contract) is False

    hyp_at_val = HypothesisSpec(
        "HYP-PUB-AT",
        contract.contract_id,
        "Published at val_start",
        "rules_ts",
        "Mechanism explanation of twenty chars",
        "Friction explanation of twenty chars",
        8,
        "Reject net Sharpe below one",
        source_published_at=contract.val_start,
    )
    assert hyp_at_val.is_retrospective_for(contract) is True

    with pytest.raises(ContractValidationError, match="timezone"):
        HypothesisSpec(
            "HYP-PUB-NAIVE",
            contract.contract_id,
            "Naive datetime",
            "rules_ts",
            "Mechanism explanation of twenty chars",
            "Friction explanation of twenty chars",
            8,
            "Reject net Sharpe below one",
            source_published_at="2025-02-01T12:00:00",
        )

    res = ExperimentResult(
        run_id="RUN-E1",
        hypothesis_id="HYP-PUB-DATE",
        contract_id=contract.contract_id,
        strategy_name="s",
        strategy_family="rules_ts",
        config_hash="abc",
        stage_reached="STAGE_3_STRESS",
        verdict="REVISE",
        gross_sharpe=1.0,
        net_sharpe=0.8,
        deflated_sharpe_prob=0.5,
        expected_max_sharpe_noise=0.1,
        total_trials_n=1,
        effective_trials_keff=1.0,
        mean_ic=None,
        ic_hac_tstat=None,
        hit_rate=0.5,
        profit_factor=1.1,
        max_drawdown_pct=5.0,
        annualized_turnover=10.0,
        breakeven_cost_bps=5.0,
        stress_2x_cost_sharpe=0.2,
        param_neighbor_stability=0.9,
        notes="test",
    )
    assert res.simulation_epoch == DEFAULT_SIMULATION_EPOCH == "legacy_matrix"


def test_dsr_non_ratchet_does_not_readd_unvectorized_penalty() -> None:
    rng = np.random.default_rng(7)
    x = rng.normal(0.0005, 0.01, 300)
    # 20 prior unvectorized screens + 1 candidate stream => raw_attempt_count = 21
    r1 = compute_selection_report(x, [], 8760, raw_attempt_count=21, prior_max_keff=1.0)
    assert r1["unvectorized_attempts"] == 20
    assert r1["k_eff_dedup"] == pytest.approx(1.0)
    assert r1["k_eff_gate"] == pytest.approx(11.0)

    # Repeating with the prior floor must hold k_eff_gate flat at 11.0 (not 11 -> 21 -> 31).
    r2 = compute_selection_report(
        x, [], 8760, raw_attempt_count=21, prior_max_keff=r1["k_eff_gate"],
        prior_max_noise=r1["expected_max_sharpe_noise"],
    )
    r3 = compute_selection_report(
        x, [], 8760, raw_attempt_count=21, prior_max_keff=r2["k_eff_gate"],
        prior_max_noise=r2["expected_max_sharpe_noise"],
    )
    assert r2["k_eff_gate"] == pytest.approx(11.0)
    assert r3["k_eff_gate"] == pytest.approx(11.0)


def test_param_grid_and_rules_ts_screens_align_into_effective_rank(pre_rg05_policy, tiny_repo) -> None:
    fixture = tiny_repo()
    import yaml

    frozen = yaml.safe_load(fixture.spec.read_text())
    frozen["parameters"] = {"lookback_bars": 4}
    frozen["param_grid"] = [{"lookback_bars": 3}, {"lookback_bars": 6}]
    fixture.spec.write_text(yaml.safe_dump(frozen))
    runner = ExperimentRunner(registry=fixture.registry, repo_root=fixture.repo)
    runner.run_stage1_screen(fixture.spec)

    # Add a cross_sectional_ml standalone_screen (stores IC series of length != bar len): must not raise SelectionHistoryError
    fixture.registry.record_selection_attempt(
        hypothesis_id="HYP-TINY",
        contract_id=fixture.contract.contract_id,
        kind="standalone_screen",
        stage="STAGE_1_SIGNAL",
        outcome="REJECT",
        strategy_family="cross_sectional_ml",
        returns=np.linspace(-0.02, 0.03, 9),
        simulation_epoch=ENGINE_SIMULATION_EPOCH,
    )

    res = runner.run_spec(fixture.spec)
    diag = json.loads(res.diagnostics_json)
    sel = diag["selection"]
    # One frozen candidate (screen/full), one historical unbound ML screen.
    # Two fixed neighbors and three fixed stresses remain raw evidence, not independent search.
    assert res.total_trials_n == 2
    assert sel["raw_attempt_count"] == 2
    assert sel["raw_operation_count"] == 8
    assert sel["streams_used"] == 1
    assert sel["unvectorized_attempts"] == 1


def test_simulation_epoch_isolation_and_same_epoch_alignment(pre_rg05_policy, tiny_repo) -> None:
    fixture = tiny_repo(n_bars=24)  # fence-compatible: ends before the fixture holdout_start
    runner = ExperimentRunner(registry=fixture.registry, repo_root=fixture.repo)
    r_live = runner.run_spec(fixture.spec)
    assert r_live.simulation_epoch == ENGINE_SIMULATION_EPOCH

    with fixture.registry._connect() as conn:
        live_len = len(
            json.loads(conn.execute("SELECT returns_json FROM trials WHERE run_id=?", (r_live.run_id,)).fetchone()[0])
        )

    # Same epoch with mismatched bar count must still raise SelectionHistoryError.  The
    # runner records a clock now, so a clockless read is refused explicitly instead of
    # falling into the positional "refusing to pad" branch; either way it never compares.
    with pytest.raises(SelectionHistoryError, match="refusing to pad|no timestamps"):
        fixture.registry.selection_history(
            fixture.contract.contract_id, live_len + 5, simulation_epoch=ENGINE_SIMULATION_EPOCH
        )

    # A historical epoch is an explicit registry replay boundary: isolated
    # streams while inheriting prior_search_floor.  It is never selected from
    # agent YAML.
    hist_legacy = fixture.registry.selection_history(
        fixture.contract.contract_id, live_len + 5, simulation_epoch=DEFAULT_SIMULATION_EPOCH
    )
    assert hist_legacy["streams"] == []
    assert hist_legacy["raw_attempt_count"] == 0
    floor, noise, _ = fixture.registry.prior_search_floor(fixture.contract)
    assert floor >= r_live.effective_trials_keff
    assert noise >= r_live.expected_max_sharpe_noise

    with fixture.registry._connect() as conn:
        before = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("trials", "selection_attempts", "screen_receipts")
        }
    fixture.spec.write_text(fixture.spec.read_text() + f"simulation_epoch: {DEFAULT_SIMULATION_EPOCH}\n")
    with pytest.raises(ValidationError, match="simulation_epoch"):
        runner.run_spec(fixture.spec)
    with fixture.registry._connect() as conn:
        after = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("trials", "selection_attempts", "screen_receipts")
        }
    assert after == before


def test_snapshot_falsifiers_flat_keff_and_epoch_transition(tmp_path: Path) -> None:
    seed_db = _seed_ledger(tmp_path / "seed")

    # Falsifier 1: 3 consecutive trials with 20 prior screens on a seeded ledger snapshot
    snap1 = _snapshot_sqlite(seed_db, tmp_path / "snap_non_ratchet.db")
    reg1 = ResearchRegistry(snap1)
    contract = load_recorded_contract(REPO_ROOT, "CRYPTO_PERPS_1H_V1")
    cid = "FALSIFY_FLAT_V1"
    from dataclasses import replace as dc_replace

    c_flat = dc_replace(contract, contract_id=cid, asset_class="metals")
    reg1.register_contract(c_flat)
    reg1.register_hypothesis(
        HypothesisSpec(
            "HYP-FALSIFY-FLAT",
            cid,
            "Flat floor falsifier",
            "rules_ts",
            "Mechanism explanation of twenty chars",
            "Friction explanation of twenty chars",
            8,
            "Reject net Sharpe below one",
        )
    )
    for i in range(20):
        reg1.record_selection_attempt(
            hypothesis_id="HYP-FALSIFY-FLAT",
            contract_id=cid,
            kind="failed_reservation",
            stage="STAGE_0",
            outcome="ERROR",
            simulation_epoch="engine_flat_test",
        )

    rng = np.random.default_rng(123)
    x = rng.normal(0.0004, 0.01, 200)
    keffs = []
    for _ in range(3):
        prior_k, prior_n, _ = reg1.prior_search_floor(c_flat)
        hist = reg1.selection_history(cid, len(x), simulation_epoch="engine_flat_test")
        rep = compute_selection_report(
            x,
            hist["streams"],
            c_flat.bars_per_year,
            raw_attempt_count=hist["raw_attempt_count"] + 1,
            prior_max_keff=prior_k,
            prior_max_noise=prior_n,
        )
        reg1.update_search_floor(cid, rep["k_eff_gate"], rep["expected_max_sharpe_noise"])
        keffs.append(rep["k_eff_gate"])
    # Must stay flat at 11.0 (1.0 + 0.50 * 20), never ratcheting +10.0 per trial
    assert keffs == pytest.approx([11.0, 11.0, 11.0])

    # Falsifier 2: insert a new simulation_epoch trial with different bar count on a seeded snapshot
    snap2 = _snapshot_sqlite(seed_db, tmp_path / "snap_epoch.db")
    reg2 = ResearchRegistry(snap2)
    existing_contract = load_contract(REPO_ROOT / "contracts" / "indices_1d_v1.yaml")
    prior_floor, prior_noise, fam = reg2.prior_search_floor(existing_contract)
    assert fam == "indices:1d"
    assert prior_floor > 1.0

    reg2.register_hypothesis(
        HypothesisSpec(
            "HYP-FALSIFY-EPOCH",
            existing_contract.contract_id,
            "Epoch transition falsifier",
            "rules_ts",
            "Mechanism explanation of twenty chars",
            "Friction explanation of twenty chars",
            4,
            "Reject net Sharpe below one",
        )
    )
    epoch_ret = rng.normal(0.001, 0.01, 512)
    hist2 = reg2.selection_history(existing_contract.contract_id, len(epoch_ret), simulation_epoch="engine_v1")
    assert hist2["streams"] == []
    rep2 = compute_selection_report(
        epoch_ret,
        hist2["streams"],
        existing_contract.bars_per_year,
        raw_attempt_count=hist2["raw_attempt_count"] + 1,
        prior_max_keff=prior_floor,
        prior_max_noise=prior_noise,
    )
    res_epoch = ExperimentResult(
        run_id="RUN-FALSIFY-EPOCH-001",
        hypothesis_id="HYP-FALSIFY-EPOCH",
        contract_id=existing_contract.contract_id,
        strategy_name="EpochTest",
        strategy_family="rules_ts",
        config_hash="epoch01",
        stage_reached="STAGE_3_STRESS",
        verdict="REVISE",
        gross_sharpe=1.1,
        net_sharpe=0.9,
        deflated_sharpe_prob=rep2["probability"],
        expected_max_sharpe_noise=rep2["expected_max_sharpe_noise"],
        total_trials_n=rep2["raw_attempt_count"],
        effective_trials_keff=rep2["k_eff_gate"],
        mean_ic=None,
        ic_hac_tstat=None,
        hit_rate=0.52,
        profit_factor=1.1,
        max_drawdown_pct=4.0,
        annualized_turnover=12.0,
        breakeven_cost_bps=10.0,
        stress_2x_cost_sharpe=0.4,
        param_neighbor_stability=None,
        notes="epoch falsifier",
        simulation_epoch="engine_v1",
    )
    reg2.record_trial(res_epoch, {"epoch": "engine_v1"}, epoch_ret)
    # Querying engine_v1 epoch succeeds cleanly alongside legacy trials of different bar counts
    post = reg2.selection_history(existing_contract.contract_id, len(epoch_ret), simulation_epoch="engine_v1")
    assert len(post["streams"]) == 1
    inherited_k, inherited_n, _ = reg2.prior_search_floor(existing_contract)
    assert inherited_k >= prior_floor
    assert inherited_n >= prior_noise


def test_source_published_at_retrospective_tagging_and_qualification_block(
    pre_rg05_policy,
    tiny_repo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    strong_module = """
import numpy as np


def generate_positions(panel_df, contract, **kwargs):
    ts = sorted(panel_df["timestamp"].unique().to_list())
    assets = sorted(panel_df["asset"].unique().to_list())
    pos = np.full((len(ts), len(assets)), 0.4)
    return {"positions": pos}
"""
    fixture = tiny_repo(module_source=strong_module, module_name="strong", n_bars=24)  # fence-compatible
    # Register a retrospective hypothesis (source_published_at >= val_start)
    retro_hyp = HypothesisSpec(
        "HYP-RETRO",
        fixture.contract.contract_id,
        "Post-publication replication",
        "rules_ts",
        "Mechanism explanation of twenty chars",
        "Friction explanation of twenty chars",
        4,
        "Reject net Sharpe below one",
        source_published_at=fixture.contract.val_start.isoformat(),
    )
    fixture.registry.register_hypothesis(retro_hyp)
    with fixture.registry._connect() as conn:
        st = conn.execute("SELECT status FROM hypotheses WHERE hypothesis_id='HYP-RETRO'").fetchone()[0]
    assert st == "RETROSPECTIVE_REPLICATION_ONLY"

    spec_data = yaml.safe_load(fixture.spec.read_text())
    spec_data["hypothesis_id"] = "HYP-RETRO"
    retro_spec = fixture.repo / "retro_spec.yaml"
    retro_spec.write_text(yaml.safe_dump(spec_data))

    runner = ExperimentRunner(registry=fixture.registry, repo_root=fixture.repo)
    screen_rep = runner.run_stage1_screen(retro_spec)
    assert screen_rep.strategy_family == "rules_ts"

    res = runner.run_spec(retro_spec)
    assert res.verdict == "RETROSPECTIVE_REPLICATION_ONLY"
    assert res.verdict != "CANDIDATE_FOR_QUALIFICATION"
    elig = fixture.registry.effective_eligibility(res.run_id)
    assert elig["eligible"] is False
    assert "RETROSPECTIVE_REPLICATION_ONLY" in elig["reason"]

    with pytest.raises(ValueError, match="not market-eligible"):
        qualify_candidate(res.run_id, "YES_UNSEAL_HOLDOUT", runner)

    # CLI register-hypothesis --json stamps retrospective_replication_only=true
    cli_root = fixture.repo / "cli_check"
    (cli_root / "contracts" / "datasets").mkdir(parents=True)
    (cli_root / "src" / "warden").mkdir(parents=True)
    (cli_root / "contracts" / "tiny_v1.yaml").write_text((fixture.repo / "contracts" / "tiny_v1.yaml").read_text())
    (cli_root / "AGENTS.md").write_text("# agents\n")
    (cli_root / "pyproject.toml").write_text("[project]\nname='mini'\n")
    (cli_root / "uv.lock").write_text("version=1\n")
    generate_manifest(cli_root)
    hyp_md = cli_root / "hyp_retro.md"
    hyp_md.write_text(
        "---\n"
        "hypothesis_id: HYP-CLI-RETRO\n"
        f"contract_id: {fixture.contract.contract_id}\n"
        "title: CLI Retro\n"
        "strategy_family: rules_ts\n"
        "economic_mechanism: Mechanism explanation of twenty chars\n"
        "counterparty_or_friction: Friction explanation of twenty chars\n"
        "expected_holding_bars: 4\n"
        "falsification_criteria: Reject net Sharpe below one\n"
        f"source_published_at: {fixture.contract.val_start.isoformat()}\n"
        "---\n"
    )
    monkeypatch.chdir(cli_root)
    monkeypatch.delenv("WARDEN_NON_INTERACTIVE", raising=False)
    capsys.readouterr()
    rc = cli_main(["register-hypothesis", str(hyp_md), "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["retrospective_replication_only"] is True
    assert out["status"] == "RETROSPECTIVE_REPLICATION_ONLY"


def test_rehearse_migration_preserves_legacy_rows_and_is_idempotent(tmp_path: Path) -> None:
    from scripts.rehearse_state_machine_migration import rehearse
    from warden.registry import LEGACY_REASON, LEGACY_RUNS

    # Deterministic pre-Ticket-05 legacy schema fixture (missing source_published_at & simulation_epoch)
    legacy_dir = tmp_path / "legacy_src"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    legacy_db = legacy_dir / "legacy.db"
    with sqlite3.connect(legacy_db) as conn:
        conn.executescript("""
            CREATE TABLE contracts (contract_id TEXT PRIMARY KEY, asset_class TEXT, frequency TEXT, bars_per_year REAL, exploratory_data_path TEXT, holdout_data_path TEXT, train_start TEXT, val_start TEXT, val_end TEXT, holdout_start TEXT, holdout_end TEXT, n_folds REAL, label_horizon_bars REAL, embargo_bars REAL, execution_delay_bars REAL, taker_fee_bps REAL, slippage_bps REAL, funding_bps_per_8h REAL, execution_mode TEXT, min_expected_move_to_cost_ratio REAL, data_snapshot_sha256 TEXT, data_provenance TEXT, spec_hash TEXT NOT NULL);
            CREATE TABLE hypotheses (hypothesis_id TEXT PRIMARY KEY, contract_id TEXT, title TEXT, strategy_family TEXT, economic_mechanism TEXT, counterparty_or_friction TEXT, expected_holding_bars REAL, falsification_criteria TEXT, parent_hypothesis_id TEXT, source_provenance TEXT, llm_model_id TEXT, strategy_module TEXT, status TEXT NOT NULL DEFAULT 'ACTIVE', trials_used INTEGER NOT NULL DEFAULT 0, created_at REAL, attempts_json TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE trials (run_id TEXT PRIMARY KEY, hypothesis_id TEXT, contract_id TEXT, strategy_name TEXT, strategy_family TEXT, config_hash TEXT, stage_reached TEXT, verdict TEXT, gross_sharpe REAL, net_sharpe REAL, deflated_sharpe_prob REAL, expected_max_sharpe_noise REAL, total_trials_n REAL, effective_trials_keff REAL, mean_ic REAL, ic_hac_tstat REAL, hit_rate REAL, profit_factor REAL, max_drawdown_pct REAL, annualized_turnover REAL, breakeven_cost_bps REAL, stress_2x_cost_sharpe REAL, param_neighbor_stability REAL, notes TEXT, delay_plus1_sharpe REAL, cvar_99_pct REAL, synthetic REAL, data_snapshot_sha256 TEXT, strategy_module TEXT, model_type TEXT, returns_sha256 TEXT, data_provenance TEXT, diagnostics_json TEXT, stress_1p5_cost_sharpe REAL, strategy_code_sha256 TEXT, config_json TEXT, returns_json TEXT, returns_parquet_path TEXT, trial_number_in_hyp INTEGER, git_commit_sha TEXT, created_at REAL);
            CREATE TABLE selection_attempts (attempt_id TEXT PRIMARY KEY, attempt_base TEXT NOT NULL, run_id TEXT DEFAULT '', hypothesis_id TEXT NOT NULL, contract_id TEXT NOT NULL, strategy_family TEXT DEFAULT '', strategy_module TEXT DEFAULT '', kind TEXT NOT NULL, stage TEXT NOT NULL, outcome TEXT NOT NULL, config_hash TEXT DEFAULT '', parameters_json TEXT DEFAULT '{}', data_provenance TEXT DEFAULT 'unknown', returns_observed INTEGER NOT NULL DEFAULT 0, returns_json TEXT DEFAULT '', returns_sha256 TEXT DEFAULT '', diagnostics_json TEXT DEFAULT '{}', created_at REAL NOT NULL);
            CREATE TABLE feature_sets (feature_hash TEXT PRIMARY KEY, contract_id TEXT, feature_names_json TEXT, ml4t_engineer_version TEXT, prefix_invariance_passed INTEGER, parquet_cache_path TEXT, created_at REAL);
            CREATE TABLE qualifications (qual_id TEXT PRIMARY KEY, run_id TEXT, contract_id TEXT, hypothesis_id TEXT, lineage_id TEXT UNIQUE, holdout_gross_sharpe REAL, holdout_net_sharpe REAL, holdout_max_dd_pct REAL, val_to_holdout_sharpe_ratio REAL, approved_by_human TEXT, qualified_at REAL);
            CREATE TABLE trial_quarantine (run_id TEXT PRIMARY KEY, reason TEXT, quarantined_at REAL);
            CREATE TABLE audit_reports (run_id TEXT PRIMARY KEY, contract_id TEXT, verdict TEXT, checks_json TEXT, created_at REAL);
            CREATE TABLE trial_dispositions (disposition_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, disposition TEXT NOT NULL, reason TEXT NOT NULL, attributed_to TEXT NOT NULL, created_at REAL NOT NULL, contract_id TEXT DEFAULT '');
            CREATE TABLE audit_evidence (audit_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, contract_id TEXT DEFAULT '', verdict TEXT NOT NULL, checks_json TEXT NOT NULL DEFAULT '{}', reason TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL);
            CREATE TABLE contract_search_state (contract_id TEXT PRIMARY KEY, max_keff_gate REAL NOT NULL, max_noise REAL NOT NULL, updated_at REAL NOT NULL);
            INSERT INTO contracts VALUES ('C1','crypto_perps','1h',8760,'e.parquet','h.parquet','2024-01-01T00:00:00+00:00','2024-06-01T00:00:00+00:00','2024-12-01T00:00:00+00:00','2024-12-01T00:00:00+00:00','2025-01-01T00:00:00+00:00',3,1,1,1,1,1,0,'NEXT_BAR',2.5,'','unknown','hash1');
            INSERT INTO hypotheses (hypothesis_id,contract_id,title,strategy_family,economic_mechanism,counterparty_or_friction,expected_holding_bars,falsification_criteria) VALUES ('H1','C1','Legacy','rules_ts','Mechanism explanation of twenty chars','Friction explanation of twenty chars',4,'Crit');
            INSERT INTO trials (run_id,hypothesis_id,contract_id,verdict,returns_json,created_at) VALUES ('R1','H1','C1','REVISE','[0.01,-0.01]',1.0);
            INSERT INTO selection_attempts (attempt_id,attempt_base,hypothesis_id,contract_id,kind,stage,outcome,created_at) VALUES ('A1','B1','H1','C1','failed_reservation','STAGE_0','ERROR',1.0);
        """)
        for r_id in LEGACY_RUNS:
            conn.execute("INSERT INTO trial_quarantine VALUES (?, ?, ?)", (r_id, LEGACY_REASON, 1.0))
    legacy_dest = tmp_path / "legacy_dest" / "migrated.db"
    res_legacy = rehearse(legacy_db, legacy_dest)
    assert res_legacy["legacy_unchanged"] is True
    assert res_legacy["idempotent"] is True
    assert "simulation_epoch" in res_legacy["added_columns"].get("trials", [])
    assert "simulation_epoch" in res_legacy["added_columns"].get("selection_attempts", [])
    assert "source_published_at" in res_legacy["added_columns"].get("hypotheses", [])

    seed_db = _seed_ledger(tmp_path / "seed_rehearse")
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    snap = _snapshot_sqlite(seed_db, src_dir / "src_snap.db")
    dest = tmp_path / "dest" / "migrated.db"
    res = rehearse(snap, dest)
    assert res["legacy_unchanged"] is True
    assert res["idempotent"] is True


def test_shadow_diagnostic_calibration_is_read_only_and_emits_deltas(tmp_path: Path) -> None:
    import hashlib

    from scripts.shadow_diagnostic_calibration import run_shadow_calibration

    seed_db = _seed_ledger(tmp_path / "seed")
    snap = _snapshot_sqlite(seed_db, tmp_path / "registry" / "cal_snap.db")
    before_sha = hashlib.sha256(snap.read_bytes()).hexdigest()
    report = run_shadow_calibration(snap, seed_db.parent / "returns", pbo_blocks=6, ras_simulations=200)
    after_sha = hashlib.sha256(snap.read_bytes()).hexdigest()

    assert before_sha == after_sha
    assert report["verdicts_mutated"] is False
    assert report["summary"]["n_trials_evaluated"] > 0
    assert report["summary"]["n_ic_series_evaluated"] > 0
    assert len(report["dsr_comparisons"]) == report["summary"]["n_trials_evaluated"]
    assert len(report["ic_hac_comparisons"]) == report["summary"]["n_ic_series_evaluated"]
    assert len(report["pbo_diagnostics"]) > 0
    assert len(report["ras_diagnostics"]) > 0
