"""Wave-3 wire node: pre-reservation alignment, timestamped returns, data-build surface.

Two integration seams are exercised here, and only through synthetic fixtures (a scratch
SQLite registry and a ``tmp_path`` repo, no market file, no network, no live registry, no
protected holdout, no signing):

*T14 in the runner.*  The alignment check runs **before** ``register_trial_start``, so an
incomparable selection history costs no trial slot, no attempt row and no search-floor
write; a recorded trial publishes the validation clock it was scored on; a legacy stream
keeps its missing clock and is charged as debt instead of being given one; and a
``target_symbols`` subset publishes its returns on the *contract's* full validation grid
(structural zeros where it held nothing), so its DSR basis is the one every other attempt
on that contract is scored on.

*T17 in the CLI/gateway/integrity surfaces.*  ``warden data build`` publishes a
write-once v2 dataset + manifest from a spec inside the protected inventory, the gateway
carries that build identity onto the panel, dataset specs join the protected set, and the
MCP adapter's allowlist gains no write authority from any of it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest
import yaml

from scripts.mcp_warden import adapter
from warden.cli import build_parser
from warden.cli import main as cli_main
from warden.contracts import (
    ENGINE_SIMULATION_EPOCH,
    EvaluationContract,
    ExperimentResult,
    HypothesisSpec,
)
from warden.market_data import MarketDataGateway, dataset_build_identity
from warden.provenance import DatasetProvenanceError
from warden.registry import ResearchRegistry, SelectionHistoryError
from warden.runner import ExperimentRunner
from warden_ops.integrity import check_worktree, generate_manifest, protected_manifest_entries

REPO_ROOT = Path(__file__).resolve().parents[1]
START = datetime(2025, 1, 1, tzinfo=UTC)
DATASET_ID = "synth_indices_tracer_15m_ohlcv_r1"


# --------------------------------------------------------------------------- fixtures


def _write_panel(
    path: Path,
    *,
    assets: tuple[str, ...],
    n_bars: int,
    freq_minutes: int,
    drift: float = 0.003,
    vol: float = 0.001,
    seed: int = 7,
    stagger_minutes: int = 0,
    start: datetime = START,
) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for idx, asset in enumerate(assets):
        price = 100.0 + 10.0 * idx
        offset = timedelta(minutes=idx * stagger_minutes)
        for t in range(n_bars):
            stamp = start + timedelta(minutes=t * freq_minutes) + offset
            nxt = price * (1.0 + drift + float(rng.normal(0.0, vol)))
            rows.append({
                "timestamp": stamp, "asset": asset, "open": price,
                "high": max(price, nxt) * 1.0001, "low": min(price, nxt) * 0.9999,
                "close": nxt, "volume": 100.0, "funding_rate": 0.0,
            })
            price = nxt
    frame = pl.DataFrame(rows).sort(["timestamp", "asset"]).with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC")))
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(path)
    return frame


STRATEGY = """from ml4t.backtest import Strategy
from ml4t.backtest.execution.rebalancer import RebalanceConfig, TargetWeightExecutor


def required_symbols(params):
    return params.get("target_symbols")


class _WireStrategy(Strategy):
    def __init__(self):
        self._bars = 0
        self._exec = TargetWeightExecutor(
            RebalanceConfig(allow_short=True, allow_fractional=True,
                            max_single_weight=0.6, max_gross_leverage=1.0)
        )

    def on_data(self, timestamp, data, context, broker):
        if self._bars >= 1:
            # Long-only, half gross: the fixture must exercise the tape, not the margin model.
            weight = 0.5 / max(len(data), 1)
            self._exec.execute({symbol: weight for symbol in sorted(data)}, data, broker,
                               timestamp=timestamp)
        self._bars += 1


def configure_strategy(contract, params):
    return _WireStrategy()
"""


def _contract(
    *,
    contract_id: str = "WIRE_RULES_1H",
    panel: str = "data/exploratory/wire.parquet",
    sha: str = "",
    bars: int = 80,
    step_minutes: int = 60,
    start: datetime = START,
    bars_per_year: int = 5850,
) -> EvaluationContract:
    span = timedelta(minutes=step_minutes * bars)
    return EvaluationContract(
        contract_id=contract_id,
        asset_class="indices",
        frequency="1h",
        bars_per_year=bars_per_year,
        exploratory_data_path=panel,
        holdout_data_path=f"data/holdout/{contract_id}.parquet",
        train_start=start - span,
        val_start=start,
        val_end=start + span,
        holdout_start=start + span,
        holdout_end=start + span * 2,
        n_folds=2,
        label_horizon_bars=2,
        embargo_bars=1,
        execution_delay_bars=1,
        taker_fee_bps=0.5,
        slippage_bps=0.5,
        funding_bps_per_8h=0.0,
        data_snapshot_sha256=sha,
        data_provenance="verified_market",
    )


def _repo(tmp_path: Path, contract: EvaluationContract, *, name: str = "repo") -> Path:
    repo = tmp_path / name
    (repo / "contracts" / "datasets").mkdir(parents=True, exist_ok=True)
    (repo / "strategies").mkdir(parents=True, exist_ok=True)
    (repo / "strategies" / "wire_strategy.py").write_text(STRATEGY, encoding="utf-8")
    (repo / "contracts" / f"{contract.contract_id.lower()}.yaml").write_text(
        yaml.safe_dump(contract.__dict__), encoding="utf-8")
    return repo


def _manifest(repo: Path, contract: EvaluationContract, sha: str) -> None:
    (repo / "contracts" / "datasets" / f"{contract.contract_id}.json").write_text(json.dumps({
        "contract_id": contract.contract_id, "path": contract.exploratory_data_path,
        "sha256": sha, "provenance": "verified_market", "source": "fixture",
    }), encoding="utf-8")


def _spec(repo: Path, contract: EvaluationContract, *, run_id: str, name: str = "spec.yaml",
          parameters: dict | None = None) -> Path:
    path = repo / "experiments" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({
        "contract_id": contract.contract_id,
        "hypothesis_id": "HYP-WIRE",
        "run_id": run_id,
        "strategy_family": "rules_ts",
        "model_type": "rules",
        "strategy_module": "strategies/wire_strategy.py",
        "parameters": parameters or {},
    }), encoding="utf-8")
    return path


def _registry(repo: Path, contract: EvaluationContract) -> ResearchRegistry:
    registry = ResearchRegistry(repo / "registry" / "registry.db")
    registry.register_contract(contract)
    registry.register_hypothesis(HypothesisSpec(
        "HYP-WIRE", contract.contract_id, "Wire fixture", "rules_ts",
        "Mechanism explanation of twenty chars", "Friction explanation of twenty chars",
        4, "Reject net Sharpe below one", strategy_module="strategies/wire_strategy.py",
    ))
    return registry


def _prior_result(run_id: str, contract_id: str) -> ExperimentResult:
    return ExperimentResult(
        run_id=run_id, hypothesis_id="HYP-WIRE", contract_id=contract_id,
        strategy_name="wire_strategy", strategy_family="rules_ts", config_hash="cfg",
        stage_reached="STAGE_3_STRESS", verdict="CANDIDATE_FOR_QUALIFICATION",
        gross_sharpe=1.0, net_sharpe=1.0, deflated_sharpe_prob=0.99,
        expected_max_sharpe_noise=0.1, total_trials_n=1, effective_trials_keff=1.0,
        mean_ic=None, ic_hac_tstat=None, hit_rate=0.55, profit_factor=1.3,
        max_drawdown_pct=4.0, annualized_turnover=25.0, breakeven_cost_bps=12.0,
        stress_2x_cost_sharpe=0.9, param_neighbor_stability=None, notes="fixture",
        data_snapshot_sha256="0" * 64, strategy_module="strategies/wire_strategy.py",
        model_type="rules", data_provenance="verified_market",
        simulation_epoch=ENGINE_SIMULATION_EPOCH,
    )


def _record_prior(registry: ResearchRegistry, contract: EvaluationContract, *, run_id: str,
                  count: int, timestamps=None, seed: int = 5) -> Path:
    registry.record_trial(
        _prior_result(run_id, contract.contract_id), {},
        np.random.default_rng(seed).normal(0.0, 0.01, count), timestamps=timestamps,
    )
    with registry._connect() as conn:
        return Path(conn.execute(
            "SELECT returns_parquet_path FROM trials WHERE run_id=?", (run_id,)).fetchone()[0])


def _counts(registry: ResearchRegistry) -> dict[str, int]:
    with registry._connect() as conn:
        return {
            "trials_used": int(conn.execute(
                "SELECT trials_used FROM hypotheses WHERE hypothesis_id='HYP-WIRE'").fetchone()[0]),
            "trials": int(conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0]),
            "receipts": int(conn.execute("SELECT COUNT(*) FROM attempt_receipts").fetchone()[0]),
            "attempts": int(conn.execute("SELECT COUNT(*) FROM selection_attempts").fetchone()[0]),
            "floors": int(conn.execute("SELECT COUNT(*) FROM contract_search_state").fetchone()[0]),
        }


def _diagnostics(registry: ResearchRegistry, run_id: str) -> dict:
    with registry._connect() as conn:
        raw = conn.execute("SELECT diagnostics_json FROM trials WHERE run_id=?",
                           (run_id,)).fetchone()[0]
    return json.loads(raw)


# ------------------------------------------------- 1. pre-reservation ordering (T14)


def test_incompatible_history_is_refused_before_a_trial_slot_is_charged(tmp_path: Path) -> None:
    panel = tmp_path / "repo" / "data" / "exploratory" / "wire.parquet"
    _write_panel(panel, assets=("AAA", "BBB"), n_bars=80, freq_minutes=60)
    sha = __import__("hashlib").sha256(panel.read_bytes()).hexdigest()
    contract = _contract(sha=sha)
    repo = _repo(tmp_path, contract)
    _manifest(repo, contract, sha)
    registry = _registry(repo, contract)
    # A prior trial of this contract on a clock 400 days away: nothing can be placed on it.
    old = [START - timedelta(days=400) + timedelta(hours=i) for i in range(80)]
    _record_prior(registry, contract, run_id="RUN-WIRE-OLD", count=80, timestamps=old)
    before = _counts(registry)

    runner = ExperimentRunner(registry=registry, repo_root=repo)
    with pytest.raises((SelectionHistoryError, ValueError), match="[Pp]re-reservation|alignment"):
        runner.run_spec(_spec(repo, contract, run_id="RUN-WIRE-NEW"))

    assert _counts(registry) == before, "an incomparable history must cost no slot and no floor"
    assert before["trials"] == 1 and before["floors"] == 0
    assert before["attempts"] == 0


def test_cli_backtest_reflects_the_refusal_without_recording_a_failed_slot(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    panel = tmp_path / "repo" / "data" / "exploratory" / "wire.parquet"
    _write_panel(panel, assets=("AAA", "BBB"), n_bars=80, freq_minutes=60)
    sha = __import__("hashlib").sha256(panel.read_bytes()).hexdigest()
    contract = _contract(sha=sha)
    repo = _repo(tmp_path, contract)
    _manifest(repo, contract, sha)
    registry = _registry(repo, contract)
    old = [START - timedelta(days=400) + timedelta(hours=i) for i in range(80)]
    _record_prior(registry, contract, run_id="RUN-WIRE-OLD", count=80, timestamps=old)
    before = _counts(registry)

    monkeypatch.chdir(repo)
    monkeypatch.setattr("warden.cli.check_worktree", lambda: True)
    spec = _spec(repo, contract, run_id="RUN-WIRE-NEW")
    code = cli_main(["backtest", str(spec)])
    assert code == 1
    stderr = capsys.readouterr().err
    assert "WARDEN_RUN_FAILED" in stderr and "alignment" in stderr.lower()
    assert _counts(registry) == before


def test_recorded_trial_carries_the_validation_clock_and_legacy_history_stays_debt(
    tmp_path: Path,
) -> None:
    panel = tmp_path / "repo" / "data" / "exploratory" / "wire.parquet"
    _write_panel(panel, assets=("AAA", "BBB"), n_bars=80, freq_minutes=60)
    sha = __import__("hashlib").sha256(panel.read_bytes()).hexdigest()
    contract = _contract(sha=sha)
    repo = _repo(tmp_path, contract)
    _manifest(repo, contract, sha)
    registry = _registry(repo, contract)
    # A legacy prior: a shorter vector with no time index at all.
    legacy_path = _record_prior(registry, contract, run_id="RUN-WIRE-LEGACY", count=50)
    legacy_bytes = legacy_path.read_bytes()

    res = ExperimentRunner(registry=registry, repo_root=repo).run_spec(
        _spec(repo, contract, run_id="RUN-WIRE-NEW"))

    with registry._connect() as conn:
        new_path = Path(conn.execute(
            "SELECT returns_parquet_path FROM trials WHERE run_id='RUN-WIRE-NEW'").fetchone()[0])
    schema = pl.read_parquet(new_path).schema
    assert schema["timestamp"] == pl.Datetime("us", "UTC")
    assert schema["net_return"] == pl.Float64
    # The legacy artifact is untouched: no clock was manufactured for it.
    assert legacy_path.read_bytes() == legacy_bytes
    assert "timestamp" not in pl.read_parquet(legacy_path).columns

    diag = _diagnostics(registry, "RUN-WIRE-NEW")
    selection = diag["selection"]
    assert selection["alignment"]["frequency"] == "bar"
    assert selection["alignment"]["aligned_bars"] == 80
    assert selection["preflight"]["aligned_bars"] == 80
    assert selection["unvectorized_attempts"] >= 1, "a clockless stream is charged, not dropped"
    assert selection["k_eff_effective"] >= 1 + 0.5 * selection["unvectorized_attempts"]
    assert any(item.get("legacy") for item in diag["excluded_streams"])
    assert res.stage_reached == "STAGE_3_STRESS"


def test_symbol_subset_is_published_on_the_contract_validation_grid(tmp_path: Path) -> None:
    panel = tmp_path / "repo" / "data" / "exploratory" / "wire.parquet"
    # AAA at :00 and BBB at :30 -> a 320-bar union, 160 bars for AAA alone.
    _write_panel(panel, assets=("AAA", "BBB"), n_bars=160, freq_minutes=60, stagger_minutes=30)
    sha = __import__("hashlib").sha256(panel.read_bytes()).hexdigest()
    contract = _contract(sha=sha, bars=160)
    repo = _repo(tmp_path, contract)
    _manifest(repo, contract, sha)
    registry = _registry(repo, contract)
    union_grid = sorted({
        *(START + timedelta(hours=i) for i in range(160)),
        *(START + timedelta(hours=i, minutes=30) for i in range(160)),
    })
    assert len(union_grid) == 320
    _record_prior(registry, contract, run_id="RUN-WIRE-UNION", count=320, timestamps=union_grid)

    ExperimentRunner(registry=registry, repo_root=repo).run_spec(
        _spec(repo, contract, run_id="RUN-WIRE-SUBSET", parameters={"target_symbols": ["AAA"]}))

    diag = _diagnostics(registry, "RUN-WIRE-SUBSET")
    selection = diag["selection"]
    assert selection["alignment"]["frequency"] == "bar"
    # #100: the subset's returns are reindexed onto the *contract's* full validation grid
    # (320), not published on its own 160-bar session — the same clock every other attempt
    # on this contract is scored on.
    assert selection["alignment"]["aligned_bars"] == 320
    assert selection["selection_clock"] == {
        "projected_onto_contract_grid": True,
        "contract_validation_bars": 320,
        "panel_validation_bars": 160,
        "structural_zero_bars": 160,
    }
    # The DSR base is the contract's own clock rate, not the narrowed session's.
    assert selection["bars_per_year_used"] == float(contract.bars_per_year)
    assert selection["streams_used"] == 2, "the union-grid prior aligns instead of being dropped"
    with registry._connect() as conn:
        row = conn.execute(
            "SELECT returns_json, returns_parquet_path FROM trials WHERE run_id='RUN-WIRE-SUBSET'"
        ).fetchone()
    published = json.loads(row[0])
    assert len(published) == 320
    stamps = pl.read_parquet(Path(row[1]))["timestamp"].to_list()
    assert stamps == union_grid, "every published stamp is the contract grid's own, in order"
    # 160 observed bars carry the strategy's returns; every :30 bar belongs to BBB only, so
    # the projection writes a structural zero there (no position, no return), never an
    # imputed one and never a shifted value.
    non_zero = int(np.count_nonzero(np.asarray(published, dtype=float)))
    assert 0 < non_zero <= 160
    by_stamp = dict(zip(stamps, published))
    assert all(by_stamp[stamp] == 0.0 for stamp in union_grid if stamp.minute == 30)


def test_sub_daily_candidate_compounds_onto_a_measured_daily_panel(tmp_path: Path) -> None:
    panel = tmp_path / "repo" / "data" / "exploratory" / "wire.parquet"
    _write_panel(panel, assets=("AAA", "BBB"), n_bars=720, freq_minutes=60, drift=0.0004, vol=0.0012)
    sha = __import__("hashlib").sha256(panel.read_bytes()).hexdigest()
    contract = _contract(sha=sha, bars=720, step_minutes=60, bars_per_year=8760)
    repo = _repo(tmp_path, contract)
    _manifest(repo, contract, sha)
    registry = _registry(repo, contract)
    # A 3-hourly prior over the same 30 days: 240 bars, a third of the candidate's grid.
    coarse = [START + timedelta(hours=3 * i) for i in range(240)]
    _record_prior(registry, contract, run_id="RUN-WIRE-COARSE", count=240, timestamps=coarse)

    ExperimentRunner(registry=registry, repo_root=repo).run_spec(
        _spec(repo, contract, run_id="RUN-WIRE-FINE"))

    selection = _diagnostics(registry, "RUN-WIRE-FINE")["selection"]
    assert selection["alignment"]["frequency"] == "utc_day"
    assert selection["alignment"]["aligned_bars"] == 30
    # The DSR base is measured from the aligned index, never borrowed from the contract.
    assert selection["bars_per_year_used"] == pytest.approx(365.25, rel=0.02)
    assert selection["streams_used"] == 2
    with registry._connect() as conn:
        recorded = json.loads(conn.execute(
            "SELECT returns_json FROM trials WHERE run_id='RUN-WIRE-FINE'").fetchone()[0])
    assert len(recorded) == 720, "the artifact keeps the bar vector, not the compounded panel"


# --------------------------------------------- 2. data build CLI surface (T17)


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _dataset_spec(repo: Path, *, dataset_id: str = DATASET_ID, name: str = "spec_tracer.yaml") -> Path:
    """A synthetic 15m two-asset spec, written inside the protected inventory."""
    for index, asset in enumerate(("ES", "NQ")):
        rows, price = [], 5000.0 + 250.0 * index
        for step in range(36):
            nxt = price * (1.0 + 0.00024)
            rows.append({
                "timestamp": _stamp(START + timedelta(minutes=5 * step)), "asset": asset,
                "open": price, "high": max(price, nxt) * 1.0005,
                "low": min(price, nxt) * 0.9995, "close": nxt, "volume": 100.0,
            })
            price = nxt
        target = repo / "raw" / f"{asset.lower()}.csv"
        target.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame(rows).write_csv(target)
    end = START + timedelta(hours=3)
    spec = {
        "schema_version": 1,
        "dataset_id": dataset_id,
        "asset_class": "indices",
        "evidence_tier": "synthetic",
        "venue": "synth_local",
        "frequency": "15m",
        "price_basis": "ohlcv",
        "volume_semantics": "real_contracts",
        "time_window": {
            "start_inclusive": _stamp(START),
            "exploratory_end_exclusive": _stamp(end),
            "holdout_start_inclusive": _stamp(end),
            "holdout_end_exclusive": _stamp(end + timedelta(hours=3)),
        },
        "time_contract": {
            "timezone": "UTC", "bar_timestamp_semantics": "open_left_closed_left",
            "session_calendar": "CME_Equity", "daily_roll_anchor": None,
        },
        "source": {
            "kind": "lake_raw", "adapter": "local_tracer",
            "inputs": {a.lower(): {"path": f"raw/{a.lower()}.csv", "format": "csv"}
                       for a in ("ES", "NQ")},
        },
        "universe": {"selection_mode": "explicit_symbols", "symbols": ["ES", "NQ"],
                     "retain_delisted": True},
        "resampling": {"enabled": True, "require_min_sub_bars": 3},
        "quality_gate": {"max_abs_bar_return": 0.20, "mad_outlier_threshold": 10.0,
                         "allow_zero_volume": False, "explicit_anomaly_filters": []},
        "parquet_write": {"compression": "zstd", "compression_level": 3, "statistics": True,
                          "row_group_size": 100000, "sort_keys": ["timestamp", "asset"]},
    }
    path = repo / "contracts" / "datasets" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(spec), encoding="utf-8")
    return path


def _signed_out(repo: Path, monkeypatch, *, worktree: bool = True) -> None:
    """The worktree gate is exercised for real in the subprocess test; here it is stubbed."""
    monkeypatch.setattr("warden.cli.check_worktree", lambda: worktree)


def _json_objects(text: str) -> list[dict]:
    """The JSON lines the CLI wrote, ignoring the builder's loguru diagnostics."""
    return [json.loads(line) for line in text.splitlines() if line.startswith("{")]


def test_data_build_publishes_a_v2_dataset_and_manifest(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    (repo / "contracts" / "datasets").mkdir(parents=True)
    spec_path = _dataset_spec(repo)
    monkeypatch.chdir(repo)
    _signed_out(repo, monkeypatch)

    assert cli_main(["data", "build", "--spec", spec_path.relative_to(repo).as_posix(), "--json"]) == 0
    out = capsys.readouterr().out
    assert "WARDEN_DATA_BUILD_COMPLETE" in out
    payload = _json_objects(out)[-1]
    assert payload["ok"] is True and payload["source"] == "warden.data-build"
    value = payload["value"]
    assert value["dataset_id"] == DATASET_ID
    dataset = repo / value["dataset_path"]
    manifest = repo / value["manifest_path"]
    assert dataset.is_file() and manifest.is_file()
    assert __import__("hashlib").sha256(dataset.read_bytes()).hexdigest() == value["parquet_sha256"]

    record = json.loads(manifest.read_text(encoding="utf-8"))
    assert record["schema_version"] == 2
    assert record["provenance"] == "synthetic", "a build never claims verified market origin"
    assert record["path"] == value["dataset_path"] and record["sha256"] == value["parquet_sha256"]
    assert record["spec_sha256"] == value["spec_sha256"]
    assert record["builder"]["code_closure_sha256"] == value["builder_code_closure_sha256"]
    # The build never re-signs the integrity manifest.
    assert not (repo / ".warden_manifest.sha256.sig").exists()


def test_data_build_accepts_the_positional_spec_and_refuses_two_specs(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo = tmp_path / "repo"
    (repo / "contracts" / "datasets").mkdir(parents=True)
    spec_path = _dataset_spec(repo)
    relative = spec_path.relative_to(repo).as_posix()
    monkeypatch.chdir(repo)
    _signed_out(repo, monkeypatch)
    # #103's documented shape: ``warden data build <spec>``.
    assert cli_main(["data", "build", relative, "--json"]) == 0
    payload = _json_objects(capsys.readouterr().out)[-1]
    assert payload["value"]["dataset_id"] == DATASET_ID
    assert (repo / payload["value"]["dataset_path"]).is_file()

    # The flag form is unchanged (write-once now refuses the repeat, before any read).
    assert cli_main(["data", "build", "--spec", relative]) == 1
    assert "write-once" in capsys.readouterr().err

    # Stating the spec twice is ambiguous, not a preference: refused before any file exists.
    other = repo / "contracts" / "datasets" / "spec_tracer_other.yaml"
    other.write_text(spec_path.read_text(encoding="utf-8"), encoding="utf-8")
    assert cli_main(["data", "build", relative, "--spec",
                     other.relative_to(repo).as_posix()]) == 1
    err = capsys.readouterr().err
    assert "state it once" in err and "WARDEN_DATA_BUILD_FAILED" in err

    # A build with no spec at all is an input fault, never a default.
    assert cli_main(["data", "build"]) == 1
    assert "a dataset spec is required" in capsys.readouterr().err


def test_data_build_is_write_once_and_leaves_the_original_bytes(tmp_path: Path, monkeypatch,
                                                               capsys) -> None:
    repo = tmp_path / "repo"
    (repo / "contracts" / "datasets").mkdir(parents=True)
    spec_path = _dataset_spec(repo)
    monkeypatch.chdir(repo)
    _signed_out(repo, monkeypatch)
    relative = spec_path.relative_to(repo).as_posix()
    assert cli_main(["data", "build", "--spec", relative]) == 0
    capsys.readouterr()
    dataset = repo / "data" / "exploratory" / f"{DATASET_ID}.parquet"
    manifest = repo / "contracts" / "datasets" / f"{DATASET_ID}.json"
    before = (dataset.read_bytes(), manifest.read_bytes())

    assert cli_main(["data", "build", "--spec", relative]) == 1
    assert "write-once" in capsys.readouterr().err
    assert (dataset.read_bytes(), manifest.read_bytes()) == before


def test_data_build_refuses_an_uncovered_spec_a_foreign_manifest_dir_and_a_pinned_target(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo = tmp_path / "repo"
    (repo / "contracts" / "datasets").mkdir(parents=True)
    spec_path = _dataset_spec(repo)
    outside = repo / "experiments" / "loose.yaml"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text(spec_path.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.chdir(repo)
    _signed_out(repo, monkeypatch)

    assert cli_main(["data", "build", "--spec", "experiments/loose.yaml"]) == 1
    assert "protected inventory" in capsys.readouterr().err
    assert not (repo / "data" / "exploratory" / f"{DATASET_ID}.parquet").exists()

    assert cli_main(["data", "build", "--spec", "contracts/datasets/spec_tracer.yaml",
                     "--manifest-dir", "data"]) == 1
    assert "manifest-dir" in capsys.readouterr().err

    # A build may not create the file a contract already pins as its dataset.
    (repo / "contracts" / "pinned.yaml").write_text(yaml.safe_dump({
        "contract_id": "PINNED_V1",
        "exploratory_data_path": f"data/exploratory/{DATASET_ID}.parquet",
        "holdout_data_path": "data/holdout/PINNED_V1.parquet",
    }), encoding="utf-8")
    assert cli_main(["data", "build", "--spec", "contracts/datasets/spec_tracer.yaml"]) == 1
    assert "pinned by contract" in capsys.readouterr().err
    assert not (repo / "data" / "exploratory" / f"{DATASET_ID}.parquet").exists()


def test_data_build_refuses_a_tampered_worktree_and_writes_no_dataset(tmp_path: Path,
                                                                     monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    (repo / "contracts" / "datasets").mkdir(parents=True)
    _dataset_spec(repo)
    monkeypatch.chdir(repo)
    _signed_out(repo, monkeypatch, worktree=False)
    with pytest.raises(SystemExit) as excinfo:
        cli_main(["data", "build", "--spec", "contracts/datasets/spec_tracer.yaml"])
    assert excinfo.value.code == 2
    assert "WARDEN_DATA_BUILD_FAILED" in capsys.readouterr().err
    assert not (repo / "data" / "exploratory").exists()


# --------------------------------- 3. protected inventory + gateway + MCP parity


def test_dataset_specs_and_manifests_are_part_of_the_protected_inventory(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "contracts" / "datasets").mkdir(parents=True)
    _dataset_spec(repo)
    (repo / "contracts" / "datasets" / "legacy.json").write_text('{"contract_id": "X"}',
                                                                 encoding="utf-8")
    entries = protected_manifest_entries(repo)
    assert "contracts/datasets/spec_tracer.yaml" in entries
    assert "contracts/datasets/legacy.json" in entries

    generate_manifest(repo)          # the unsigned integrity checksum, in tmp_path only
    assert check_worktree(repo) is True
    # Adding a spec makes the protected set drift; that is the whole point of covering it.
    (repo / "contracts" / "datasets" / "extra.yml").write_text("dataset_id: EXTRA\n",
                                                               encoding="utf-8")
    assert check_worktree(repo) is False


def test_gateway_carries_the_build_identity_and_refuses_a_malformed_v2_claim(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path / "repo"
    (repo / "contracts" / "datasets").mkdir(parents=True)
    _dataset_spec(repo)
    monkeypatch.chdir(repo)
    _signed_out(repo, monkeypatch)
    assert cli_main(["data", "build", "--spec", "contracts/datasets/spec_tracer.yaml", "--json"]) == 0
    manifest = json.loads((repo / "contracts" / "datasets" / f"{DATASET_ID}.json").read_text())

    contract = EvaluationContract(
        contract_id=DATASET_ID,
        asset_class="indices",
        frequency="15m",
        bars_per_year=35040,
        exploratory_data_path=manifest["path"],
        holdout_data_path=f"data/holdout/{DATASET_ID}.parquet",
        train_start=START,
        val_start=START + timedelta(hours=1),
        val_end=START + timedelta(hours=2),
        holdout_start=START + timedelta(hours=3),
        holdout_end=START + timedelta(hours=6),
        n_folds=2,
        label_horizon_bars=1,
        embargo_bars=1,
        execution_delay_bars=1,
        taker_fee_bps=1.0,
        slippage_bps=1.0,
        data_provenance="synthetic",
        data_snapshot_sha256=manifest["sha256"],
    )
    panel = MarketDataGateway(repo_root=repo).load(contract, role="exploratory")
    assert panel.dataset_is_built is True
    assert panel.build_identity["spec_sha256"] == manifest["spec_sha256"]
    assert panel.build_identity["builder_code_closure_sha256"] == \
        manifest["builder"]["code_closure_sha256"]
    assert panel.market_eligible is False

    # A v1 manifest has no build identity and none is invented for it.
    assert dataset_build_identity({"contract_id": "X", "path": "p", "sha256": "0" * 64}) == {}
    with pytest.raises(DatasetProvenanceError, match="code_closure_sha256"):
        dataset_build_identity({"schema_version": 2, "spec_sha256": "0" * 64,
                                "raw_inputs_merkle_sha256": "0" * 64,
                                "builder": {"code_closure_sha256": "not-a-digest"}})


def test_mcp_adapter_gains_no_data_build_authority_but_parses_its_real_output(
    tmp_path: Path, monkeypatch
) -> None:
    parser = build_parser()
    subparsers = next(a for a in parser._actions if hasattr(a, "choices") and a.choices)
    assert "data" in subparsers.choices, "the CLI surface must exist"
    assert "data" not in adapter.COMMANDS, "MCP must not gain a dataset-writing route"
    tool_schema = next(tool for tool in adapter.TOOLS if tool["name"] == "warden_exec")
    assert "data" not in tool_schema["inputSchema"]["properties"]["command"]["enum"]
    with pytest.raises(adapter.RestrictedCommandError):
        adapter.build_argv("data", ["build"], {"--spec": "contracts/datasets/spec_tracer.yaml"},
                           root=tmp_path)

    # Real parser parity: the adapter's JSON extractor reads the CLI's actual stdout.
    repo = tmp_path / "repo"
    (repo / "contracts" / "datasets").mkdir(parents=True)
    _dataset_spec(repo)
    generate_manifest(repo)  # makes check_worktree() true inside this isolated repo
    proc = subprocess.run(
        [sys.executable, "-m", "warden.cli", "data", "build",
         "--spec", "contracts/datasets/spec_tracer.yaml", "--json"],
        cwd=repo, capture_output=True, text=True, check=False, timeout=300,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
    )
    assert proc.returncode == 0, proc.stderr
    assert "WARDEN_DATA_BUILD_COMPLETE" in proc.stdout
    parsed = adapter.parse_json_document(proc.stdout)
    assert parsed is not None and parsed["source"] == "warden.data-build"
    assert parsed["value"]["dataset_id"] == DATASET_ID
    assert parsed["value"]["rows"] > 0
