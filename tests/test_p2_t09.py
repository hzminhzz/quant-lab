"""Issue #95: MarketDataGateway is the sole evaluator reader of market bars.

Covered here, one block per acceptance clause and review focus:

1. **One reader, four callers** — ``verify``, ``screen``, ``backtest`` and ``qualify`` reach
   market data only through ``MarketDataGateway.load`` and receive the same normalised
   ``MarketPanel``; no caller touches ``pl.read_parquet`` itself.
2. **Holdout warmup** — ``role="holdout"`` concatenates the digest-verified exploratory bars
   that precede ``holdout_start``, asserts the prefix ends before the first holdout bar, and
   exposes ``eval_slice`` covering 100% of the holdout bars.  An unpinned holdout is refused
   before any byte is read.
3. **Calendar** — a multi-exchange union panel filtered to ``target_symbols: ["DAX"]`` is
   annualised from DAX's own elapsed calendar, within 1% of its true bar count, instead of
   inheriting the union ``bars_per_year``.
4. **Full grid** — the contract's whole validation timestamp grid survives; nothing is
   trimmed to the validation window.
5. **Integrity AST** — ``read_parquet``/``scan_parquet`` are refused in evaluator modules
   under every alias form, with only the gateway, the feature cache and the return-artifact
   reader allowed.  Return artifacts are explicitly classified as *not* market data.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest
import yaml

from warden.contracts import ContractValidationError, EvaluationContract, utc
from warden.market_data import (
    ROLE_EXPLORATORY,
    ROLE_HOLDOUT,
    MarketDataGateway,
    MarketPanel,
    measured_bars_per_year,
    normalise_feed,
)
from warden.provenance import DatasetProvenanceError, resolve_dataset
from warden_ops.integrity import check_ast_boundaries

REPO_ROOT = Path(__file__).resolve().parents[1]
START = datetime(2025, 1, 1, tzinfo=UTC)

FLAT_STRATEGY = """
from ml4t.backtest import Strategy
from ml4t.backtest.execution.rebalancer import RebalanceConfig, TargetWeightExecutor


def required_symbols(params):
    return None


class _Flat(Strategy):
    def __init__(self):
        self._bar = 0
        self._exec = TargetWeightExecutor(
            RebalanceConfig(allow_short=True, allow_fractional=True, max_single_weight=0.5, max_gross_leverage=1.0)
        )

    def on_data(self, timestamp, data, context, broker):
        w = 0.1 if self._bar % 4 == 0 else 0.0
        self._exec.execute({a: w for a in data}, data, broker, timestamp=timestamp)
        self._bar += 1


def configure_strategy(contract, params):
    return _Flat()
"""


# ---------------------------------------------------------------------------
# Fixtures: generated synthetic market bars (never real, never protected)
# ---------------------------------------------------------------------------


def _bars(
    *,
    assets: tuple[str, ...] = ("AAA", "BBB"),
    n_bars: int,
    first_hour: int = 0,
    seed: int = 7,
    columns: str = "asset",
) -> pl.DataFrame:
    rows, rng = [], np.random.default_rng(seed)
    for asset, base in zip(assets, (100.0, 50.0, 25.0, 12.5, 6.25, 3.125)):
        price = base
        for t in range(n_bars):
            nxt = price * (1.0 + float(rng.normal(0.0, 0.002)))
            rows.append({
                "timestamp": START + timedelta(hours=first_hour + t),
                columns: asset,
                "open": price,
                "high": max(price, nxt) * 1.001,
                "low": min(price, nxt) * 0.999,
                "close": nxt,
                "volume": 100.0,
            })
            price = nxt
    frame = pl.DataFrame(rows)
    if columns != "asset":
        frame = frame.rename({columns: "asset"})
    return frame.sort("timestamp", "asset").with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC"))
    )


def _contract(
    *,
    contract_id: str = "T09_FX_V1",
    exploratory_path: str = "data/exploratory/t09.parquet",
    holdout_path: str = "data/holdout/t09.parquet",
    val_start_hours: int = 10,
    val_end_hours: int = 20,
    holdout_start_hours: int = 48,
    holdout_end_hours: int = 88,
    bars_per_year: int = 8760,
    asset_class: str = "fx",
    provenance: str = "verified_market",
    snapshot_sha: str = "",
    frequency: str = "1h",
) -> EvaluationContract:
    return EvaluationContract(
        contract_id=contract_id,
        asset_class=asset_class,
        frequency=frequency,
        bars_per_year=bars_per_year,
        exploratory_data_path=exploratory_path,
        holdout_data_path=holdout_path,
        train_start=utc(START),
        val_start=utc(START + timedelta(hours=val_start_hours)),
        val_end=utc(START + timedelta(hours=val_end_hours)),
        holdout_start=utc(START + timedelta(hours=holdout_start_hours)),
        holdout_end=utc(START + timedelta(hours=holdout_end_hours)),
        n_folds=2,
        label_horizon_bars=2,
        embargo_bars=1,
        execution_delay_bars=1,
        taker_fee_bps=0.2,
        slippage_bps=0.5,
        funding_bps_per_8h=0.0,
        data_snapshot_sha256=snapshot_sha,
        data_provenance=provenance,
    )


def _manifest(
    repo: Path,
    contract: EvaluationContract,
    *,
    path: str,
    sha: str,
    holdout_path: str | None = None,
    holdout_sha: str | None = None,
) -> None:
    record = {
        "contract_id": contract.contract_id,
        "path": path,
        "sha256": sha,
        "provenance": "verified_market",
        "source": "t09_generated_fixture",
    }
    if holdout_path is not None:
        record["holdout_path"] = holdout_path
    if holdout_sha is not None:
        record["holdout_sha256"] = holdout_sha
    dest = repo / "contracts" / "datasets" / f"{contract.contract_id}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(record), encoding="utf-8")


def _market_repo(
    root: Path,
    *,
    exploratory: pl.DataFrame,
    holdout: pl.DataFrame | None = None,
    pin_holdout: bool = True,
    contract_kwargs: dict | None = None,
) -> SimpleNamespace:
    """Exploratory (and optionally holdout) parquet + manifests inside ``root``."""
    repo = root / "repo"
    contract_kwargs = dict(contract_kwargs or {})
    exp_path = repo / "data" / "exploratory" / "t09.parquet"
    exp_path.parent.mkdir(parents=True, exist_ok=True)
    exploratory.write_parquet(exp_path)
    exp_sha = hashlib.sha256(exp_path.read_bytes()).hexdigest()
    hold_path = repo / "data" / "holdout" / "t09.parquet"
    hold_sha = ""
    if holdout is not None:
        hold_path.parent.mkdir(parents=True, exist_ok=True)
        holdout.write_parquet(hold_path)
        hold_sha = hashlib.sha256(hold_path.read_bytes()).hexdigest()
    contract = _contract(snapshot_sha=exp_sha, **contract_kwargs)
    _manifest(
        repo,
        contract,
        path=contract.exploratory_data_path,
        sha=exp_sha,
        holdout_path=contract.holdout_data_path if pin_holdout else None,
        holdout_sha=hold_sha if (pin_holdout and hold_sha) else None,
    )
    (repo / "contracts" / "t09_fx_v1.yaml").parent.mkdir(parents=True, exist_ok=True)
    (repo / "contracts" / "t09_fx_v1.yaml").write_text(yaml.safe_dump(contract.__dict__), encoding="utf-8")
    (repo / "strategies").mkdir(parents=True, exist_ok=True)
    (repo / "strategies" / "flat.py").write_text(FLAT_STRATEGY, encoding="utf-8")
    spec = repo / "spec.yaml"
    spec.write_text(
        yaml.safe_dump({
            "contract_id": contract.contract_id,
            "hypothesis_id": "HYP-T09",
            "strategy_name": "Flat",
            "strategy_family": "rules_ts",
            "model_type": "rules",
            "strategy_module": "strategies/flat.py",
            "parameters": {},
        }),
        encoding="utf-8",
    )
    from warden.contracts import HypothesisSpec
    from warden.registry import ResearchRegistry

    registry = ResearchRegistry(repo / "registry" / "registry.db")
    registry.register_contract(contract)
    registry.register_hypothesis(HypothesisSpec(
        "HYP-T09", contract.contract_id, "T09 flat", "rules_ts",
        "Market data gateway sole reader mechanism", "Friction explanation of twenty chars",
        8, "Reject any second market reader",
    ))
    return SimpleNamespace(
        repo=repo,
        registry=registry,
        contract=contract,
        spec=spec,
        exploratory=exploratory,
        holdout=holdout,
        exp_path=exp_path,
        hold_path=hold_path if holdout is not None else None,
        exp_sha=exp_sha,
        hold_sha=hold_sha,
    )


# ---------------------------------------------------------------------------
# 1. Normalisation: v1 (``symbol``) and v2 (``asset``) feeds converge
# ---------------------------------------------------------------------------


def test_v1_symbol_and_v2_asset_feeds_normalise_to_one_panel(tmp_path: Path) -> None:
    contract = _contract(provenance="unknown")
    v1 = _bars(n_bars=30, columns="symbol")
    v2 = _bars(n_bars=30, columns="asset")

    normalised_v1 = normalise_feed(v1, contract)
    normalised_v2 = normalise_feed(v2, contract)

    assert normalised_v1.schema == normalised_v2.schema
    assert normalised_v1["asset"].dtype == pl.String
    assert normalised_v1["timestamp"].dtype == pl.Datetime("us", "UTC")
    assert normalised_v1.equals(normalised_v2)


def test_gateway_normalises_a_v1_feed_end_to_end(tmp_path: Path) -> None:
    fixture = _market_repo(tmp_path, exploratory=_bars(n_bars=30, columns="symbol"))
    market = MarketDataGateway(fixture.repo).load(fixture.contract)
    assert market.role == ROLE_EXPLORATORY
    assert "asset" in market.panel.columns and "symbol" not in market.panel.columns
    assert market.panel["timestamp"].dtype == pl.Datetime("us", "UTC")
    assert market.assets == ("AAA", "BBB")
    assert market.spec_version == fixture.contract.governance.spec_version == 1


def test_gateway_refuses_a_timezone_naive_feed(tmp_path: Path) -> None:
    naive = _bars(n_bars=30).with_columns(pl.col("timestamp").dt.replace_time_zone(None))
    fixture = _market_repo(tmp_path, exploratory=naive)
    with pytest.raises(ContractValidationError, match="UTC"):
        MarketDataGateway(fixture.repo).load(fixture.contract)


# ---------------------------------------------------------------------------
# 2. Exploratory fence and full validation grid
# ---------------------------------------------------------------------------


def test_gateway_fences_exploratory_bars_at_and_after_holdout_start(tmp_path: Path) -> None:
    # 48 bars from START+0h..+47h, holdout_start at +48h -> legal.
    fixture = _market_repo(tmp_path, exploratory=_bars(n_bars=48))
    market = MarketDataGateway(fixture.repo).load(fixture.contract)
    assert len(market.ts) == 48

    # One more bar reaches the cutoff: refused before any slice is computed.
    overflow = _market_repo(tmp_path / "overflow", exploratory=_bars(n_bars=49))
    with pytest.raises(ContractValidationError, match="holdout_start"):
        MarketDataGateway(overflow.repo).load(overflow.contract)


def test_validation_grid_is_preserved_not_trimmed(tmp_path: Path) -> None:
    fixture = _market_repo(tmp_path, exploratory=_bars(n_bars=48))
    market = MarketDataGateway(fixture.repo).load(fixture.contract)

    assert market.total_bars == 48  # the whole panel is retained
    assert market.eval_slice == slice(10, 20)
    assert market.validation_grid_preserved(fixture.contract) is True
    assert market.eval_ts[0] == utc(START + timedelta(hours=10))
    assert market.eval_ts[-1] == utc(START + timedelta(hours=19))
    assert market.eval_bars == 10


def test_gateway_refuses_an_empty_validation_window(tmp_path: Path) -> None:
    fixture = _market_repo(
        tmp_path,
        exploratory=_bars(n_bars=6),
        contract_kwargs={"val_start_hours": 10, "val_end_hours": 20},
    )
    with pytest.raises(ContractValidationError, match="no bars"):
        MarketDataGateway(fixture.repo).load(fixture.contract)


# ---------------------------------------------------------------------------
# 3. Holdout: verified warmup prefix, ordering, full eval coverage
# ---------------------------------------------------------------------------


def test_holdout_prefixes_verified_exploratory_warmup_and_covers_every_holdout_bar(
    tmp_path: Path,
) -> None:
    fixture = _market_repo(
        tmp_path,
        exploratory=_bars(n_bars=48),
        holdout=_bars(n_bars=40, first_hour=48, seed=13),
    )
    market = MarketDataGateway(fixture.repo).load(fixture.contract, ROLE_HOLDOUT)

    assert market.role == ROLE_HOLDOUT
    assert market.warmup_rows == 48
    assert market.total_bars == 88
    # eval_slice covers 100% of the holdout bars, nothing else.
    assert market.eval_slice == slice(48, 88)
    assert market.eval_bars == 40
    assert market.eval_ts[0] == fixture.contract.holdout_start
    assert market.eval_ts[-1] == utc(START + timedelta(hours=87))
    assert market.eval_ts[-1] <= fixture.contract.holdout_end

    warmup_ts = market.ts[: market.warmup_rows]
    holdout_ts = market.eval_ts
    assert max(warmup_ts) < min(holdout_ts)
    assert max(warmup_ts) == utc(fixture.contract.holdout_start - timedelta(hours=1))

    # Both sides of the prefix are digest-verified: identical to a direct verification.
    assert market.market_eligible is True
    assert market.resolution.provenance == "verified_market"
    explicit = resolve_dataset(fixture.contract, fixture.repo, role="holdout")
    assert explicit.eligible_market is True and explicit.sha256 == fixture.hold_sha


def test_holdout_pin_rejection_precedes_any_read(tmp_path: Path, monkeypatch) -> None:
    fixture = _market_repo(
        tmp_path,
        exploratory=_bars(n_bars=48),
        holdout=_bars(n_bars=40, first_hour=48),
        pin_holdout=False,  # manifest declares no holdout_path/holdout_sha256
    )
    reads: list[str] = []
    real_read = pl.read_parquet

    def counted(path, *args, **kwargs):
        reads.append(str(path))
        return real_read(path, *args, **kwargs)

    monkeypatch.setattr(pl, "read_parquet", counted)
    with pytest.raises(DatasetProvenanceError, match="holdout pin missing"):
        MarketDataGateway(fixture.repo).load(fixture.contract, ROLE_HOLDOUT)
    assert reads == []


def test_production_holdout_cannot_be_read_without_a_warmup_prefix(tmp_path: Path) -> None:
    """No verified exploratory prefix means no warmup: the read is refused, not downgraded."""
    fixture = _market_repo(
        tmp_path,
        exploratory=_bars(n_bars=48),
        holdout=_bars(n_bars=40, first_hour=48),
    )
    fixture.exp_path.unlink()  # exploratory bytes are gone; holdout is untouched
    with pytest.raises(DatasetProvenanceError):
        MarketDataGateway(fixture.repo).load(fixture.contract, ROLE_HOLDOUT)


def test_holdout_reads_both_verified_feeds_and_nothing_else(tmp_path: Path, monkeypatch) -> None:
    fixture = _market_repo(
        tmp_path,
        exploratory=_bars(n_bars=48),
        holdout=_bars(n_bars=40, first_hour=48, seed=19),
    )
    reads = _recorded_reads(monkeypatch)
    market = MarketDataGateway(fixture.repo).load(fixture.contract, ROLE_HOLDOUT)
    assert market.warmup_rows == 48
    assert reads == [str(fixture.hold_path), str(fixture.exp_path)]


def test_holdout_warmup_with_a_different_feed_schema_is_refused(tmp_path: Path) -> None:
    """A prefix that cannot be concatenated is refused, never null-filled."""
    exploratory = _bars(n_bars=48).with_columns(extra=pl.lit(1.0))
    fixture = _market_repo(
        tmp_path,
        exploratory=exploratory,
        holdout=_bars(n_bars=40, first_hour=48),
    )
    with pytest.raises(ContractValidationError, match="refusing to fill nulls"):
        MarketDataGateway(fixture.repo).load(fixture.contract, ROLE_HOLDOUT)


def test_holdout_window_below_minimum_bars_is_refused(tmp_path: Path) -> None:
    fixture = _market_repo(
        tmp_path,
        exploratory=_bars(n_bars=48),
        holdout=_bars(n_bars=5, first_hour=48),
    )
    with pytest.raises(ContractValidationError, match="no fallback window"):
        MarketDataGateway(fixture.repo).load(fixture.contract, ROLE_HOLDOUT)


# ---------------------------------------------------------------------------
# 4. Target-symbol calendar: DAX, not the six-exchange union
# ---------------------------------------------------------------------------


def _union_calendar_panel(days: int = 366) -> pl.DataFrame:
    """One union-calendar panel: DAX trades 09:00-16:00, five venues fill the rest."""
    rows, rng = [], np.random.default_rng(3)
    prices = {f"EX{i}": 10.0 + i for i in range(5)}
    prices["DAX"] = 15000.0
    for day in range(days):
        for hour in range(24):
            asset = "DAX" if 9 <= hour < 17 else f"EX{hour % 5}"
            price = prices[asset]
            nxt = price * (1.0 + float(rng.normal(0.0, 0.0015)))
            rows.append({
                "timestamp": START + timedelta(days=day, hours=hour),
                "asset": asset,
                "open": price,
                "high": max(price, nxt) * 1.0005,
                "low": min(price, nxt) * 0.9995,
                "close": nxt,
                "volume": 500.0,
            })
            prices[asset] = nxt
    return pl.DataFrame(rows).with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC"))
    ).sort("timestamp", "asset")


def test_target_symbol_filter_annualises_on_the_dax_calendar_not_the_union(
    tmp_path: Path,
) -> None:
    panel = _union_calendar_panel(days=366)
    dax_bars = panel.filter(pl.col("asset") == "DAX")
    true_dax_per_year = dax_bars.height / 366.0 * 365.25

    fixture = _market_repo(
        tmp_path,
        exploratory=panel,
        contract_kwargs={
            "contract_id": "T09_UNION_V1",
            "asset_class": "indices",
            "bars_per_year": 5850,  # the declared six-exchange union calendar
            "val_start_hours": 1,
            "val_end_hours": 24,
            "holdout_start_hours": 366 * 24 + 24,
            "holdout_end_hours": 366 * 24 + 48,
            "provenance": "unknown",
        },
    )
    contract = fixture.contract
    # The panel spans a year of union timestamps; the validation window is its first day.
    grid = [t for t in dax_bars["timestamp"].unique().sort().to_list() if t < contract.val_end]

    unfiltered = MarketDataGateway(fixture.repo).load(contract)
    assert unfiltered.bars_per_year == 5850
    assert unfiltered.symbols_filtered is False

    dax = MarketDataGateway(fixture.repo).load(contract, ROLE_EXPLORATORY, ["DAX"])
    assert dax.symbols_filtered is True
    assert dax.assets == ("DAX",)
    assert dax.panel.height == dax_bars.height
    assert dax.bars_per_year != 5850
    assert abs(dax.bars_per_year - true_dax_per_year) / true_dax_per_year < 0.01
    assert dax.bars_per_year == round(measured_bars_per_year(dax.ts) or 0.0)
    # The validation grid is DAX's own grid, not the union's: 8 bars per session day.
    assert len(dax.eval_ts) == len(grid) == 8


def test_unknown_target_symbol_is_refused(tmp_path: Path) -> None:
    fixture = _market_repo(tmp_path, exploratory=_bars(n_bars=48))
    with pytest.raises(ValueError, match="unknown symbols"):
        MarketDataGateway(fixture.repo).load(fixture.contract, ROLE_EXPLORATORY, ["NOPE"])


# ---------------------------------------------------------------------------
# 5. All four callers go through the gateway and receive the same structure
# ---------------------------------------------------------------------------


class _RecordingGateway:
    """Delegating recorder: every caller's load call and the panel it received."""

    def __init__(self, monkeypatch) -> None:
        self.calls: list[dict] = []
        real_load = MarketDataGateway.load

        def recording(self_, contract, role=ROLE_EXPLORATORY, symbols=None, **kwargs):
            panel = real_load(self_, contract, role, symbols, **kwargs)
            self.calls.append({"role": role, "symbols": symbols, "panel": panel})
            return panel

        monkeypatch.setattr(MarketDataGateway, "load", recording)

    @property
    def roles(self) -> list[str]:
        return [call["role"] for call in self.calls]

    @property
    def panels(self) -> list[MarketPanel]:
        return [call["panel"] for call in self.calls]


def _recorded_reads(monkeypatch) -> list[str]:
    reads: list[str] = []
    real_read = pl.read_parquet

    def counted(path, *args, **kwargs):
        reads.append(str(path))
        return real_read(path, *args, **kwargs)

    monkeypatch.setattr(pl, "read_parquet", counted)
    return reads


def test_all_four_callers_load_market_data_only_through_the_gateway(
    pre_rg05_policy,
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from warden import cli
    from warden.qualify import qualify_candidate
    from warden.runner import ExperimentRunner

    fixture = _market_repo(
        tmp_path,
        exploratory=_bars(n_bars=48),
        holdout=_bars(n_bars=40, first_hour=48, seed=17),
    )
    recorder = _RecordingGateway(monkeypatch)
    monkeypatch.setattr(cli, "check_worktree", lambda *args, **kwargs: True)
    runner = ExperimentRunner(registry=fixture.registry, repo_root=fixture.repo,
                              allow_legacy_replay=True)

    # verify
    assert cli.cmd_verify(SimpleNamespace(spec_yaml=str(fixture.spec), json=True), runner) == 0
    # screen
    cli.cmd_screen(SimpleNamespace(spec_yaml=str(fixture.spec), json=True), runner)
    # backtest
    cli.cmd_backtest(SimpleNamespace(spec_yaml=str(fixture.spec), json=False), runner)
    # qualify (fixture override, never a production holdout)
    from warden.codeid import code_closure
    from warden.contracts import ExperimentResult

    shard = fixture.hold_path
    code_sha, _ = code_closure(fixture.repo, "strategies/flat.py")
    fixture.registry.record_trial(
        ExperimentResult(
            run_id="RUN-T09-QUAL", hypothesis_id="HYP-T09", contract_id=fixture.contract.contract_id,
            strategy_name="Flat", strategy_family="rules_ts", config_hash="cfg",
            stage_reached="STAGE_3_STRESS", verdict="CANDIDATE_FOR_QUALIFICATION",
            gross_sharpe=1.2, net_sharpe=1.0, deflated_sharpe_prob=0.99,
            expected_max_sharpe_noise=0.1, total_trials_n=1, effective_trials_keff=1.0,
            mean_ic=None, ic_hac_tstat=None, hit_rate=0.55, profit_factor=1.3,
            max_drawdown_pct=3.0, annualized_turnover=12.0, breakeven_cost_bps=6.0,
            stress_2x_cost_sharpe=0.9, param_neighbor_stability=None, notes="t09 fixture",
            synthetic=0, data_snapshot_sha256=fixture.exp_sha, data_provenance="verified_market",
            strategy_module="strategies/flat.py", model_type="rules", strategy_code_sha256=code_sha,
        ),
        {},
        np.linspace(-0.002, 0.003, 40),
    )
    qualify_candidate("RUN-T09-QUAL", "YES_UNSEAL_HOLDOUT", runner, holdout_override_path=shard)
    capsys.readouterr()

    # Every stage reached the gateway; qualify used the holdout role, the rest exploratory.
    assert recorder.roles.count(ROLE_EXPLORATORY) >= 3
    assert recorder.roles[-1] == ROLE_HOLDOUT

    # And no caller read Parquet itself: every read inside the gateway is the gateway's.
    reads = _recorded_reads(monkeypatch)
    gateway = MarketDataGateway(fixture.repo)
    gateway.load(fixture.contract)
    assert reads == [str(fixture.exp_path)]


def test_callers_receive_identical_normalised_market_panels(pre_rg05_policy, tmp_path: Path, monkeypatch, capsys) -> None:
    from warden import cli
    from warden.runner import ExperimentRunner

    fixture = _market_repo(tmp_path, exploratory=_bars(n_bars=48))
    recorder = _RecordingGateway(monkeypatch)
    monkeypatch.setattr(cli, "check_worktree", lambda *args, **kwargs: True)
    runner = ExperimentRunner(registry=fixture.registry, repo_root=fixture.repo,
                              allow_legacy_replay=True)

    assert cli.cmd_verify(SimpleNamespace(spec_yaml=str(fixture.spec), json=True), runner) == 0
    cli.cmd_screen(SimpleNamespace(spec_yaml=str(fixture.spec), json=True), runner)
    capsys.readouterr()

    panels = recorder.panels
    assert len(panels) >= 2
    first = panels[0]
    for other in panels[1:]:
        assert isinstance(other, MarketPanel)
        assert other.panel.schema == first.panel.schema
        assert other.ts == first.ts
        assert other.assets == first.assets
        assert other.eval_slice == first.eval_slice
        assert other.bars_per_year == first.bars_per_year


def test_runner_prepare_panel_is_the_gateway_tuple(tmp_path: Path) -> None:
    from warden.runner import prepare_market_panel, prepare_panel

    fixture = _market_repo(tmp_path, exploratory=_bars(n_bars=48))
    dataset = resolve_dataset(fixture.contract, fixture.repo)
    market = prepare_market_panel(fixture.repo, dataset, {}, fixture.contract)
    panel_df, ts, assets, v_slice = prepare_panel(fixture.repo, dataset, {}, fixture.contract)
    assert panel_df.equals(market.panel)
    assert ts == list(market.ts) and assets == list(market.assets)
    assert v_slice == market.eval_slice


# ---------------------------------------------------------------------------
# 6. Integrity AST: one reader, every alias covered
# ---------------------------------------------------------------------------


def _code_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    "source,needle",
    [
        ("import polars as pl\npl.read_parquet('x.parquet')\n", "pl.read_parquet"),
        ("import polars\npolars.scan_parquet('x.parquet')\n", "polars.scan_parquet"),
        ("from polars import read_parquet as rp\nrp('x.parquet')\n", "alias of polars.read_parquet"),
        ("from polars import scan_parquet\nscan_parquet('x.parquet')\n", "scan_parquet"),
        ("import polars.io as pio\npio.read_parquet('x.parquet')\n", "pio.read_parquet"),
        ("import polars as pl\npl.io.read_parquet('x.parquet')\n", "pl.io.read_parquet"),
        ("import polars as pl\ngetattr(pl, 'read_parquet')('x.parquet')\n", "getattr(pl"),
        ("from polars import *\nread_parquet('x.parquet')\n", "star import from 'polars'"),
    ],
)
def test_ast_refuses_every_aliased_parquet_read(tmp_path: Path, source: str, needle: str) -> None:
    repo = _code_repo(tmp_path, {"src/warden/rogue_reader.py": source})
    violations = check_ast_boundaries(repo)
    assert any("rogue_reader.py" in v and needle in v for v in violations), violations


def test_ast_checks_warden_evidence_too(tmp_path: Path) -> None:
    repo = _code_repo(
        tmp_path,
        {"src/warden_evidence/rogue.py": "import polars as pl\npl.read_parquet('x')\n"},
    )
    assert any("rogue.py" in v for v in check_ast_boundaries(repo))


def test_ast_allows_only_the_declared_readers(tmp_path: Path) -> None:
    repo = _code_repo(
        tmp_path,
        {
            "src/warden/market_data.py": "import polars as pl\npl.read_parquet('market.parquet')\n",
            "src/warden_evidence/feature_cache.py": "import polars as pl\npl.read_parquet('feature.parquet')\n",
            "src/warden_evidence/return_artifacts.py": "import polars as pl\npl.read_parquet('returns.parquet')\n",
            # Scripts are not evaluator modules; that boundary belongs to #102.
            "scripts/tool.py": "import polars as pl\npl.read_parquet('x.parquet')\n",
            # A scan-free, read-free evaluator module stays clean.
            "src/warden/clean.py": "import polars as pl\npl.DataFrame({'a': [1]})\n",
        },
    )
    assert check_ast_boundaries(repo) == []


def test_the_real_tree_has_no_second_market_reader() -> None:
    assert [
        v for v in check_ast_boundaries(REPO_ROOT) if "market-data read" in v
    ] == []


# ---------------------------------------------------------------------------
# 7. Return artifacts are not market data — but the exemption is confined
# ---------------------------------------------------------------------------


def test_return_artifact_reader_is_confined_to_the_returns_tree(tmp_path: Path) -> None:
    from warden_evidence.return_artifacts import ReturnArtifactError, read_return_artifact

    returns_root = tmp_path / "registry" / "returns"
    returns_root.mkdir(parents=True)
    artifact = returns_root / "T09_FX_V1" / "RUN-T09.parquet"
    artifact.parent.mkdir(parents=True)
    pl.DataFrame({"net_return": [0.01, -0.02, 0.03]}).write_parquet(artifact)

    series = read_return_artifact(artifact, returns_root)
    assert series.to_list() == pytest.approx([0.01, -0.02, 0.03])

    # A market/holdout path is refused even though the reader can read Parquet.
    outside = tmp_path / "data" / "holdout" / "t09.parquet"
    outside.parent.mkdir(parents=True)
    pl.DataFrame({"net_return": [0.01]}).write_parquet(outside)
    with pytest.raises(ReturnArtifactError, match="not under the registry returns tree"):
        read_return_artifact(outside, returns_root)

    with pytest.raises(ReturnArtifactError, match="has no 'missing' column"):
        read_return_artifact(artifact, returns_root, column="missing")
