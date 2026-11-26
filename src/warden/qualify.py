"""Human-only holdout qualification gate and one-shot lineage unsealing."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import yaml

from warden.codeid import code_closure
from warden.contracts import PROMOTED, PROXY_HOLDOUT_PASS, REJECTED_ON_HOLDOUT
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


def qualify_candidate(
    run_id: str,
    human_confirm_token: str,
    runner: ExperimentRunner,
    holdout_override_path: Path | None = None,
) -> dict[str, Any]:
    if os.environ.get("WARDEN_NON_INTERACTIVE") == "1":
        raise PermissionError("warden qualify is an interactive human-only gate.")
    if human_confirm_token != "YES_UNSEAL_HOLDOUT":
        raise PermissionError("Holdout unsealing requires confirmation token 'YES_UNSEAL_HOLDOUT'.")

    from warden.contract_source import is_sandbox_contract

    reg = runner.registry
    # Namespace refusal first: a compiled sandbox artifact (#117) never reaches a holdout
    # claim, an eligibility verdict or a 500-line replay.  Promotion into ``contracts/`` and
    # the human re-sign are a separate, human-only step; this gate stays shut until then.
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
    if not holdout_override:
        # The six-check audit is a required production admission, not merely
        # a suggested CLI step. Fail before opening or claiming the holdout.
        with reg._connect() as conn:
            approved = conn.execute(
                "SELECT verdict FROM audit_evidence WHERE run_id=? ORDER BY created_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        if not approved or approved[0] != "APPROVED":
            raise PermissionError("Production qualification requires an APPROVED six-check audit")
        proof = reg.causal_proof_for_run(run_id, runner.repo_root)
        if not proof["verified"]:
            raise PermissionError(f"Production qualification requires run-bound causal proof: {proof['reason']}")
    h_path = holdout_override_path or (runner.repo_root / contract.holdout_data_path)
    if not h_path.is_file():
        raise FileNotFoundError(f"Holdout dataset not found at '{h_path}'.")

    # Critical ordering: resolve_dataset hashes the entire holdout file.
    # Therefore even provenance verification must happen only after the
    # exclusive claim and durable read-start record have committed.
    try:
        reg.reserve_holdout_lineage(lineage_id, run_id)
    except PermissionError as exc:
        raise HoldoutAlreadyUnsealedError(str(exc)) from exc
    reg.mark_holdout_read_started(lineage_id, run_id)
    holdout_res = None
    if not holdout_override:
        holdout_res = resolve_dataset(contract, runner.repo_root, role="holdout")
        if not holdout_res.eligible_market:
            raise ValueError(f"Holdout dataset for '{cid}' is not market-eligible: {holdout_res.reason}")

    raw_cfg = trial.get("config_json", "{}")
    cfg_data = yaml.safe_load(raw_cfg) if raw_cfg else {}
    cfg_dict = cfg_data if isinstance(cfg_data, dict) else {}
    params = cfg_dict.get("parameters", cfg_dict)

    policy_kw = extract_execution_policy(cfg_dict, params, trial.get("diagnostics_json"), contract=contract)

    mod = load_module_from_path(strat_path)
    protocol = detect_strategy_protocol(mod)
    effective_params = resolve_effective_params(mod, protocol, params)

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
    market = MarketDataGateway(runner.repo_root).load(
        contract,
        ROLE_HOLDOUT,
        effective_params.get("target_symbols"),
        resolution=holdout_res,
        holdout_override_path=holdout_override_path,
    )
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
        allow_legacy_replay=True,
        legacy_isolated=False,
        refit_mode=refit_mode,
    )
    m = calculate_metrics_from_returns(
        tape.gross_returns, tape.net_returns, tape.turnover, market.bars_per_year
    )

    h_net_sr = m["net_sharpe"]
    ratio = float(h_net_sr / max(float(trial.get("net_sharpe", 0.0)), 1e-6))
    # The numeric holdout conjunct is unchanged; the evidence tier decides only
    # whether clearing it may be called market promotion.
    numeric_pass = (h_net_sr > HOLDOUT_SR_FLOOR) and (ratio >= HOLDOUT_RATIO_FLOOR)
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

    with reg._connect() as conn:
        conn.execute("INSERT INTO qualifications VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (qual_id, run_id, cid, hyp_id, lineage_id, m["gross_sharpe"], h_net_sr, m["max_drawdown_pct"], ratio, "HUMAN_OPERATOR", time.time()))
        conn.execute("UPDATE hypotheses SET status = ? WHERE hypothesis_id = ?", (verdict, hyp_id))
        conn.execute("UPDATE holdout_claims SET state=?,finalized_at=? WHERE lineage_id=? AND run_id=? AND state='READ_STARTED'",
                     (verdict, time.time(), lineage_id, run_id))

    return {"qual_id": qual_id, "run_id": run_id, "lineage_id": lineage_id, "holdout_net_sharpe": h_net_sr,
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
