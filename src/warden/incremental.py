"""RG-06: frozen comparative policy and fixed, governed benchmark evidence.

The comparator is not a selectable candidate or a new runner. It is an RG-04
operation of an admitted candidate. Only the contract's own kernel may execute
it. The production holdout still requires the later frozen-batch exposure gate.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

import numpy as np

from warden.contracts import canonical_json
from warden_evidence.benchmark_comparison import (
    BootstrapSettings,
    ComparisonBasis,
    ReturnTape,
    compare_return_artifacts,
)
from warden_evidence.return_artifacts import read_return_stream, return_frame
from warden_evidence.selection_alignment import project_onto_grid, utc_ns

SCENARIO = "benchmark"


def digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def approved_policy(block_length=5, block_reason=None):
    """Principal-approved 2026-11-27 policy; no outcome-selected settings."""
    if type(block_length) is not int or block_length < 5:
        raise ValueError("Comparative block length must be at least five sessions")
    if block_length > 5 and not (isinstance(block_reason, str) and block_reason.strip()):
        raise ValueError("Longer comparative block length requires preregistered justification")
    return {
        "version": "rg06-daily-sharpe-v1",
        "metric": "delta_annualised_net_sharpe",
        "observation_unit": "utc_day",
        "annualisation": "365_crypto_perps_else_252",
        "materiality": 0.25,
        "min_observations": 120,
        "min_blocks": 20,
        "block_length": block_length,
        "block_reason": block_reason,
        "bootstrap_method": "paired_circular_block_bootstrap_v1",
        "n_resamples": 20000,
        "seed": 731,
        "one_sided_alpha": 0.05,
        "multiplicity": "bonferroni_fixed_lineage_cap",
        "comparisons": 6,
        "capital_risk_basis": "same_contract_initial_capital_and_risk_limits",
    }


def require_policy(intent):
    if intent is None or intent["plan"]["purpose"] == "absolute":
        return None
    plan = intent["plan"]
    expected = approved_policy(plan.get("comparative_block_length", 5),
                               plan.get("comparative_block_reason"))
    if intent.get("comparison_policy") != expected or not intent.get("benchmark"):
        raise ValueError("Missing or changed approved comparative policy; register a related intent")
    return expected


def extend_plan(plan, intent):
    policy = require_policy(intent)
    if policy is None:
        return plan
    return {**plan, SCENARIO: {"kind": "benchmark", "benchmark": intent["benchmark"],
                               "comparison_policy": policy}}


def require_plan(plan, intent):
    expected = extend_plan({}, intent).get(SCENARIO)
    if (plan or {}).get(SCENARIO) != expected:
        raise ValueError("Frozen benchmark operation differs from approved comparative intent")


def prepare_benchmark(root, contract, intent, effective_params):
    """Validate frozen comparator metadata and universe before scoring."""
    if require_policy(intent) is None:
        return None
    from warden.params_model import validate_strategy_params
    from warden.protocols import load_and_enforce_strategy_protocol, resolve_effective_params
    from warden.research_intent import benchmark_binding
    from warden.schemas import load_experiment_spec

    benchmark = intent["benchmark"]
    if benchmark_binding(root, benchmark["spec_path"], contract.contract_id) != benchmark:
        raise ValueError("Frozen research intent benchmark fingerprint changed")
    spec = load_experiment_spec(Path(root) / benchmark["spec_path"])
    if spec.param_grid:
        raise ValueError("A fixed benchmark cannot contain selectable parameter variants")
    mod, protocol = load_and_enforce_strategy_protocol(
        Path(root) / spec.strategy_module, hypothesis_status="ACTIVE",
        strategy_family=spec.strategy_family, allow_legacy_replay=False,
    )
    validate_strategy_params(mod, spec.parameters)
    effective = resolve_effective_params(mod, protocol, spec.parameters)
    available = set(intent["resolution"]["symbols"])
    required = available - set(intent["resolution"]["symbols_to_drop"])

    def universe(params):
        symbols = params.get("target_symbols")
        return available if symbols is None else set(symbols)

    if universe(effective) != required or universe(effective_params) != required:
        raise ValueError("Candidate and benchmark must use the identical approved universe")
    return spec, mod, protocol, benchmark["code_sha256"]


def execute_benchmark(prepared, panel, ts, assets, contract, val_slice):
    """Run the approved comparator under the same kernel and causal checks.

    Separate kernel instance is deliberate: the existing kernel caches by
    protocol/panel/parameters, not strategy code. Never share it between legs.
    """
    from warden.evaluation import EvaluationKernel
    from warden.leakage import (
        verify_model_prefix_invariance,
        verify_rules_strategy_asset_independence,
        verify_rules_strategy_prefix_invariance,
    )

    spec, mod, protocol, code_sha = prepared
    kernel = EvaluationKernel()
    causal_evidence = {"protocol": protocol, "strategy_sha256": code_sha}
    if protocol == "causal_rules":
        proof = verify_rules_strategy_prefix_invariance(
            mod, panel, contract, spec.parameters, kernel=kernel, code_sha256=code_sha)
        if not proof.covers(panel, contract, spec.parameters, code_sha256=code_sha):
            raise ValueError("Benchmark prefix proof does not cover the frozen execution")
        verify_rules_strategy_asset_independence(mod, panel, contract, spec.parameters,
                                                 kernel=kernel)
        causal_evidence.update(prefix_proof=proof.payload(), prefix_proof_sha256=proof.digest,
                               asset_independence_check="PASS")
    elif protocol == "causal_model":
        verify_model_prefix_invariance(mod, panel, contract, spec.parameters)
    else:
        raise ValueError("Benchmark requires an active causal protocol")
    tape = kernel.run(mod, panel, ts, assets, contract, val_slice, spec.parameters,
                      protocol=protocol, policy=kernel.policy_for(contract),
                      allow_legacy_replay=False)
    if int(tape.strategy_output().get("signal_delay_bars", 0)) > 0:
        raise ValueError("Benchmark cannot pre-lag Warden execution")
    causal_evidence["prefix_checks"] = "PASS"
    if protocol == "causal_model":
        causal_evidence["fold_isolation"] = tape.fold_isolation
    return tape, causal_evidence


def _publish(path, root, values, clock):
    path = path.resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Paired return artifact escapes the returns tree")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".pending") as handle:
        return_frame(values, clock).write_parquet(handle)
        handle.flush()
        os.fsync(handle.fileno())
        os.link(handle.name, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def classify(report, policy):
    """One-sided lower bound from RG-02's symmetric percentile interval.

    The lower tail is alpha/6 regardless of how many candidates have finished.
    A definite failure is an upper bound at/below materiality. Otherwise lack
    of a positive conclusion is inconclusive, never scientific success.
    """
    interval = report.get("delta_sharpe_ci")
    if not report.get("sample_adequate") or interval is None:
        return "INCONCLUSIVE"
    if interval[0] > policy["materiality"]:
        return "PASS"
    if interval[1] <= policy["materiality"]:
        return "REJECT"
    return "INCONCLUSIVE"


def evaluate_pair(*, root, run_id, candidate, intent, contract, panel_sha,
                  execution_policy, candidate_returns, benchmark_returns, timestamps,
                  benchmark_causality, phase="development", holdout_sha256=None):
    """Publish paired tapes after the governed benchmark or holdout claim."""
    if phase not in ("development", "holdout") or (phase == "holdout") != bool(holdout_sha256):
        raise ValueError("Paired evidence phase and holdout pin disagree")
    from warden.contracts import valid_id
    from warden.engine_sim import ENGINE_INITIAL_CASH, extract_execution_policy

    if execution_policy != extract_execution_policy(contract=contract):
        raise ValueError("Paired execution policy differs from the contract")
    valid_id(run_id)
    valid_id(contract.contract_id)
    policy = require_policy(intent)
    if policy is None:
        raise ValueError("Absolute research has no benchmark operation")
    clock = utc_ns(timestamps)
    c = np.asarray(candidate_returns, dtype=float)
    b = np.asarray(benchmark_returns, dtype=float)
    if (not len(clock) or c.shape != clock.shape or b.shape != clock.shape
            or not np.isfinite(c).all() or not np.isfinite(b).all()):
        raise ValueError("Both paired tapes require the complete finite eligible bar clock")
    root = Path(root).resolve()
    directory = root / contract.contract_id / (".pairs" if phase == "development" else ".holdout-pairs") / run_id
    basis = ComparisonBasis(
        dataset_sha256=holdout_sha256 or candidate.dataset_sha256,
        calendar_id=digest([contract.contract_id, clock.tolist()]),
        information_set_sha256=panel_sha,
        execution_policy_sha256=digest([contract.spec_hash(), execution_policy]),
        cost_policy_sha256=digest([contract.spec_hash(), "1x_contract_costs"]),
        risk_basis_sha256=digest([contract.spec_hash(), execution_policy, ENGINE_INITIAL_CASH]),
        simulation_epoch=candidate.simulation_epoch,
        observation_unit=policy["observation_unit"],
        periods_per_year=365 if contract.asset_class == "crypto_perps" else 252,
    )
    c_path, b_path = directory / "candidate.parquet", directory / "benchmark.parquet"
    c_sha, b_sha = _publish(c_path, root, c, clock), _publish(b_path, root, b, clock)
    benchmark = intent["benchmark"]
    c_tape = ReturnTape(run_id, c_path, c_sha, candidate.strategy_code_sha256,
                        digest(json.loads(candidate.parameters_json)), basis)
    b_tape = ReturnTape(run_id + ":benchmark", b_path, b_sha, benchmark["code_sha256"],
                        benchmark["config_sha256"], basis)
    alpha = policy["one_sided_alpha"] / policy["comparisons"]
    settings = BootstrapSettings(
        seed=policy["seed"], block_length=policy["block_length"],
        n_resamples=policy["n_resamples"], confidence_level=1 - 2 * alpha,
        min_observations=policy["min_observations"], min_blocks=policy["min_blocks"],
    )
    report = compare_return_artifacts(c_tape, b_tape, returns_root=root,
                                      expected_timestamps_ns=clock, settings=settings)
    status = classify(report, policy)
    return {**report, "diagnostic_status": report["status"], "status": status,
            "reason": report["reason"] or ("materiality_cleared" if status == "PASS" else
                                           "materiality_not_established"),
            "phase": phase, "policy": policy, "adjusted_alpha": alpha,
            "candidate_id": candidate.candidate_id,
            "candidate_path": str(c_path), "benchmark_path": str(b_path),
            "execution_policy": execution_policy, "initial_cash": ENGINE_INITIAL_CASH,
            "benchmark_causality": benchmark_causality}


def verify_artifacts(report, root):
    """Check exact bytes and same raw clock, without ever reading market data."""
    c = read_return_stream(report["candidate_path"], root,
                           expected_sha256=report["candidate"]["artifact_sha256"])
    b = read_return_stream(report["benchmark_path"], root,
                           expected_sha256=report["benchmark"]["artifact_sha256"])
    clock = c.timestamps_ns
    if (clock is None or not np.array_equal(clock, b.timestamps_ns)
            or len(clock) != report["n_bars"]
            or not np.isfinite(c.net_returns).all() or not np.isfinite(b.net_returns).all()
            or hashlib.sha256(clock.astype("<i8").tobytes()).hexdigest() != report["raw_clock_sha256"]):
        raise ValueError("Paired return artifact clock/evidence drift")
    return c


def require_trial_pair(conn, root, run_id, *, verdict=None, returns=None, timestamps=None):
    """Check the durable governed comparative evidence; absolute mode is unchanged."""
    row = conn.execute(
        "SELECT c.identity_json,i.intent_json,p.plan_json "
        "FROM candidate_operations o JOIN candidate_admissions c ON c.candidate_id=o.candidate_id "
        "JOIN candidate_intent_bindings l ON l.candidate_id=c.candidate_id "
        "JOIN research_intents i ON i.intent_sha256=l.intent_sha256 "
        "JOIN robustness_plans p ON p.candidate_id=c.candidate_id "
        "WHERE o.kind='trial' AND o.operation_id=?", (run_id,),
    ).fetchone()
    if row is None:
        return None
    from warden.candidate_admission import CandidateIdentity

    candidate = CandidateIdentity(**json.loads(row[0]))
    intent, plan = json.loads(row[1]), json.loads(row[2])
    policy = require_policy(intent)
    if policy is None:
        return None
    require_plan(plan, intent)
    receipt = conn.execute(
        "SELECT r.scenario_json,o.state,o.evidence_json,o.evidence_sha256 "
        "FROM robustness_receipts r JOIN robustness_outcomes o ON o.scenario_id=r.scenario_id "
        "WHERE r.run_id=? AND r.candidate_id=? AND r.scenario_key=?",
        (run_id, candidate.candidate_id, SCENARIO),
    ).fetchone()
    if not receipt or receipt[1] != "FINALIZED" or hashlib.sha256(
            receipt[2].encode()).hexdigest() != receipt[3]:
        raise ValueError("Missing finalized comparative benchmark evidence")
    outcome = json.loads(receipt[2])
    report = outcome["metrics"]["incremental_comparison"]
    if (json.loads(receipt[0]) != plan[SCENARIO] or outcome["scenario"] != plan[SCENARIO]
            or report["policy"] != policy or report["candidate_id"] != candidate.candidate_id
            or report["phase"] != "development" or report["candidate"]["run_id"] != run_id
            or report["benchmark"]["run_id"] != run_id + ":benchmark"
            or report["candidate"]["code_sha256"] != candidate.strategy_code_sha256
            or report["candidate"]["config_sha256"] != digest(json.loads(candidate.parameters_json))
            or report["benchmark"]["code_sha256"] != intent["benchmark"]["code_sha256"]
            or report["benchmark"]["config_sha256"] != intent["benchmark"]["config_sha256"]
            or report["basis"]["dataset_sha256"] != candidate.dataset_sha256
            or report["basis"]["simulation_epoch"] != candidate.simulation_epoch
            or report["basis"]["observation_unit"] != policy["observation_unit"]
            or report["adjusted_alpha"] != policy["one_sided_alpha"] / policy["comparisons"]
            or report["benchmark_causality"]["strategy_sha256"] != intent["benchmark"]["code_sha256"]
            or report["benchmark_causality"]["prefix_checks"] != "PASS"
            or report["status"] != classify(report, policy)):
        raise ValueError("Comparative evidence differs from frozen research intent")
    tape = verify_artifacts(report, root)
    if returns is not None:
        if timestamps is None:
            raise ValueError("Comparative trial needs an explicit selection clock")
        selected = project_onto_grid(tape.timestamps_ns, tape.net_returns, timestamps)
        if not np.array_equal(selected, returns):
            raise ValueError("Paired candidate returns differ from trial evidence")
    if verdict == "CANDIDATE_FOR_QUALIFICATION" and report["status"] != "PASS":
        raise ValueError("Incremental candidate cannot qualify without comparative PASS")
    return report
