"""Source-faithful read-only PIT low-vol portfolio economics preflight.

Uses the exact frozen PIT Warden Stage1 low-vol module and unchanged targets,
one-time execution delay, exact NEXT-HOUR Binance opens (never next available
PIT row), a SHA-verified source bridge for monthly roster exits, and actual
Binance settlement-event funding timestamps from V3 source recovery.

Produces preliminary economic evidence only. This DOES NOT call Warden
screen/backtest, create a full trial, alter protected contracts, infer broker
fills, use post-validation market prices, or qualify a strategy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from bisect import bisect_left
from collections import Counter
from datetime import datetime, timedelta
from itertools import pairwise
from pathlib import Path

import numpy as np
import polars as pl
import yaml

from scripts.audit_funding_boundary_ordering import reconcile_event_positions
from scripts.evaluate_lowvol_terminal_close import terminal_flat_targets
from scripts.recover_pit_funding_archives import hash_file
from warden.contracts import load_contract
from warden.runner import _run_strategy_isolated, calculate_metrics_from_returns

SPEC = Path("experiments/active/arxiv_crypto_lowvol_pit100_20261102.yaml")
CONTRACT = Path("contracts/crypto_perps_pit100_1h_v1.yaml")
PIT = Path("data/exploratory/crypto_perps_pit100_1h_v1.parquet")
LEDGER = Path("data/exploratory/crypto_perps_pit100_event_ledger_v3.parquet")
LEDGER_RECEIPT = Path("docs/evidence/arxiv_stage1_20261102/pit_funding_overlay_v3_20261102.json")
PRICE_RECEIPT = Path("docs/evidence/arxiv_stage1_20261102/pit_monthly_exit_source_audit_20261102.json")
FUNDING_ROOT = Path("/home/quant/dev/quant/ml4t-data/.data/store/crypto_futures_ohlcv_1h_BINANCE_UM_PERP")
OUT = Path("docs/evidence/arxiv_stage1_20261102/pit_lowvol_event_economics_preflight_20261102.json")
TERMINAL_PRICE_CUTOFF = datetime.fromisoformat("2025-07-01T00:00:00+00:00")
FEE_MODELS = (
    ("base_cost", 1.0, 0),
    ("double_cost", 2.0, 0),
    ("delay_plus_one", 1.0, 1),
)


def exact_hour_matrix(
    panel: pl.DataFrame, timestamps: list[datetime], assets: list[str],
) -> np.ndarray:
    if "timestamp" not in panel.columns or "asset" not in panel.columns or "open" not in panel.columns:
        raise ValueError("Missing original observed PIT source fields")
    if len(set(timestamps)) != len(timestamps) or len(set(assets)) != len(assets):
        raise ValueError("Duplicate time/asset keys")
    if panel.select(pl.struct(["timestamp", "asset"]).is_duplicated().any()).item():
        raise ValueError("Duplicate original PIT price observations")
    pivot = panel.select("timestamp", "asset", "open").pivot(
        index="timestamp", on="asset", values="open"
    ).sort("timestamp")
    if set(timestamps) != set(pivot["timestamp"].to_list()):
        raise ValueError("PIT clock changed under price pivot")
    if set(assets) - set(pivot.columns):
        raise ValueError("PIT asset universe changed under price pivot")
    return pivot.select(assets).to_numpy().astype(np.float64)


def bridge_month_exits(
    opens: np.ndarray,
    timestamps: list[datetime],
    assets: list[str],
    evidence: dict,
    source_root: Path = FUNDING_ROOT,
) -> tuple[np.ndarray, dict]:
    """Add only HASH-PINNED exact UTC hourly opens for 30 roster exits.

    Does not alter the original PIT data source or look beyond validation
    cutoff. Verifies each market source byte and parity when a source price
    overlaps an observed PIT panel price. No future-dependent ranking:
    frozen target weights are computed BEFORE calling this method.
    """
    if opens.shape != (len(timestamps), len(assets)):
        raise ValueError("Observed PIT price matrix has wrong shape")
    if timestamps[-1] >= TERMINAL_PRICE_CUTOFF:
        raise ValueError("Clock crosses protected price cutoff")
    if any((b - a) != timedelta(hours=1) for a, b in pairwise(timestamps)):
        raise ValueError("Non-contiguous global validation clock")
    transitions = evidence["exit_coverage"]["transition_evidence"]
    if (
        evidence["status"] != "READ_ONLY_EXECUTION_SOURCE_BRIDGE_NOT_STAGE3"
        or evidence["exit_coverage"]["monthly_position_boundary_pairs"] != len(transitions)
        or len(transitions) == 0
        or evidence["no_holdout_access"] is not True
    ):
        raise ValueError("Untrusted or incomplete original monthly exit receipt")
    idx = {t: i for i, t in enumerate(timestamps)}
    col = {a: j for j, a in enumerate(assets)}
    out = opens.copy()
    source_count = Counter()
    injected = 0
    injected_keys: list[str] = []
    verified_sources: dict[str, str] = {}
    for entry in transitions:
        if entry["status"] != "SOURCE_OBSERVED_NEXT_OPEN" or entry["actual_fill_verified"]:
            raise ValueError("Missing raw source or invalid fill claim")
        asset = entry["asset"]
        first = datetime.fromisoformat(entry["required_exit_hour"])
        if (
            asset not in col or first not in idx
            or first >= TERMINAL_PRICE_CUTOFF
            or first - timedelta(hours=1) != datetime.fromisoformat(entry["decision"])
        ):
            raise ValueError("Wrong monthly bridge asset/UTC hour")
        rel = entry["relative_source"]
        expected = entry["source_digest"]
        if not isinstance(expected, str) or len(expected) != 64:
            raise ValueError("Missing source SHA for monthly exit")
        source = source_root / rel
        if (source_root not in source.parents or not source.is_file()
                or hash_file(source) != expected):
            raise ValueError(f"Monthly Binance price source missing or changed: {rel}")
        verified_sources[rel] = expected
        source_count[asset] += 1
        # The held position can persist 1-2 additional hours after
        # a selection change due to Warden's NEXT_BAR execution delay.
        cutoff = min(first + timedelta(hours=4), TERMINAL_PRICE_CUTOFF)
        rows = (
            pl.scan_parquet(source)
            .select(
                pl.col("timestamp").cast(pl.Datetime("us", "UTC")),
                pl.col("symbol").alias("asset"),
                pl.col("open").cast(pl.Float64),
                pl.col("volume").cast(pl.Float64),
            )
            .filter((pl.col("timestamp") >= first) & (pl.col("timestamp") < cutoff))
            .collect()
        )
        if rows.is_empty():
            raise ValueError(f"No observed exact source open for {asset} {first}")
        seen = set()
        for row in rows.iter_rows(named=True):
            t, sym, px, vol = row["timestamp"], row["asset"], row["open"], row["volume"]
            if t not in idx or sym != asset or t in seen:
                raise ValueError("Invalid raw price hour, identity, or duplicate")
            seen.add(t)
            if px is None or not np.isfinite(px) or px <= 0 or vol is None or vol <= 0:
                raise ValueError("Unexecutable/nonpositive hourly raw open or volume")
            i, j = idx[t], col[asset]
            if np.isfinite(out[i, j]):
                if not np.isclose(out[i, j], px, atol=1e-10, rtol=1e-12):
                    raise ValueError("Raw monthly source disagrees with existing PIT observed price")
            else:
                out[i, j] = px
                injected += 1
                injected_keys.append(f"{t.isoformat()}|{asset}")
        i, j = idx[first], col[asset]
        if not np.isclose(out[i, j], entry["observed_next_open"], atol=1e-10, rtol=1e-12):
            raise ValueError("Observed next-hour source mismatch with immutable exit receipt")
    return out, {
        "source_verified_transition_count": len(transitions),
        "raw_verified_source_files": len(verified_sources),
        "bridged_missing_asset_hour_opens": injected,
        "bridged_keys_sha256": hashlib.sha256(
            json.dumps(sorted(injected_keys)).encode()
        ).hexdigest(),
        "bridge_assets_distinct": len(source_count),
        "sources_sha256": hashlib.sha256(
            json.dumps(sorted(verified_sources.items())).encode()
        ).hexdigest(),
        "source_price_never_fabricated": True,
        "fills_observed": False,
    }


def observed_portfolio_returns(
    filled: np.ndarray,
    validated_opens: np.ndarray,
    *,
    validation_start: int,
    validation_stop: int,
    per_side_cost: float,
) -> dict[str, np.ndarray | dict]:
    """Replay exactly one observed UTC hour; no next-available-bar shortcut."""
    if filled.shape != validated_opens.shape:
        raise ValueError("Portfolio and open-price matrices are incompatible")
    if per_side_cost < 0 or validation_start < 0 or validation_stop > len(filled):
        raise ValueError("Invalid execution costs or slice")
    if validation_stop <= validation_start or not np.isfinite(filled).all():
        raise ValueError("Invalid position state")
    pos = filled[validation_start:validation_stop]
    prices = validated_opens[validation_start:validation_stop]
    active = np.abs(pos) > 1e-12
    valid_current = np.isfinite(prices) & (prices > 0)
    nxt = np.full_like(prices, np.nan, dtype=np.float64)
    nxt[:-1] = prices[1:]
    valid_next = np.isfinite(nxt) & (nxt > 0)
    invalid = np.argwhere(active & (~valid_current | ~valid_next))
    if len(invalid):
        raise ValueError(f"Active position lacks exact current/next open: {invalid[:8].tolist()}")
    if np.abs(pos[-1]).sum() > 1e-12:
        raise ValueError("Terminal position must be flattened before protected cutoff")
    hourly_ret = np.zeros_like(prices)
    np.divide(nxt, prices, out=hourly_ret, where=valid_current & valid_next)
    hourly_ret -= np.where(valid_current & valid_next, 1.0, 0.0)
    # Suppress 0*nan; do not mask missing values on an active position.
    gross = np.sum(np.where(active, pos * hourly_ret, 0.0), axis=1)
    turn = np.abs(np.diff(pos, axis=0, prepend=0.0)).sum(axis=1)
    fees = turn * per_side_cost
    if not (np.isfinite(gross).all() and np.isfinite(turn).all()):
        raise ValueError("Nonfinite causal portfolio returns")
    return {
        "gross": gross,
        "turnover": turn,
        "execution_fees": fees,
        "coverage": {
            "active_asset_hours": int(active.sum()),
            "missing_active_price_events": len(invalid),
            "maximum_gross_exposure": float(np.abs(pos).sum(axis=1).max()),
            "terminal_position_gross": float(np.abs(pos[-1]).sum()),
            "sum_turnover": float(turn.sum()),
        },
    }


def evaluate(
    panel_path: Path = PIT,
    ledger_path: Path = LEDGER,
    source_receipt: Path = PRICE_RECEIPT,
) -> dict:
    spec = yaml.safe_load(SPEC.read_text())
    contract = load_contract(CONTRACT)
    v3 = json.loads(LEDGER_RECEIPT.read_text())
    if spec["contract_id"] != contract.contract_id:
        raise ValueError("Wrong signed PIT Stage1 contract")
    if contract.val_end != TERMINAL_PRICE_CUTOFF:
        raise ValueError("Frozen validation boundary changed")
    if hash_file(panel_path) != contract.data_snapshot_sha256:
        raise ValueError("Original PIT source hash changed")
    if hash_file(ledger_path) != v3["data"]["event_ledger_sha256"]:
        raise ValueError("Repaired observed-event ledger changed")
    if v3["data"]["after_overlay_missing_rows"]:
        raise ValueError("Recovered source funding rate still unknown")
    if hash_file(source_receipt) != (
        "28bc41728a746c2571386baa2ce7da4cdd2272a28ae680900998456060e96aa8"
    ):
        raise ValueError("Signed PIT exit evidence source SHA changed")
    exit_json = json.loads(source_receipt.read_text())
    if (
        exit_json["dataset_sha256"] != contract.data_snapshot_sha256
        or exit_json["exit_coverage"]["monthly_position_boundary_pairs"] != 30
    ):
        raise ValueError("Exit source pin is for different PIT contract")

    panel = pl.read_parquet(panel_path).sort("timestamp", "asset")
    stamps = sorted(panel["timestamp"].unique().to_list())
    assets = sorted(panel["asset"].unique().to_list())
    start = bisect_left(stamps, contract.val_start)
    stop = bisect_left(stamps, contract.val_end)
    if stop != len(stamps) or stop <= start or stamps[stop - 1] >= contract.val_end:
        raise ValueError("PIT source contains unavailable protected prices")
    if any((b - a) != timedelta(hours=1) for a, b in pairwise(stamps)):
        raise ValueError("Non-consecutive original validation hourly time grid")
    original_opens = exact_hour_matrix(panel, stamps, assets)
    targets_result = _run_strategy_isolated(
        Path(spec["strategy_module"]), panel, contract, spec["parameters"]
    )
    targets = np.asarray(targets_result["positions"], dtype=np.float64)
    if targets.shape != original_opens.shape or not np.isfinite(targets).all():
        raise ValueError("Frozen PIT Stage1 target positions are malformed")
    if (np.abs(targets).sum(axis=1) > 1.0000001).any():
        raise ValueError("Unexpected portfolio leverage in frozen target vector")

    bridged, bridge_info = bridge_month_exits(
        original_opens, stamps, assets, exit_json,
    )
    rates = pl.read_parquet(ledger_path)
    rates = rates.filter(
        (pl.col("timestamp") >= contract.val_start)
        & (pl.col("timestamp") < contract.val_end)
    )
    if rates["funding_rate"].null_count():
        raise ValueError("Actual event ledger has unknown historical settlement rates")
    scenarios = {}
    for name, multiplier, delay_extra in FEE_MODELS:
        lag = contract.execution_delay_bars + delay_extra
        flat = terminal_flat_targets(
            targets, val_start=start, val_end=stop, execution_delay=lag,
        )
        held = np.zeros_like(flat)
        held[lag:] = flat[:-lag]
        replay = observed_portfolio_returns(
            held, bridged,
            validation_start=start, validation_stop=stop,
            per_side_cost=contract.total_one_way_cost_decimal() * multiplier,
        )
        positions = held[start:stop]
        event_cash = reconcile_event_positions(
            rates, stamps[start:stop], assets, positions
        )
        variants = {}
        for outcome, key in (
            ("settlement_pre_open", "funding_cost_pre_open"),
            ("settlement_post_open", "funding_cost_post_open"),
            ("optimistic_ordering_bound", "funding_cost_best_bound"),
            ("conservative_ordering_bound", "funding_cost_worst_bound"),
        ):
            net = replay["gross"] - replay["execution_fees"] - event_cash[key]
            metrics = calculate_metrics_from_returns(
                replay["gross"], net, replay["turnover"], contract.bars_per_year,
            )
            variants[outcome] = {
                "net_sharpe": metrics["net_sharpe"],
                "profit_factor": metrics["profit_factor"],
                "gross_sharpe": metrics["gross_sharpe"],
                "max_drawdown_pct": metrics["max_drawdown_pct"],
                "annualized_turnover": metrics["annualized_turnover"],
                "net_simple_return_pct": float(100.0 * (np.prod(1.0 + net) - 1)),
                "observed_event_funding_equity_pct": float(100 * event_cash[key].sum()),
            }
        scenarios[name] = {
            "cost_multiplier": multiplier,
            "additional_hours_delay": delay_extra,
            "portfolio_coverage": replay["coverage"],
            "event_classification": event_cash["event_statistics"],
            "variants": variants,
        }
    return {
        "schema": "quantlab-pit-lowvol-event-funded-economic-preflight-v1",
        "status": "SOURCE_VERIFIED_RESEARCH_DIAGNOSTIC_NOT_WARDEN_STAGE3",
        "original_hypothesis_id": spec["hypothesis_id"],
        "original_stage1_attempt": "ATT-480df2ef5d8788f2-001",
        "original_contract_sha256": contract.data_snapshot_sha256,
        "event_ledger_sha256": hash_file(ledger_path),
        "price_exit_evidence_sha256": hash_file(source_receipt),
        "model_module": spec["strategy_module"],
        "model_frozen_parameters": spec["parameters"],
        "valuation_window": {
            "begin": contract.val_start.isoformat(),
            "end_exclusive": contract.val_end.isoformat(),
            "hours": stop - start,
            "asset_count": len(assets),
        },
        "market_price_bridge": bridge_info,
        "scenarios": scenarios,
        "known_limitations": [
            "Previously inspected 2024-25 validation set, not independent holdout",
            "Next-bar open is hypothetical and cannot prove exchange order fills or impact",
            "Funding observed at source event timestamps; 94 interval metadata unknown",
            "Complete historical 16-hour previous-rate lookup is not proof of every actual settlement",
            "Exchange archive may exclude early delisted instruments",
            "No native Warden Stage3 on sparse time-indexed PIT panel",
        ],
        "user_authorization_to_trade": False,
        "native_warden_trial_created": False,
        "sealed_holdout_read": False,
        "original_sources_modified": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", type=Path, default=PIT)
    parser.add_argument("--ledger", type=Path, default=LEDGER)
    parser.add_argument("--exit-evidence", type=Path, default=PRICE_RECEIPT)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("PIT economic preflight evidence is write-once")
    report = evaluate(args.panel, args.ledger, args.exit_evidence)
    payload = (json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=args.output.parent, delete=False) as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
        temp = Path(fh.name)
    try:
        os.link(temp, args.output)
    finally:
        temp.unlink(missing_ok=True)
    print(json.dumps({
        "output": str(args.output),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "market_price_bridge": report["market_price_bridge"],
        "scenarios": {
            name: {
                "coverage": value["portfolio_coverage"],
                "event_classification": value["event_classification"],
                "economic_metrics": {
                    typ: vals for typ, vals in value["variants"].items()
                    if typ in {"settlement_pre_open", "settlement_post_open", "conservative_ordering_bound"}
                },
            }
            for name, value in report["scenarios"].items()
        },
        "native_stage3": False,
    }, indent=2))


if __name__ == "__main__":
    main()
