"""Ticket #100 — timestamped return artifacts, pre-reservation alignment, group DSR scope.

What this file exists to guard:

* new return Parquets carry ``timestamp`` (UTC) plus ``net_return`` and legacy
  single-column Parquets still load — a legacy row is debt, never a fabricated clock;
* a narrowed ``target_symbols`` run aligns against the wider history on the candidate's
  own window instead of raising, mixed frequencies fall back to UTC-day compounding, and
  a genuinely incompatible grid fails *before* ``trials_used`` moves;
* ``prior_search_floor`` / ``selection_history`` are scoped by ``selection_group_id`` plus
  ``inherits_selection_from``, while a legacy contract keeps its historical family floor
  and the stored floors stay monotone.

Every fixture here is synthetic: a scratch SQLite file under ``tmp_path``, an isolated
returns tree, no live registry, no market data and no protected holdout.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from warden.contracts import ContractPolicy, EvaluationContract, ExperimentResult, HypothesisSpec
from warden.registry import (
    SCHEMA_VERSION,
    ResearchRegistry,
    SelectionHistoryError,
)
from warden_evidence.return_artifacts import (
    RETURN_COLUMN,
    TIMESTAMP_COLUMN,
    ReturnArtifactError,
    read_return_artifact,
    read_return_stream,
)
from warden_evidence.selection_alignment import (
    AlignmentError,
    ReturnStream,
    align_streams,
    compound_by_utc_day,
    utc_ns,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
START = datetime(2025, 3, 3, tzinfo=UTC)


# --------------------------------------------------------------------------- fixtures


def _contract(
    contract_id: str,
    *,
    asset_class: str = "multi_asset",
    frequency: str = "1h",
    group: str = "",
    inherit: str = "",
    spec_version: int = 1,
) -> EvaluationContract:
    policy = None
    if spec_version == 2:
        policy = ContractPolicy(
            spec_version=2, selection_group_id=group, inherits_selection_from=inherit)
    return EvaluationContract(
        contract_id=contract_id,
        asset_class=asset_class,
        frequency=frequency,
        bars_per_year=8760,
        exploratory_data_path=f"data/exploratory/{contract_id}.parquet",
        holdout_data_path=f"data/holdout/{contract_id}.parquet",
        train_start=datetime(2024, 1, 1, tzinfo=UTC),
        val_start=datetime(2025, 1, 1, tzinfo=UTC),
        val_end=datetime(2025, 2, 1, tzinfo=UTC),
        holdout_start=datetime(2025, 2, 1, tzinfo=UTC),
        holdout_end=datetime(2025, 3, 1, tzinfo=UTC),
        n_folds=2,
        label_horizon_bars=2,
        embargo_bars=1,
        execution_delay_bars=1,
        taker_fee_bps=1.0,
        slippage_bps=1.0,
        data_snapshot_sha256="0" * 64,
        data_provenance="verified_market",
        policy=policy,
    )


def _hypothesis(hypothesis_id: str, contract_id: str, module: str) -> HypothesisSpec:
    return HypothesisSpec(
        hypothesis_id,
        contract_id,
        "T14 selection scope fixture",
        "rules_ts",
        "Mechanism explanation of twenty chars",
        "Friction explanation of twenty chars",
        8,
        "Reject net Sharpe below one",
        strategy_module=module,
    )


def _result(run_id: str, hypothesis_id: str, contract_id: str, *, module: str,
            keff: float = 1.0) -> ExperimentResult:
    return ExperimentResult(
        run_id=run_id,
        hypothesis_id=hypothesis_id,
        contract_id=contract_id,
        strategy_name=Path(module).stem,
        strategy_family="rules_ts",
        config_hash="cfg",
        stage_reached="STAGE_3_STRESS",
        verdict="CANDIDATE_FOR_QUALIFICATION",
        gross_sharpe=1.0,
        net_sharpe=1.0,
        deflated_sharpe_prob=0.99,
        expected_max_sharpe_noise=0.1,
        total_trials_n=1,
        effective_trials_keff=keff,
        mean_ic=None,
        ic_hac_tstat=None,
        hit_rate=0.55,
        profit_factor=1.3,
        max_drawdown_pct=4.0,
        annualized_turnover=25.0,
        breakeven_cost_bps=12.0,
        stress_2x_cost_sharpe=0.9,
        param_neighbor_stability=None,
        notes="fixture",
        data_snapshot_sha256="0" * 64,
        strategy_module=module,
        model_type="rules",
        data_provenance="verified_market",
    )


def _grid(count: int, *, step_minutes: int = 60, start: datetime = START) -> list[datetime]:
    return [start + timedelta(minutes=step_minutes * index) for index in range(count)]


def _returns(count: int, seed: int = 7) -> np.ndarray:
    return np.random.default_rng(seed).normal(0.0, 0.01, count)


def _registry(tmp_path: Path) -> ResearchRegistry:
    return ResearchRegistry(tmp_path / "registry" / "registry.db")


def _seed(registry: ResearchRegistry, contract: EvaluationContract, *, hypothesis_id: str,
          module: str) -> None:
    registry.register_contract(contract)
    registry.register_hypothesis(_hypothesis(hypothesis_id, contract.contract_id, module))


def _record(registry: ResearchRegistry, *, run_id: str, hypothesis_id: str, contract_id: str,
            module: str, returns, timestamps=None, keff: float = 1.0) -> None:
    registry.record_trial(
        _result(run_id, hypothesis_id, contract_id, module=module, keff=keff),
        {},
        returns,
        timestamps=timestamps,
    )


# --------------------------------------------------------------- 1. artifact generation


def test_new_artifact_is_timestamped_and_legacy_artifact_still_reads(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    contract = _contract("T14_TS_V2", group="T14-GRP-A", spec_version=2)
    _seed(registry, contract, hypothesis_id="HYP-TS", module="strategies/candidates/t14_a.py")
    stamps = _grid(24)
    values = _returns(24)
    _record(registry, run_id="RUN-TS-01", hypothesis_id="HYP-TS", contract_id=contract.contract_id,
            module="strategies/candidates/t14_a.py", returns=values, timestamps=stamps)

    root = registry.db_path.parent / "returns"
    with registry._connect() as conn:
        path = Path(conn.execute(
            "SELECT returns_parquet_path FROM trials WHERE run_id='RUN-TS-01'").fetchone()[0])
    schema = pl.read_parquet(path).schema
    assert schema[TIMESTAMP_COLUMN] == pl.Datetime("us", "UTC")
    assert schema[RETURN_COLUMN] == pl.Float64

    stream = read_return_stream(path, root)
    assert stream.legacy is False
    np.testing.assert_array_equal(stream.timestamps_ns, utc_ns(stamps))
    np.testing.assert_allclose(stream.net_returns, values)
    # The pre-#100 reader entry point keeps working on the new generation.
    np.testing.assert_allclose(read_return_artifact(path, root).to_numpy(), values)

    legacy_path = root / contract.contract_id / "RUN-LEGACY.parquet"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({RETURN_COLUMN: pl.Series(values, dtype=pl.Float64)}).write_parquet(legacy_path)
    legacy = read_return_stream(legacy_path, root)
    assert legacy.legacy is True and legacy.timestamps_ns is None
    np.testing.assert_allclose(legacy.net_returns, values)

    foreign = tmp_path / "outside.parquet"
    pl.DataFrame({RETURN_COLUMN: [0.0]}).write_parquet(foreign)
    with pytest.raises(ReturnArtifactError, match="not under the registry returns tree"):
        read_return_stream(foreign, root)


def test_naive_and_misaligned_timestamps_are_refused_before_a_slot_is_reserved(
        tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    contract = _contract("T14_BAD_TS", group="T14-GRP-A", spec_version=2)
    _seed(registry, contract, hypothesis_id="HYP-BAD", module="strategies/candidates/t14_bad.py")
    keyword = {"run_id": "RUN-BAD-01", "hypothesis_id": "HYP-BAD",
               "contract_id": contract.contract_id,
               "module": "strategies/candidates/t14_bad.py", "returns": _returns(10)}

    with pytest.raises(ValueError, match="naive timestamps are refused"):
        _record(registry, **keyword,
                timestamps=[datetime(2025, 3, 3) + timedelta(hours=i) for i in range(10)])
    with pytest.raises(ValueError, match="10 returns but 9 timestamps"):
        _record(registry, **keyword, timestamps=_grid(9))
    with pytest.raises(ValueError, match="strictly increasing"):
        _record(registry, **keyword, timestamps=[START, START, *(_grid(8, start=START + timedelta(hours=1)))])

    with registry._connect() as conn:
        assert conn.execute("SELECT trials_used FROM hypotheses WHERE hypothesis_id='HYP-BAD'"
                            ).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM attempt_receipts").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0] == 0
    assert not (registry.db_path.parent / "returns" / contract.contract_id).exists()


# ------------------------------------------------- 2. alignment without a spurious raise


def _multi_exchange_history(tmp_path: Path):
    """One contract with 24h/30d history and a candidate narrowed to an 8h session."""
    registry = _registry(tmp_path)
    contract = _contract("T14_DAX", group="T14-GRP-A", spec_version=2)
    _seed(registry, contract, hypothesis_id="HYP-DAX", module="strategies/candidates/t14_dax.py")
    full = _grid(24 * 30)
    _record(registry, run_id="RUN-DAX-01", hypothesis_id="HYP-DAX",
            contract_id=contract.contract_id, module="strategies/candidates/t14_dax.py",
            returns=_returns(len(full), seed=3), timestamps=full)
    session = [stamp for stamp in full if 8 <= stamp.hour < 17]
    candidate = _returns(len(session), seed=11)
    return registry, contract, session, candidate, full


def test_single_symbol_session_aligns_with_wider_history_without_raising(tmp_path: Path) -> None:
    registry, contract, session, candidate, full = _multi_exchange_history(tmp_path)
    hist = registry.selection_history(
        contract.contract_id, len(session), timestamps=session, candidate_returns=candidate,
        contract=contract)
    # A 24h participant cannot be compared bar-by-bar against an 8h session: the wide
    # stream keeps only a third of its own bars, so the panel compounds to UTC days.  It
    # aligns cleanly and nothing is dropped -- no raise, no padding, no truncation.
    assert hist["alignment"]["frequency"] == "utc_day"
    assert len(hist["streams"]) == 1
    days = sorted({stamp.date() for stamp in session})
    assert len(hist["streams"][0]) == len(days)
    assert hist["alignment"]["aligned_bars"] == len(days)
    # Day-by-day the two streams agree completely; it is the *bar-level* retention of the
    # wide stream (a third) that made the panel compound instead of aligning bar by bar.
    assert hist["alignment"]["overlap_fraction"] == pytest.approx(1.0)
    assert hist["alignment"]["periods_per_year"] == pytest.approx(365.25, rel=0.02)
    assert hist["unvectorized_attempts"] == 0
    assert len(hist["candidate_returns"]) == len(days)
    # The candidate is compounded onto the shared day index, never positionally sliced.
    first_day = [float(r) for r, stamp in zip(candidate, session) if stamp.date() == days[0]]
    assert hist["candidate_returns"][0] == pytest.approx(
        float(np.prod(1.0 + np.asarray(first_day)) - 1.0), rel=1e-12)
    # The pre-reservation guard agrees, and it is still a pure read.
    preflight = registry.preflight_selection_alignment(
        contract, session, candidate_returns=candidate)
    assert preflight["frequency"] == "utc_day"
    assert preflight["aligned_bars"] == len(days)
    assert preflight["scope"] == [contract.contract_id]
    # ... and the *reciprocal* trial picks the same basis: the DSR panel cannot depend on
    # which side of the same pair happens to be the candidate.
    wide_returns = _returns(len(full), seed=3)
    narrow_stream = ReturnStream("RUN-NARROW", candidate, utc_ns(session), required=True)
    reverse = align_streams(
        [narrow_stream], wide_returns, full, expected_len=len(full))
    assert reverse.frequency == hist["alignment"]["frequency"]
    assert reverse.n_bars == hist["alignment"]["aligned_bars"]
    assert reverse.periods_per_year == pytest.approx(
        hist["alignment"]["periods_per_year"], rel=1e-12)


def test_mixed_frequency_history_compounds_utc_daily(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    contract = _contract("T14_MIX", group="T14-GRP-A", spec_version=2)
    _seed(registry, contract, hypothesis_id="HYP-MIX", module="strategies/candidates/t14_mix.py")
    daily = _grid(30, step_minutes=24 * 60)
    _record(registry, run_id="RUN-MIX-D", hypothesis_id="HYP-MIX",
            contract_id=contract.contract_id, module="strategies/candidates/t14_mix.py",
            returns=_returns(30, seed=5), timestamps=daily)
    hourly = _grid(24 * 30)
    candidate = _returns(len(hourly), seed=13)

    hist = registry.selection_history(
        contract.contract_id, len(hourly), timestamps=hourly, candidate_returns=candidate,
        contract=contract)
    assert hist["alignment"]["frequency"] == "utc_day"
    assert len(hist["streams"][0]) == 30
    assert len(hist["candidate_returns"]) == 30
    expected_first_day = float(np.prod(1.0 + candidate[:24]) - 1.0)
    assert hist["candidate_returns"][0] == pytest.approx(expected_first_day, rel=1e-12)
    assert hist["alignment"]["periods_per_year"] == pytest.approx(30 / (30 / 365.25), rel=0.05)


def test_true_incompatibility_fails_before_the_trial_slot_is_consumed(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    contract = _contract("T14_FAR", group="T14-GRP-A", spec_version=2)
    _seed(registry, contract, hypothesis_id="HYP-FAR", module="strategies/candidates/t14_far.py")
    old = [stamp - timedelta(days=400) for stamp in _grid(48)]
    _record(registry, run_id="RUN-FAR-01", hypothesis_id="HYP-FAR",
            contract_id=contract.contract_id, module="strategies/candidates/t14_far.py",
            returns=_returns(48, seed=2), timestamps=old)
    candidate = _returns(48, seed=17)

    with registry._connect() as conn:
        before_trials = conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0]
        before_receipts = conn.execute("SELECT COUNT(*) FROM attempt_receipts").fetchone()[0]
        before_used = conn.execute(
            "SELECT trials_used FROM hypotheses WHERE hypothesis_id='HYP-FAR'").fetchone()[0]

    # The gateway refuses *outside* the reservation: preflight must raise, not reserve.
    with pytest.raises(SelectionHistoryError, match="Pre-reservation alignment refused"):
        registry.preflight_selection_alignment(contract, _grid(48), candidate_returns=candidate)

    with registry._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0] == before_trials
        assert conn.execute("SELECT COUNT(*) FROM attempt_receipts").fetchone()[0] == before_receipts
        assert conn.execute(
            "SELECT trials_used FROM hypotheses WHERE hypothesis_id='HYP-FAR'").fetchone()[0] == before_used
    # The same incompatibility is fatal during history assembly as well.
    with pytest.raises(SelectionHistoryError):
        registry.selection_history(contract.contract_id, 48, timestamps=_grid(48),
                                   candidate_returns=candidate, contract=contract)


# ------------------------------------------------ 3. legacy rows are debt, not inference


def test_legacy_stream_is_conservative_debt_on_a_timestamped_grid(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    contract = _contract("T14_LEGACY", group="T14-GRP-A", spec_version=2)
    _seed(registry, contract, hypothesis_id="HYP-LEG", module="strategies/candidates/t14_leg.py")
    _record(registry, run_id="RUN-LEG-01", hypothesis_id="HYP-LEG",
            contract_id=contract.contract_id, module="strategies/candidates/t14_leg.py",
            returns=_returns(100, seed=4))
    stamps = _grid(120)
    candidate = _returns(120, seed=19)

    hist = registry.selection_history(
        contract.contract_id, len(stamps), timestamps=stamps, candidate_returns=candidate,
        contract=contract)
    assert hist["streams"] == []
    assert hist["raw_attempt_count"] == 1
    assert hist["unvectorized_attempts"] == 1
    assert hist["alignment"]["legacy_excluded"] == 1
    legacy_reasons = [item["reason"] for item in hist["excluded"] if item.get("legacy")]
    assert any("without timestamps" in reason for reason in legacy_reasons)
    # No fabricated index: the aligned candidate keeps its own length and values.
    assert len(hist["candidate_returns"]) == 120


def test_equal_length_legacy_stream_keeps_the_positional_proof(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    contract = _contract("T14_LEGACY_EQ", group="T14-GRP-A", spec_version=2)
    _seed(registry, contract, hypothesis_id="HYP-LEGEQ",
          module="strategies/candidates/t14_legeq.py")
    prior = _returns(120, seed=23)
    _record(registry, run_id="RUN-LEGEQ-01", hypothesis_id="HYP-LEGEQ",
            contract_id=contract.contract_id, module="strategies/candidates/t14_legeq.py",
            returns=prior)
    stamps = _grid(120)
    candidate = _returns(120, seed=29)
    hist = registry.selection_history(
        contract.contract_id, 120, timestamps=stamps, candidate_returns=candidate, contract=contract)
    assert hist["alignment"]["frequency"] == "bar"
    assert len(hist["streams"]) == 1
    np.testing.assert_allclose(hist["streams"][0], prior)
    assert hist["unvectorized_attempts"] == 0


def test_legacy_only_history_still_refuses_a_padded_comparison(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    contract = _contract("T14_PURE_LEGACY", group="T14-GRP-A", spec_version=2)
    _seed(registry, contract, hypothesis_id="HYP-PL", module="strategies/candidates/t14_pl.py")
    for index in (1, 2):
        _record(registry, run_id=f"RUN-PL-0{index}", hypothesis_id="HYP-PL",
                contract_id=contract.contract_id, module="strategies/candidates/t14_pl.py",
                returns=_returns(100, seed=index))
    hist = registry.selection_history(contract.contract_id, 100)
    assert hist["alignment"]["frequency"] == "legacy_positional"
    assert len(hist["streams"]) == 2 and hist["unvectorized_attempts"] == 0
    with pytest.raises(SelectionHistoryError, match="refusing to pad"):
        registry.selection_history(contract.contract_id, 99)


# ------------------------------------------------------------- 4. group floor & scope


def test_group_floor_is_shared_inherited_and_still_monotone(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    group_a = _contract("T14_GRP_A", group="T14-GRP-A", spec_version=2)
    group_a_peer = _contract("T14_GRP_A_PEER", group="T14-GRP-A", spec_version=2)
    group_b = _contract("T14_GRP_B", group="T14-GRP-B", spec_version=2)
    inheritor = _contract("T14_GRP_B_INH", group="T14-GRP-B", inherit="T14_GRP_A",
                          spec_version=2)
    for contract in (group_a, group_a_peer, group_b, inheritor):
        registry.register_contract(contract)
    registry.update_search_floor(group_a.contract_id, 5.0, 0.04)

    floor, noise, family = registry.prior_search_floor(group_a_peer)
    assert (floor, noise, family) == (pytest.approx(5.0), pytest.approx(0.04), "T14-GRP-A")
    # A different group in the same asset_class/frequency does not inherit it.
    assert registry.prior_search_floor(group_b)[0] == pytest.approx(1.0)
    # Declared inheritance crosses groups and is reported in the scope.
    inherited_floor, inherited_noise, inherited_family = registry.prior_search_floor(inheritor)
    assert inherited_floor == pytest.approx(5.0) and inherited_noise == pytest.approx(0.04)
    assert inherited_family == "T14-GRP-B"
    scope = registry.selection_scope(inheritor)
    assert scope.group_id == "T14-GRP-B" and "T14_GRP_A" in scope.members
    assert scope.inherited == ("T14_GRP_A",)

    # The stored floor still cannot be relaxed.
    with registry._connect() as conn, pytest.raises(sqlite3.IntegrityError,
                                                   match="may not be relaxed"):
        conn.execute("UPDATE contract_search_state SET max_keff_gate=1.0 WHERE contract_id=?",
                     (group_a.contract_id,))
    assert registry.prior_search_floor(group_a_peer)[0] == pytest.approx(5.0)


def test_legacy_contract_keeps_the_family_floor(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    old = _contract("T14_OLD_A")
    later = _contract("T14_OLD_A_V2")
    registry.register_contract(old)
    registry.register_contract(later)
    registry.update_search_floor(old.contract_id, 3.5, 0.02)
    floor, noise, family = registry.prior_search_floor(later)
    assert (floor, noise, family) == (pytest.approx(3.5), pytest.approx(0.02),
                                      "multi_asset:1h")
    assert registry.selection_scope(later).members == (later.contract_id,)


def test_selection_history_scopes_peer_streams_to_the_group(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    source = _contract("T14_SRC", group="T14-GRP-S", spec_version=2)
    peer = _contract("T14_PEER", group="T14-GRP-S", spec_version=2)
    outside = _contract("T14_OUT", group="T14-GRP-O", spec_version=2)
    for contract in (source, peer, outside):
        registry.register_contract(contract)
    registry.register_hypothesis(
        _hypothesis("HYP-SRC", source.contract_id, "strategies/candidates/t14_src.py"))
    stamps = _grid(120)
    prior = _returns(120, seed=31)
    _record(registry, run_id="RUN-SRC-01", hypothesis_id="HYP-SRC",
            contract_id=source.contract_id, module="strategies/candidates/t14_src.py",
            returns=prior, timestamps=stamps)
    candidate = _returns(120, seed=37)

    peer_hist = registry.selection_history(peer.contract_id, 120, timestamps=stamps,
                                           candidate_returns=candidate, contract=peer)
    assert len(peer_hist["streams"]) == 1
    np.testing.assert_allclose(peer_hist["streams"][0], prior)
    assert peer_hist["raw_attempt_count"] == 1
    assert peer_hist["alignment"]["selection_group_id"] == "T14-GRP-S"
    assert set(peer_hist["alignment"]["scope"]) == {peer.contract_id, source.contract_id}

    outside_hist = registry.selection_history(outside.contract_id, 120, timestamps=stamps,
                                              candidate_returns=candidate, contract=outside)
    assert outside_hist["streams"] == []
    assert outside_hist["raw_attempt_count"] == 0


def test_group_governance_is_recorded_write_once(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    contract = _contract("T14_GOV", group="T14-GRP-G", spec_version=2)
    registry.register_contract(contract)
    registry.register_contract(contract)
    with registry._connect() as conn:
        rows = conn.execute("SELECT contract_id,selection_group_id,spec_version "
                            "FROM contract_governance").fetchall()
    assert [tuple(row) for row in rows] == [("T14_GOV", "T14-GRP-G", 2)]
    # A legacy contract records nothing at all: no scope is invented for it.
    legacy = _contract("T14_GOV_LEGACY")
    registry.register_contract(legacy)
    with registry._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM contract_governance WHERE contract_id=?",
                            (legacy.contract_id,)).fetchone()[0] == 0
    with pytest.raises(ValueError, match="immutable"):
        registry.register_contract(replace(contract, policy=ContractPolicy(
            spec_version=2, selection_group_id="T14-GRP-OTHER")))


# ------------------------------------------------------------- 5. alignment primitives


def test_naive_timestamps_and_negative_equity_are_refused() -> None:
    with pytest.raises(AlignmentError, match="naive"):
        utc_ns([datetime(2025, 1, 1), datetime(2025, 1, 2)])
    with pytest.raises(AlignmentError, match="strictly increasing"):
        utc_ns([START, START])
    with pytest.raises(AlignmentError, match="cannot be compounded"):
        compound_by_utc_day(utc_ns(_grid(2)), np.array([0.01, -1.0]))


def test_alignment_reports_exclusions_instead_of_dropping_them() -> None:
    stamps = _grid(24 * 17)
    streams = [
        ReturnStream("RUN-OK", _returns(len(stamps), seed=41), utc_ns(stamps), required=False),
        ReturnStream("RUN-FAR", _returns(len(stamps), seed=43),
                     utc_ns([stamp - timedelta(days=365) for stamp in stamps]), required=False),
        ReturnStream("RUN-LEGACY", _returns(len(stamps), seed=45), None,
                     legacy_positional=False),
    ]
    result = align_streams(streams, _returns(len(stamps), seed=47), stamps,
                           expected_len=len(stamps))
    assert result.stream_ids == ("RUN-OK",)
    excluded = {item.stream_id for item in result.excluded}
    assert excluded == {"RUN-FAR", "RUN-LEGACY"}
    assert result.legacy_excluded == 1
    # Twelve of the seventeen days keep a comparable bar grid; the outlier stream does not.
    assert result.frequency == "utc_day"
    assert result.n_bars == 17


def test_schema_version_advertises_the_additive_governance_table(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    assert registry.schema_version == SCHEMA_VERSION == 9  # AC-03 additive request mapping
    with registry._connect() as conn:
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == 9
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
            "AND name='contract_governance'").fetchone()[0] == 1
        # No new trigger: the migration family pins the pre-#100 trigger set exactly.
        names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'")}
    assert not any("governance" in name for name in names)
