"""Prepare immutable Warden exploratory panels from SHA-pinned Syncster CME bars.

Research-only. Never reads or writes data/holdout. Does not execute Warden trials.
Run from quant-lab with --symbol ES|NQ --frequency 15m|1h --prepare.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import yaml

SOURCE_SHA256 = {
    "ES": "e6320a6772fc5819b12605ffc63f88ac1861c26b8fa528d052e1b21d914438a8",
    "NQ": "35071bdad8b4abefabdf2543543d56b39c54dd56ee0ae7251ffed73107fb1681",
}
TRAIN_START = datetime(2016, 6, 1, tzinfo=UTC)
VAL_START = datetime(2023, 1, 1, tzinfo=UTC)
VAL_END = datetime(2025, 7, 1, tzinfo=UTC)  # Exclusive; never take protected 2025-07+ data.
FREQUENCIES = {"15m": (23_184, 8), "1h": (5_796, 4)}


@dataclass(frozen=True)
class SourceAudit:
    source: str
    sha256: str
    symbol: str
    observed_rows: int
    first_timestamp: str
    last_timestamp: str
    missing_minutes: int
    gaps_over_60m: int


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def source_path(symbol: str, repo_root: Path, ml4t_root: Path | None = None) -> Path:
    """Prefer the canonical ml4t-data source; do not silently fallback on a bad digest."""
    repo = ml4t_root or Path(os.environ.get("ML4T_DATA_REPO", "/home/quant/dev/quant/ml4t-data"))
    locations = (repo / "data" / "futures" / f"{symbol}.parquet",
                 repo_root / "data" / "exploratory" / f"cme_{symbol.lower()}_1m.parquet")
    for p in locations:
        if p.is_file():
            return p
    raise FileNotFoundError(
        f"No installed {symbol} source at {locations}. "
        "Download the exact approved Syncster source using ml4t-data's utility first."
    )


def prepare_panel(path: Path, symbol: str, frequency: str,
                  expected_sha: str | None = None) -> tuple[pl.DataFrame, SourceAudit]:
    if symbol not in SOURCE_SHA256 or frequency not in FREQUENCIES:
        raise ValueError("Only SHA-pinned ES/NQ 15m or 1h series are approved")
    digest = sha256(path)
    pinned = expected_sha if expected_sha is not None else SOURCE_SHA256[symbol]
    if digest != pinned:
        raise ValueError(f"Source SHA256 mismatch for {symbol}: {digest} != {pinned}")
    scan = pl.scan_parquet(path)
    schema = scan.collect_schema()
    required = {"ts", "symbol", "open", "high", "low", "close", "volume"}
    if not required.issubset(schema.names()):
        raise ValueError(f"Missing Syncster columns: {sorted(required - set(schema.names()))}")
    if schema["ts"] != pl.Datetime("us", "UTC"):
        raise ValueError("CME timestamps must be timezone-aware UTC microseconds")
    df = (
        scan.filter((pl.col("ts") >= TRAIN_START) & (pl.col("ts") < VAL_END))
        .select(sorted(required))
        .sort("ts")
        .collect()
    )
    if df.is_empty() or df["ts"].min() > TRAIN_START + timedelta(days=1) or df["ts"].max() < VAL_END - timedelta(days=1):
        raise ValueError("CME source does not cover the frozen train/validation study")
    if df["symbol"].unique().to_list() != [symbol]:
        raise ValueError(f"Wrong CME symbol(s) in {path}")
    if df["ts"].n_unique() != len(df):
        raise ValueError("Duplicate minute timestamps: refusing to silently deduplicate")
    if df.select(pl.any_horizontal(pl.all().is_null()).sum()).item() != 0:
        raise ValueError("Null raw observations")
    inconsistent = df.filter(
        (pl.col("high") < pl.max_horizontal("open", "close", "low"))
        | (pl.col("low") > pl.min_horizontal("open", "close", "high"))
        | (pl.col("low") <= 0)
        | (pl.col("volume") <= 0)
    )
    if not inconsistent.is_empty():
        raise ValueError(f"Invalid raw OHLC/volume rows: {len(inconsistent)}")
    gaps = df.select(pl.col("ts").diff().dt.total_minutes().alias("minutes"))["minutes"].drop_nulls()
    missing_minutes = int((gaps - 1).clip(lower_bound=0).sum())
    big_gaps = int((gaps > 60).sum())

    panel = (
        df.lazy().group_by_dynamic("ts", every=frequency, period=frequency,
                                   closed="left", label="left")
        .agg(
            pl.col("open").first().alias("open"),
            pl.col("high").max().alias("high"),
            pl.col("low").min().alias("low"),
            pl.col("close").last().alias("close"),
            pl.col("volume").sum().alias("volume"),
            pl.len().alias("observed_minutes"),
        )
        .rename({"ts": "timestamp"})
        .with_columns(pl.lit(symbol).alias("asset"))
        .select("timestamp", "asset", "open", "high", "low", "close",
                "volume", "observed_minutes")
        .sort("timestamp")
        .collect()
    )
    if panel.is_empty() or panel["timestamp"].max() >= VAL_END:
        raise ValueError("Derived panel violates the frozen validation cutoff")
    first, last = df["ts"].min(), df["ts"].max()
    audit = SourceAudit(str(path), digest, symbol, len(df), first.isoformat(),
                        last.isoformat(), missing_minutes, big_gaps)
    return panel, audit


def write_once(path: Path, content: bytes) -> None:
    """Freeze files, preserve other sessions, and never rewrite changed evidence."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if sha256(path) != hashlib.sha256(content).hexdigest():
            raise FileExistsError(f"Different frozen file exists: {path}")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        tmp.write(content)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    try:
        # Hardlink creates atomically and refuses to overwrite a concurrent writer.
        try:
            os.link(tmp_path, path)
        except FileExistsError:
            if sha256(path) != hashlib.sha256(content).hexdigest():
                raise
    finally:
        tmp_path.unlink(missing_ok=True)


def stage(symbol: str, frequency: str, repo_root: Path, ml4t_root: Path | None = None,
          commit_files: bool = False) -> dict:
    symbol = symbol.upper()
    path = source_path(symbol, repo_root, ml4t_root)
    panel, audit = prepare_panel(path, symbol, frequency)
    key = f"CME_{symbol}_{frequency.upper()}_SYNCSTER_V1"
    name = key.lower()
    data_rel = f"data/exploratory/{name}.parquet"
    contract_rel = f"contracts/{name}.yaml"
    manifest_rel = f"contracts/datasets/{key}.json"
    # Construct the actual deterministic parquet bytes in a temp outside the repository.
    with tempfile.TemporaryDirectory() as t:
        parquet_path = Path(t) / "panel.parquet"
        panel.write_parquet(parquet_path, compression="zstd", statistics=True)
        payload = parquet_path.read_bytes()
    data_sha = hashlib.sha256(payload).hexdigest()
    bars_per_year, label_bars = FREQUENCIES[frequency]
    spec = {
        "contract_id": key, "asset_class": "indices", "frequency": frequency,
        "bars_per_year": bars_per_year,
        "exploratory_data_path": data_rel,
        "holdout_data_path": f"data/holdout/{name}.parquet",
        "train_start": TRAIN_START.isoformat(),
        "val_start": VAL_START.isoformat(),
        "val_end": VAL_END.isoformat(),
        "holdout_start": VAL_END.isoformat(),
        "holdout_end": "2026-10-21T00:00:00+00:00",
        "n_folds": 4, "label_horizon_bars": label_bars,
        "embargo_bars": label_bars, "execution_delay_bars": 1,
        "execution_mode": "NEXT_BAR",
        "taker_fee_bps": 0.5, "slippage_bps": 1.5,
        "funding_bps_per_8h": 0.0,
        "min_expected_move_to_cost_ratio": 2.5,
        "data_snapshot_sha256": data_sha,
        "data_provenance": "verified_market",
    }
    manifest = {
        "contract_id": key, "path": data_rel, "sha256": data_sha,
        "provenance": "verified_market",
        "source": "syncster_market_data",
        "market_type": "cme_futures",
        "source_repo": "syncster-data/historical-market-data",
        "raw_source_sha256": audit.sha256,
        "raw_source_name": f"data/futures/{symbol}.parquet",
        "pipeline": "scripts/prepare_syncster_cme.py",
        "cutoff_exclusive": VAL_END.isoformat(),
        "roll_method": "provider_continuous_unverified",
        "execution_evidence": "OHLCV_NEXT_BAR_only",
        "costs": "assumed, not actual CME fill/commission TCA",
        "attestation": "Exact SHA-256 for provider-labeled CME series; independent exchange origin "
                       "and continuous-roll economic fidelity are not yet attested.",
    }
    result = {
        "source": audit.__dict__, "derived_rows": len(panel),
        "incomplete_derived_bars": int((panel["observed_minutes"] <
                                        {"15m": 15, "1h": 60}[frequency]).sum()),
        "derived_sha256": data_sha, "contract_id": key,
        "data_path": data_rel, "contract_path": contract_rel,
        "manifest_path": manifest_rel, "prepared": commit_files,
    }
    if commit_files:
        from warden.contracts import EvaluationContract
        EvaluationContract(**spec)  # Validate before touching any file.
        data_dest = repo_root / data_rel
        # Refuse mismatched existing contract/manifest before materializing the panel.
        contract_bytes = yaml.safe_dump(spec, sort_keys=False).encode()
        manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
        for target, blob in ((repo_root / contract_rel, contract_bytes),
                             (repo_root / manifest_rel, manifest_bytes),
                             (data_dest, payload)):
            if target.exists() and sha256(target) != hashlib.sha256(blob).hexdigest():
                raise FileExistsError(f"Conflicting pinned source/contract: {target}")
        write_once(data_dest, payload)
        write_once(repo_root / contract_rel, contract_bytes)
        write_once(repo_root / manifest_rel, manifest_bytes)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True, choices=["ES", "NQ"])
    parser.add_argument("--frequency", default="15m", choices=sorted(FREQUENCIES))
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--ml4t-root", type=Path, default=None)
    parser.add_argument("--prepare", action="store_true",
                        help="Create the pinned, write-once research panel, contract and manifest")
    args = parser.parse_args()
    print(json.dumps(stage(args.symbol, args.frequency, args.repo_root,
                           args.ml4t_root, args.prepare), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
