"""Shared market data validation, multi-exchange session assignment, and atomic contract/manifest writes."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import polars as pl
import yaml
from ml4t.data.anomaly import AnomalyConfig, AnomalyManager
from ml4t.data.anomaly.base import AnomalyType
from ml4t.data.anomaly.config import PriceStalenessConfig, ReturnOutlierConfig, VolumeSpikeConfig
from ml4t.data.sessions import SessionAssigner
from ml4t.data.validation import OHLCVValidator
from ml4t.data.validation.rules import ValidationRulePresets
from ml4t.specs import FeedSpec

from warden.contracts import MARKET, ContractValidationError, EvaluationContract

CANONICAL_COLUMNS = ["timestamp", "asset", "open", "high", "low", "close", "volume", "funding_rate"]
_ALIASES = {"symbol": "asset", "ticker": "asset", "datetime": "timestamp", "ts": "timestamp"}
_PRESET_MAP = {
    "crypto_perps": "crypto",
    "fx": "forex",
    "indices": "equities",
    "futures": "futures",
    "metals": "commodities",
    "commodities": "commodities",
    "multi_asset": "equities",
}
DEFAULT_EXCHANGE_MAP: dict[str, str] = {
    "ES": "CME_Equity",
    "NQ": "CME_Equity",
    "DAX": "EUREX",
    "EUSIDXEUR": "EUREX",
    "HK33": "HKEX",
    "NIKKEI225": "JPX",
    "CL": "CMEGlobex_CL",
    "XAUUSD": "CMEGlobex_Gold",
    "XAGUSD": "CMEGlobex_Gold",
    "EURUSD": "24/5",
    "GBPUSD": "24/5",
    "USDJPY": "24/5",
    "AUDUSD": "24/5",
    "USDCAD": "24/5",
    "USDCHF": "24/5",
    "CADJPY": "24/5",
    "GBPJPY": "24/5",
}


def _normalize_aliases(panel: pl.DataFrame) -> pl.DataFrame:
    renames = {k: v for k, v in _ALIASES.items() if k in panel.columns and v not in panel.columns}
    return panel.rename(renames) if renames else panel


def _normalize_timestamp_expr(dtype: pl.DataType) -> pl.Expr:
    if dtype == pl.String:
        return pl.col("timestamp").str.to_datetime(time_zone="UTC").cast(pl.Datetime("us", "UTC"))
    if isinstance(dtype, pl.Datetime):
        if dtype.time_zone is None:
            return pl.col("timestamp").dt.replace_time_zone("UTC").cast(pl.Datetime("us", "UTC"))
        return pl.col("timestamp").dt.convert_time_zone("UTC").cast(pl.Datetime("us", "UTC"))
    raise ValueError(f"Invalid timestamp dtype: {dtype}")


def _raise_validation_issues(symbol: str, issues: list) -> None:
    checks = {issue.check: issue.message for issue in issues}
    if "duplicate_timestamps" in checks:
        raise ValueError(f"Duplicate timestamp/asset keys for {symbol}; refusing silent data loss ({checks['duplicate_timestamps']})")
    if "non_finite_values" in checks:
        raise ValueError(f"Invalid non-finite market values for {symbol} ({checks['non_finite_values']})")
    if "chronological_order" in checks:
        raise ValueError(f"Non-monotonic timestamps for {symbol} ({checks['chronological_order']})")
    detail = "; ".join(f"{c}: {m}" for c, m in checks.items())
    raise ValueError(f"Invalid prices, OHLC ordering, volume or null market values for {symbol} ({detail})")


def validate_ohlcv_panel(
    panel: pl.DataFrame,
    asset_class: str,
    *,
    allow_null_funding: bool | None = None,
    check_anomalies: bool = True,
    max_return_threshold: float | None = None,
) -> pl.DataFrame:
    """Validate and normalize an OHLCV(+funding) panel using OHLCVValidator and AnomalyManager."""
    if asset_class not in _PRESET_MAP:
        raise ValueError(f"Unsupported asset_class: {asset_class!r}")
    panel = _normalize_aliases(panel)
    required = ["timestamp", "asset", "open", "high", "low", "close", "volume"]
    if missing := set(required) - set(panel.columns):
        raise ValueError(f"Missing source columns: {sorted(missing)}")

    null_funding = (asset_class != "crypto_perps") if allow_null_funding is None else allow_null_funding
    if not null_funding and "funding_rate" not in panel.columns:
        raise ValueError("Missing source columns: ['funding_rate']")

    ts_expr = _normalize_timestamp_expr(panel.schema["timestamp"])
    extra_cols = [c for c in panel.columns if c not in CANONICAL_COLUMNS]
    funding_expr = (
        pl.col("funding_rate").cast(pl.Float64)
        if "funding_rate" in panel.columns
        else pl.lit(None, dtype=pl.Float64).alias("funding_rate")
    )
    panel = panel.select(
        ts_expr.alias("timestamp"),
        pl.col("asset").cast(pl.String),
        pl.col(["open", "high", "low", "close", "volume"]).cast(pl.Float64),
        funding_expr,
        *[pl.col(c) for c in extra_cols],
    )

    if panel.is_empty() or panel.select(
        (pl.col("timestamp").is_null() | pl.col("asset").is_null() | (pl.col("asset").str.len_chars() == 0)).any()
    ).item():
        raise ValueError("Empty panel or null market values")

    if not null_funding:
        if panel["funding_rate"].null_count() > 0:
            raise ValueError("Empty panel or null market values in 'funding_rate'")
        if panel.select((~pl.col("funding_rate").is_finite()).any()).item():
            raise ValueError("Invalid non-finite market values in 'funding_rate'")
    elif panel.select((pl.col("funding_rate").is_not_null() & ~pl.col("funding_rate").is_finite()).any()).item():
        raise ValueError("Invalid non-finite market values in 'funding_rate'")

    preset_name = _PRESET_MAP[asset_class]
    rules = ValidationRulePresets.for_asset_class(preset_name)
    ret_threshold = max_return_threshold if max_return_threshold is not None else rules.max_return_threshold
    validator = OHLCVValidator(
        check_nulls=rules.check_nulls,
        check_price_consistency=rules.check_price_consistency,
        negative_price_policy="forbid",
        check_negative_volume=rules.check_negative_volume,
        check_duplicate_timestamps=rules.check_duplicate_timestamps,
        check_chronological_order=rules.check_chronological_order,
        check_price_staleness=rules.check_price_staleness,
        check_extreme_returns=rules.check_extreme_returns,
        max_return_threshold=ret_threshold,
        staleness_threshold=rules.staleness_threshold,
    )
    anomaly_mgr = (
        AnomalyManager(
            AnomalyConfig(
                return_outliers=ReturnOutlierConfig(method="mad", threshold=10.0),
                volume_spikes=VolumeSpikeConfig(enabled=False),
                price_staleness=PriceStalenessConfig(enabled=False),
            )
        )
        if check_anomalies
        else None
    )

    for sub_df in panel.partition_by("asset", maintain_order=True):
        symbol = str(sub_df["asset"][0])
        vr = validator.validate(sub_df)
        if not vr.passed:
            _raise_validation_issues(symbol, [i for i in vr.issues if i.severity.value in {"critical", "error"}])
        if sub_df.filter(pl.any_horizontal(pl.col(["open", "high", "low", "close"]) <= 0)).height:
            raise ValueError(f"Invalid non-positive prices for {symbol}")
        if check_anomalies:
            extreme = [i for i in vr.issues if i.check == "extreme_returns"]
            if extreme:
                raise ValueError(f"Extreme return anomaly for {symbol}: {extreme[0].message}")
            assert anomaly_mgr is not None
            report = anomaly_mgr.analyze(sub_df, symbol=symbol, asset_class=preset_name)
            outliers = report.get_by_type(AnomalyType.RETURN_OUTLIER)
            if max_return_threshold is not None:
                outliers = [a for a in outliers if abs(a.value) / 100.0 > max_return_threshold]
            else:
                outliers = [a for a in outliers if a.severity.value == "critical"]
            if outliers:
                raise ValueError(f"Extreme return anomaly detected for {symbol}: {outliers[0].message}")

    return panel.sort("timestamp", "asset")


def _resolve_exchange_calendar(symbol: str, exchange_map: dict[str, str] | None) -> str:
    if exchange_map and symbol in exchange_map:
        return exchange_map[symbol]
    if symbol in DEFAULT_EXCHANGE_MAP:
        return DEFAULT_EXCHANGE_MAP[symbol]
    if symbol.endswith(("USDT", "USDC", "_PERP", "-PERP")):
        return "24/7"
    raise ValueError(f"No exchange calendar mapped for asset {symbol!r}")


def assign_multi_exchange_sessions(
    panel: pl.DataFrame,
    exchange_map: dict[str, str] | None = None,
) -> pl.DataFrame:
    """Assign per-venue session_date via SessionAssigner without fabricating or dropping bars."""
    panel = _normalize_aliases(panel)
    if "timestamp" not in panel.columns or "asset" not in panel.columns:
        raise ValueError("Panel must contain 'timestamp' and 'asset' columns")
    if panel.is_empty():
        return panel.with_columns(pl.lit(None, dtype=pl.Date).alias("session_date"))

    assigners: dict[str, SessionAssigner] = {}
    frames: list[pl.DataFrame] = []
    for sub_df in panel.partition_by("asset", maintain_order=True):
        symbol = str(sub_df["asset"][0])
        calendar = _resolve_exchange_calendar(symbol, exchange_map)
        if calendar not in assigners:
            assigners[calendar] = SessionAssigner(
                calendar,
                feed_spec=FeedSpec(
                    timestamp_col="timestamp",
                    calendar=calendar,
                    timezone="UTC",
                    time_contract_version=2,
                ),
            )
        frames.append(assigners[calendar].assign_sessions(sub_df, outside_session="null"))
    return pl.concat(frames).sort("timestamp", "asset")


def _fsync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_temp(directory: Path, payload: bytes) -> Path:
    with tempfile.NamedTemporaryFile(dir=directory, delete=False, prefix=".tmp_warden_") as tmp:
        tmp.write(payload)
        tmp.flush()
        os.fsync(tmp.fileno())
        return Path(tmp.name)


def build_dataset_manifest(
    contract_id: str,
    path: str,
    sha256: str,
    provenance: str,
    source: str,
    pipeline: str,
    **extra: Any,
) -> dict[str, Any]:
    """Build a canonical dataset manifest dictionary."""
    return {
        "contract_id": contract_id,
        "path": path,
        "sha256": sha256,
        "provenance": provenance,
        "source": source,
        "pipeline": pipeline,
        **extra,
    }


def write_contract_and_manifest_atomic(
    contract_path: Path,
    contract_dict: dict[str, Any],
    manifest_path: Path,
    manifest_dict: dict[str, Any],
    *,
    allow_overwrite: bool = False,
    repo_root: Path | None = None,
) -> None:
    """Validate provenance and digest synchronization, then atomically write contract YAML + manifest JSON."""
    prov = contract_dict.get("data_provenance")
    if prov not in {MARKET, "synthetic"}:
        raise ContractValidationError(f"data_provenance must be {MARKET!r} or 'synthetic', got {prov!r}")
    try:
        contract = EvaluationContract(**contract_dict)
    except TypeError as exc:
        raise ContractValidationError(f"Invalid contract payload: {exc}") from exc

    m_prov = manifest_dict.get("provenance")
    if m_prov not in {MARKET, "synthetic"} or m_prov != contract.data_provenance:
        raise ContractValidationError(
            f"Manifest provenance {m_prov!r} must match contract provenance {contract.data_provenance!r}"
        )
    if manifest_dict.get("contract_id") != contract.contract_id:
        raise ContractValidationError(
            f"Manifest contract_id {manifest_dict.get('contract_id')!r} != {contract.contract_id!r}"
        )
    m_path = manifest_dict.get("path") or manifest_dict.get("exploratory_path")
    if m_path != contract.exploratory_data_path:
        raise ContractValidationError(
            f"Manifest path {m_path!r} != contract path {contract.exploratory_data_path!r}"
        )
    m_sha = manifest_dict.get("sha256") or manifest_dict.get("exploratory_sha256")
    if not m_sha or m_sha != contract.data_snapshot_sha256:
        raise ContractValidationError(
            f"Manifest sha256 {m_sha!r} != contract data_snapshot_sha256 {contract.data_snapshot_sha256!r}"
        )

    exp_path = Path(contract.exploratory_data_path)
    if exp_path.is_absolute():
        data_file: Path | None = exp_path
    elif repo_root is not None:
        data_file = Path(repo_root) / exp_path
    elif contract_path.parent.name == "contracts":
        data_file = contract_path.parent.parent / exp_path
    else:
        data_file = None

    if data_file is not None:
        if repo_root is not None and not data_file.is_file():
            raise ContractValidationError(f"Exploratory data file not found: {data_file}")
        if (
            data_file.is_file()
            and hashlib.sha256(data_file.read_bytes()).hexdigest() != contract.data_snapshot_sha256
        ):
            raise ContractValidationError(
                f"Exploratory data digest differs from pinned sha256 for {contract.contract_id}"
            )

    contract_bytes = yaml.safe_dump(contract_dict, sort_keys=False).encode("utf-8")
    manifest_bytes = (json.dumps(manifest_dict, indent=2, sort_keys=True) + "\n").encode("utf-8")

    existing_doc = (
        yaml.safe_load(contract_path.read_text(encoding="utf-8")) or {}
        if contract_path.exists()
        else {}
    )
    if (
        isinstance(existing_doc, dict)
        and existing_doc.get("data_provenance") == MARKET
        and contract.data_provenance != MARKET
    ):
        raise ContractValidationError(
            f"Refusing to downgrade {contract.contract_id} provenance from {MARKET!r} to {contract.data_provenance!r}"
        )

    if not allow_overwrite:
        if (
            contract_path.exists()
            and manifest_path.exists()
            and contract_path.read_bytes() == contract_bytes
            and manifest_path.read_bytes() == manifest_bytes
        ):
            return
        is_frozen = (
            isinstance(existing_doc, dict)
            and bool(existing_doc.get("data_snapshot_sha256"))
            and existing_doc.get("data_provenance") not in (None, "", "unknown")
        )
        if contract_path.exists() and contract_path.read_bytes() != contract_bytes and is_frozen:
            raise FileExistsError(f"Refusing to overwrite existing frozen contract {contract_path}")
        if manifest_path.exists() and manifest_path.read_bytes() != manifest_bytes:
            raise FileExistsError(f"Refusing to overwrite existing frozen manifest {manifest_path}")

    contract_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    c_tmp = _write_temp(contract_path.parent, contract_bytes)
    m_tmp = _write_temp(manifest_path.parent, manifest_bytes)
    prev_c = contract_path.read_bytes() if contract_path.exists() else None
    try:
        os.replace(c_tmp, contract_path)
        try:
            os.replace(m_tmp, manifest_path)
        except Exception:
            if prev_c is not None:
                rollback = _write_temp(contract_path.parent, prev_c)
                os.replace(rollback, contract_path)
            else:
                contract_path.unlink(missing_ok=True)
            raise
        _fsync_dir(contract_path.parent)
        if manifest_path.parent != contract_path.parent:
            _fsync_dir(manifest_path.parent)
    finally:
        c_tmp.unlink(missing_ok=True)
        m_tmp.unlink(missing_ok=True)
