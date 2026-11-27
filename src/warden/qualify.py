"""Human-only holdout qualification gate and one-shot lineage unsealing."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import yaml

from warden import exposure, incremental
from warden.codeid import code_closure
from warden.contracts import PROMOTED, PROXY_HOLDOUT_PASS, REJECTED_ON_HOLDOUT, canonical_json
from warden.engine_sim import extract_execution_policy
from warden.evaluation import EvaluationKernel
from warden.gates import HOLDOUT_RATIO_FLOOR, HOLDOUT_SR_FLOOR
from warden.market_data import ROLE_HOLDOUT, MarketDataGateway
from warden.protocols import (
    detect_strategy_protocol,
    resolve_effective_params,
)
from warden.provenance import resolve_contract_governance, resolve_dataset
from warden.runner import (
    ExperimentRunner,
    calculate_metrics_from_returns,
)
from warden.strategy_loader import load_module_from_path


class HoldoutAlreadyUnsealedError(PermissionError): pass


def _human_gate(token, expected):
    if os.environ.get("WARDEN_NON_INTERACTIVE") == "1":
        raise PermissionError("warden qualification is an interactive human-only gate.")
    if token != expected:
        raise PermissionError(f"Holdout authorization requires confirmation token {expected!r}.")


def _prepare_qualification(run_id, runner, holdout_override_path=None):
    """No holdout bytes or results are accessed here; reused at freeze and claim."""
    from warden.contract_source import is_sandbox_contract

    reg = runner.registry
    # The namespace refusal's only ledger act is one read of the run's contract id — there
    # is no other way to learn which namespace a run belongs to.  Everything *after* that
    # read is what it guarantees: no holdout claim, no eligibility verdict, no holdout stat
    # and no replay.  Promotion into ``contracts/`` and the human re-sign are a separate,
    # human-only step; this gate stays shut until then.
    with reg._connect() as conn:
        sandbox_row = conn.execute(
            "SELECT contract_id FROM trials WHERE run_id = ?", (run_id,)
        ).fetchone()
    if sandbox_row is not None and is_sandbox_contract(runner.repo_root, sandbox_row[0]):
        raise PermissionError(
            f"Run ID '{run_id}' belongs to sandbox contract '{sandbox_row[0]}' under "
            "contracts/sandbox/: a compiled exploratory artifact is never holdout-qualified. "
            "Promote the contract and its dataset manifest into contracts/ and let the human "
            "re-sign the manifest first."
        )
    with reg._connect() as conn:
        t_row = conn.execute("SELECT * FROM trials WHERE run_id = ?", (run_id,)).fetchone()
        if not t_row:
            raise ValueError(f"Run ID '{run_id}' not found in registry.")
        trial = dict(t_row)
        hyp_id, cid = trial["hypothesis_id"], trial["contract_id"]
        q_entry = conn.execute("SELECT reason FROM trial_quarantine WHERE run_id = ?", (run_id,)).fetchone()
        if q_entry:
            raise ValueError(f"Run ID '{run_id}' is quarantined ({q_entry[0]}) and cannot be qualified on holdout.")
        if trial.get("synthetic") in (1, 1.0, "1", True):
            raise ValueError(f"Run ID '{run_id}' was generated on synthetic benchmark data and cannot be qualified on holdout.")
        eligibility = reg.effective_eligibility(run_id)
        if not eligibility["eligible"]:
            raise ValueError(
                f"Run ID '{run_id}' is not market-eligible for holdout qualification: {eligibility['reason']}")
        if trial.get("verdict") != "CANDIDATE_FOR_QUALIFICATION":
            raise ValueError(f"Run ID '{run_id}' has verdict '{trial.get('verdict')}'; only CANDIDATE_FOR_QUALIFICATION trials can be qualified.")
        val_sr = float(trial.get("net_sharpe", 0.0))
        if val_sr <= 0.0:
            raise ValueError(f"Trial '{run_id}' has non-positive validation Net Sharpe ({val_sr:.2f}); cannot qualify.")
        curr_hyp = hyp_id
        while True:
            p_row = conn.execute("SELECT parent_hypothesis_id FROM hypotheses WHERE hypothesis_id = ?", (curr_hyp,)).fetchone()
            if not p_row or not p_row[0] or p_row[0] == curr_hyp:
                lineage_id = curr_hyp
                break
            curr_hyp = p_row[0]

        q_row = conn.execute("SELECT qual_id, run_id FROM qualifications WHERE lineage_id = ?", (lineage_id,)).fetchone()
        if q_row:
            raise HoldoutAlreadyUnsealedError(f"Lineage '{lineage_id}' was already unsealed by run '{q_row[1]}'.")

    contract = runner.resolve_active_contract(cid)
    # Active lookup, not the recorded one: an archived contract can never be qualified,
    # and the refusal names its archive location (#114).
    # Evidence tier is contract-owned.  A synthetic tier can never be qualified on
    # a production holdout, so it is refused before any claim or read begins.
    governance = resolve_contract_governance(contract, runner.repo_root)
    if governance.evidence_tier == "synthetic":
        raise ValueError(
            f"Contract '{cid}' declares evidence_tier 'synthetic'; synthetic evidence is never qualified "
            "on a production holdout.")
    # Qualification may not silently execute an unrelated strategy when the
    # original source was moved or never recorded.
    module = trial.get("strategy_module") or ""
    rel = Path(module)
    strat_path = (runner.repo_root / rel).resolve()
    allowed = (runner.repo_root / "strategies").resolve()
    if not module or rel.is_absolute() or ".." in rel.parts or not strat_path.is_relative_to(allowed):
        raise ValueError("Missing or unsafe frozen strategy module; holdout remains sealed")
    if not strat_path.is_file():
        raise FileNotFoundError(f"Frozen strategy module unavailable: {module}")
    code_sha, _ = code_closure(runner.repo_root, module)
    recorded_code_sha = trial.get("strategy_code_sha256")
    if not recorded_code_sha:
        raise ValueError(f"Trial '{run_id}' has no recorded strategy_code_sha256; qualification requires a code binding (re-run the trial).")
    if code_sha != recorded_code_sha:
        raise ValueError(f"Strategy code for trial '{run_id}' changed since the trial; refusing to qualify on different code.")
    holdout_override = holdout_override_path is not None
    approved = proof = None
    if not holdout_override:
        # The six-check audit is a required production admission, not merely
        # a suggested CLI step. Fail before opening or claiming the holdout.
        with reg._connect() as conn:
            approved = conn.execute(
                "SELECT audit_id,verdict,checks_json FROM audit_evidence WHERE run_id=? ORDER BY created_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        if not approved or approved["verdict"] != "APPROVED":
            raise PermissionError("Production qualification requires an APPROVED six-check audit")
        proof = reg.causal_proof_for_run(run_id, runner.repo_root)
        if not proof["verified"]:
            raise PermissionError(f"Production qualification requires run-bound causal proof: {proof['reason']}")
    from warden.research_intent import require_dataset_resolution, require_qualification_conformance

    binding = require_qualification_conformance(reg, run_id, contract, code_sha, runner.repo_root)

    raw_cfg = trial.get("config_json", "{}")
    cfg_data = yaml.safe_load(raw_cfg) if raw_cfg else {}
    cfg_dict = cfg_data if isinstance(cfg_data, dict) else {}
    params = cfg_dict.get("parameters", cfg_dict)

    policy_kw = extract_execution_policy(cfg_dict, params, trial.get("diagnostics_json"), contract=contract)

    mod = load_module_from_path(strat_path)
    protocol = detect_strategy_protocol(mod)
    effective_params = resolve_effective_params(mod, protocol, params)
    benchmark = None
    if binding is not None:
        candidate, intent = binding
        if not holdout_override:
            require_dataset_resolution(resolve_dataset(contract, runner.repo_root), intent)
            import json

            checks = json.loads(approved["checks_json"])
            required_checks = {"1_target_rebalance_alignment", "2_outlier_sensitivity",
                               "3_point_in_time_causality", "4_fold_local_fitting",
                               "5_cost_and_latency_survival", "6_selection_bias_accounting"}
            if set(checks) != required_checks or not all(v is True for v in checks.values()):
                raise ValueError("Qualification requires all six frozen audit checks")
        if canonical_json(effective_params) != candidate.effective_parameters_json:
            raise ValueError("Frozen effective candidate parameters changed")
        if protocol not in ("causal_rules", "causal_model"):
            raise ValueError("Governed holdout requires an active causal protocol")
        benchmark = incremental.prepare_benchmark(runner.repo_root, contract, intent, effective_params)
    return {
        "trial": trial, "contract": contract, "governance": governance,
        "lineage_id": lineage_id, "eligibility": eligibility, "binding": binding,
        "audit": dict(approved) if approved else None, "proof": proof,
        "mod": mod, "protocol": protocol, "params": params,
        "effective_params": effective_params, "policy_kw": policy_kw,
        "benchmark": benchmark,
    }


def describe_holdout_batch(run_id, runner):
    prepared = _prepare_qualification(run_id, runner)
    return exposure.describe(runner.registry, prepared, runner.repo_root)


def authorize_holdout_batch(run_id, runner, approval, principal, human_confirm_token):
    _human_gate(human_confirm_token, "YES_AUTHORIZE_HOLDOUT_BATCH")
    prepared = _prepare_qualification(run_id, runner)
    return exposure.authorize(runner.registry, prepared, runner.repo_root, approval, principal)


def qualify_candidate(
    run_id: str,
    human_confirm_token: str,
    runner: ExperimentRunner,
    holdout_override_path: Path | None = None,
    *,
    batch_id: str | None = None,
) -> dict[str, Any]:
    _human_gate(human_confirm_token, "YES_UNSEAL_HOLDOUT")
    reg = runner.registry
    prepared = _prepare_qualification(run_id, runner, holdout_override_path)
    trial, contract, governance = (prepared[k] for k in ("trial", "contract", "governance"))
    hyp_id, cid = trial["hypothesis_id"], trial["contract_id"]
    lineage_id, eligibility = prepared["lineage_id"], prepared["eligibility"]
    mod, protocol, params = (prepared[k] for k in ("mod", "protocol", "params"))
    effective_params, policy_kw = prepared["effective_params"], prepared["policy_kw"]
    holdout_override = holdout_override_path is not None
    # No production override is authorized by the batch gate. The legacy
    # branch below remains a seam for pre-governance scratch fixture tests.
    batch = exposure.claim(reg, prepared, runner.repo_root, batch_id, holdout_override=holdout_override)
    h_path = holdout_override_path or (runner.repo_root / contract.holdout_data_path)
    if not h_path.is_file():
        raise FileNotFoundError(f"Holdout dataset not found at '{h_path}'.")
    if batch is not None:
        lineage_id = batch["lineage"]
        exposure.mark_read(reg, batch_id, batch)
    else:
        try:
            reg.reserve_holdout_lineage(lineage_id, run_id)
        except PermissionError as exc:
            raise HoldoutAlreadyUnsealedError(str(exc)) from exc
        reg.mark_holdout_read_started(lineage_id, run_id)
    # Even hashing the protected file is a read. Both claims are already durable.
    holdout_res = None

    # #97: the holdout refit schedule is contract-declared, not caller-chosen.  A model
    # strategy qualifies under `purged_expanding` (fold 0 trained strictly before
    # `holdout_start - (label_horizon + execution_delay + embargo)`, expanding across the
    # window) or `frozen_pre_holdout` (a single pre-holdout fit, never refit on a holdout
    # bar).  Rules strategies choose their own orders, so the prefix only warms them; no
    # refit mode applies and none is silently substituted for one.
    refit_mode = governance.holdout_refit_mode if protocol == "causal_model" else None

    # #95: one reader for the holdout too.  The claim and durable read-start record above
    # stay exactly where they were — the gateway is only handed the already-verified
    # resolution, so no holdout byte (or digest) is touched before the one-shot claim.
    # A production holdout is prefixed with its digest-verified exploratory warmup; the
    # fixture override reads exactly one file and no market gate.
    symbols = effective_params.get("target_symbols")
    if batch is not None:
        resolution = prepared["binding"][1]["resolution"]
        symbols = sorted(set(resolution["symbols"]) - set(resolution["symbols_to_drop"]))
    try:
        if not holdout_override:
            if batch is not None and h_path.resolve() != runner.repo_root.resolve() / contract.holdout_data_path:
                raise exposure.ScopeIncident("Frozen holdout artifact path changed")
            holdout_res = resolve_dataset(contract, runner.repo_root, role="holdout")
            if batch is not None and holdout_res.sha256 != batch["scope"]["dataset_sha256"]:
                raise exposure.ScopeIncident("Frozen holdout artifact digest changed")
            if not holdout_res.eligible_market:
                raise ValueError(f"Holdout dataset for '{cid}' is not market-eligible: {holdout_res.reason}")
        market = MarketDataGateway(runner.repo_root).load(
            contract, ROLE_HOLDOUT, symbols, resolution=holdout_res,
            holdout_override_path=holdout_override_path,
            **({"holdout_scope": batch["scope"]} if batch is not None else {}),
        )
    except exposure.ScopeIncident as exc:
        exposure.record_scope_incident(reg, batch_id, run_id, str(exc))
        raise
    if batch is not None and set(market.assets) != set(symbols):
        raise ValueError("Holdout evaluation universe differs from frozen intent")
    panel_df, ts, assets = market.panel, list(market.ts), list(market.assets)
    n_bars = len(ts)
    # eval_slice covers 100% of the holdout bars.  The warmup prefix is training-only.
    eval_slice = market.eval_slice
    execution_slice = eval_slice
    if protocol == "causal_model" and market.warmup_rows == 0:
        # Fixture override without a prefix: same in-window split qualify always used.
        # Production never reaches this branch (the gateway requires the prefix).
        min_train = max(
            int(contract.label_horizon_bars) + int(contract.execution_delay_bars) + int(contract.embargo_bars) + 4,
            n_bars // 4,
        )
        execution_slice = slice(min_train, n_bars)

    tape = EvaluationKernel().run(
        mod,
        panel_df,
        ts,
        assets,
        contract,
        execution_slice,
        params,
        protocol=protocol,
        policy=policy_kw,
        # Qualification replays an already-recorded trial; for a legacy trial that is
        # the read-only historical replay the freeze permits.  The ACTIVE-hypothesis
        # refusal happened when the trial was created, not here.
        allow_legacy_replay=batch is None,
        legacy_isolated=False,
        refit_mode=refit_mode,
    )
    m = calculate_metrics_from_returns(
        tape.gross_returns, tape.net_returns, tape.turnover, market.bars_per_year
    )
    comparison = None
    artifacts = None
    if batch is not None:
        from warden.evaluation import panel_digest
        from warden.research_intent import benchmark_binding

        candidate, intent = prepared["binding"]
        clock = ts[execution_slice]
        root = reg.db_path.parent / "returns"
        benchmark = prepared["benchmark"]
        if benchmark is not None:
            spec, benchmark_mod, benchmark_protocol, benchmark_sha = benchmark
            comparator = EvaluationKernel().run(
                benchmark_mod, panel_df, ts, assets, contract, execution_slice, spec.parameters,
                protocol=benchmark_protocol, policy=policy_kw, allow_legacy_replay=False,
                legacy_isolated=False,
                refit_mode=governance.holdout_refit_mode if benchmark_protocol == "causal_model" else None)
            if comparator.val_slice != tape.val_slice or comparator.session != tape.session:
                raise ValueError("Frozen holdout comparison clocks differ")
            if benchmark_binding(runner.repo_root, intent["benchmark"]["spec_path"], cid) != intent["benchmark"]:
                raise ValueError("Frozen holdout benchmark code/config changed")
            comparison = incremental.evaluate_pair(
                root=root, run_id=run_id, candidate=candidate, intent=intent, contract=contract,
                panel_sha=panel_digest(panel_df), execution_policy=policy_kw,
                candidate_returns=tape.net_returns, benchmark_returns=comparator.net_returns,
                timestamps=clock, benchmark_causality={
                    "strategy_sha256": benchmark_sha, "protocol": benchmark_protocol,
                    "development_proof": "verified_rg06_pair",
                    "holdout_refit": comparator.model_refit,
                }, phase="holdout", holdout_sha256=holdout_res.sha256)
        else:
            path = root / cid / ".holdout" / run_id / "candidate.parquet"
            from warden_evidence.selection_alignment import utc_ns

            sha = incremental._publish(path, root, tape.net_returns, utc_ns(clock))
            artifacts = {"candidate_path": str(path.resolve()), "candidate_sha256": sha}
        if code_closure(runner.repo_root, candidate.strategy_module)[0] != candidate.strategy_code_sha256:
            raise ValueError("Frozen holdout candidate code changed")

    h_net_sr = m["net_sharpe"]
    ratio = float(h_net_sr / max(float(trial.get("net_sharpe", 0.0)), 1e-6))
    # The numeric holdout conjunct is unchanged; the evidence tier decides only
    # whether clearing it may be called market promotion.
    absolute_pass = (h_net_sr > HOLDOUT_SR_FLOOR) and (ratio >= HOLDOUT_RATIO_FLOOR)
    numeric_pass = absolute_pass and (comparison is None or comparison["status"] == "PASS")
    promotion_capped = governance.is_promotion_capped
    promoted = bool(numeric_pass and not promotion_capped)
    if not numeric_pass:
        verdict = REJECTED_ON_HOLDOUT
    elif promotion_capped:
        verdict = PROXY_HOLDOUT_PASS
    else:
        verdict = PROMOTED
    if promotion_capped and verdict == PROMOTED:
        raise RuntimeError("evidence-tier cap violated: a capped tier may never be recorded as promoted")
    qual_id = f"QUAL-{run_id}"

    result = {"qual_id": qual_id, "run_id": run_id, "lineage_id": lineage_id, "holdout_net_sharpe": h_net_sr,
            "val_to_holdout_ratio": ratio, "promoted": promoted, "verdict": verdict,
            "holdout_override": holdout_override, "data_provenance": eligibility["data_provenance"],
            "evidence_tier": governance.evidence_tier,
            "promotion_capped": promotion_capped,
            "promotion_cap_reason": governance.promotion_cap_reason(),
            "contract_spec_version": governance.spec_version,
            "terminal_equity": tape.terminal_equity,
            "engine_fills_count": len(tape.backtest_result.fills),
            "engine_trades_count": len(tape.backtest_result.trades),
            # #97 observed schedule evidence: what the holdout actually executed.
            "holdout_refit_mode": refit_mode,
            "holdout_warmup_rows": int(market.warmup_rows),
            "holdout_eval_bars": int(market.eval_bars),
            "holdout_model_refit": tape.model_refit}
    if batch is not None:
        result.update(batch_id=batch_id, absolute_pass=absolute_pass, comparison=comparison,
                      artifacts=artifacts, holdout_sha256=holdout_res.sha256,
                      scientific_disposition=("QUALIFIED" if numeric_pass else
                          "INCONCLUSIVE" if absolute_pass and comparison is not None
                          and comparison["status"] == "INCONCLUSIVE" else "REJECTED"))
    with reg._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if batch is not None:
            exposure.finalize(conn, batch_id, result)
        conn.execute("INSERT INTO qualifications VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (qual_id, run_id, cid, hyp_id, lineage_id, m["gross_sharpe"], h_net_sr, m["max_drawdown_pct"], ratio, "HUMAN_OPERATOR", time.time()))
        conn.execute("UPDATE hypotheses SET status = ? WHERE hypothesis_id = ?", (verdict, hyp_id))
        updated = conn.execute("UPDATE holdout_claims SET state=?,finalized_at=? WHERE lineage_id=? AND run_id=? AND state='READ_STARTED'",
                     (verdict, time.time(), lineage_id, run_id)).rowcount
        if updated != 1:
            raise ValueError("Missing matching holdout read-start")
    return result
