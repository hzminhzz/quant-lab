"""Frozen governance records."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import yaml
from ml4t.specs import FeedSpec

MARKET = "verified_market"
FREQUENCY_BAR_DURATION: dict[str, timedelta] = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
}


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
    # Declared origin, independent of the digest. Only "verified_market" may reach
    # market qualification; "unknown" fails closed.
    data_provenance: str = "unknown"

    def __post_init__(self):
        valid_id(self.contract_id)
        for n in ("train_start", "val_start", "val_end", "holdout_start", "holdout_end"):
            object.__setattr__(self, n, utc(getattr(self, n)))
        d = [self.train_start, self.val_start, self.val_end, self.holdout_start, self.holdout_end]
        if not (d[0] < d[1] < d[2] <= d[3] < d[4]):
            raise ContractValidationError("chronological boundaries overlap")
        if self.asset_class not in {"crypto_perps", "fx", "indices", "metals", "commodities", "multi_asset"} or self.frequency not in FREQUENCY_BAR_DURATION or self.execution_mode not in {"NEXT_BAR", "QUOTE_SIDE"}:
            raise ContractValidationError("invalid asset_class, frequency, or execution_mode")
        for n in ("bars_per_year", "n_folds", "label_horizon_bars", "embargo_bars", "execution_delay_bars"):
            if type(getattr(self, n)) is not int or getattr(self, n) < 1:
                raise ContractValidationError(f"{n} must be positive int")
        for n in ("taker_fee_bps", "slippage_bps", "min_expected_move_to_cost_ratio"):
            v = getattr(self, n)
            if not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
                raise ContractValidationError(f"{n} must be finite positive")
        if not math.isfinite(self.funding_bps_per_8h) or self.funding_bps_per_8h < 0 or (self.data_snapshot_sha256 and not re.fullmatch(r"[0-9a-f]{64}", self.data_snapshot_sha256)) or not self.exploratory_data_path or self.exploratory_data_path == self.holdout_data_path:
            raise ContractValidationError("invalid funding, sha256 or data paths")
        if self.data_provenance not in {MARKET, "synthetic", "unknown"}:
            raise ContractValidationError("data_provenance must be verified_market, synthetic or unknown")
        if self.data_provenance != "unknown" and not re.fullmatch(r"[0-9a-f]{64}", self.data_snapshot_sha256 or ""):
            raise ContractValidationError("declared data_provenance requires a data_snapshot_sha256 pin")

    def total_one_way_cost_decimal(self) -> float:
        return (self.taker_fee_bps + self.slippage_bps) * 1e-4

    def bar_duration(self) -> timedelta:
        return FREQUENCY_BAR_DURATION[self.frequency]

    def spec_hash(self) -> str:
        return hashlib.sha256(canonical_json(asdict(self)).encode()).hexdigest()[:16]

    def to_feed_spec(self) -> FeedSpec:
        return FeedSpec(
            timestamp_col="timestamp",
            entity_col="asset",
            open_col="open",
            high_col="high",
            low_col="low",
            close_col="close",
            volume_col="volume",
            bid_col="bid" if self.execution_mode == "QUOTE_SIDE" else None,
            ask_col="ask" if self.execution_mode == "QUOTE_SIDE" else None,
            calendar=None,
            timezone="UTC",
            data_frequency=self.frequency,
            time_contract_version=2,
        )


def validate_panel_feed_spec(panel: pl.DataFrame, contract: EvaluationContract) -> FeedSpec:
    spec = contract.to_feed_spec()
    if panel.is_empty():
        raise ContractValidationError(f"{contract.contract_id}: panel is empty")
    try:
        resolved = spec.resolve(panel.columns, ("asset",))
    except ValueError as exc:
        raise ContractValidationError(f"{contract.contract_id}: {exc}") from exc

    ts_col = resolved.timestamp_col
    ts_dtype = panel.schema[ts_col]
    if not isinstance(ts_dtype, pl.Datetime) or ts_dtype.time_zone not in {"UTC", "Etc/UTC"}:
        raise ContractValidationError(
            f"{contract.contract_id}: '{ts_col}' must be UTC-aware Datetime, got {ts_dtype}"
        )
    if panel.select(pl.col(ts_col).is_null().any()).item():
        raise ContractValidationError(f"{contract.contract_id}: null timestamps are not allowed")
    entity_col = str(resolved.entity_col)
    if panel.select(
        (pl.col(entity_col).is_null() | (pl.col(entity_col).cast(pl.String).str.len_chars() == 0)).any()
    ).item():
        raise ContractValidationError(f"{contract.contract_id}: null or empty '{entity_col}' values")

    ohlcv_cols = [resolved.open_col, resolved.high_col, resolved.low_col, resolved.close_col, resolved.volume_col]
    missing_ohlcv = [c for c in ohlcv_cols if c not in panel.columns]
    if missing_ohlcv:
        raise ContractValidationError(f"{contract.contract_id}: missing OHLCV columns {missing_ohlcv}")
    for col in ohlcv_cols:
        if not panel.schema[col].is_numeric():
            raise ContractValidationError(f"{contract.contract_id}: '{col}' must be numeric, got {panel.schema[col]}")
        if panel.select((pl.col(col).is_null() | ~pl.col(col).is_finite()).any()).item():
            raise ContractValidationError(f"{contract.contract_id}: null or non-finite values in '{col}'")
    for col in ohlcv_cols[:4]:
        if panel.select((pl.col(col) <= 0).any()).item():
            raise ContractValidationError(f"{contract.contract_id}: non-positive prices in '{col}'")
    if panel.select((pl.col(resolved.volume_col) < 0).any()).item():
        raise ContractValidationError(f"{contract.contract_id}: negative volume in '{resolved.volume_col}'")

    if contract.execution_mode == "QUOTE_SIDE":
        for col in (resolved.bid_col, resolved.ask_col):
            if col not in panel.columns:
                raise ContractValidationError(f"{contract.contract_id}: QUOTE_SIDE requires '{col}'")
            if not panel.schema[col].is_numeric():
                raise ContractValidationError(f"{contract.contract_id}: '{col}' must be numeric")
            if panel.select((pl.col(col).is_null() | ~pl.col(col).is_finite() | (pl.col(col) <= 0)).any()).item():
                raise ContractValidationError(f"{contract.contract_id}: '{col}' must be non-null finite positive")
        if panel.select((pl.col(resolved.ask_col) < pl.col(resolved.bid_col)).any()).item():
            raise ContractValidationError(f"{contract.contract_id}: QUOTE_SIDE requires ask >= bid")

    if contract.asset_class == "crypto_perps":
        if "funding_rate" not in panel.columns:
            raise ContractValidationError(f"{contract.contract_id}: crypto_perps requires 'funding_rate'")
        if not panel.schema["funding_rate"].is_numeric():
            raise ContractValidationError(f"{contract.contract_id}: 'funding_rate' must be numeric")
        if panel.select((pl.col("funding_rate").is_null() | ~pl.col("funding_rate").is_finite()).any()).item():
            raise ContractValidationError(f"{contract.contract_id}: crypto_perps requires non-null finite 'funding_rate'")
    elif "funding_rate" in panel.columns:
        f_dtype = panel.schema["funding_rate"]
        if f_dtype != pl.Null and not f_dtype.is_numeric():
            raise ContractValidationError(f"{contract.contract_id}: 'funding_rate' must be numeric or null")
        if f_dtype != pl.Null and panel.select(
            (pl.col("funding_rate").is_not_null() & ~pl.col("funding_rate").is_finite()).any()
        ).item():
            raise ContractValidationError(f"{contract.contract_id}: non-finite values in 'funding_rate'")

    return resolved


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
    strategy_module: str = ""

    def __post_init__(self):
        valid_id(self.hypothesis_id)
        valid_id(self.contract_id)
        if self.parent_hypothesis_id:
            valid_id(self.parent_hypothesis_id)
        if self.parent_hypothesis_id == self.hypothesis_id or self.strategy_family not in {"rules_ts", "cross_sectional_ml", "carry_rv", "meta_overlay"}:
            raise ContractValidationError("invalid parent or strategy_family")
        for n in ("economic_mechanism", "counterparty_or_friction"):
            if not isinstance(getattr(self, n), str) or len(getattr(self, n).strip()) < 20:
                raise ContractValidationError(f"{n} must explain at least 20 chars")
        if type(self.expected_holding_bars) is not int or self.expected_holding_bars < 1 or not self.title.strip() or not self.falsification_criteria.strip():
            raise ContractValidationError("invalid holding bars, title, or criteria")

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
    param_neighbor_stability: float | None
    notes: str
    delay_plus1_sharpe: float = 0.0
    cvar_99_pct: float = 0.0
    synthetic: int = 0
    data_snapshot_sha256: str = ""
    strategy_module: str = ""
    model_type: str = ""
    returns_sha256: str = ""
    data_provenance: str = "unknown"
    diagnostics_json: str = "{}"
    stress_1p5_cost_sharpe: float | None = None
    strategy_code_sha256: str = ""

    def to_dict(self):
        return asdict(self)


def _load(path, cls, markdown=False):
    try:
        text = Path(path).read_text()
        if markdown:
            m = re.match(r"\A---\s*\n(.*?)\n---(?:\n|$)", text, re.DOTALL)
            if not m:
                raise ContractValidationError("missing Markdown frontmatter")
            text = m.group(1)
        v = yaml.safe_load(text)
        if not isinstance(v, dict):
            raise ContractValidationError("spec must be YAML mapping")
        valid_keys = {f.name for f in fields(cls)}
        return cls(**{k: val for k, val in v.items() if k in valid_keys})
    except (TypeError, ValueError, yaml.YAMLError) as exc:
        raise ContractValidationError(f"{path}: {exc}") from exc


def load_contract(path: Path) -> EvaluationContract:
    return _load(path, EvaluationContract)


def load_hypothesis(path: Path) -> HypothesisSpec:
    return _load(path, HypothesisSpec, markdown=Path(path).suffix == ".md")
