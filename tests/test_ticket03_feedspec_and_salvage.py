"""Ticket 03 tests: FeedSpec contracts, OHLCVValidator + AnomalyManager, SessionAssigner, and atomic salvage."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest
import yaml

from warden.contracts import (
    ContractValidationError,
    EvaluationContract,
    HypothesisSpec,
    load_contract,
)
from warden.registry import ResearchRegistry
from warden.runner import ExecutionDataError, ExperimentRunner
from warden.synthetic import generate_canonical_market_panel
from warden_ops.integrity import generate_manifest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _make_clean_panel(
    assets: tuple[str, ...] = ("EURUSD", "USDJPY"),
    n_bars: int = 80,
    freq_hours: int = 24,
    start: datetime = datetime(2025, 1, 1, tzinfo=UTC),
    vol: float = 0.004,
    seed: int = 17,
    with_funding: bool = False,
) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for idx, asset in enumerate(assets):
        price = 1.10 + idx * 100.0
        for i in range(n_bars):
            ret = float(rng.normal(0.0, vol))
            nxt = price * (1.0 + ret)
            row = {
                "timestamp": start + timedelta(hours=i * freq_hours),
                "asset": asset,
                "open": price,
                "high": max(price, nxt) * 1.002,
                "low": min(price, nxt) * 0.998,
                "close": nxt,
                "volume": 1000.0,
            }
            if with_funding:
                row["funding_rate"] = 0.0001
            rows.append(row)
            price = nxt
    return (
        pl.DataFrame(rows)
        .with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))
        .sort("timestamp", "asset")
    )


def _setup_isolated_repo(tmp_path: Path, name: str = "iso") -> Path:
    repo = tmp_path / name
    for rel in ("src", "scripts", "pyproject.toml", "uv.lock", "AGENTS.md"):
        src = REPO_ROOT / rel
        dst = repo / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst) if src.is_dir() else shutil.copy2(src, dst)
    (repo / "contracts" / "datasets").mkdir(parents=True, exist_ok=True)
    (repo / "strategies" / "candidates").mkdir(parents=True, exist_ok=True)
    (repo / "data" / "exploratory").mkdir(parents=True, exist_ok=True)
    ResearchRegistry(repo / "registry" / "registry.db")
    return repo


# ---------------------------------------------------------------------------
# 1. LSE level-shift anomaly & OHLCV validation fail-closed
# ---------------------------------------------------------------------------


def test_validate_ohlcv_panel_rejects_lse_level_shift_and_corrupt_ohlcv() -> None:
    from warden_evidence.market_validation import validate_ohlcv_panel

    synth = generate_canonical_market_panel()
    validated_synth = validate_ohlcv_panel(synth, "crypto_perps")
    assert validated_synth.height == synth.height
    assert validated_synth["funding_rate"].null_count() == 0

    fx_clean = _make_clean_panel(assets=("USDCAD", "AUDUSD"), n_bars=100, freq_hours=24, vol=0.003)
    validated_fx = validate_ohlcv_panel(fx_clean, "fx")
    assert validated_fx.schema["funding_rate"] == pl.Float64
    assert validated_fx["funding_rate"].null_count() == validated_fx.height

    futures_clean = _make_clean_panel(assets=("ES", "NQ"), n_bars=60, freq_hours=1, vol=0.003)
    validated_futures = validate_ohlcv_panel(futures_clean, "futures")
    assert validated_futures.height == futures_clean.height
    assert validated_futures.schema["funding_rate"] == pl.Float64
    assert validated_futures["funding_rate"].null_count() == validated_futures.height

    # A) LSE V1 level-shift anomaly: sudden single-bar 55% drop + immediate snapback on USDCAD
    ts_list = sorted(fx_clean["timestamp"].unique().to_list())
    corrupt_ts = ts_list[50]
    fx_lse_shift = fx_clean.with_columns(
        pl.when((pl.col("asset") == "USDCAD") & (pl.col("timestamp") == corrupt_ts))
        .then(pl.col("close") * 0.45)
        .otherwise(pl.col("close"))
        .alias("close"),
        pl.when((pl.col("asset") == "USDCAD") & (pl.col("timestamp") == corrupt_ts))
        .then(pl.col("low") * 0.44)
        .otherwise(pl.col("low"))
        .alias("low"),
    )
    with pytest.raises(ValueError, match="(?i)anomal|extreme|outlier"):
        validate_ohlcv_panel(fx_lse_shift, "fx")

    # B) Duplicate timestamp/asset key
    dup = pl.concat([fx_clean, fx_clean.head(1)])
    with pytest.raises(ValueError, match="Duplicate"):
        validate_ohlcv_panel(dup, "fx")

    # C) Non-monotonic timestamps within an asset
    usdcad = fx_clean.filter(pl.col("asset") == "USDCAD")
    swapped = pl.concat([usdcad.slice(0, 5), usdcad.slice(6, 1), usdcad.slice(5, 1), usdcad.slice(7)])
    with pytest.raises(ValueError, match="(?i)chronological|monotonic|order"):
        validate_ohlcv_panel(swapped, "fx")

    # D) Invalid OHLC (high < close)
    bad_ohlc = fx_clean.with_columns(
        pl.when(pl.int_range(pl.len()) == 0).then(pl.col("close") * 0.9).otherwise(pl.col("high")).alias("high")
    )
    with pytest.raises(ValueError, match="Invalid"):
        validate_ohlcv_panel(bad_ohlc, "fx")

    # E) Non-finite prices (NaN / Inf)
    nan_panel = fx_clean.with_columns(
        pl.when(pl.int_range(pl.len()) == 0).then(pl.lit(float("nan"))).otherwise(pl.col("close")).alias("close")
    )
    with pytest.raises(ValueError, match="non-finite"):
        validate_ohlcv_panel(nan_panel, "fx")

    inf_panel = fx_clean.with_columns(
        pl.when(pl.int_range(pl.len()) == 0).then(pl.lit(float("inf"))).otherwise(pl.col("open")).alias("open")
    )
    with pytest.raises(ValueError, match="non-finite"):
        validate_ohlcv_panel(inf_panel, "fx")


# ---------------------------------------------------------------------------
# 2. QUOTE_SIDE FeedSpec enforcement in preflight & runner BEFORE registry touch
# ---------------------------------------------------------------------------


def test_quote_side_missing_bid_ask_fails_preflight_and_runner_before_registry_touch(tmp_path: Path) -> None:
    from warden.contracts import validate_panel_feed_spec

    repo = _setup_isolated_repo(tmp_path, name="quote_repo")
    base_contract = load_contract(REPO_ROOT / "contracts" / "fx_majors_1d_v1.yaml")

    # Panel lacks bid/ask (only OHLCV + null funding_rate)
    start = datetime(2024, 1, 1, tzinfo=UTC)
    panel_no_quotes = _make_clean_panel(assets=("EURUSD", "USDJPY"), n_bars=90, freq_hours=24, start=start).with_columns(
        pl.lit(None, dtype=pl.Float64).alias("funding_rate")
    )
    exp_rel = "data/exploratory/fx_majors_1d.parquet"
    exp_file = repo / exp_rel
    panel_no_quotes.write_parquet(exp_file)
    sha = hashlib.sha256(exp_file.read_bytes()).hexdigest()

    contract = EvaluationContract(
        contract_id="FX_MAJORS_1D_V1",
        asset_class="fx",
        frequency="1d",
        bars_per_year=314,
        exploratory_data_path=exp_rel,
        holdout_data_path="data/holdout/fx_majors_1d.parquet",
        train_start=start,
        val_start=start + timedelta(days=30),
        val_end=start + timedelta(days=90),
        holdout_start=start + timedelta(days=90),
        holdout_end=start + timedelta(days=120),
        n_folds=2,
        label_horizon_bars=2,
        embargo_bars=1,
        execution_delay_bars=1,
        taker_fee_bps=0.2,
        slippage_bps=0.5,
        funding_bps_per_8h=0.0,
        execution_mode="QUOTE_SIDE",
        data_snapshot_sha256=sha,
        data_provenance="verified_market",
    )
    (repo / "contracts" / "fx_majors_1d_v1.yaml").write_text(yaml.safe_dump(contract.__dict__), encoding="utf-8")
    (repo / "contracts" / "datasets" / "FX_MAJORS_1D_V1.json").write_text(
        json.dumps(
            {
                "contract_id": "FX_MAJORS_1D_V1",
                "path": exp_rel,
                "sha256": sha,
                "provenance": "verified_market",
                "source": "fixture_no_quotes",
            }
        ),
        encoding="utf-8",
    )
    (repo / "strategies" / "candidates" / "fx_flat.py").write_text(
        "import numpy as np\n"
        "def generate_positions(panel_df, contract, **kwargs):\n"
        "    ts = sorted(panel_df['timestamp'].unique().to_list())\n"
        "    assets = sorted(panel_df['asset'].unique().to_list())\n"
        "    return {'positions': np.zeros((len(ts), len(assets)))}\n",
        encoding="utf-8",
    )
    reg = ResearchRegistry(repo / "registry" / "registry.db")
    reg.register_contract(contract)
    hyp = HypothesisSpec(
        "HYP-FX-QUOTE",
        contract.contract_id,
        "FX Quote Test",
        "rules_ts",
        "Testing missing quote fail closed behavior",
        "Spread and slippage require real bid ask",
        4,
        "Reject on missing quotes",
    )
    reg.register_hypothesis(hyp)
    spec_path = repo / "spec.yaml"
    spec_path.write_text(
        yaml.safe_dump(
            {
                "contract_id": contract.contract_id,
                "hypothesis_id": hyp.hypothesis_id,
                "strategy_name": "FxFlat",
                "strategy_family": "rules_ts",
                "model_type": "rules",
                "strategy_module": "strategies/candidates/fx_flat.py",
                "parameters": {},
            }
        ),
        encoding="utf-8",
    )
    generate_manifest(repo)

    # 1) Direct validate_panel_feed_spec fails with ContractValidationError
    with pytest.raises(ContractValidationError, match="bid|ask"):
        validate_panel_feed_spec(panel_no_quotes, contract)

    # 2) preflight_thesis.py subprocess fails closed: rc=3, STOP, feed_spec_ok=False, zero registry mutation
    proc = subprocess.run(
        [sys.executable, str(repo / "scripts" / "preflight_thesis.py"), "--contract", "FX_MAJORS_1D_V1", "--json"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 3, f"Expected rc=3 STOP, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    card = json.loads(proc.stdout)
    assert card["next"]["command"] == "STOP"
    assert card["contract"]["feed_spec_ok"] is False
    assert card["contract"]["feed_spec_error"]

    # 3) ExperimentRunner.run_spec fails closed BEFORE touching trials / trials_used
    runner = ExperimentRunner(registry=reg, repo_root=repo)
    with pytest.raises((ExecutionDataError, ContractValidationError)):
        runner.run_spec(spec_path)

    with sqlite3.connect(repo / "registry" / "registry.db") as conn:
        trials_used = conn.execute(
            "SELECT trials_used FROM hypotheses WHERE hypothesis_id=?", (hyp.hypothesis_id,)
        ).fetchone()[0]
        trial_count = conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0]
    assert trials_used == 0
    assert trial_count == 0

    # 4) Valid QUOTE_SIDE fixture WITH bid/ask passes FeedSpec validation & preflight
    panel_with_quotes = panel_no_quotes.with_columns(
        (pl.col("open") * 0.9999).alias("bid"),
        (pl.col("open") * 1.0001).alias("ask"),
    )
    fs = validate_panel_feed_spec(panel_with_quotes, contract)
    assert fs.time_contract_version == 2
    assert fs.bid_col == "bid" and fs.ask_col == "ask"
    assert base_contract.to_feed_spec().time_contract_version == 2

    # Inverted quotes (ask < bid) must fail closed
    with pytest.raises(ContractValidationError, match="ask >= bid|bid"):
        validate_panel_feed_spec(
            panel_with_quotes.with_columns((pl.col("bid") * 0.99).alias("ask")), contract
        )

    # Write valid quote panel, re-pin manifest, verify preflight passes (feed_spec_ok=True)
    panel_with_quotes.write_parquet(exp_file)
    sha2 = hashlib.sha256(exp_file.read_bytes()).hexdigest()
    contract_ok = EvaluationContract(**{**contract.__dict__, "data_snapshot_sha256": sha2})
    (repo / "contracts" / "fx_majors_1d_v1.yaml").write_text(
        yaml.safe_dump(contract_ok.__dict__), encoding="utf-8"
    )
    (repo / "contracts" / "datasets" / "FX_MAJORS_1D_V1.json").write_text(
        json.dumps(
            {
                "contract_id": "FX_MAJORS_1D_V1",
                "path": exp_rel,
                "sha256": sha2,
                "provenance": "verified_market",
                "source": "fixture_with_quotes",
            }
        ),
        encoding="utf-8",
    )
    generate_manifest(repo)
    proc_ok = subprocess.run(
        [
            sys.executable,
            str(repo / "scripts" / "preflight_thesis.py"),
            "--contract",
            "FX_MAJORS_1D_V1",
            "--hypothesis",
            hyp.hypothesis_id,
            "--json",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc_ok.returncode == 0, proc_ok.stdout + proc_ok.stderr
    card_ok = json.loads(proc_ok.stdout)
    assert card_ok["contract"]["feed_spec_ok"] is True
    assert card_ok["next"]["command"] != "STOP"


# ---------------------------------------------------------------------------
# 3. Multi-exchange INDICES_1H_V2 DST + holiday + zero fabricated bars + null funding
# ---------------------------------------------------------------------------


def test_multi_exchange_indices_dst_holiday_preserves_bars_and_null_funding() -> None:
    from warden_evidence.market_validation import (
        assign_multi_exchange_sessions,
        validate_ohlcv_panel,
    )

    # Span 2025-03-07 .. 2025-05-02 (covers US DST 2025-03-09, EU DST 2025-03-30, and 2025-05-01 Labour Day
    # where CME_Equity ES is open while HKEX HK33 and EUREX DAX are closed).
    rng = np.random.default_rng(101)
    rows = []

    def add_bars(asset: str, timestamps: list[datetime], base_px: float) -> None:
        px = base_px
        for ts in timestamps:
            nxt = px * (1.0 + float(rng.normal(0.0, 0.001)))
            rows.append(
                {
                    "timestamp": ts,
                    "asset": asset,
                    "open": px,
                    "high": max(px, nxt) * 1.001,
                    "low": min(px, nxt) * 0.999,
                    "close": nxt,
                    "volume": 500.0,
                }
            )
            px = nxt

    # Build hourly grids over two windows:
    # Window 1: US DST week 2025-03-07 .. 2025-03-11 (Fri + Sun night + Mon + Tue)
    # Window 2: EU/HK holiday 2025-04-30 .. 2025-05-01 (ES open on 2025-05-01, HK33 open 2025-04-30 & closed 2025-05-01)
    all_days = [
        datetime(2025, 3, 7, tzinfo=UTC) + timedelta(hours=h) for h in range(5 * 24)
    ] + [
        datetime(2025, 4, 30, tzinfo=UTC) + timedelta(hours=h) for h in range(2 * 24)
    ]

    # ES: open Fri Mar 7 (hours 0..21), Sun Mar 9 post-DST opens 22:00 UTC, Mon-Tue open, May 1 open
    es_ts = [
        t
        for t in all_days
        if (t.weekday() < 4 or (t.weekday() == 4 and t.hour < 22) or (t.weekday() == 6 and t.hour >= 22))
        and t.hour != 21  # skip 21:00 UTC daily CME maintenance break on Mon-Thu
    ]
    # DAX (EUREX): open weekdays 07..20 UTC in March before EU DST, closed weekends & May 1
    dax_ts = [
        t
        for t in all_days
        if t.weekday() < 5 and t.date() != date(2025, 5, 1) and 7 <= t.hour <= 20
    ]
    # HK33 (HKEX): open weekdays 02..03 & 05..07 UTC, closed weekends & May 1
    hk_ts = [
        t
        for t in all_days
        if t.weekday() < 5 and t.date() != date(2025, 5, 1) and t.hour in {2, 3, 5, 6, 7}
    ]

    add_bars("ES", es_ts, 5700.0)
    add_bars("DAX", dax_ts, 22500.0)
    add_bars("HK33", hk_ts, 23000.0)

    raw = pl.DataFrame(rows).with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))
    expected_counts = {
        "ES": len(es_ts),
        "DAX": len(dax_ts),
        "HK33": len(hk_ts),
    }

    validated = validate_ohlcv_panel(raw, "indices")
    assigned = assign_multi_exchange_sessions(validated)

    # Zero bars fabricated or dropped; original timestamps preserved exactly
    assert assigned.height == sum(expected_counts.values())
    for sym, cnt in expected_counts.items():
        assert assigned.filter(pl.col("asset") == sym).height == cnt

    # Unknown funding_rate MUST be explicit null (Float64), NEVER 0.0
    assert assigned.schema["funding_rate"] == pl.Float64
    assert assigned["funding_rate"].null_count() == assigned.height

    # Session dates assigned per exchange calendar across DST & holiday
    assert assigned.schema["session_date"] == pl.Date

    # Post-US DST: ES bar at 2025-03-09 22:00 UTC belongs to Monday 2025-03-10 CME_Equity session
    es_sun_post_dst = assigned.filter(
        (pl.col("asset") == "ES") & (pl.col("timestamp") == datetime(2025, 3, 9, 22, 0, tzinfo=UTC))
    )
    assert es_sun_post_dst["session_date"].to_list() == [date(2025, 3, 10)]

    # Pre-US DST: ES bar at 2025-03-07 20:00 UTC belongs to 2025-03-07 session
    es_pre_dst = assigned.filter(
        (pl.col("asset") == "ES") & (pl.col("timestamp") == datetime(2025, 3, 7, 20, 0, tzinfo=UTC))
    )
    assert es_pre_dst["session_date"].to_list() == [date(2025, 3, 7)]

    # Single-venue holiday 2025-05-01: ES has active 2025-05-01 session; HK33 has zero bars on 2025-05-01
    es_may1 = assigned.filter(
        (pl.col("asset") == "ES") & (pl.col("session_date") == date(2025, 5, 1))
    )
    assert es_may1.height > 0
    hk_may1 = assigned.filter(
        (pl.col("asset") == "HK33")
        & (pl.col("timestamp") >= datetime(2025, 5, 1, tzinfo=UTC))
        & (pl.col("timestamp") < datetime(2025, 5, 2, tzinfo=UTC))
    )
    assert hk_may1.height == 0

    # DAX bar on 2025-03-10 10:00 UTC gets 2025-03-10 EUREX session_date
    dax_mar10 = assigned.filter(
        (pl.col("asset") == "DAX") & (pl.col("timestamp") == datetime(2025, 3, 10, 10, 0, tzinfo=UTC))
    )
    assert dax_mar10["session_date"].to_list() == [date(2025, 3, 10)]


# ---------------------------------------------------------------------------
# 4. Atomic contract + manifest write & consolidated salvage scripts
# ---------------------------------------------------------------------------


def test_write_contract_and_manifest_atomic_and_salvage_consolidation(tmp_path: Path) -> None:
    from warden_evidence.market_validation import (
        build_dataset_manifest,
        write_contract_and_manifest_atomic,
    )

    exp_path = tmp_path / "data" / "exploratory" / "crypto_majors_1h.parquet"
    exp_path.parent.mkdir(parents=True, exist_ok=True)
    generate_canonical_market_panel().head(240).write_parquet(exp_path)
    sha = hashlib.sha256(exp_path.read_bytes()).hexdigest()

    contract_path = tmp_path / "contracts" / "crypto_majors_1h_v1.yaml"
    manifest_path = tmp_path / "contracts" / "datasets" / "CRYPTO_MAJORS_1H_V1.json"

    base_contract = {
        "contract_id": "CRYPTO_MAJORS_1H_V1",
        "asset_class": "crypto_perps",
        "frequency": "1h",
        "bars_per_year": 8760,
        "exploratory_data_path": "data/exploratory/crypto_majors_1h.parquet",
        "holdout_data_path": "data/holdout/crypto_majors_1h.parquet",
        "train_start": "2020-01-01T00:00:00Z",
        "val_start": "2023-01-01T00:00:00Z",
        "val_end": "2025-01-01T00:00:00Z",
        "holdout_start": "2025-01-01T00:00:00Z",
        "holdout_end": "2026-10-24T00:00:00Z",
        "n_folds": 4,
        "label_horizon_bars": 4,
        "embargo_bars": 2,
        "execution_delay_bars": 1,
        "execution_mode": "NEXT_BAR",
        "taker_fee_bps": 4.0,
        "slippage_bps": 2.0,
        "funding_bps_per_8h": 1.0,
        "min_expected_move_to_cost_ratio": 2.5,
        "data_snapshot_sha256": sha,
        "data_provenance": "verified_market",
    }
    base_manifest = build_dataset_manifest(
        contract_id="CRYPTO_MAJORS_1H_V1",
        path="data/exploratory/crypto_majors_1h.parquet",
        sha256=sha,
        provenance="verified_market",
        source="ml4t-data binance perps test",
        pipeline="tests/test_ticket03_feedspec_and_salvage.py",
    )

    with pytest.raises(ContractValidationError):
        write_contract_and_manifest_atomic(
            contract_path,
            {**base_contract, "data_provenance": "unknown"},
            manifest_path,
            base_manifest,
        )
    assert not contract_path.exists()
    assert not manifest_path.exists()

    with pytest.raises(ContractValidationError):
        write_contract_and_manifest_atomic(
            contract_path,
            base_contract,
            manifest_path,
            {**base_manifest, "sha256": "0" * 64},
        )
    assert not contract_path.exists()
    assert not manifest_path.exists()

    write_contract_and_manifest_atomic(contract_path, base_contract, manifest_path, base_manifest)
    assert contract_path.is_file() and manifest_path.is_file()
    loaded = load_contract(contract_path)
    assert loaded.data_provenance == "verified_market"
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["sha256"] == sha

    before_c = contract_path.read_bytes()
    before_m = manifest_path.read_bytes()

    # Re-running with identical bytes is an idempotent no-op
    write_contract_and_manifest_atomic(contract_path, base_contract, manifest_path, base_manifest)
    assert contract_path.read_bytes() == before_c
    assert manifest_path.read_bytes() == before_m

    with pytest.raises(ContractValidationError):
        write_contract_and_manifest_atomic(
            contract_path,
            {**base_contract, "data_snapshot_sha256": "f" * 64},
            manifest_path,
            {**base_manifest, "sha256": "f" * 64},
        )
    assert contract_path.read_bytes() == before_c
    assert manifest_path.read_bytes() == before_m

    # Mutating an already-pinned contract or manifest refuses overwrite
    with pytest.raises(FileExistsError):
        write_contract_and_manifest_atomic(
            contract_path,
            {**base_contract, "taker_fee_bps": 5.0},
            manifest_path,
            base_manifest,
        )
    assert contract_path.read_bytes() == before_c
    assert manifest_path.read_bytes() == before_m

    with pytest.raises(FileExistsError):
        write_contract_and_manifest_atomic(
            contract_path,
            base_contract,
            manifest_path,
            {**base_manifest, "source": "tampered_source"},
        )
    assert contract_path.read_bytes() == before_c
    assert manifest_path.read_bytes() == before_m

    # Downgrading verified_market provenance fails even with allow_overwrite=True
    with pytest.raises(ContractValidationError, match="downgrade"):
        write_contract_and_manifest_atomic(
            contract_path,
            {**base_contract, "data_provenance": "synthetic"},
            manifest_path,
            {**base_manifest, "provenance": "synthetic"},
            allow_overwrite=True,
        )
    assert contract_path.read_bytes() == before_c
    assert manifest_path.read_bytes() == before_m

    # repo_root checks missing exploratory file or SHA-256 mismatch before writing
    missing_repo = tmp_path / "missing_repo"
    missing_c = missing_repo / "out_contract.yaml"
    missing_m = missing_repo / "out_manifest.json"
    with pytest.raises(ContractValidationError, match="not found"):
        write_contract_and_manifest_atomic(
            missing_c, base_contract, missing_m, base_manifest, repo_root=missing_repo
        )
    assert not missing_c.exists()
    assert not missing_m.exists()

    mismatch_repo = tmp_path / "mismatch_repo"
    bad_exp = mismatch_repo / base_contract["exploratory_data_path"]
    bad_exp.parent.mkdir(parents=True, exist_ok=True)
    bad_exp.write_bytes(b"corrupted-parquet-bytes")
    mismatch_c = mismatch_repo / "out_contract.yaml"
    mismatch_m = mismatch_repo / "out_manifest.json"
    with pytest.raises(ContractValidationError, match="digest differs"):
        write_contract_and_manifest_atomic(
            mismatch_c, base_contract, mismatch_m, base_manifest, repo_root=mismatch_repo
        )
    assert not mismatch_c.exists()
    assert not mismatch_m.exists()

    from warden.contracts import validate_panel_feed_spec

    naive_panel = _make_clean_panel(assets=("EURUSD",), n_bars=5, with_funding=True).with_columns(
        pl.col("timestamp").dt.replace_time_zone(None)
    )
    perp_contract = load_contract(REPO_ROOT / "contracts" / "crypto_perps_1h_v1.yaml")
    with pytest.raises(ContractValidationError, match="UTC-aware"):
        validate_panel_feed_spec(naive_panel, perp_contract)

    null_perp = _make_clean_panel(assets=("BTCUSDT",), n_bars=5).with_columns(
        pl.lit(None, dtype=pl.Float64).alias("funding_rate")
    )
    with pytest.raises(ContractValidationError, match="funding_rate"):
        validate_panel_feed_spec(null_perp, perp_contract)

    for rel in (
        "scripts/salvage_crypto.py",
        "scripts/salvage_crypto_perps.py",
        "scripts/salvage_multi_asset.py",
        "scripts/build_fx_majors_1d_v2.py",
        "scripts/prepare_syncster_cme.py",
    ):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "warden_evidence.market_validation" in text, f"{rel} must use market_validation.py"

    import runpy

    scp = runpy.run_path(str(REPO_ROOT / "scripts" / "salvage_crypto_perps.py"))
    iso_contracts = tmp_path / "iso_salvage" / "contracts"
    iso_contracts.mkdir(parents=True, exist_ok=True)
    frozen_market_contract = iso_contracts / "crypto_perps_1h_v1.yaml"
    frozen_market_contract.write_text(
        (REPO_ROOT / "contracts" / "crypto_perps_1h_v1.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    frozen_before = frozen_market_contract.read_bytes()
    m_frozen = iso_contracts / "datasets" / "CRYPTO_PERPS_1H_V1.json"
    with pytest.raises(ContractValidationError, match="downgrade"):
        scp["salvage"](
            frozen_market_contract,
            tmp_path / "iso_salvage" / "data_frozen",
            synthetic=True,
            update_contract=True,
        )
    assert frozen_market_contract.read_bytes() == frozen_before
    assert not m_frozen.exists()

    draft_contracts = tmp_path / "iso_draft" / "contracts"
    draft_contracts.mkdir(parents=True, exist_ok=True)
    draft_doc = yaml.safe_load(
        (REPO_ROOT / "contracts" / "crypto_perps_1h_v1.yaml").read_text(encoding="utf-8")
    )
    draft_doc["data_snapshot_sha256"] = ""
    draft_doc["data_provenance"] = "unknown"
    unpinned_draft_contract = draft_contracts / "crypto_perps_1h_v1.yaml"
    unpinned_draft_contract.write_text(yaml.safe_dump(draft_doc, sort_keys=False), encoding="utf-8")

    ev = scp["salvage"](
        unpinned_draft_contract,
        tmp_path / "iso_draft" / "data",
        synthetic=True,
        update_contract=True,
    )
    m_copy = draft_contracts / "datasets" / "CRYPTO_PERPS_1H_V1.json"
    assert m_copy.is_file()
    manifest_payload = json.loads(m_copy.read_text(encoding="utf-8"))
    assert manifest_payload["sha256"] == ev["exploratory"]["sha256"]
    assert manifest_payload["provenance"] == "synthetic"
    assert load_contract(unpinned_draft_contract).data_provenance == "synthetic"
    assert load_contract(unpinned_draft_contract).data_snapshot_sha256 == ev["exploratory"]["sha256"]

    bfx = runpy.run_path(str(REPO_ROOT / "scripts" / "build_fx_majors_1d_v2.py"))
    with pytest.raises(FileNotFoundError, match="Contract YAML not found"):
        bfx["main"](["--contract", str(tmp_path / "missing_contract.yaml")])
