"""Frozen governance records, adapted from run_vertical_slice.py section 1."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml


class ContractValidationError(ValueError):
    """An experiment specification violates the evaluation boundary."""


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, default=str, allow_nan=False)


def utc(value) -> datetime:
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(result, datetime) or result.tzinfo is None:
        raise ContractValidationError("timestamps must include an explicit timezone")
    return result.astimezone(UTC)


def valid_id(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ContractValidationError("IDs must contain only letters, digits, '_' or '-'")


@dataclass(frozen=True)
class EvaluationContract:
    contract_id: str
    asset_class: str
    frequency: str
    bars_per_year: int
    exploratory_data_path: str
    holdout_data_path: str
    train_start: datetime
    val_start: datetime
    val_end: datetime
    holdout_start: datetime
    holdout_end: datetime
    n_folds: int
    label_horizon_bars: int
    embargo_bars: int
    execution_delay_bars: int
    taker_fee_bps: float
    slippage_bps: float
    funding_bps_per_8h: float = 0.0
    execution_mode: str = "NEXT_BAR"
    min_expected_move_to_cost_ratio: float = 2.5
    data_snapshot_sha256: str = ""

    def __post_init__(self):
        valid_id(self.contract_id)
        for name in ("train_start", "val_start", "val_end", "holdout_start", "holdout_end"):
            object.__setattr__(self, name, utc(getattr(self, name)))
        dates = [self.train_start, self.val_start, self.val_end, self.holdout_start, self.holdout_end]
        if not (dates[0] < dates[1] < dates[2] <= dates[3] < dates[4]):
            raise ContractValidationError("chronological train/validation/holdout boundaries overlap")
        if self.asset_class not in {"crypto_perps", "fx", "indices", "metals"}:
            raise ContractValidationError("unknown asset_class")
        if self.frequency not in {"5m", "15m", "1h", "4h", "1d"}:
            raise ContractValidationError("unknown frequency")
        if self.execution_mode not in {"NEXT_BAR", "QUOTE_SIDE"}:
            raise ContractValidationError("noncausal execution mode")
        for name in ("bars_per_year", "n_folds", "label_horizon_bars", "embargo_bars", "execution_delay_bars"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ContractValidationError(f"{name} must be a positive integer")
        for name in ("taker_fee_bps", "slippage_bps", "min_expected_move_to_cost_ratio"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ContractValidationError(f"{name} must be finite and positive")
        if not math.isfinite(self.funding_bps_per_8h) or self.funding_bps_per_8h < 0:
            raise ContractValidationError("funding floor must be finite and nonnegative")
        if self.data_snapshot_sha256 and not re.fullmatch(r"[0-9a-f]{64}", self.data_snapshot_sha256):
            raise ContractValidationError("data_snapshot_sha256 must be a SHA-256 digest")
        if not self.exploratory_data_path or self.exploratory_data_path == self.holdout_data_path:
            raise ContractValidationError("exploratory and holdout paths must be distinct")

    def total_one_way_cost_decimal(self) -> float:
        return (self.taker_fee_bps + self.slippage_bps) * 1e-4

    def spec_hash(self) -> str:
        return hashlib.sha256(canonical_json(asdict(self)).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class HypothesisSpec:
    hypothesis_id: str
    contract_id: str
    title: str
    strategy_family: str
    economic_mechanism: str
    counterparty_or_friction: str
    expected_holding_bars: int
    falsification_criteria: str
    parent_hypothesis_id: str | None = None
    source_provenance: str = "internal_research"
    llm_model_id: str = "unspecified"

    def __post_init__(self):
        valid_id(self.hypothesis_id)
        valid_id(self.contract_id)
        if self.parent_hypothesis_id:
            valid_id(self.parent_hypothesis_id)
        if self.parent_hypothesis_id == self.hypothesis_id:
            raise ContractValidationError("hypothesis cannot be its own parent")
        if self.strategy_family not in {"rules_ts", "cross_sectional_ml", "carry_rv", "meta_overlay"}:
            raise ContractValidationError("unknown strategy_family")
        for name in ("economic_mechanism", "counterparty_or_friction"):
            if not isinstance(getattr(self, name), str) or len(getattr(self, name).strip()) < 20:
                raise ContractValidationError(f"{name} must explain at least 20 characters")
        if type(self.expected_holding_bars) is not int or self.expected_holding_bars < 1:
            raise ContractValidationError("expected_holding_bars must be a positive integer")
        if not self.title.strip() or not self.falsification_criteria.strip():
            raise ContractValidationError("title and falsification criteria are required")

    @classmethod
    def from_yaml(cls, path: Path):
        return _load(path, cls)

    @classmethod
    def from_markdown(cls, path: Path):
        return _load(path, cls, markdown=True)


@dataclass(frozen=True)
class Stage1Report:
    strategy_family: str
    passed: bool
    metrics: dict[str, float | None] = field(default_factory=dict)
    notes: str = ""

    def to_dict(self):
        return asdict(self)

    def to_json(self):
        return canonical_json(self.to_dict())


@dataclass(frozen=True)
class ExperimentResult:
    run_id: str
    hypothesis_id: str
    contract_id: str
    strategy_name: str
    strategy_family: str
    config_hash: str
    stage_reached: str
    verdict: str
    gross_sharpe: float
    net_sharpe: float
    deflated_sharpe_prob: float
    expected_max_sharpe_noise: float
    total_trials_n: int
    effective_trials_keff: float
    mean_ic: float | None
    ic_hac_tstat: float | None
    hit_rate: float
    profit_factor: float
    max_drawdown_pct: float
    annualized_turnover: float
    breakeven_cost_bps: float
    stress_2x_cost_sharpe: float
    param_neighbor_stability: float
    notes: str
    delay_plus1_sharpe: float = 0.0
    cvar_99_pct: float = 0.0

    def to_dict(self):
        return asdict(self)

    def to_json(self):
        return canonical_json(self.to_dict())


def _load(path, cls, markdown=False):
    try:
        text = Path(path).read_text()
        if markdown:
            match = re.match(r"\A---\s*\n(.*?)\n---(?:\n|$)", text, re.DOTALL)
            if not match:
                raise ContractValidationError("missing Markdown YAML frontmatter")
            text = match.group(1)
        value = yaml.safe_load(text)
        if not isinstance(value, dict):
            raise ContractValidationError("specification must be a YAML mapping")
        return cls(**value)
    except (TypeError, ValueError, yaml.YAMLError) as exc:
        raise ContractValidationError(f"{path}: {exc}") from exc


def load_contract(path: Path) -> EvaluationContract:
    return _load(path, EvaluationContract)


def load_hypothesis(path: Path) -> HypothesisSpec:
    return _load(path, HypothesisSpec, markdown=Path(path).suffix == ".md")
