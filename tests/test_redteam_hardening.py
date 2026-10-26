"""Comprehensive adversarial regression tests verifying red-team hardening."""
from __future__ import annotations

import sqlite3
from argparse import Namespace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from warden.cli import (
    check_worktree,
    cmd_continue,
    cmd_verify,
)
from warden.contracts import (
    EvaluationContract,
    ExperimentResult,
    HypothesisSpec,
    load_contract,
)
from warden.gates import compute_hac_tstat
from warden.leakage import (
    LookaheadLeakageError,
    verify_fold_isolation,
    verify_prefix_invariance,
)
from warden.qualify import HoldoutAlreadyUnsealedError, qualify_candidate
from warden.registry import (
    ResearchRegistry,
    compute_selection_report,
)
from warden.runner import (
    ExperimentRunner,
    _aligned_matrix,
    _funding_settlement_mask,
    simulate_positions,
)
from warden.synthetic import generate_canonical_market_panel

CONTRACT_PATH = Path("contracts/crypto_perps_1h_v1.yaml")


@pytest.fixture
def clean_contract() -> EvaluationContract:
    return load_contract(CONTRACT_PATH)


@pytest.fixture
def synthetic_panel() -> pl.DataFrame:
    return generate_canonical_market_panel()


# ---------------------------------------------------------------------------
# 1. Leakage Prover Traps
# ---------------------------------------------------------------------------


def test_sneaky_column_omission_trap_fails_closed(clean_contract, synthetic_panel) -> None:
    """Extra column emitted only on the full dataset must trigger LookaheadLeakageError."""
    class SneakyColumns:
        def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
            df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
            out = df.with_columns(x=pl.col("close").pct_change().over("asset"))
            if df.height == synthetic_panel.height:
                out = out.with_columns(leak=pl.col("close").shift(-1).over("asset") / pl.col("close") - 1.0)
            return out

    with pytest.raises(LookaheadLeakageError, match="Missing column 'leak'"):
        verify_prefix_invariance(SneakyColumns(), synthetic_panel, clean_contract)


def test_forward_window_ratio_leakage_fails_closed(clean_contract, synthetic_panel) -> None:
    """Forward-looking ratio masked by timestamp must be caught by per-row varying perturbation."""
    t0 = sorted(synthetic_panel["timestamp"].unique().to_list())[len(synthetic_panel["timestamp"].unique()) // 2]
    K = 10

    class ForwardWindow:
        def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
            df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
            out = df.with_columns(raw=pl.col("close").shift(-K).over("asset") / pl.col("close") - 1.0)
            return out.with_columns(
                leak=pl.when(pl.col("timestamp") >= pl.lit(t0) - pl.duration(hours=K)).then(None).otherwise(pl.col("raw"))
            ).drop("raw")

    with pytest.raises(LookaheadLeakageError, match="leak"):
        verify_prefix_invariance(ForwardWindow(), synthetic_panel, clean_contract)


def test_fold_isolation_enforces_purge_and_strips_target_copies(clean_contract, synthetic_panel) -> None:
    """Fold isolation must enforce label_horizon + embargo gap and strip target copies."""
    class DummyModel:
        def fit_predict_fold(self, train_df, val_features_df, feature_cols, label_col, seed=42):
            assert "target" not in val_features_df.columns
            assert "target_copy" not in val_features_df.columns
            assert "forward_label" not in val_features_df.columns
            return val_features_df.select("timestamp", "asset").with_columns(prediction_value=pl.lit(0.0))

    # Test 1: Overlapping horizon raises
    t_split = sorted(synthetic_panel["timestamp"].unique().to_list())[200]
    t_too_close = sorted(synthetic_panel["timestamp"].unique().to_list())[202]  # only 2 hours gap (< 4+2=6)
    train_leaky = synthetic_panel.filter(pl.col("timestamp") <= t_split).with_columns(target=pl.lit(1.0))
    val_leaky = synthetic_panel.filter(pl.col("timestamp") >= t_too_close).with_columns(target=pl.lit(1.0))

    with pytest.raises(LookaheadLeakageError, match="Fold purge/embargo violation"):
        verify_fold_isolation(
            DummyModel(), train_leaky, val_leaky, ["close"], "target",
            label_horizon_bars=4, embargo_bars=2
        )

    # Test 2: Purged gap succeeds and strips all target aliases
    t_safe = sorted(synthetic_panel["timestamp"].unique().to_list())[210]  # 10 hours gap (> 6)
    val_safe = synthetic_panel.filter(pl.col("timestamp") >= t_safe).with_columns(
        target=pl.lit(1.0), target_copy=pl.lit(2.0), forward_label=pl.lit(3.0)
    )
    preds = verify_fold_isolation(
        DummyModel(), train_leaky, val_safe, ["close"], "target",
        label_horizon_bars=4, embargo_bars=2
    )
    assert "prediction_value" in preds.columns


# ---------------------------------------------------------------------------
# 2. Accounting & Funding Fidelity
# ---------------------------------------------------------------------------


def test_eight_hour_funding_settlement_charges_once_per_eight_hours(clean_contract) -> None:
    """24-hour simulation on crypto perps must incur exactly 2 funding settlements (00:00 & 08:00 UTC)."""
    ts = [datetime(2025, 1, 1, 0, tzinfo=UTC) + timedelta(hours=i) for i in range(24)]
    mask = _funding_settlement_mask(ts, clean_contract)
    pos = np.ones((24, 1))
    rets = np.zeros((24, 1))
    fund = np.full((24, 1), 0.0005)  # 5 bps

    sim = simulate_positions(pos, rets, fund, clean_contract, slice(0, 24), funding_mask=mask)
    # Total funding charge is 2 bars * 5 bps = 10 bps (0.0010)
    funding_cost = -float(np.sum(sim["net_returns"])) - 0.0006  # subtract initial entry turnover fee
    assert np.isclose(funding_cost, 2 * 0.0005, atol=1e-8)


def test_aligned_matrix_preserves_strict_asset_order() -> None:
    """_aligned_matrix must return columns strictly matching sorted_assets, regardless of appearance order."""
    df = pl.DataFrame({
        "timestamp": [1, 1, 1, 2, 2, 2],
        "asset": ["XRP", "BTC", "ETH", "ETH", "XRP", "BTC"],
        "val": [10.0, 100.0, 50.0, 55.0, 11.0, 105.0]
    })
    sorted_assets = ["BTC", "ETH", "XRP"]
    mat = _aligned_matrix(df, "val", sorted_assets)
    # Row 0: BTC=100, ETH=50, XRP=10
    assert np.allclose(mat[0], [100.0, 50.0, 10.0])
    # Row 1: BTC=105, ETH=55, XRP=11
    assert np.allclose(mat[1], [105.0, 55.0, 11.0])


# ---------------------------------------------------------------------------
# 3. Governance Gates & Trial Budget
# ---------------------------------------------------------------------------


def test_quarantined_trials_cannot_be_qualified_on_holdout(tmp_path: Path, clean_contract) -> None:
    """Quarantined trials must be refused qualification on holdout."""
    reg = ResearchRegistry(tmp_path / "reg.db")
    runner = ExperimentRunner(registry=reg, repo_root=Path("."))
    reg.register_contract(clean_contract)

    with reg._connect() as conn:
        conn.execute("INSERT INTO trial_quarantine VALUES ('RUN-QUAR-TEST', 'Synthetic test', 1.0)")
        conn.execute(f"INSERT INTO trials (run_id, hypothesis_id, contract_id, verdict, net_sharpe, stage_reached, synthetic) VALUES ('RUN-QUAR-TEST', 'HYP-1', '{clean_contract.contract_id}', 'CANDIDATE_FOR_QUALIFICATION', 1.5, 'STAGE_3_STRESS', 0)")

    with pytest.raises(ValueError, match="is quarantined"):
        qualify_candidate("RUN-QUAR-TEST", "YES_UNSEAL_HOLDOUT", runner)


def test_zero_and_negative_validation_sharpe_cannot_be_qualified(tmp_path: Path, clean_contract, synthetic_panel) -> None:
    """Non-positive Net Sharpe must be rejected by qualify_candidate."""
    reg = ResearchRegistry(tmp_path / "reg.db")
    runner = ExperimentRunner(registry=reg, repo_root=Path("."))
    reg.register_contract(clean_contract)
    hyp = HypothesisSpec("HYP-ZERO", clean_contract.contract_id, "T", "rules_ts", "Mechanism at least twenty chars", "Friction at least twenty chars", 8, "Crit")
    reg.register_hypothesis(hyp)

    r_zero = ExperimentResult("RUN-ZERO", hyp.hypothesis_id, clean_contract.contract_id, "S", "rules_ts", "x", "STAGE_3_STRESS", "CANDIDATE_FOR_QUALIFICATION", 0.0, 0.0, 0.95, 0.1, 1, 1.0, None, None, 0.5, 1.2, 5.0, 10.0, 20.0, 0.8, 1.0, "notes", 0.1, 0.0, 0, clean_contract.data_snapshot_sha256, "strategies/candidates/vol_breakout_1h.py", "rules", "", data_provenance="verified_market")
    reg.record_trial(r_zero, {}, np.zeros(100))

    with pytest.raises(ValueError, match="non-positive validation Net Sharpe"):
        qualify_candidate("RUN-ZERO", "YES_UNSEAL_HOLDOUT", runner, reviewer="human")


def test_grandchild_hypothesis_cannot_re_unseal_root_lineage(qualify_repo) -> None:
    """Unsealing a root hypothesis must lock all child and grandchild hypotheses in that lineage."""
    fixture = qualify_repo()
    reg, runner = fixture.registry, fixture.runner
    for hid, parent in (("HYP-Q-CH", "HYP-Q"), ("HYP-Q-GD", "HYP-Q-CH")):
        reg.register_hypothesis(HypothesisSpec(hid, fixture.contract.contract_id, "C", "rules_ts",
                                               "Mechanism at least twenty chars",
                                               "Friction at least twenty chars", 8, "Crit",
                                               parent_hypothesis_id=parent))
    q1 = qualify_candidate("RUN-Q1", "YES_UNSEAL_HOLDOUT", runner, reviewer="human-operator")
    assert q1["lineage_id"] == "HYP-Q"

    from warden.contracts import ExperimentResult
    r_grand = ExperimentResult("RUN-Q-GD", "HYP-Q-GD", fixture.contract.contract_id, "S", "rules_ts", "x",
                               "STAGE_3_STRESS", "CANDIDATE_FOR_QUALIFICATION", 1.5, 1.2, 0.95, 0.1, 1, 1.0,
                               None, None, 0.5, 1.2, 5.0, 10.0, 20.0, 0.8, 1.0, "notes", 0.1, 0.0, 0,
                               fixture.contract.data_snapshot_sha256, "strategies/flat.py", "rules", "",
                               data_provenance="verified_market")
    reg.record_trial(r_grand, {}, np.linspace(0.01, -0.004, 14))
    reg.record_audit("RUN-Q-GD", fixture.contract.contract_id, "APPROVED",
                     {f"{n}_check": True for n in range(1, 7)}, "fixture")
    with pytest.raises(HoldoutAlreadyUnsealedError, match="Lineage 'HYP-Q' was already unsealed"):
        qualify_candidate("RUN-Q-GD", "YES_UNSEAL_HOLDOUT", runner, reviewer="human-operator")


def test_qualification_requires_an_approved_audit_and_reviewer(qualify_repo) -> None:
    from warden.qualify import qualify_candidate as qualify

    unaudited = qualify_repo(audit_approved=False)
    with pytest.raises(ValueError, match="APPROVED six-check audit"):
        qualify("RUN-Q1", "YES_UNSEAL_HOLDOUT", unaudited.runner, reviewer="human")

    no_manifest = qualify_repo(holdout_manifest=False)
    with pytest.raises(ValueError, match="no 'holdout' record"):
        qualify("RUN-Q1", "YES_UNSEAL_HOLDOUT", no_manifest.runner, reviewer="human")

    no_funding = qualify_repo(drop_funding=True)
    with pytest.raises(ValueError, match="funding_rate"):
        qualify("RUN-Q1", "YES_UNSEAL_HOLDOUT", no_funding.runner, reviewer="human")

    unreviewed = qualify_repo()
    out = qualify("RUN-Q1", "YES_UNSEAL_HOLDOUT", unreviewed.runner, reviewer="")
    assert out["promoted"] is False and out["reviewer"] == "UNVERIFIED_REVIEWER"


def test_hypotheses_trials_used_cannot_be_decreased_via_sqlite(tmp_path: Path, clean_contract) -> None:
    """SQLite trigger must abort any attempt to reset or decrease trials_used."""
    reg = ResearchRegistry(tmp_path / "reg.db")
    reg.register_contract(clean_contract)
    hyp = HypothesisSpec("HYP-LOCK", clean_contract.contract_id, "T", "rules_ts", "Mechanism at least twenty chars", "Friction at least twenty chars", 8, "Crit")
    reg.register_hypothesis(hyp)

    reg.register_trial_start("HYP-LOCK", "RUN-1")
    reg.register_trial_start("HYP-LOCK", "RUN-2")

    with reg._connect() as conn, pytest.raises(sqlite3.IntegrityError, match="trials_used cannot be decreased"):
        conn.execute("UPDATE hypotheses SET trials_used = 0 WHERE hypothesis_id = 'HYP-LOCK'")


def test_stage_1_reject_increments_raw_N_and_fails_closed() -> None:
    """A Stage 1 early reject counts in raw N; without comparable evidence promotion fails closed."""
    candidate = np.random.default_rng(42).normal(0.001, 0.01, 100)
    clean = compute_selection_report(candidate, [], 8760, raw_attempt_count=1)
    assert clean["raw_attempt_count"] == 1 and clean["k_eff_gate"] == 1.0
    assert clean["non_promotable_reason"] == ""

    # The unvectorised reject keeps the raw count but gets no invented numeric penalty.
    blocked = compute_selection_report(candidate, [], 8760, raw_attempt_count=2)
    assert blocked["raw_attempt_count"] == 2 and blocked["unvectorized_attempts"] == 1
    assert blocked["k_eff_gate"] == clean["k_eff_gate"]  # no fabricated 0.50-per-attempt penalty
    assert "no comparable return stream" in blocked["non_promotable_reason"]


def test_constant_ic_series_yields_zero_hac_tstat() -> None:
    """Degenerate zero-variance IC series must yield HAC t-stat == 0.0."""
    const_t = compute_hac_tstat(np.full(50, 0.05))
    assert const_t == 0.0


# ---------------------------------------------------------------------------
# 4. CLI Shell Integrity
# ---------------------------------------------------------------------------


def test_continue_rejects_unallowlisted_shell_commands(tmp_path: Path) -> None:
    """cmd_continue must refuse arbitrary commands not in the read-only allowlist."""
    runner = ExperimentRunner(repo_root=Path("."))
    evil_trans = tmp_path / "evil.md"
    evil_trans.write_text("```bash\ntouch /tmp/forbidden_pwn\n```\n")
    ret = cmd_continue(Namespace(transition_path=str(evil_trans)), runner)
    assert ret == 1
    assert not Path("/tmp/forbidden_pwn").exists()


def test_continue_exact_match_rejects_substring_count_drift(tmp_path: Path) -> None:
    """cmd_continue must reject substring matching (e.g. expected 5 matching actual 15)."""
    runner = ExperimentRunner(repo_root=Path("."))
    drift_trans = tmp_path / "drift.md"
    drift_trans.write_text("```bash\necho 15\n# expect: 5\n```\n")
    ret = cmd_continue(Namespace(transition_path=str(drift_trans)), runner)
    assert ret == 1


def test_verify_without_pipeline_exits_with_code_1(tmp_path: Path, signed_repo) -> None:
    """cmd_verify on a module without FeaturePipeline must exit with code 1."""
    runner = ExperimentRunner(repo_root=signed_repo)
    (signed_repo / "strategies" / "nopipe.py").write_text("def generate_positions(df, contract, **kw): return {}\n")
    spec_f = tmp_path / "spec.yaml"
    spec_f.write_text("contract_id: CRYPTO_PERPS_1H_V1\nstrategy_module: strategies/nopipe.py\n")
    ret = cmd_verify(Namespace(spec_yaml=str(spec_f), json=False), runner)
    assert ret == 1


def test_missing_loc_budget_script_fails_worktree_check(signed_repo) -> None:
    """Deleting the protected LOC budget script must cause check_worktree() to return False."""
    assert check_worktree(signed_repo) is True
    (signed_repo / "scripts" / "check_loc_budget.py").unlink()
    assert check_worktree(signed_repo) is False
