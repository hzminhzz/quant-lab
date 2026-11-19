"""A generic contract-level feature row cannot satisfy an unrelated run's audit."""

from __future__ import annotations

import argparse
import hashlib

import numpy as np

from warden.cli import cmd_verify, cmd_verify_causality
from warden.contracts import ExperimentResult, canonical_json
from warden.runner import ExperimentRunner

MODULE = """
import numpy as np
import polars as pl

class CausalPipeline:
    def compute(self, bars_df, contract):
        return bars_df.sort("timestamp", "asset").with_columns(
            pl.col("close").shift(1).over("asset").alias("past_close"))

pipeline = CausalPipeline()

def generate_positions(panel_df, contract, **kwargs):
    times = len(panel_df["timestamp"].unique())
    assets = len(panel_df["asset"].unique())
    p = np.zeros((times, assets))
    p[::4] = 0.1
    return {"positions": p}
"""


def _run(f, run_id="RUN-CAUSAL", module="strategies/causal.py") -> ExperimentResult:
    return ExperimentResult(
        run_id=run_id, hypothesis_id="HYP-TINY", contract_id="TINY_V1",
        strategy_name="Causal fixture", strategy_family="rules_ts",
        config_hash=hashlib.sha256(canonical_json({}).encode()).hexdigest()[:12],
        stage_reached="STAGE_3_STRESS", verdict="CANDIDATE_FOR_QUALIFICATION",
        gross_sharpe=1.4, net_sharpe=1.2, deflated_sharpe_prob=0.93,
        expected_max_sharpe_noise=0.3, total_trials_n=1, effective_trials_keff=1.0,
        mean_ic=None, ic_hac_tstat=None, hit_rate=0.52, profit_factor=1.3,
        max_drawdown_pct=2.0, annualized_turnover=10.0,
        breakeven_cost_bps=6.0, stress_2x_cost_sharpe=0.6,
        param_neighbor_stability=0.9, notes="Synthetic fixture",
        delay_plus1_sharpe=0.3, synthetic=0, data_snapshot_sha256=f.sha,
        strategy_module=module, data_provenance="verified_market",
    )


def test_verified_spec_proof_resolves_run_and_rejects_code_drift(tiny_repo, monkeypatch, capsys) -> None:
    f = tiny_repo(module_source=MODULE, module_name="causal")
    monkeypatch.setattr("warden.cli.check_worktree", lambda: True)
    runner = ExperimentRunner(registry=f.registry, repo_root=f.repo)
    assert cmd_verify(argparse.Namespace(spec_yaml=str(f.spec), json=True), runner) == 0
    f.registry.record_trial(_run(f), {}, np.linspace(-0.001, 0.004, 35))
    assert f.registry.causal_proof_for_run("RUN-CAUSAL", f.repo)["verified"]
    assert cmd_verify_causality(argparse.Namespace(run_id="RUN-CAUSAL", json=True), runner) == 0
    assert '"status": "PASS"' in capsys.readouterr().out

    # Corrupt sidecars must be ordinary denied evidence, not a crash or approval.
    proof_path = next((f.repo / "cache" / "features").glob("*.proof.json"))
    proof_original = proof_path.read_bytes()
    proof_path.write_bytes(b"this-is-not-json")
    corrupt = f.registry.causal_proof_for_run("RUN-CAUSAL", f.repo)
    assert not corrupt["verified"]
    proof_path.write_bytes(proof_original)

    path = f.repo / "strategies" / "causal.py"
    path.write_text(path.read_text() + "\n# intentional source revision invalidates immutable proof\n")
    mismatch = f.registry.causal_proof_for_run("RUN-CAUSAL", f.repo)
    assert not mismatch["verified"]
    assert "drift" in mismatch["reason"]
    assert cmd_verify_causality(argparse.Namespace(run_id="RUN-CAUSAL", json=True), runner) == 1


def test_unrelated_legacy_contract_feature_proof_cannot_certify_run(tiny_repo) -> None:
    f = tiny_repo(module_source=MODULE, module_name="causal")
    f.registry.record_trial(_run(f), {}, np.linspace(-0.001, 0.004, 35))
    with f.registry._connect() as conn:
        conn.execute("INSERT INTO feature_sets VALUES ('legacy','TINY_V1','[]','0.2.0',1,'p',1.0)")
    assert not f.registry.causal_proof_for_run("RUN-CAUSAL", f.repo)["verified"]
    assert f.registry.causal_proof_for_run("NO-SUCH-RUN", f.repo)["reason"] == "run not found"
