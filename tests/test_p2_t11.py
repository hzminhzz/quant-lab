"""#97 qualification warmup and contract-declared holdout refit schedule.

Every fixture here is synthetic and lives under ``tmp_path``: panels are generated
in-process, the registry is a scratch SQLite file, and the "holdout" is a parquet this
module writes.  No real holdout, staging data, live ledger, provider or venue is touched.

Acceptance coverage:

1. rules warmup — a 64/256-bar EWMA is fed the digest-verified pre-holdout prefix and
   fills on the first executable holdout bar instead of 256 holdout bars in;
2. ``purged_expanding`` — fold 0 trains strictly before
   ``holdout_start - (label_horizon + execution_delay + embargo)`` and predictions cover
   100 % of the holdout window;
3. ``frozen_pre_holdout`` — exactly one fit, strictly pre-cutoff, no refit on any holdout
   bar, still 100 % coverage;
4. the pre-read gates (one-shot claim, APPROVED audit, code closure, run-bound causal
   proof) keep their order — re-observed here, never loosened.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest
import yaml

from warden.codeid import code_closure
from warden.contracts import (
    CURRENT_SPEC_VERSION,
    EvaluationContract,
    ExperimentResult,
    HypothesisSpec,
    canonical_json,
    load_contract,
)
from warden.engine_sim import run_engine_strategy
from warden.protocols import (
    _UniqueTimestampPanelCV,
    iter_causal_model_folds,
    prepare_causal_model_dataset,
    run_causal_model_strategy,
)
from warden.qualify import qualify_candidate
from warden.registry import ResearchRegistry
from warden.runner import ExperimentRunner
from warden.strategy_loader import load_module_from_path

START = datetime(2025, 1, 1, tzinfo=UTC)
# 300 warmup bars, 60 holdout bars; label 2 + delay 1 + embargo 1 => 4-bar purge.
WARMUP_BARS = 300
HOLDOUT_BARS = 60
TOTAL_GAP = 4


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
def _panel(
    *,
    n_bars: int,
    first_bar: int = 0,
    assets: tuple[str, ...] = ("AAA", "BBB", "CCC"),
    seed: int = 7,
    drift: float = 0.0006,
    vol: float = 0.0035,
    drop_bars: tuple[int, ...] = (),
) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows: list[dict] = []
    for idx, asset in enumerate(assets):
        price = 100.0 + 10.0 * idx
        for step in range(first_bar, first_bar + n_bars):
            if step in drop_bars and asset == assets[0]:
                continue
            ret = drift + float(rng.normal(0.0, vol))
            open_px = price
            close_px = max(1.0, open_px * (1.0 + ret))
            rows.append({
                "timestamp": START + timedelta(hours=step),
                "asset": asset,
                "open": float(open_px),
                "high": float(max(open_px, close_px) * 1.0008),
                "low": float(min(open_px, close_px) * 0.9992),
                "close": float(close_px),
                "volume": float(500.0 + rng.uniform(0.0, 100.0)),
                "funding_rate": 0.0,
            })
            price = close_px
    return (
        pl.DataFrame(rows)
        .sort("timestamp", "asset")
        .with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))
    )


def _contract(**overrides) -> EvaluationContract:
    defaults: dict = {
        "contract_id": "T11_QUAL_V1",
        "asset_class": "crypto_perps",
        "frequency": "1h",
        "bars_per_year": 8760,
        "exploratory_data_path": "data/exploratory/t11.parquet",
        "holdout_data_path": "data/holdout/t11.parquet",
        "train_start": START,
        "val_start": START + timedelta(hours=100),
        "val_end": START + timedelta(hours=WARMUP_BARS),
        "holdout_start": START + timedelta(hours=WARMUP_BARS),
        "holdout_end": START + timedelta(hours=WARMUP_BARS + HOLDOUT_BARS - 1),
        "n_folds": 3,
        "label_horizon_bars": 2,
        "embargo_bars": 1,
        "execution_delay_bars": 1,
        "taker_fee_bps": 1.0,
        "slippage_bps": 1.0,
        "data_snapshot_sha256": "0" * 64,
        "data_provenance": "verified_market",
    }
    defaults.update(overrides)
    return EvaluationContract(**defaults)  # type: ignore[arg-type]


def _load_scratch_module(tmp_path: Path, name: str, source: str):
    """Write a strategy module into the test's own scratch dir and import it."""
    directory = tmp_path / "strategies"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.py"
    path.write_text(source, encoding="utf-8")
    return load_module_from_path(path)


MODEL_MODULE = '''
import numpy as np
import polars as pl

# Observed by the test after qualification: one entry per estimator fit.  Warden's
# ``refit`` block is bookkeeping; this log is what the strategy actually did.
FIT_LOG = []


class _Pipeline:
    feature_cols = ("feat_roc1", "feat_roc2", "feat_roc5")

    def compute(self, bars_df, contract):
        _ = contract
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in df.columns and "asset" not in df.columns:
            df = df.rename({"symbol": "asset"})
        df = df.sort("timestamp", "asset")
        close = pl.col("close").cast(pl.Float64)
        return df.with_columns(
            feat_roc1=(close / close.shift(1).over("asset") - 1.0).fill_null(0.0).fill_nan(0.0),
            feat_roc2=(close / close.shift(2).over("asset") - 1.0).fill_null(0.0).fill_nan(0.0),
            feat_roc5=(close / close.shift(5).over("asset") - 1.0).fill_null(0.0).fill_nan(0.0),
        )


class _Estimator:
    def __init__(self):
        self.fits = 0

    def fit_predict_fold(self, train_df, val_features_df, feature_cols, label_col, seed=42):
        _ = seed
        self.fits += 1
        FIT_LOG.append({
            "fit": self.fits,
            "train_rows": int(train_df.height),
            "train_last_ts": train_df["timestamp"].max(),
            "train_bars": int(train_df["timestamp"].n_unique()),
            "test_rows": int(val_features_df.height),
            "test_bars": int(val_features_df["timestamp"].n_unique()),
            "test_first_ts": val_features_df["timestamp"].min(),
            "test_last_ts": val_features_df["timestamp"].max(),
            # Physical exclusion: the validation frame must arrive without labels.
            "label_columns_present": [
                c for c in val_features_df.columns
                if c == label_col or any(k in c.lower() for k in ("label", "target", "forward", "fut_"))
            ],
        })
        x = train_df.select(feature_cols).to_numpy()
        y = train_df[label_col].to_numpy()
        ok = np.isfinite(x).all(axis=1) & np.isfinite(y)
        design = np.c_[x[ok], np.ones(int(ok.sum()))]
        beta, *_ = np.linalg.lstsq(design, y[ok], rcond=None)
        xv = np.nan_to_num(val_features_df.select(feature_cols).to_numpy(), nan=0.0)
        pred = np.c_[xv, np.ones(xv.shape[0])] @ beta
        frame = val_features_df.select("timestamp", "asset").with_columns(
            prediction_value=pl.Series(np.asarray(pred, dtype=float), dtype=pl.Float64)
        )
        # Small, cross-sectionally centred book: 0.1 gross per leg keeps the Engine's
        # cash/margin constraint out of the way of the schedule assertion.
        centred = pl.col("prediction_value") - pl.col("prediction_value").mean().over("timestamp")
        scale = centred.abs().mean().over("timestamp")
        weight = (0.1 * centred / pl.when(scale > 0.0).then(scale).otherwise(1.0)).fill_null(0.0)
        return frame.with_columns(target_weight=weight.cast(pl.Float64))


pipeline = _Pipeline()


def build_estimator(params):
    return _Estimator()
'''

# A 64/256 EWMA cross: it cannot hold a position until the slow average exists, so a
# cold start would keep it flat for the first 256 holdout bars.
RULES_MODULE = '''
from ml4t.backtest import Strategy
from ml4t.backtest.execution.rebalancer import RebalanceConfig, TargetWeightExecutor

# Observed by the test: every bar the strategy saw, every bar it submitted on, and every
# timestamp on which the 64/256 EMAs were warm *and* long, i.e. an entry was possible.
SEEN = []
ORDERS = []
WARM_LONG = []


class _EwmaCross(Strategy):
    def __init__(self, fast_span=64, slow_span=256):
        self._fast = int(fast_span)
        self._slow = int(slow_span)
        self._ema_fast = {}
        self._ema_slow = {}
        self._bars = {}
        self._exec = TargetWeightExecutor(
            RebalanceConfig(allow_short=False, allow_fractional=True,
                            max_single_weight=0.5, max_gross_leverage=1.0)
        )

    def on_data(self, timestamp, data, context, broker):
        SEEN.append(timestamp)
        weights = {}
        long_ready = False
        for asset, bar in data.items():
            price = float(bar["close"])
            fast = self._ema_fast.get(asset)
            slow = self._ema_slow.get(asset)
            fast = price if fast is None else fast + (price - fast) * (2.0 / (self._fast + 1))
            slow = price if slow is None else slow + (price - slow) * (2.0 / (self._slow + 1))
            self._ema_fast[asset] = fast
            self._ema_slow[asset] = slow
            bars = self._bars.get(asset, 0) + 1
            self._bars[asset] = bars
            weights[asset] = 0.2 if fast > slow else 0.0
            if bars >= self._slow and fast > slow:
                long_ready = True
        if long_ready:
            WARM_LONG.append(timestamp)
        ORDERS.append(timestamp)
        self._exec.execute(weights, data, broker, timestamp=timestamp)


def required_symbols(params):
    return None


def configure_strategy(contract, params):
    return _EwmaCross(float(params.get("fast_span", 64)), float(params.get("slow_span", 256)))
'''


def _strategy_log(module_name: str, attribute: str) -> list:
    """Read a counter list off the module the qualification actually executed."""
    module = __import__(module_name)
    return list(getattr(module, attribute))


def _ts(value: str) -> datetime:
    """Parse the ISO-8601 string a fold record carries back to a datetime."""
    return datetime.fromisoformat(str(value))


def _qualification_repo(
    tmp_path: Path,
    *,
    module_rel: str,
    module_source: str,
    refit_mode: str = "purged_expanding",
    evidence_tier: str = "research_proxy",
    spec_version: int = CURRENT_SPEC_VERSION,
    params: dict | None = None,
    production_holdout: bool = False,
) -> SimpleNamespace:
    """A synthetic repo whose trial is ready to qualify against a fixture holdout.

    ``production_holdout`` provisions the contract's own pinned holdout parquet and
    drops the override, so qualification runs the unmodified pre-read gates (claim,
    APPROVED audit, code closure, run-bound causal proof) against the gateway.
    """
    repo = tmp_path / "repo"
    exploratory = _panel(n_bars=WARMUP_BARS)
    exp_rel = "data/exploratory/t11.parquet"
    exp_path = repo / exp_rel
    exp_path.parent.mkdir(parents=True, exist_ok=True)
    exploratory.write_parquet(exp_path)
    exp_sha = hashlib.sha256(exp_path.read_bytes()).hexdigest()

    contract = _contract(data_snapshot_sha256=exp_sha)
    document = yaml.safe_load(yaml.safe_dump(
        {k: v for k, v in contract.__dict__.items() if k not in {"policy", "_governance"}}
    ))
    if spec_version >= CURRENT_SPEC_VERSION:
        document.update({
            "spec_version": CURRENT_SPEC_VERSION,
            "selection_group_id": "GRP-T11",
            "evidence_tier": evidence_tier,
            "allow_leverage": False,
            "max_gross_leverage": 1.0,
            "holdout_refit_mode": refit_mode,
        })
    contract_path = repo / "contracts" / "t11_qual_v1.yaml"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(yaml.safe_dump(document), encoding="utf-8")

    manifest = repo / "contracts" / "datasets" / f"{contract.contract_id}.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    lifecycle = {} if spec_version >= CURRENT_SPEC_VERSION else {
        "evidence_tier": evidence_tier, "holdout_refit_mode": refit_mode,
    }
    manifest.write_text(json.dumps({
        "contract_id": contract.contract_id,
        "path": exp_rel,
        "sha256": exp_sha,
        "provenance": "verified_market",
        "source": "synthetic_t11_fixture",
        **lifecycle,
    }), encoding="utf-8")

    module_path = _write_strategy(repo, module_rel, module_source)
    holdout = _panel(n_bars=HOLDOUT_BARS, first_bar=WARMUP_BARS, seed=13, drift=0.0009)
    holdout_path = repo / contract.holdout_data_path
    holdout_path.parent.mkdir(parents=True, exist_ok=True)
    holdout.write_parquet(holdout_path)
    if production_holdout:
        record = json.loads(manifest.read_text(encoding="utf-8"))
        record.update({
            "holdout_path": contract.holdout_data_path,
            "holdout_sha256": hashlib.sha256(holdout_path.read_bytes()).hexdigest(),
        })
        manifest.write_text(json.dumps(record), encoding="utf-8")
        override_path = None
    else:
        # The fixture override carries warmup + window so it has the same shape as a
        # production holdout panel (prefix is training-only, eval slice is the window).
        override_frame = pl.concat([exploratory, holdout], how="vertical").sort("timestamp", "asset")
        override_path = tmp_path / "holdout_override.parquet"
        override_frame.write_parquet(override_path)

    returned = load_contract(contract_path)
    reg = ResearchRegistry(repo / "registry" / "registry.db")
    reg.register_contract(returned)
    hyp = HypothesisSpec(
        "HYP-T11", returned.contract_id, "T11 warmup and refit", "cross_sectional_ml",
        "Cross-sectional momentum captured causally on the strategy's own bars",
        "Taker fee and slippage paid to the passive side",
        4, "Reject if the holdout refit schedule trains inside the window",
    )
    reg.register_hypothesis(hyp)
    code_sha, _ = code_closure(repo, module_rel)
    trial = ExperimentResult(
        run_id="RUN-T11-001", hypothesis_id="HYP-T11", contract_id=returned.contract_id,
        strategy_name="t11", strategy_family="cross_sectional_ml",
        config_hash=hashlib.sha256(canonical_json(dict(params or {})).encode()).hexdigest()[:12],
        stage_reached="STAGE_3_STRESS", verdict="CANDIDATE_FOR_QUALIFICATION",
        gross_sharpe=1.4, net_sharpe=1.1, deflated_sharpe_prob=0.97,
        expected_max_sharpe_noise=0.1, total_trials_n=1, effective_trials_keff=1.0,
        mean_ic=0.03, ic_hac_tstat=2.4, hit_rate=0.52, profit_factor=1.25,
        max_drawdown_pct=3.0, annualized_turnover=20.0, breakeven_cost_bps=10.0,
        stress_2x_cost_sharpe=0.9, param_neighbor_stability=None, notes="synthetic fixture",
        synthetic=0, data_snapshot_sha256=exp_sha, data_provenance="verified_market",
        strategy_module=module_rel, model_type="ml", strategy_code_sha256=code_sha,
    )
    reg.record_trial(trial, dict(params or {}), np.full(WARMUP_BARS, 0.0004))
    return SimpleNamespace(
        repo=repo, registry=reg, contract=returned, contract_path=contract_path,
        module_rel=module_rel, module_name=module_path.stem, module_path=module_path,
        override_path=override_path, runner=ExperimentRunner(registry=reg, repo_root=repo),
        exploratory=exploratory, holdout=holdout, params=dict(params or {}),
    )


def _write_strategy(repo: Path, module_rel: str, source: str) -> Path:
    path = repo / module_rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1. contract declaration + legacy digest preservation
# ---------------------------------------------------------------------------
def _v2_document(mode: str) -> dict:
    base = _contract()
    document = yaml.safe_load(yaml.safe_dump(
        {k: v for k, v in base.__dict__.items() if k not in {"policy", "_governance"}}
    ))
    document.update({
        "spec_version": CURRENT_SPEC_VERSION,
        "selection_group_id": "GRP-T11",
        "evidence_tier": "research_proxy",
        "holdout_refit_mode": mode,
    })
    return document


def test_refit_mode_is_contract_declared_and_covered_by_the_v2_hash(tmp_path: Path) -> None:
    path = tmp_path / "contracts" / "t11.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text(yaml.safe_dump(_v2_document("frozen_pre_holdout")), encoding="utf-8")
    frozen = load_contract(path)
    assert frozen.governance.holdout_refit_mode == "frozen_pre_holdout"

    path.write_text(yaml.safe_dump(_v2_document("purged_expanding")), encoding="utf-8")
    purged = load_contract(path)
    assert purged.governance.holdout_refit_mode == "purged_expanding"
    # The mode is part of the declared governance block, so it moves the digest.
    assert purged.spec_hash() != frozen.spec_hash()

    # An unknown mode is refused at load, never silently defaulted.
    path.write_text(yaml.safe_dump(_v2_document("expanding_on_all")), encoding="utf-8")
    with pytest.raises(Exception, match="holdout_refit_mode"):
        load_contract(path)


def test_legacy_v1_digest_is_unchanged_by_a_declared_refit_mode(tmp_path: Path) -> None:
    """#97 must not move any legacy hash: a v1 mode is lifecycle metadata, not digest."""
    from warden.provenance import resolve_contract_governance

    repo = tmp_path / "repo"
    base = _contract()
    document = yaml.safe_load(yaml.safe_dump(
        {k: v for k, v in base.__dict__.items() if k not in {"policy", "_governance"}}
    ))
    path = repo / "contracts" / "t11.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    contract = load_contract(path)
    assert contract.governance.spec_version == 1
    before = contract.spec_hash()

    manifest = repo / "contracts" / "datasets" / f"{contract.contract_id}.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({
        "contract_id": contract.contract_id, "path": contract.exploratory_data_path,
        "sha256": contract.data_snapshot_sha256, "provenance": "verified_market",
        "source": "fixture", "holdout_refit_mode": "frozen_pre_holdout",
    }), encoding="utf-8")

    resolved = resolve_contract_governance(contract, repo)
    assert resolved.holdout_refit_mode == "frozen_pre_holdout"
    assert contract.spec_hash() == before  # reading lifecycle metadata is not a mutation


# ---------------------------------------------------------------------------
# 2. schedule: strict pre-cutoff training, timestamp-safe partitions
# ---------------------------------------------------------------------------
def _run_model(tmp_path: Path, panel: pl.DataFrame, contract: EvaluationContract, *, refit_mode):
    mod = _load_scratch_module(tmp_path, "t11_model", MODEL_MODULE)
    ts = sorted(panel["timestamp"].unique().to_list())
    assets = sorted(panel["asset"].unique().to_list())
    return run_causal_model_strategy(
        mod, panel, contract, ts, assets,
        slice(WARMUP_BARS, WARMUP_BARS + HOLDOUT_BARS), {}, refit_mode=refit_mode,
    )


def test_purged_expanding_trains_strictly_pre_cutoff_and_covers_every_holdout_bar(
    tmp_path: Path,
) -> None:
    panel = _panel(n_bars=WARMUP_BARS + HOLDOUT_BARS)
    out = _run_model(tmp_path, panel, _contract(n_folds=3), refit_mode="purged_expanding")

    ts = sorted(panel["timestamp"].unique().to_list())
    cutoff = ts[WARMUP_BARS - TOTAL_GAP]  # first timestamp at/after holdout_start - gap
    window = ts[WARMUP_BARS:]
    refit = out["refit"]

    assert refit["mode"] == "purged_expanding"
    assert refit["fits"] == 3
    assert refit["covers_window"] is True
    assert refit["prediction_bars"] == len(window) == HOLDOUT_BARS

    first = refit["folds"][0]
    assert _ts(first["train_last_ts"]) < cutoff
    assert first["train_bars"] == WARMUP_BARS - TOTAL_GAP  # no holdout bar in fold 0
    assert _ts(first["test_first_ts"]) == window[0]
    # Expanding: later folds legitimately add earlier holdout bars, never the future.
    assert refit["folds"][1]["train_bars"] > first["train_bars"]
    assert _ts(refit["folds"][-1]["test_last_ts"]) == window[-1]

    preds = out["predictions"]
    assert set(preds["timestamp"].unique().to_list()) == set(window)
    assert preds.height == len(window) * 3


def test_frozen_pre_holdout_fits_once_and_never_refits_on_a_holdout_bar(tmp_path: Path) -> None:
    panel = _panel(n_bars=WARMUP_BARS + HOLDOUT_BARS)
    out = _run_model(tmp_path, panel, _contract(n_folds=3), refit_mode="frozen_pre_holdout")

    ts = sorted(panel["timestamp"].unique().to_list())
    cutoff = ts[WARMUP_BARS - TOTAL_GAP]
    refit = out["refit"]

    assert refit["mode"] == "frozen_pre_holdout"
    assert refit["fits"] == 1
    assert refit["covers_window"] is True
    assert refit["prediction_bars"] == HOLDOUT_BARS

    only = refit["folds"][0]
    assert _ts(only["train_last_ts"]) < cutoff
    assert only["train_bars"] == WARMUP_BARS - TOTAL_GAP
    assert only["test_bars"] == HOLDOUT_BARS
    assert _ts(only["test_first_ts"]) == ts[WARMUP_BARS]
    assert _ts(only["test_last_ts"]) == ts[-1]
    assert set(out["predictions"]["timestamp"].unique().to_list()) == set(ts[WARMUP_BARS:])

    fits = _strategy_log("t11_model", "FIT_LOG")
    assert len(fits) == 1
    assert fits[0]["label_columns_present"] == []


def test_refit_schedule_is_timestamp_safe_on_an_async_panel(tmp_path: Path) -> None:
    """A missing asset bar must not shift a fold boundary onto the neighbouring bar."""
    missing = (WARMUP_BARS - 1, WARMUP_BARS + 7, WARMUP_BARS + 30)
    panel = _panel(n_bars=WARMUP_BARS + HOLDOUT_BARS, drop_bars=missing)
    out = _run_model(tmp_path, panel, _contract(n_folds=3), refit_mode="purged_expanding")

    ts = sorted(panel["timestamp"].unique().to_list())
    # The mutilated asset lost three rows; the timestamp grid is unchanged.
    assert len(ts) == WARMUP_BARS + HOLDOUT_BARS
    window = ts[WARMUP_BARS:]
    assert out["refit"]["prediction_bars"] == len(window)
    assert out["refit"]["covers_window"] is True
    for fold in out["refit"]["folds"]:
        assert _ts(fold["test_first_ts"]) in window
        # Timestamp-index gap, not a row offset: every fold keeps the full purge+embargo
        # distance on the *unique* clock even though one asset lost bars.
        gap = ts.index(_ts(fold["test_first_ts"])) - ts.index(_ts(fold["train_last_ts"]))
        assert gap >= TOTAL_GAP
    # Fold 0 — the only fold that could contaminate the window — is strictly pre-cutoff.
    assert _ts(out["refit"]["folds"][0]["train_last_ts"]) < ts[WARMUP_BARS - TOTAL_GAP]
    # Every fold test window is a contiguous run of holdout timestamps.
    segments = [(_ts(f["test_first_ts"]), _ts(f["test_last_ts"])) for f in out["refit"]["folds"]]
    assert segments[0][0] == window[0] and segments[-1][1] == window[-1]
    for earlier, later in itertools.pairwise(segments):
        assert window.index(later[0]) == window.index(earlier[1]) + 1


def test_default_validation_schedule_is_untouched(tmp_path: Path) -> None:
    """``refit_mode=None`` must reproduce the historical validation partitions exactly."""
    panel = _panel(n_bars=400)
    contract = _contract()
    mod = _load_scratch_module(tmp_path, "t11_model", MODEL_MODULE)
    ts = sorted(panel["timestamp"].unique().to_list())
    _norm, model_df, _fwd, feature_cols, mask = prepare_causal_model_dataset(
        mod, panel, contract, ts
    )
    reference = list(_UniqueTimestampPanelCV(
        panel_timestamps=model_df["timestamp"].to_list(),
        unique_ts=ts,
        val_start_idx=WARMUP_BARS,
        val_end_idx=WARMUP_BARS + HOLDOUT_BARS,
        n_folds=3,
        total_gap=TOTAL_GAP,
        valid_train_mask=mask,
    ).split(None))
    observed = list(iter_causal_model_folds(
        model_df, feature_cols, mask, ts, contract, WARMUP_BARS, WARMUP_BARS + HOLDOUT_BARS,
        refit_mode=None,
    ))
    assert len(observed) == len(reference) == 3
    for (train_df, val_df), (tr_rows, te_rows) in zip(observed, reference):
        assert set(train_df["timestamp"].unique().to_list()) == set(
            model_df[tr_rows]["timestamp"].unique().to_list())
        assert set(val_df["timestamp"].unique().to_list()) == set(
            model_df[te_rows]["timestamp"].unique().to_list())


def test_kernel_memo_never_serves_a_validation_tape_to_a_refit_qualification(
    tmp_path: Path,
) -> None:
    """Same panel bytes, same contract: a refit qualification is its own execution."""
    from warden.evaluation import EvaluationKernel

    panel = _panel(n_bars=WARMUP_BARS + HOLDOUT_BARS)
    contract = _contract()
    mod = _load_scratch_module(tmp_path, "t11_model", MODEL_MODULE)
    ts = sorted(panel["timestamp"].unique().to_list())
    assets = sorted(panel["asset"].unique().to_list())
    kernel = EvaluationKernel()
    window = slice(WARMUP_BARS, WARMUP_BARS + HOLDOUT_BARS)

    validation = kernel.run(mod, panel, ts, assets, contract, window, {}, protocol="causal_model")
    assert validation.model_refit["mode"] == "walk_forward"

    refit = kernel.run(
        mod, panel, ts, assets, contract, window, {}, protocol="causal_model",
        refit_mode="purged_expanding",
    )
    assert refit.model_refit["mode"] == "purged_expanding"
    # A memo hit would have reused the validation tape and its schedule evidence.
    assert kernel.engine_runs == 2
    assert refit.model_refit["folds"][0]["train_last_ts"] != validation.model_refit["folds"][0]["train_last_ts"]


def test_refit_refuses_a_short_prefix_instead_of_training_inside_the_window(
    tmp_path: Path,
) -> None:
    panel = _panel(n_bars=WARMUP_BARS + HOLDOUT_BARS)
    contract = _contract()
    mod = _load_scratch_module(tmp_path, "t11_model", MODEL_MODULE)
    ts = sorted(panel["timestamp"].unique().to_list())
    # A window that starts before the 4-bar purge can fit is refused outright: the
    # alternative would be training on the window it is meant to evaluate.
    with pytest.raises(ValueError, match="pre-holdout warmup prefix"):
        run_causal_model_strategy(
            mod, panel, contract, ts, sorted(panel["asset"].unique().to_list()),
            slice(2, 2 + HOLDOUT_BARS), {}, refit_mode="purged_expanding",
        )


# ---------------------------------------------------------------------------
# 3. rules warmup: first executable holdout bar
# ---------------------------------------------------------------------------
def test_rules_ewma_fills_on_the_first_executable_holdout_bar(tmp_path: Path) -> None:
    """A 64/256 EWMA warmed on the prefix cannot wait 256 holdout bars to enter."""
    panel = _panel(n_bars=WARMUP_BARS + HOLDOUT_BARS)
    contract = _contract()
    mod = _load_scratch_module(tmp_path, "t11_rules", RULES_MODULE)
    strategy = mod.configure_strategy(contract, {"fast_span": 64, "slow_span": 256})

    ts = sorted(panel["timestamp"].unique().to_list())
    assets = sorted(panel["asset"].unique().to_list())
    sim = run_engine_strategy(panel, strategy, ts, assets, contract, slice(WARMUP_BARS, len(ts)))

    assert sim.backtest_result.fills, "the warmed strategy never traded on the holdout window"
    first_fill = min(f.timestamp for f in sim.backtest_result.fills)
    # execution_delay_bars=1: a signal on the last prefix bar fills on the first holdout bar.
    assert first_fill == ts[WARMUP_BARS] == START + timedelta(hours=WARMUP_BARS)
    assert first_fill < ts[WARMUP_BARS + 2]

    seen = _strategy_log("t11_rules", "SEEN")
    assert min(seen) == ts[0]  # the prefix really was fed to the indicator
    assert len(seen) == len(ts)


def test_qualify_rules_sees_the_prefix_and_reports_no_cold_start(tmp_path: Path) -> None:
    fixture = _qualification_repo(
        tmp_path,
        module_rel="strategies/candidates/t11_rules.py",
        module_source=RULES_MODULE,
        params={"fast_span": 64, "slow_span": 256},
    )
    result = qualify_candidate(
        "RUN-T11-001", "YES_UNSEAL_HOLDOUT", fixture.runner,
        holdout_override_path=fixture.override_path,
    )
    assert result["holdout_override"] is True
    assert result["holdout_warmup_rows"] == WARMUP_BARS
    assert result["holdout_eval_bars"] == HOLDOUT_BARS
    assert result["holdout_refit_mode"] is None  # rules choose their own orders
    assert result["promoted"] is False and result["evidence_tier"] == "research_proxy"
    assert result["engine_fills_count"] > 0

    seen = _strategy_log(fixture.module_name, "SEEN")
    assert min(seen) == fixture.exploratory["timestamp"].min()
    assert len(seen) == WARMUP_BARS + HOLDOUT_BARS


# ---------------------------------------------------------------------------
# 4. model qualification end-to-end through the contract-declared mode
# ---------------------------------------------------------------------------
def test_qualify_model_purged_expanding_covers_every_holdout_bar(tmp_path: Path) -> None:
    fixture = _qualification_repo(
        tmp_path,
        module_rel="strategies/candidates/t11_model.py",
        module_source=MODEL_MODULE,
        refit_mode="purged_expanding",
        params={"rebalance_every": 4, "top_k": 2},
    )
    result = qualify_candidate(
        "RUN-T11-001", "YES_UNSEAL_HOLDOUT", fixture.runner,
        holdout_override_path=fixture.override_path,
    )
    refit = result["holdout_model_refit"]
    assert result["holdout_refit_mode"] == "purged_expanding"
    assert refit["mode"] == "purged_expanding"
    assert refit["fits"] == 3
    assert refit["covers_window"] is True
    assert refit["prediction_bars"] == refit["window_bars"] == HOLDOUT_BARS
    assert result["holdout_eval_bars"] == HOLDOUT_BARS
    assert result["promoted"] is False

    ts = sorted(pl.read_parquet(fixture.override_path)["timestamp"].unique().to_list())
    assert _ts(refit["folds"][0]["train_last_ts"]) < ts[WARMUP_BARS - TOTAL_GAP]
    assert _ts(refit["folds"][0]["test_first_ts"]) == ts[WARMUP_BARS]

    fits = _strategy_log(fixture.module_name, "FIT_LOG")
    assert len(fits) == 3
    assert all(f["label_columns_present"] == [] for f in fits)


def test_qualify_model_frozen_pre_holdout_never_refits(tmp_path: Path) -> None:
    fixture = _qualification_repo(
        tmp_path,
        module_rel="strategies/candidates/t11_model_frozen.py",
        module_source=MODEL_MODULE,
        refit_mode="frozen_pre_holdout",
        params={"rebalance_every": 4, "top_k": 2},
    )
    result = qualify_candidate(
        "RUN-T11-001", "YES_UNSEAL_HOLDOUT", fixture.runner,
        holdout_override_path=fixture.override_path,
    )
    refit = result["holdout_model_refit"]
    assert result["holdout_refit_mode"] == "frozen_pre_holdout"
    assert refit["mode"] == "frozen_pre_holdout"
    assert refit["fits"] == 1
    assert refit["covers_window"] is True
    assert refit["prediction_bars"] == HOLDOUT_BARS

    ts = sorted(pl.read_parquet(fixture.override_path)["timestamp"].unique().to_list())
    only = refit["folds"][0]
    assert _ts(only["train_last_ts"]) < ts[WARMUP_BARS - TOTAL_GAP]
    assert _ts(only["test_first_ts"]) == ts[WARMUP_BARS]
    assert _ts(only["test_last_ts"]) == ts[-1]

    fits = _strategy_log(fixture.module_name, "FIT_LOG")
    assert len(fits) == 1


def test_production_path_qualification_warms_on_the_verified_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No override: the gateway's own digest-verified prefix warms the strategy.

    The audit row is a fixture (as in the holdout parity test); the causal proof is a
    real ``cmd_verify`` output over this repo's synthetic exploratory bytes, so both
    required pre-read gates are satisfied by evidence rather than bypassed.
    """
    import argparse

    from warden.cli import cmd_verify

    params = {"fast_span": 64, "slow_span": 256}
    fixture = _qualification_repo(
        tmp_path,
        module_rel="strategies/candidates/t11_rules.py",
        module_source=RULES_MODULE,
        params=params,
        production_holdout=True,
    )
    monkeypatch.setattr("warden.cli.check_worktree", lambda: True)
    spec = fixture.repo / "experiments" / "active" / "hyp_t11_rules.yaml"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(yaml.safe_dump({
        "contract_id": fixture.contract.contract_id,
        "hypothesis_id": "HYP-T11",
        "strategy_name": "t11 rules",
        "strategy_family": "rules_ts",
        "model_type": "rules",
        "strategy_module": fixture.module_rel,
        "parameters": params,
    }), encoding="utf-8")
    assert cmd_verify(argparse.Namespace(spec_yaml=str(spec), json=True), fixture.runner) == 0
    assert fixture.registry.causal_proof_for_run("RUN-T11-001", fixture.repo)["verified"] is True
    fixture.registry.record_audit("RUN-T11-001", fixture.contract.contract_id, "APPROVED",
                                  {"fixture": True})

    result = qualify_candidate("RUN-T11-001", "YES_UNSEAL_HOLDOUT", fixture.runner)
    assert result["holdout_override"] is False
    assert result["holdout_warmup_rows"] == WARMUP_BARS  # gateway prefix, not a fixture
    assert result["holdout_eval_bars"] == HOLDOUT_BARS

    seen = _strategy_log(fixture.module_name, "SEEN")
    assert min(seen) == fixture.exploratory["timestamp"].min()
    # Warmth came from the prefix, and a long signal already existed on the last prefix
    # bar — the only bar an order may be submitted on before the window opens, so the
    # entry lands on the first executable holdout bar instead of 256 bars into it.
    warm_long = _strategy_log(fixture.module_name, "WARM_LONG")
    assert warm_long and min(warm_long) <= START + timedelta(hours=WARMUP_BARS - 1)
    assert START + timedelta(hours=WARMUP_BARS - 1) in warm_long
    assert result["engine_fills_count"] > 0


def test_claim_and_read_start_commit_before_the_holdout_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ordering: claim + durable read-start exist before any holdout byte is opened."""
    fixture = _qualification_repo(
        tmp_path,
        module_rel="strategies/candidates/t11_model.py",
        module_source=MODEL_MODULE,
        params={"rebalance_every": 4, "top_k": 2},
    )
    real_read = pl.read_parquet
    holdout_bytes = fixture.override_path.read_bytes()
    observed: list[dict] = []

    def counting_read(source, *args, **kwargs):
        # Accept a path or in-memory bytes: the integrity boundary may hand the override
        # reader either, and the observation must not depend on which one it is.
        payload = None
        if isinstance(source, (bytes, bytearray, memoryview)):
            payload = bytes(source)
        elif isinstance(source, (str, Path)) and Path(source) == fixture.override_path:
            payload = holdout_bytes
        if payload is not None and payload == holdout_bytes:
            with fixture.registry._connect() as conn:
                claims = [dict(r) for r in conn.execute(
                    "SELECT state, run_id FROM holdout_claims").fetchall()]
                quals = conn.execute("SELECT COUNT(*) FROM qualifications").fetchone()[0]
            observed.append({"claims": claims, "qualifications": quals})
        return real_read(source, *args, **kwargs)

    monkeypatch.setattr(pl, "read_parquet", counting_read)

    result = qualify_candidate(
        "RUN-T11-001", "YES_UNSEAL_HOLDOUT", fixture.runner,
        holdout_override_path=fixture.override_path,
    )
    assert observed, "the holdout read was not observed"
    at_first_read = observed[0]
    assert at_first_read["claims"] == [{"state": "READ_STARTED", "run_id": "RUN-T11-001"}]
    assert at_first_read["qualifications"] == 0
    assert result["verdict"] in {"REJECTED_ON_HOLDOUT", "PROXY_HOLDOUT_PASS"}
    with fixture.registry._connect() as conn:
        assert conn.execute("SELECT state FROM holdout_claims").fetchone()[0] == result["verdict"]
        assert conn.execute("SELECT COUNT(*) FROM qualifications").fetchone()[0] == 1
