"""T12 (#98): typed strategy parameters and genuine numeric neighbors.

The acceptance facts these tests pin:

* a strategy that declares a ``warden.params_model.ParamsModel`` gets its spec
  parameters validated against that model, and a mistyped or out-of-range
  declaration is refused *before* a trial row or a selection attempt exists;
* a strategy with any numeric parameter (declared, or supplied as numbers when
  no model is declared) can reach ``CANDIDATE_FOR_QUALIFICATION`` only with at
  least two **distinct, genuine** numeric ``param_grid`` neighbors;
* ``param_grid: []``, an omitted key, duplicates and entries that restate the
  effective baseline all record ``neighbor_stability`` in
  ``diagnostics_json["failed_gates"]`` and yield ``REVISE`` -- the empty-grid
  bypass is gone;
* the effective baseline is the model's defaults merged with the spec's
  parameters, so a grid entry equal to a default is not a perturbation;
* a genuinely parameter-free strategy (``ParamsModel`` with zero numeric fields)
  qualifies with ``param_grid: []`` and records
  ``neighbor_exemption="parameter_free"``;
* the shipped reference templates declare a model, and the reference funnel
  records the empty-grid rejection separately from the passing neighbor run.

All panels, contracts, registries and holdouts here are synthetic ``tmp_path``
fixtures; nothing touches the live registry, holdout or providers.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest
import yaml

from warden.cli import main as cli_main
from warden.contracts import EvaluationContract
from warden.params_model import (
    NEIGHBOR_MIN_DISTINCT,
    ParamsModel,
    ParamsModelError,
    classify_numeric_neighbors,
    evaluate_neighbor_requirement,
    resolve_params_model,
    validate_strategy_params,
)
from warden.registry import ResearchRegistry
from warden.runner import ExperimentRunner
from warden_ops.integrity import generate_manifest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Rules strategy with a typed declaration.  The trading logic is the shipped
#: reference drawdown template, so the only variable under test is the
#: parameter declaration and the neighbor grid.
GATED_MODULE = '''
from causal_rules_drawdown_template import CausalDrawdownRulesStrategy
from pydantic import Field

from warden.params_model import ParamsModel


class MomentumParams(ParamsModel):
    fast_span: int = Field(default=6, ge=1)
    slow_span: int = Field(default=18, ge=2)
    max_drawdown: float = Field(default=0.10, gt=0.0, le=1.0)
    stop_loss_pct: float = Field(default=0.05, gt=0.0, le=1.0)


params_model = MomentumParams


def required_symbols(params):
    return None


def configure_strategy(contract, params):
    declared = {k: v for k, v in params.items() if k in MomentumParams.model_fields}
    tuned = MomentumParams.model_validate(declared)
    return CausalDrawdownRulesStrategy(
        fast_span=tuned.fast_span,
        slow_span=tuned.slow_span,
        max_drawdown=tuned.max_drawdown,
        stop_loss_pct=tuned.stop_loss_pct,
    )
'''

#: Same trading logic, zero numeric fields: the parameter-free case.
FREE_MODULE = '''
from causal_rules_drawdown_template import CausalDrawdownRulesStrategy

from warden.params_model import ParamsModel


class NoTunables(ParamsModel):
    """Nothing to perturb; a degenerate grid would be theatre."""


params_model = NoTunables


def required_symbols(params):
    return None


def configure_strategy(contract, params):
    return CausalDrawdownRulesStrategy(
        fast_span=6,
        slow_span=18,
        max_drawdown=0.10,
        stop_loss_pct=0.05,
    )
'''

RULES_REL = "strategies/candidates/causal_rules_drawdown_template.py"
RIDGE_REL = "strategies/baselines/causal_ridge_1h.py"
GATED_REL = "strategies/candidates/gated_momentum_t12.py"
FREE_REL = "strategies/candidates/parameter_free_t12.py"


def _hash_bytes(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_persistent_trend_panel(
    *,
    n_bars: int = 180,
    assets: tuple[str, ...] = ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH"),
    seed: int = 101,
    t0: datetime = datetime(2025, 1, 1, tzinfo=UTC),
) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows: list[dict] = []
    half = len(assets) // 2
    for idx, asset in enumerate(assets):
        price = 100.0 + idx * 5.0
        drift = 0.0028 if idx < half else -0.0022
        for t in range(n_bars):
            stamp = t0 + timedelta(hours=t)
            ret = drift + float(rng.normal(0.0, 0.0025))
            open_px = price
            close_px = max(1.0, open_px * (1.0 + ret))
            rows.append(
                {
                    "timestamp": stamp,
                    "asset": asset,
                    "open": float(open_px),
                    "high": float(max(open_px, close_px) * 1.003),
                    "low": float(min(open_px, close_px) * 0.997),
                    "close": float(close_px),
                    "volume": float(1500.0 + rng.uniform(0.0, 300.0)),
                    "funding_rate": 0.0,
                }
            )
            price = close_px
    return (
        pl.DataFrame(rows)
        .sort("timestamp", "asset")
        .with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))
    )


def _setup_repo(tmp_path: Path, *, name: str, panel: pl.DataFrame) -> tuple[Path, EvaluationContract]:
    repo = tmp_path / name
    for rel in (
        "contracts/datasets",
        "strategies/candidates",
        "strategies/baselines",
        "experiments/active",
        "cache/features",
    ):
        (repo / rel).mkdir(parents=True, exist_ok=True)
    (repo / "AGENTS.md").write_text("# test\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname='t12'\n", encoding="utf-8")
    (repo / "uv.lock").write_text("version=1\n", encoding="utf-8")

    ts = sorted(panel["timestamp"].unique().to_list())
    n_bars = len(ts)
    train_start = ts[0]
    val_start = ts[max(16, n_bars // 3)]
    val_end = ts[-1] + timedelta(hours=1)
    holdout_start = val_end
    holdout_end = holdout_start + timedelta(hours=max(40, n_bars))

    data_rel = "data/exploratory/panel.parquet"
    data_path = repo / data_rel
    data_path.parent.mkdir(parents=True, exist_ok=True)
    panel.write_parquet(data_path)
    sha = _hash_bytes(data_path)

    holdout_rel = "data/holdout/panel_holdout.parquet"
    holdout_path = repo / holdout_rel
    holdout_path.parent.mkdir(parents=True, exist_ok=True)
    panel.with_columns(
        (pl.col("timestamp") + timedelta(hours=n_bars + 2)).alias("timestamp")
    ).write_parquet(holdout_path)

    contract = EvaluationContract(
        contract_id="T12_CRYPTO_1H",
        asset_class="crypto_perps",
        frequency="1h",
        bars_per_year=8760,
        exploratory_data_path=data_rel,
        holdout_data_path=holdout_rel,
        train_start=train_start,
        val_start=val_start,
        val_end=val_end,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        n_folds=3,
        label_horizon_bars=2,
        embargo_bars=1,
        execution_delay_bars=1,
        taker_fee_bps=0.5,
        slippage_bps=0.5,
        data_snapshot_sha256=sha,
        data_provenance="verified_market",
    )
    cid = contract.contract_id
    (repo / "contracts" / f"{cid.lower()}.yaml").write_text(
        yaml.safe_dump(contract.__dict__), encoding="utf-8"
    )
    (repo / "contracts" / "datasets" / f"{cid}.json").write_text(
        json.dumps(
            {
                "contract_id": cid,
                "path": data_rel,
                "sha256": sha,
                "holdout_path": holdout_rel,
                "holdout_sha256": _hash_bytes(holdout_path),
                "provenance": "verified_market",
                "source": "t12_test",
            }
        ),
        encoding="utf-8",
    )
    ResearchRegistry(repo / "registry" / "registry.db").register_contract(contract)
    generate_manifest(repo)
    return repo, contract


def _install_shared_templates(repo: Path) -> None:
    """Copy the reference strategies the inline test modules import."""

    for rel in (RULES_REL, RIDGE_REL):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text((REPO_ROOT / rel).read_text(encoding="utf-8"), encoding="utf-8")


def _register(
    repo: Path,
    contract: EvaluationContract,
    *,
    hyp_id: str,
    module_rel: str,
    module_source: str,
    family: str = "rules_ts",
    model_type: str = "rules",
    parameters: dict | None = None,
    param_grid: list[dict] | None = None,
    omit_param_grid: bool = False,
    expected_holding_bars: int = 8,
) -> Path:
    (repo / module_rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / module_rel).write_text(module_source, encoding="utf-8")
    hyp_md = repo / ".workspace" / "hypotheses" / f"{hyp_id}.md"
    hyp_md.parent.mkdir(parents=True, exist_ok=True)
    hyp_md.write_text(
        f"---\n"
        f"hypothesis_id: {hyp_id}\n"
        f"contract_id: {contract.contract_id}\n"
        f"title: T12 {hyp_id}\n"
        f"strategy_family: {family}\n"
        f"economic_mechanism: Persistent cross-asset trend drift captured causally\n"
        f"counterparty_or_friction: Taker fee and slippage paid to passive liquidity providers\n"
        f"expected_holding_bars: {expected_holding_bars}\n"
        f"falsification_criteria: Reject if Stage 1 or Stage 3 statistical gates fail\n"
        f"---\n",
        encoding="utf-8",
    )
    payload: dict = {
        "contract_id": contract.contract_id,
        "hypothesis_id": hyp_id,
        "strategy_name": f"Strat_{hyp_id}",
        "strategy_family": family,
        "model_type": model_type,
        "strategy_module": module_rel,
        "parameters": parameters or {},
    }
    if not omit_param_grid:
        payload["param_grid"] = param_grid if param_grid is not None else []
    spec_yaml = repo / "experiments" / "active" / f"{hyp_id.lower()}.yaml"
    spec_yaml.write_text(yaml.safe_dump(payload), encoding="utf-8")
    assert cli_main(["register-hypothesis", str(hyp_md), "--json"]) == 0
    generate_manifest(repo)  # strategy + spec are part of the worktree identity
    return spec_yaml


def _diag(result) -> dict:
    return json.loads(result.diagnostics_json)


def _counts(repo: Path) -> tuple[int, int]:
    db = repo / "registry" / "registry.db"
    with sqlite3.connect(db) as conn:
        trials = conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0]
        attempts = conn.execute("SELECT COUNT(*) FROM selection_attempts").fetchone()[0]
    return int(trials), int(attempts)


# --------------------------------------------------------------------------- #
# Unit level: typed declaration and grid classification
# --------------------------------------------------------------------------- #


def test_params_model_rejects_bad_types_and_out_of_range_before_side_effects() -> None:
    class Holder:
        params_model = type(
            "Strict",
            (ParamsModel,),
            {"__annotations__": {"fast": int, "size": float}, "fast": 3, "size": 0.5},
        )

    with pytest.raises(ParamsModelError, match="Strict rejected"):
        validate_strategy_params(Holder, {"fast": "three"})
    with pytest.raises(ParamsModelError, match="Strict rejected"):
        validate_strategy_params(Holder, {"size": [0.1]})
    # Unknown keys are recorded, never silently treated as typed fields.
    report = validate_strategy_params(Holder, {"fast": 4, "target_symbols": ["AAA"]})
    assert report.unknown_keys == ("target_symbols",)
    assert report.baseline_numeric == {"fast": 4.0, "size": 0.5}


def test_grid_classification_separates_genuine_degenerate_duplicate_and_non_numeric() -> None:
    class Model(ParamsModel):
        fast: int = 3
        slow: int = 8
        label: str = "x"

    class Holder:
        params_model = Model

    report = validate_strategy_params(Holder, {"fast": 4})
    grid = classify_numeric_neighbors(
        [
            {"fast": 4},      # restates the effective baseline
            {"label": "y"},   # non-numeric edit; defaults its way to fast=3 but not a perturbation
            {"fast": 3},      # genuine: default value, differs from baseline 4
            {"fast": 3.0},    # duplicate of the previous configuration
            {"slow": 8},      # restates slow; genuine only if it also moved a number (it does not)
            {"fast": 5},      # genuine
            {"fast": 6},      # genuine
        ],
        report,
    )
    assert grid.requested == 7
    assert grid.numeric_indices == (2, 5, 6)
    assert grid.evaluated_indices == (1, 2, 5, 6)
    assert grid.distinct_numeric == 3
    assert grid.degenerate_indices == (0,)
    # 3 is a float spelling and 4 ("slow": 8) silently falls back to fast=3:
    # both execute the same configuration as 2, so neither is a second neighbor.
    assert grid.duplicate_indices == (3, 4)
    # 1 changes behaviour but sets no numeric field: evidence, not perturbation.
    assert grid.non_numeric_indices == (1,)

    requirement = evaluate_neighbor_requirement(report, grid)
    assert requirement["required"] is True
    assert requirement["met"] is True
    assert requirement["exemption"] is None


def test_no_model_numeric_parameters_still_require_neighbors() -> None:
    class Legacy:
        pass

    report = validate_strategy_params(Legacy, {"fast": 4, "mode": "trend"})
    assert report.declared is False
    assert report.numeric_fields == ("fast",)
    empty = classify_numeric_neighbors([], report)
    requirement = evaluate_neighbor_requirement(report, empty)
    assert requirement["required"] is True
    assert requirement["met"] is False

    two = classify_numeric_neighbors([{"fast": 3}, {"fast": 5}, {"mode": "x"}], report)
    assert evaluate_neighbor_requirement(report, two)["met"] is True

    text_only = validate_strategy_params(Legacy, {"mode": "trend"})
    exempt = evaluate_neighbor_requirement(text_only, classify_numeric_neighbors([], text_only))
    assert exempt["required"] is False
    assert exempt["exemption"] == "parameter_free"
    assert exempt["met"] is True


def test_reference_templates_declare_typed_models() -> None:
    from warden.strategy_loader import load_module_from_path

    for rel in (RULES_REL, RIDGE_REL, "strategies/baselines/causal_ipca_1h.py",
                "strategies/baselines/causal_linear_portfolio_1h.py"):
        module = load_module_from_path(REPO_ROOT / rel)
        model = resolve_params_model(module)
        assert model is not None, f"{rel} must declare params_model"
        assert issubclass(model, ParamsModel)
        assert model.model_fields, f"{rel} declares an empty ParamsModel"


# --------------------------------------------------------------------------- #
# Runner level: the Stage-3 neighbor gate
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def planned_panel() -> pl.DataFrame:
    return _make_persistent_trend_panel(n_bars=180, seed=101)


def _run(
    tmp_path: Path,
    panel: pl.DataFrame,
    *,
    name: str,
    hyp_id: str,
    module_rel: str,
    module_source: str,
    monkeypatch: pytest.MonkeyPatch,
    family: str = "rules_ts",
    model_type: str = "rules",
    parameters: dict | None = None,
    param_grid: list[dict] | None = None,
    omit_param_grid: bool = False,
):
    repo, contract = _setup_repo(tmp_path, name=name, panel=panel)
    _install_shared_templates(repo)
    monkeypatch.chdir(repo)  # every CLI call must resolve this tmp repo, never the worktree
    spec = _register(
        repo,
        contract,
        hyp_id=hyp_id,
        module_rel=module_rel,
        module_source=module_source,
        family=family,
        model_type=model_type,
        parameters=parameters,
        param_grid=param_grid,
        omit_param_grid=omit_param_grid,
    )
    runner = ExperimentRunner(repo_root=repo)
    return repo, runner.run_spec(spec)


@pytest.mark.parametrize("variant", ["empty", "omitted"])
def test_parameterized_strategy_without_neighbors_is_revise(
    pre_rg05_policy,
    tmp_path: Path, planned_panel: pl.DataFrame, monkeypatch: pytest.MonkeyPatch, variant: str
) -> None:
    repo, result = _run(
        tmp_path,
        planned_panel,
        monkeypatch=monkeypatch,
        name=f"t12_empty_{variant}",
        hyp_id=f"HYP-T12-EMPTY-{variant.upper()}",
        module_rel=GATED_REL,
        module_source=GATED_MODULE,
        parameters={"fast_span": 6, "slow_span": 18},
        param_grid=[],
        omit_param_grid=(variant == "omitted"),
    )
    diag = _diag(result)
    assert result.verdict == "REVISE"
    assert "neighbor_stability" in diag["failed_gates"]
    assert diag["neighbor_tested"] is False
    assert diag["neighbor_stability"]["stability"] is None
    assert result.param_neighbor_stability is None
    assert "neighbor_exemption" not in diag
    assert diag["neighbor_requirement"]["required"] is True
    assert diag["neighbor_requirement"]["met"] is False
    assert diag["neighbor_grid"]["distinct_numeric_neighbors"] == 0
    assert _counts(repo) == (1, 0)


def test_degenerate_and_duplicate_grid_entries_never_count_as_neighbors(
    pre_rg05_policy,
    tmp_path: Path, planned_panel: pl.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The old bypass: four entries, one real configuration, stability 1.0."""

    repo, result = _run(
        tmp_path,
        planned_panel,
        monkeypatch=monkeypatch,
        name="t12_degenerate",
        hyp_id="HYP-T12-DEGENERATE",
        module_rel=GATED_REL,
        module_source=GATED_MODULE,
        parameters={"fast_span": 6, "slow_span": 18},
        param_grid=[
            {"fast_span": 6},   # restates the baseline
            {"fast_span": 6.0}, # duplicate spelling of the same non-perturbation
            {"fast_span": 7},   # one genuine neighbor
            {"fast_span": 7},   # exact duplicate
        ],
    )
    diag = _diag(result)
    assert result.verdict == "REVISE"
    assert "neighbor_stability" in diag["failed_gates"]
    # One configuration really was executed and scored, so it is still "tested";
    # what it cannot do is satisfy the two-distinct-numeric-neighbor requirement.
    assert diag["neighbor_tested"] is True
    assert diag["neighbor_requirement"]["met"] is False
    assert diag["neighbor_grid"]["requested"] == 4
    assert diag["neighbor_grid"]["evaluated_configurations"] == 1
    assert diag["neighbor_grid"]["distinct_numeric_neighbors"] == 1
    assert diag["neighbor_grid"]["numeric_indices"] == [2]
    assert diag["neighbor_grid"]["degenerate_indices"] == [0, 1]
    assert diag["neighbor_grid"]["duplicate_indices"] == [3]
    # Accounting is preserved: every supplied configuration is still an attempt.
    trials, attempts = _counts(repo)
    assert trials == 1
    assert attempts == 4


def test_two_distinct_genuine_neighbors_open_the_gate(
    pre_rg05_policy,
    tmp_path: Path, planned_panel: pl.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, result = _run(
        tmp_path,
        planned_panel,
        monkeypatch=monkeypatch,
        name="t12_two_neighbors",
        hyp_id="HYP-T12-TWO",
        module_rel=GATED_REL,
        module_source=GATED_MODULE,
        parameters={"fast_span": 6, "slow_span": 18},
        param_grid=[{"fast_span": 5}, {"fast_span": 7}],
    )
    diag = _diag(result)
    assert diag["neighbor_grid"]["distinct_numeric_neighbors"] == NEIGHBOR_MIN_DISTINCT
    assert diag["neighbor_requirement"]["met"] is True
    assert diag["neighbor_tested"] is True
    assert "neighbor_stability" not in diag["failed_gates"]
    assert "neighbor_exemption" not in diag
    assert result.verdict == "CANDIDATE_FOR_QUALIFICATION"
    assert result.stage_reached == "STAGE_3_STRESS"


def test_effective_defaults_are_the_neighbor_baseline(
    pre_rg05_policy,
    tmp_path: Path, planned_panel: pl.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no spec parameters the baseline is the model's own defaults."""

    _, restated = _run(
        tmp_path,
        planned_panel,
        monkeypatch=monkeypatch,
        name="t12_defaults_restated",
        hyp_id="HYP-T12-DEFAULTS-A",
        module_rel=GATED_REL,
        module_source=GATED_MODULE,
        parameters={},
        param_grid=[{"fast_span": 6}, {"slow_span": 18}],
    )
    diag = _diag(restated)
    assert restated.verdict == "REVISE"
    assert diag["neighbor_grid"]["distinct_numeric_neighbors"] == 0
    assert sorted(diag["neighbor_grid"]["degenerate_indices"]) == [0, 1]

    _, perturbed = _run(
        tmp_path,
        planned_panel,
        monkeypatch=monkeypatch,
        name="t12_defaults_perturbed",
        hyp_id="HYP-T12-DEFAULTS-B",
        module_rel=GATED_REL,
        module_source=GATED_MODULE,
        parameters={},
        param_grid=[{"fast_span": 5}, {"fast_span": 7}],
    )
    diag_ok = _diag(perturbed)
    assert diag_ok["neighbor_grid"]["distinct_numeric_neighbors"] == 2
    assert diag_ok["neighbor_tested"] is True


def test_parameter_free_strategy_qualifies_with_empty_grid_and_exemption(
    pre_rg05_policy,
    tmp_path: Path, planned_panel: pl.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, result = _run(
        tmp_path,
        planned_panel,
        monkeypatch=monkeypatch,
        name="t12_parameter_free",
        hyp_id="HYP-T12-FREE",
        module_rel=FREE_REL,
        module_source=FREE_MODULE,
        parameters={},
        param_grid=[],
    )
    diag = _diag(result)
    assert result.verdict == "CANDIDATE_FOR_QUALIFICATION"
    assert diag["neighbor_exemption"] == "parameter_free"
    assert diag["neighbor_requirement"]["required"] is False
    assert diag["neighbor_requirement"]["met"] is True
    assert diag["neighbor_tested"] is False
    assert "neighbor_stability" not in diag["failed_gates"]
    assert result.param_neighbor_stability is None


def test_bad_declaration_and_bad_grid_cost_no_trial(
    pre_rg05_policy,
    tmp_path: Path, planned_panel: pl.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, contract = _setup_repo(tmp_path, name="t12_bad_params", panel=planned_panel)
    _install_shared_templates(repo)
    monkeypatch.chdir(repo)
    runner = ExperimentRunner(repo_root=repo)

    bad_type = _register(
        repo, contract,
        hyp_id="HYP-T12-BADTYPE", module_rel=GATED_REL, module_source=GATED_MODULE,
        parameters={"fast_span": "six"},
    )
    with pytest.raises(ParamsModelError, match="fast_span"):
        runner.run_spec(bad_type)
    assert _counts(repo) == (0, 0)

    bad_range = _register(
        repo, contract,
        hyp_id="HYP-T12-BADRANGE", module_rel=GATED_REL, module_source=GATED_MODULE,
        parameters={"max_drawdown": 2.0},
    )
    with pytest.raises(ParamsModelError, match="max_drawdown"):
        runner.run_spec(bad_range)
    assert _counts(repo) == (0, 0)

    bad_grid = _register(
        repo, contract,
        hyp_id="HYP-T12-BADGRID", module_rel=GATED_REL, module_source=GATED_MODULE,
        parameters={"fast_span": 6},
        param_grid=[{"fast_span": 1.5}],
    )
    with pytest.raises(ParamsModelError, match=r"param_grid\[0\]"):
        runner.run_spec(bad_grid)
    assert _counts(repo) == (0, 0)

    # The same refusal reaches the CLI as a controlled failure, not a traceback.
    assert cli_main(["backtest", str(bad_grid), "--json"]) == 1
    # ``verify`` validates the declaration before reading market data or writing
    # the feature cache, so a typo cannot produce causal evidence either.
    assert cli_main(["verify", str(bad_type), "--json"]) == 1
    assert _counts(repo) == (0, 0)


# --------------------------------------------------------------------------- #
# Reference funnel evidence
# --------------------------------------------------------------------------- #


def test_reference_rules_template_baseline_rejection_then_neighbor_pass(
    pre_rg05_policy,
    tmp_path: Path, planned_panel: pl.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recorded separately: the shipped reference is REVISE without neighbors."""

    _, rejected = _run(
        tmp_path,
        planned_panel,
        monkeypatch=monkeypatch,
        name="t12_ref_rules_reject",
        hyp_id="HYP-T12-REF-RULES-REJECT",
        module_rel=RULES_REL,
        module_source=(REPO_ROOT / RULES_REL).read_text(encoding="utf-8"),
        parameters={"fast_span": 6, "slow_span": 18, "max_drawdown": 0.10, "stop_loss_pct": 0.05},
        param_grid=[],
    )
    diag = _diag(rejected)
    assert rejected.verdict == "REVISE"
    assert "neighbor_stability" in diag["failed_gates"]
    assert diag["params_model"]["name"] == "DrawdownRulesParams"

    _, accepted = _run(
        tmp_path,
        planned_panel,
        monkeypatch=monkeypatch,
        name="t12_ref_rules_accept",
        hyp_id="HYP-T12-REF-RULES-ACCEPT",
        module_rel=RULES_REL,
        module_source=(REPO_ROOT / RULES_REL).read_text(encoding="utf-8"),
        parameters={"fast_span": 6, "slow_span": 18, "max_drawdown": 0.10, "stop_loss_pct": 0.05},
        param_grid=[
            {"fast_span": 5, "slow_span": 15},
            {"fast_span": 7, "slow_span": 21},
        ],
    )
    diag_ok = _diag(accepted)
    assert diag_ok["neighbor_tested"] is True
    assert accepted.verdict == "CANDIDATE_FOR_QUALIFICATION"


def test_reference_ridge_template_requires_and_accepts_distinct_neighbors(
    pre_rg05_policy,
    tmp_path: Path, planned_panel: pl.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    ridge_source = (REPO_ROOT / RIDGE_REL).read_text(encoding="utf-8")
    parameters = {"ridge_alpha": 10.0, "rebalance_every": 4, "top_k": 3}

    _, rejected = _run(
        tmp_path,
        planned_panel,
        monkeypatch=monkeypatch,
        name="t12_ref_ridge_reject",
        hyp_id="HYP-T12-REF-RIDGE-REJECT",
        module_rel=RIDGE_REL,
        module_source=ridge_source,
        family="cross_sectional_ml",
        model_type="ridge",
        parameters=parameters,
        param_grid=[],
    )
    assert rejected.verdict == "REVISE"
    assert "neighbor_stability" in _diag(rejected)["failed_gates"]

    _, accepted = _run(
        tmp_path,
        planned_panel,
        monkeypatch=monkeypatch,
        name="t12_ref_ridge_accept",
        hyp_id="HYP-T12-REF-RIDGE-ACCEPT",
        module_rel=RIDGE_REL,
        module_source=ridge_source,
        family="cross_sectional_ml",
        model_type="ridge",
        parameters=parameters,
        param_grid=[{"ridge_alpha": 8.5}, {"ridge_alpha": 11.5}],
    )
    diag_ok = _diag(accepted)
    assert diag_ok["neighbor_grid"]["distinct_numeric_neighbors"] == 2
    assert diag_ok["params_model"]["name"] == "RidgeParams"
    assert diag_ok["neighbor_tested"] is True
    assert accepted.verdict == "CANDIDATE_FOR_QUALIFICATION"
