"""One execution kernel for every active strategy protocol.

``EvaluationKernel`` is the single place where an *active* strategy reaches
``ml4t.backtest.Engine``.  ``screen``, ``backtest`` (1x / 2x / +1 bar /
``param_grid``), the leakage proofs and ``qualify`` all call :meth:`run` and get
back an :class:`EvaluationTape` aligned to the gateway's ``eval_slice``, so the
protocol-branching trees that used to live in ``runner.py``, ``qualify.py`` and
``cli.py`` are gone: the kernel dispatches once, and its callers consume a tape.

The kernel also owns a per-instance memo of executions keyed by the exact panel
bytes, contract spec hash, parameter block and cost/latency setting.  An Engine
run is a pure function of that key, so reusing it inside one ``verify`` session
is evidence reuse, never a shortcut: a different panel (a truncated or shocked
prefix) always gets its own run.  ``engine_runs`` counts the real executions.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import polars as pl
from ml4t.backtest import BacktestResult

from warden import engine_sim
from warden.contracts import EvaluationContract, canonical_json
from warden.engine_sim import SessionPolicy, extract_execution_policy


def panel_digest(panel_df: pl.DataFrame) -> str:
    """Content digest of a panel (schema + rows), stable across frame identity."""
    hasher = hashlib.sha256()
    hasher.update(str(panel_df.schema).encode())
    hasher.update(str(panel_df.height).encode())
    for column in panel_df.columns:
        if panel_df[column].dtype.is_numeric():
            values = panel_df[column].to_numpy()
            hasher.update(np.ascontiguousarray(values, dtype=np.float64).tobytes())
        else:
            hasher.update("\x1f".join(map(str, panel_df[column].to_list())).encode())
    return hasher.hexdigest()


def policy_payload(policy: dict[str, Any] | None) -> str:
    return canonical_json(policy or {})


@dataclass(frozen=True)
class EvaluationTape:
    """Executed strategy output aligned to one ``eval_slice``.

    ``positions`` are the weights the Engine actually held on each evaluation bar;
    ``target_positions`` are the raw targets the strategy asked for (``None`` when
    the strategy chose its own orders).  ``session`` records the explicit session
    decision for this feed, so no reader has to guess whether daily-session
    semantics were carried.
    """

    protocol: str
    val_slice: slice
    positions: np.ndarray
    gross_returns: np.ndarray
    net_returns: np.ndarray
    turnover: np.ndarray
    backtest_result: BacktestResult
    terminal_equity: float
    session: SessionPolicy
    funding_events: int
    engine_runs: int = 1
    target_positions: np.ndarray | None = None
    predictions: pl.DataFrame | None = None
    forward_returns: pl.DataFrame | None = None
    ic_series: Any = None
    legacy_output: dict[str, Any] | None = None
    #: Observed model refit schedule evidence (``mode``/``fits``/``folds``/
    #: ``covers_window``); ``None`` for any non-model protocol.
    model_refit: dict[str, Any] | None = None
    #: Evaluator-recorded fold-isolation facts for a ``causal_model`` execution.  Filled
    #: by the kernel from what the folds actually did, never by the strategy.
    fold_isolation: dict[str, Any] | None = None

    @property
    def session_col_used(self) -> str | None:
        return self.session.session_col

    @property
    def session_mode(self) -> str:
        return self.session.mode

    @property
    def session_reason(self) -> str:
        return self.session.reason

    def strategy_output(self) -> dict[str, Any]:
        """Legacy-shaped payload for gates that consume ``run_causal_model_strategy``.

        A legacy replay's own mapping is carried through unchanged (it may expose
        ``ic_series`` or ``signal_delay_bars``); positions always come from the tape
        the Engine actually executed.
        """
        payload: dict[str, Any] = dict(self.legacy_output or {})
        if self.target_positions is not None:
            payload["positions"] = self.target_positions
        elif "positions" not in payload:
            payload["positions"] = self.positions
        if self.predictions is not None:
            payload["predictions"] = self.predictions
        if self.forward_returns is not None:
            payload["forward_returns"] = self.forward_returns
        if self.ic_series is not None:
            payload["ic_series"] = self.ic_series
        if self.fold_isolation is not None:
            payload["fold_isolation"] = self.fold_isolation
        return payload


@dataclass
class _TapeCache:
    """Memo tables for one kernel instance (one evaluation session)."""

    engine: dict[tuple, EvaluationTape] = field(default_factory=dict)
    targets: dict[tuple, dict[str, Any]] = field(default_factory=dict)


class EvaluationKernel:
    """Single dispatch point from an active strategy protocol to an Engine tape."""

    def __init__(self) -> None:
        self._cache = _TapeCache()
        self.engine_runs = 0

    def __repr__(self) -> str:
        # Deliberately stateless: a kernel handle can end up inside a feature-cache
        # identity (which strings its inputs), and a memory address or run counter
        # there would make two identical evaluations hash differently.
        return "<EvaluationKernel>"

    @staticmethod
    def policy_for(contract: EvaluationContract) -> dict[str, Any]:
        """The one canonical execution policy for a contract (no caller overrides)."""
        return extract_execution_policy(contract=contract)

    # -- keys -----------------------------------------------------------------
    def _target_key(
        self,
        protocol: str,
        panel_df: pl.DataFrame,
        contract: EvaluationContract,
        val_slice: slice,
        params: dict[str, Any],
    ) -> tuple:
        start, stop = _slice_bounds(val_slice)
        return (
            protocol,
            panel_digest(panel_df),
            contract.spec_hash(),
            canonical_json(params),
            start,
            stop,
        )

    def _engine_key(
        self,
        protocol: str,
        panel_df: pl.DataFrame,
        contract: EvaluationContract,
        val_slice: slice,
        params: dict[str, Any],
        policy: dict[str, Any],
        cost_multiplier: float,
        delay_offset: int,
    ) -> tuple:
        return self._target_key(protocol, panel_df, contract, val_slice, params) + (
            policy_payload(policy),
            round(float(cost_multiplier), 12),
            int(delay_offset),
        )

    # -- public ---------------------------------------------------------------
    def run(
        self,
        mod: Any,
        panel_df: pl.DataFrame,
        ts: list[Any],
        assets: list[str],
        contract: EvaluationContract,
        val_slice: slice,
        params: dict[str, Any] | None = None,
        *,
        protocol: str | None = None,
        policy: dict[str, Any] | None = None,
        cost_multiplier: float = 1.0,
        delay_offset: int = 0,
        allow_legacy_replay: bool = True,
        legacy_isolated: bool = True,
        seed: int = 42,
        refit_mode: str | None = None,
    ) -> EvaluationTape:
        """Execute one strategy through the kernel.

        ``protocol`` defaults to the protocol the module itself implements, so a
        caller never re-derives the dispatch.  ``policy`` is the Warden-recorded
        execution policy; absent it, contract-owned defaults are used.
        ``refit_mode`` is the contract-declared holdout refit schedule; only a
        holdout qualification passes one, so a validation run keeps the schedule
        it always had.
        """
        from warden.protocols import detect_strategy_protocol, resolve_protocol_target

        params = dict(params or {})
        resolved = protocol or detect_strategy_protocol(mod)
        effective_policy = (
            dict(policy) if policy is not None else self.policy_for(contract)
        )
        if resolved == "causal_rules":
            strategy = resolve_protocol_target(mod).configure_strategy(contract, params)
            return self._run_rules(
                panel_df, strategy, ts, assets, contract, val_slice, params,
                effective_policy, cost_multiplier, delay_offset,
            )
        if resolved == "causal_model":
            targets = self._model_targets(
                mod, panel_df, ts, assets, contract, val_slice, params, seed, refit_mode
            )
            tape = self._simulate(
                panel_df, targets["positions"], ts, assets, contract, val_slice,
                params, effective_policy, cost_multiplier, delay_offset,
                protocol=resolved,
                predictions=targets["predictions"],
                forward_returns=targets["forward_returns"],
                ic_series=targets["ic_series"],
                model_refit=targets["model_refit"],
                model_refit_key=str(refit_mode or ""),
            )
            fold_isolation = targets.get("fold_isolation")
            if fold_isolation is not None:
                tape = _replace_extra(tape, fold_isolation=fold_isolation)
            return tape
        if resolved == "legacy":
            return self._run_legacy(
                mod, panel_df, ts, assets, contract, val_slice, params,
                effective_policy, cost_multiplier, delay_offset,
                allow_legacy_replay=allow_legacy_replay,
                isolated=legacy_isolated,
            )
        raise ValueError(f"Unsupported strategy protocol {resolved!r} for EvaluationKernel")

    def run_rules(
        self,
        mod: Any,
        panel_df: pl.DataFrame,
        ts: list[Any],
        assets: list[str],
        contract: EvaluationContract,
        val_slice: slice,
        params: dict[str, Any] | None = None,
        *,
        policy: dict[str, Any] | None = None,
        cost_multiplier: float = 1.0,
        delay_offset: int = 0,
    ) -> EvaluationTape:
        """Execute a ``CausalRulesStrategy`` (explicit entry point for proofs)."""
        from warden.protocols import resolve_protocol_target

        params = dict(params or {})
        effective_policy = (
            dict(policy) if policy is not None else self.policy_for(contract)
        )
        strategy = resolve_protocol_target(mod).configure_strategy(contract, params)
        return self._run_rules(
            panel_df, strategy, ts, assets, contract, val_slice, params,
            effective_policy, cost_multiplier, delay_offset,
        )

    def tape_for(
        self,
        mod: Any,
        panel_df: pl.DataFrame,
        ts: list[Any],
        assets: list[str],
        contract: EvaluationContract,
        val_slice: slice,
        params: dict[str, Any] | None = None,
        *,
        policy: dict[str, Any] | None = None,
    ) -> EvaluationTape:
        """Return the 1x-validated tape for this exact panel without a second run."""
        return self.run(
            mod, panel_df, ts, assets, contract, val_slice, params, policy=policy
        )

    def run_targets(
        self,
        panel_df: pl.DataFrame,
        positions: Any,
        ts: list[Any],
        assets: list[str],
        contract: EvaluationContract,
        val_slice: slice,
        params: dict[str, Any] | None = None,
        *,
        protocol: str = "target_weight",
        policy: dict[str, Any] | None = None,
        cost_multiplier: float = 1.0,
        delay_offset: int = 0,
    ) -> EvaluationTape:
        """Execute a pre-computed target-weight matrix through the Engine."""
        return self._simulate(
            panel_df,
            positions,
            ts,
            assets,
            contract,
            val_slice,
            dict(params or {}),
            dict(policy) if policy is not None else self.policy_for(contract),
            cost_multiplier,
            delay_offset,
            protocol=protocol,
        )

    def reprice(
        self,
        tape: EvaluationTape,
        mod: Any,
        panel_df: pl.DataFrame,
        ts: list[Any],
        assets: list[str],
        contract: EvaluationContract,
        val_slice: slice,
        params: dict[str, Any] | None = None,
        *,
        policy: dict[str, Any] | None = None,
        cost_multiplier: float = 1.0,
        delay_offset: int = 0,
    ) -> EvaluationTape:
        """Re-run the same strategy under a cost multiplier or extra latency bar.

        A rules strategy chooses its own orders, so it must see the stressed
        execution and is executed again.  A model or legacy strategy yields target
        weights that do not depend on the cost model, so its recorded targets are
        re-simulated — exactly the pre-kernel behaviour, with the model fit memoised.
        """
        if tape.protocol == "causal_rules":
            return self.run(
                mod,
                panel_df,
                ts,
                assets,
                contract,
                val_slice,
                params,
                protocol="causal_rules",
                policy=policy,
                cost_multiplier=cost_multiplier,
                delay_offset=delay_offset,
            )
        targets = tape.target_positions
        if targets is None:
            raise ValueError(
                f"{tape.protocol} tape has no recorded target weights to re-simulate"
            )
        repriced = self.run_targets(
            panel_df,
            targets,
            ts,
            assets,
            contract,
            val_slice,
            params,
            protocol=tape.protocol,
            policy=policy,
            cost_multiplier=cost_multiplier,
            delay_offset=delay_offset,
        )
        if tape.model_refit is not None:
            # Stress re-simulation cannot re-derive the fit schedule; carry the observed
            # one instead of presenting a stressed tape with no schedule evidence.
            repriced = _replace_extra(repriced, model_refit=tape.model_refit)
        return repriced

    # -- protocol paths -------------------------------------------------------
    def _run_rules(
        self,
        panel_df: pl.DataFrame,
        strategy: Any,
        ts: list[Any],
        assets: list[str],
        contract: EvaluationContract,
        val_slice: slice,
        params: dict[str, Any],
        policy: dict[str, Any],
        cost_multiplier: float,
        delay_offset: int,
    ) -> EvaluationTape:
        key = self._engine_key(
            "causal_rules", panel_df, contract, val_slice, params, policy,
            cost_multiplier, delay_offset,
        )
        cached = self._cache.engine.get(key)
        if cached is not None:
            return cached
        sim = engine_sim.run_engine_strategy(
            panel_df,
            strategy,
            ts,
            assets,
            contract,
            val_slice,
            cost_multiplier=cost_multiplier,
            delay_offset=delay_offset,
            **policy,
        )
        self.engine_runs += 1
        tape = _tape_from_sim(
            "causal_rules", sim, val_slice, engine_runs=self.engine_runs
        )
        self._cache.engine[key] = tape
        return tape

    def _model_targets(
        self,
        mod: Any,
        panel_df: pl.DataFrame,
        ts: list[Any],
        assets: list[str],
        contract: EvaluationContract,
        val_slice: slice,
        params: dict[str, Any],
        seed: int,
        refit_mode: str | None = None,
    ) -> dict[str, Any]:
        # ``refit_mode`` is part of the identity: a validation walk-forward must never
        # be served as a holdout refit (or vice versa) on identical panel bytes.
        key = self._target_key("causal_model", panel_df, contract, val_slice, params) + (
            int(seed),
            str(refit_mode or ""),
        )
        cached = self._cache.targets.get(key)
        if cached is not None:
            return cached
        from warden.protocols import run_causal_model_strategy

        out = run_causal_model_strategy(
            mod, panel_df, contract, ts, assets, val_slice, params, seed=seed,
            refit_mode=refit_mode,
        )
        targets = {
            "positions": np.asarray(out["positions"], dtype=float),
            "predictions": out["predictions"],
            "forward_returns": out["forward_returns"],
            "ic_series": out["ic_series"],
            "model_refit": out.get("refit"),
            "fold_isolation": out.get("fold_isolation"),
        }
        self._cache.targets[key] = targets
        return targets

    def _run_legacy(
        self,
        mod: Any,
        panel_df: pl.DataFrame,
        ts: list[Any],
        assets: list[str],
        contract: EvaluationContract,
        val_slice: slice,
        params: dict[str, Any],
        policy: dict[str, Any],
        cost_multiplier: float,
        delay_offset: int,
        *,
        allow_legacy_replay: bool,
        isolated: bool,
    ) -> EvaluationTape:
        from pathlib import Path

        from warden.legacy_replay import (
            run_generate_positions_in_process,
            run_generate_positions_isolated,
        )

        key = self._engine_key(
            "legacy", panel_df, contract, val_slice, params, policy,
            cost_multiplier, delay_offset,
        ) + (bool(isolated),)
        cached = self._cache.engine.get(key)
        if cached is not None:
            return cached
        if isolated:
            module_path = getattr(mod, "__file__", None)
            if not module_path:
                raise ValueError("Legacy replay requires a strategy module path")
            out = run_generate_positions_isolated(
                Path(module_path), panel_df, contract, params,
                allow_legacy_replay=allow_legacy_replay,
            )
        else:
            out = run_generate_positions_in_process(
                mod, panel_df, contract, params, allow_legacy_replay=allow_legacy_replay
            )
        tape = self._simulate(
            panel_df, out["positions"], ts, assets, contract, val_slice,
            params, policy, cost_multiplier, delay_offset, protocol="legacy",
            legacy_output=out,
        )
        self._cache.engine[key] = tape
        return tape

    def _simulate(
        self,
        panel_df: pl.DataFrame,
        positions: Any,
        ts: list[Any],
        assets: list[str],
        contract: EvaluationContract,
        val_slice: slice,
        params: dict[str, Any],
        policy: dict[str, Any],
        cost_multiplier: float,
        delay_offset: int,
        *,
        protocol: str,
        predictions: pl.DataFrame | None = None,
        forward_returns: pl.DataFrame | None = None,
        ic_series: Any = None,
        legacy_output: dict[str, Any] | None = None,
        model_refit: dict[str, Any] | None = None,
        model_refit_key: str = "",
    ) -> EvaluationTape:
        raw_positions = np.asarray(positions, dtype=float)
        key = self._engine_key(
            protocol, panel_df, contract, val_slice, params, policy,
            cost_multiplier, delay_offset,
        ) + (
            hashlib.sha256(np.ascontiguousarray(raw_positions, dtype=np.float64).tobytes()).hexdigest(),
            # A holdout refit and the validation walk-forward can produce identical
            # positions; their tapes still carry different schedule evidence.
            model_refit_key,
        )
        cached = self._cache.engine.get(key)
        if cached is not None:
            return cached
        sim = engine_sim.run_engine_simulation(
            panel_df,
            raw_positions,
            ts,
            assets,
            contract,
            val_slice,
            cost_multiplier=cost_multiplier,
            delay_offset=delay_offset,
            **policy,
        )
        self.engine_runs += 1
        tape = _tape_from_sim(protocol, sim, val_slice, engine_runs=self.engine_runs)
        tape = _replace_targets(
            tape, raw_positions, predictions, forward_returns, ic_series, model_refit
        )
        if legacy_output is not None:
            tape = _replace_extra(tape, legacy_output=legacy_output)
        self._cache.engine[key] = tape
        return tape


def _slice_bounds(val_slice: slice) -> tuple[int, int]:
    start = int(val_slice.start or 0)
    stop = int(val_slice.stop) if val_slice.stop is not None else -1
    return start, stop


def normalise_panel(panel_df: pl.DataFrame) -> pl.DataFrame:
    """Rename ``symbol`` to ``asset`` and sort, the one shape proofs are keyed on."""
    df = (
        panel_df.rename({"symbol": "asset"})
        if "symbol" in panel_df.columns and "asset" not in panel_df.columns
        else panel_df
    )
    return df.sort("timestamp", "asset")


@dataclass(frozen=True)
class PrefixInvarianceProof:
    """Evidence that one exact execution is prefix-invariant at known checkpoints.

    Produced only by :func:`warden.leakage.verify_rules_strategy_prefix_invariance`.
    ``covers`` re-derives the identity from the live panel/contract/params, so a
    proof from a different panel, contract or parameter block never satisfies a
    later invariance gate.
    """

    protocol: str
    panel_sha256: str
    contract_spec_hash: str
    params_json: str
    policy_json: str
    checkpoints: tuple[str, ...]
    evidence: tuple[tuple[str, str], ...]
    engine_runs: int
    #: Code closure of the strategy whose execution was proved.  A proof is only
    #: reusable for the module that produced it: the panel/contract/params identity
    #: above says nothing about *which* strategy was executed on that panel.
    strategy_sha256: str = ""

    @property
    def cut_count(self) -> int:
        return len(self.checkpoints)

    def payload(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol,
            "panel_sha256": self.panel_sha256,
            "contract_spec_hash": self.contract_spec_hash,
            "params_json": self.params_json,
            "policy_json": self.policy_json,
            "checkpoints": list(self.checkpoints),
            "evidence": [list(item) for item in self.evidence],
            "strategy_sha256": self.strategy_sha256,
        }

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical_json(self.payload()).encode()).hexdigest()

    def covers(
        self,
        panel_df: pl.DataFrame,
        contract: EvaluationContract,
        params: dict[str, Any] | None = None,
        *,
        code_sha256: str | None = None,
    ) -> bool:
        """True only for the exact panel/contract/parameters this proof executed.

        The execution policy is contract-derived (``resolve_execution_limits`` owns it),
        so ``contract_spec_hash`` already pins it — comparing a caller-supplied policy
        dict here would only re-assert the same contract field.  ``code_sha256`` pins
        *what* was executed: without it a proof taken from one strategy would satisfy
        the gate for another on the same panel.
        """
        if self.cut_count < 2 or not self.evidence:
            return False
        if not self.strategy_sha256 or code_sha256 is None:
            return False
        if self.strategy_sha256 != str(code_sha256):
            return False
        if self.panel_sha256 != panel_digest(normalise_panel(panel_df)):
            return False
        if self.contract_spec_hash != contract.spec_hash():
            return False
        if params is not None and self.params_json != canonical_json(dict(params)):
            return False
        return all(len(digest) == 64 for _, digest in self.evidence)


#: Proofs the evaluator itself issued in this process, keyed by content digest.
#: The leakage gate's fast path is only ever honoured for a proof recorded here:
#: an attribute carried by a strategy-supplied object is a claim, and a claim is
#: not evidence.  Issuance is explicit (``register_prefix_proof``) so constructing
#: a look-alike proof cannot enrol it.
_ISSUED_PREFIX_PROOFS: dict[str, str] = {}
_MAX_ISSUED_PREFIX_PROOFS = 256


def register_prefix_proof(proof: PrefixInvarianceProof) -> PrefixInvarianceProof:
    """Enrol a kernel-issued proof so the leakage gate may reuse it."""
    if not isinstance(proof, PrefixInvarianceProof):  # pragma: no cover - defensive
        raise TypeError("only a PrefixInvarianceProof issued by the kernel can be enrolled")
    _ISSUED_PREFIX_PROOFS[proof.digest] = proof.protocol
    while len(_ISSUED_PREFIX_PROOFS) > _MAX_ISSUED_PREFIX_PROOFS:
        _ISSUED_PREFIX_PROOFS.pop(next(iter(_ISSUED_PREFIX_PROOFS)))
    return proof


def trusted_prefix_proof(
    pipeline: Any,
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
) -> PrefixInvarianceProof | None:
    """The only way a prefix proof may satisfy the leakage gate.

    Four conditions, all required: the object on the pipeline is a
    :class:`PrefixInvarianceProof` (not a duck-typed attribute), the pipeline is the
    evaluator's own :class:`~warden.protocols.RulesProofPipeline` (not a strategy
    module), the proof's digest is in the evaluator's issuance ledger, and it still
    covers this exact panel/contract/params/code closure.  Anything else returns
    ``None`` and the caller runs the full sampled check, so a strategy that attaches
    its own ``prefix_proof`` gains nothing.
    """
    from warden.protocols import RulesProofPipeline

    proof = getattr(pipeline, "prefix_proof", None)
    if not isinstance(proof, PrefixInvarianceProof):
        return None
    if not isinstance(pipeline, RulesProofPipeline):
        return None
    if _ISSUED_PREFIX_PROOFS.get(proof.digest) is None:
        return None
    if not proof.covers(
        panel_df,
        contract,
        getattr(pipeline, "params", None),
        code_sha256=getattr(pipeline, "code_sha256", None),
    ):
        return None
    return proof


def checkpoint_label(stamp: Any) -> str:
    return stamp.isoformat() if hasattr(stamp, "isoformat") else str(stamp)


def evidence_digest(*frames: Any) -> str:
    """Digest of the compared values at one checkpoint (order-preserving)."""
    hasher = hashlib.sha256()
    for frame in frames:
        if frame is None:
            hasher.update(b"\x00")
        elif isinstance(frame, np.ndarray):
            hasher.update(np.ascontiguousarray(frame, dtype=np.float64).tobytes())
        else:
            hasher.update(repr(frame).encode())
        hasher.update(b"\x1f")
    return hasher.hexdigest()


def _tape_from_sim(
    protocol: str,
    sim: Any,
    val_slice: slice,
    *,
    engine_runs: int,
) -> EvaluationTape:
    mode = getattr(sim, "session_mode", None)
    if mode is None:
        session = SessionPolicy(
            mode="venue_calendar",
            session_col=getattr(sim, "session_col_used", None),
            reason="session policy not reported by this simulator",
        )
    else:
        session = SessionPolicy(
            mode=mode,
            session_col=getattr(sim, "session_col_used", None),
            reason=getattr(sim, "session_reason", ""),
        )
    positions = np.asarray(sim.positions, dtype=float)
    zeros = np.zeros(positions.shape[0], dtype=float)
    return EvaluationTape(
        protocol=protocol,
        val_slice=val_slice,
        positions=positions,
        # A duck-typed simulator (a test double, or a caller that only needs the tape)
        # may expose positions alone; the alignment series then stay empty rather than
        # inventing returns that were never simulated.
        gross_returns=np.asarray(getattr(sim, "gross_returns", zeros), dtype=float),
        net_returns=np.asarray(getattr(sim, "net_returns", zeros), dtype=float),
        turnover=np.asarray(getattr(sim, "turnover", zeros), dtype=float),
        backtest_result=sim.backtest_result,
        terminal_equity=float(getattr(sim, "terminal_equity", 0.0)),
        session=session,
        funding_events=int(getattr(sim, "funding_events", 0)),
        engine_runs=engine_runs,
    )


def _replace_targets(
    tape: EvaluationTape,
    target_positions: np.ndarray | None,
    predictions: pl.DataFrame | None,
    forward_returns: pl.DataFrame | None,
    ic_series: Any,
    model_refit: dict[str, Any] | None = None,
) -> EvaluationTape:
    from dataclasses import replace

    return replace(
        tape,
        target_positions=target_positions,
        predictions=predictions,
        forward_returns=forward_returns,
        ic_series=ic_series,
        model_refit=model_refit,
    )


def _replace_extra(tape: EvaluationTape, **kwargs: Any) -> EvaluationTape:
    from dataclasses import replace

    return replace(tape, **kwargs)
