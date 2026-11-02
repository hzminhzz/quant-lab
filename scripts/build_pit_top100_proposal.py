"""Materialize an independent PIT Binance Top-100 *proposal* for future Warden approval.

Consumes the write-once as-of monthly universe audit and SHA-verifies every
historical source parquet shard used. Includes symbols that are absent from
the hindsight-selected legacy panel, and never accesses a month on or after
the 2025-07-01 protected boundary.

Does not modify or sign contracts/, the Warden evaluator or any source store.
Stage 1 revalidation requires the principal-approved versioned contract.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import tempfile
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import yaml

from warden.contracts import load_contract

ROOT = Path("/home/quant/dev/quant/ml4t-data/.data/store")
STORE = ROOT / "crypto_futures_ohlcv_1h_BINANCE_UM_PERP"
OLD_CONTRACT = Path("contracts/crypto_perps_top100_1h_v1.yaml")
OLD_AUDIT = Path("docs/evidence/arxiv_stage1_20261102/pit_universe_membership_20261102.json.gz")
OLD_AUDIT_SHA = "7672cb880aaef849df12312192b93942bd4c653476915a66baa3f3e4205f75d2"
OUTPUT = Path("data/exploratory/crypto_perps_pit100_1h_v1.parquet")
PROPOSAL = Path("docs/contract-proposals/CRYPTO_PERPS_PIT100_1H_V1.yaml")
MANIFEST = Path("docs/contract-proposals/CRYPTO_PERPS_PIT100_1H_V1.dataset.json")
SOURCE_REPORT = Path("docs/evidence/arxiv_stage1_20261102/pit_source_provenance.json.gz")
CONTRACT_ID = "CRYPTO_PERPS_PIT100_1H_V1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_membership(path: Path) -> dict:
    if _sha256(path) != OLD_AUDIT_SHA:
        raise ValueError("As-of membership audit is not the frozen observed source")
    with gzip.open(path, "rt") as f:
        audit = json.load(f)
    if audit["schema"] != "quantlab-asof-binance-top100-membership-v1":
        raise ValueError("PIT membership schema mismatch")
    if audit["window"]["holdout_start_exclusive"][:10] != "2025-07-01":
        raise ValueError("Refusing unapproved holdout dates")
    return audit


def selected_ohlcv_paths(
    audit: dict, store: Path,
) -> tuple[list[Path], list[dict], list[dict]]:
    sources = {entry["path"]: entry for entry in audit["source_shards"]}
    selected = []
    missing = []
    selected_months = []
    for month in audit["months"]:
        asof = datetime.fromisoformat(month["asof_utc"])
        if asof.year > 2025 or (asof.year == 2025 and asof.month >= 7):
            raise ValueError("Refusing protected month")
        for symbol in month["symbols"]:
            rel = f"symbol={symbol}/year={asof.year}/month={asof.month}/data.parquet"
            if rel not in sources:
                missing.append({"month": month["asof_utc"], "symbol": symbol})
                continue
            item = sources[rel]
            path = store / rel
            if not path.is_file() or _sha256(path) != item["sha256"]:
                raise ValueError(f"OHLCV source changed since historical membership audit: {rel}")
            selected.append(path)
            selected_months.append({
                "month": month["asof_utc"], "symbol": symbol, "source_sha256": item["sha256"]
            })
    if not selected:
        raise ValueError("No historical observed price shards are available")
    return selected, missing, selected_months


def funding_paths(
    store_root: Path, selected_assets: set[str], min_year: int = 2022
) -> list[Path]:
    files = []
    for asset in sorted(selected_assets):
        base = store_root / f"crypto_futures_funding_{asset}_PERP"
        for year in range(min_year, 2026):
            folder = base / f"year={year}"
            if not folder.is_dir():
                continue
            for m in sorted(folder.glob("month=*")):
                try:
                    month = int(m.name.removeprefix("month="))
                except ValueError:
                    continue
                eligible = (
                    year > 2022 or month == 12
                ) and (year < 2025 or month < 7)
                if not eligible:
                    continue
                p = m / "data.parquet"
                if p.is_file():
                    files.append(p)
    return sorted(files)


def _funding_frame(files: list[Path]) -> pl.DataFrame:
    if not files:
        # An empty schema makes missing provenance explicit; never inject 0.
        return pl.DataFrame(
            schema={
                "asset": pl.String, "timestamp": pl.Datetime("us", "UTC"),
                "funding_rate": pl.Float64, "funding_event_time": pl.Datetime("us", "UTC"),
            }
        )
    return (
        pl.scan_parquet([str(x) for x in files], hive_partitioning=False)
        .select(
            pl.col("timestamp").cast(pl.Datetime("us","UTC")),
            pl.col("symbol").alias("asset"),
            pl.col("funding_rate").cast(pl.Float64),
        )
        .filter(pl.col("timestamp").is_not_null())
        .filter(
            (pl.col("timestamp") >= datetime(2022, 12, 1, tzinfo=UTC))
            & (pl.col("timestamp") < datetime(2025, 7, 1, tzinfo=UTC))
        )
        .with_columns(pl.col("timestamp").alias("funding_event_time"))
        .sort("timestamp", "asset")
        .collect(engine="streaming")
    )


def assemble_panel(
    files: list[Path],
    funding: pl.DataFrame,
) -> tuple[pl.DataFrame, dict]:
    frame = (
        pl.scan_parquet([str(x) for x in files], hive_partitioning=False)
        .select(
            pl.col("timestamp").cast(pl.Datetime("us","UTC")),
            pl.col("symbol").alias("asset"),
            pl.col("open").cast(pl.Float64),
            pl.col("high").cast(pl.Float64),
            pl.col("low").cast(pl.Float64),
            pl.col("close").cast(pl.Float64),
            pl.col("volume").cast(pl.Float64),
        )
        .filter(
            (pl.col("timestamp") >= datetime(2023, 1, 1, tzinfo=UTC))
            & (pl.col("timestamp") < datetime(2025, 7, 1, tzinfo=UTC))
        )
        .sort("timestamp", "asset")
        .collect(engine="streaming")
    )
    count_raw = frame.height
    frame = frame.filter(
        (pl.col("open") > 0) & pl.col("open").is_finite()
        & (pl.col("high") >= pl.max_horizontal("open","close"))
        & (pl.col("low") <= pl.min_horizontal("open","close"))
        & (pl.col("low") > 0) & pl.col("close").is_finite()
        & (pl.col("volume") >= 0) & pl.col("volume").is_finite()
    )
    duplicates = (
        frame.group_by("timestamp", "asset").len().filter(pl.col("len") > 1).height
    )
    if duplicates:
        raise ValueError(f"Source has {duplicates} duplicate OHLCV timestamps")
    joined = frame.sort("timestamp", "asset").join_asof(
        funding.sort("timestamp", "asset"),
        on="timestamp", by="asset", strategy="backward", tolerance="16h",
    )
    joined = joined.with_columns(
        funding_observed=pl.col("funding_event_time").is_not_null(),
        funding_age_hours=(
            (pl.col("timestamp") - pl.col("funding_event_time")).dt.total_seconds() / 3600.0
        ),
    ).sort("timestamp","asset")

    audit = {
        "rows_before_strict_ohlcv_validation": count_raw,
        "rows_after_strict_ohlcv_validation": joined.height,
        "strict_ohlcv_dropped": count_raw - joined.height,
        "distinct_assets": joined["asset"].n_unique(),
        "timestamp_start": str(joined["timestamp"].min()),
        "timestamp_end": str(joined["timestamp"].max()),
        "funding_observed_rows": int(joined["funding_observed"].sum()),
        "funding_missing_rows": int((~joined["funding_observed"]).sum()),
        "funding_missing_settlement_rows": int(joined.filter(
            (~pl.col("funding_observed")) & (pl.col("timestamp").dt.hour() % 8 == 0)
        ).height),
        "funding_no_zero_imputation": True,
    }
    if not joined.height:
        raise ValueError("No source complete observed PIT price rows")
    return joined, audit


def materialize(
    membership: Path = OLD_AUDIT,
    source_root: Path = ROOT,
    output: Path = OUTPUT,
    proposal: Path = PROPOSAL,
    manifest: Path = MANIFEST,
    evidence: Path = SOURCE_REPORT,
) -> dict:
    targets = (output, proposal, manifest, evidence)
    if any(p.exists() for p in targets):
        raise FileExistsError("PIT dataset/proposal is write-once; never silently replace")
    audit = load_membership(membership)
    source = source_root / STORE.name
    price_paths, missing_months, selected = selected_ohlcv_paths(audit, source)
    symbols = {item["symbol"] for item in selected}
    funds = funding_paths(source_root, symbols)
    fund_frame = _funding_frame(funds)
    panel, quality = assemble_panel(price_paths, fund_frame)

    # Input SHA proves bytes read, not merely date/path metadata.
    price_manifest = [
        {"path": str(p.relative_to(source)), "sha256": _sha256(p)}
        for p in price_paths
    ]
    fund_manifest = [
        {"path": str(p.relative_to(source_root)), "sha256": _sha256(p)}
        for p in funds
    ]
    for path in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".pit-",suffix=".parquet",dir=output.parent)
    os.close(fd)
    tmp = Path(name)
    try:
        panel.write_parquet(tmp, compression="zstd", statistics=True)
        os.link(tmp, output)
    finally:
        tmp.unlink(missing_ok=True)

    sha = _sha256(output)
    historical = load_contract(OLD_CONTRACT)
    spec = asdict(historical)
    spec.update(
        contract_id=CONTRACT_ID,
        exploratory_data_path=str(output),
        holdout_data_path="data/holdout/crypto_perps_pit100_1h_v1.parquet",
        data_snapshot_sha256=sha,
        data_provenance="verified_market",
    )
    for key in ("train_start", "val_start", "val_end", "holdout_start", "holdout_end"):
        spec[key] = spec[key].isoformat()
    proposed_manifest = {
        "contract_id": CONTRACT_ID,
        "path": str(output),
        "sha256": sha,
        "provenance": "verified_market",
        "source": "Historical Binance USD-M hourly raw quote-volume/OHLCV and funding store",
        "pipeline": "scripts/build_pit_top100_proposal.py",
        "source_membership_root_sha256": audit["source_manifest_sha256"],
        "asof_rule": audit["policy"],
        "qualification_limit": "Funding gaps and missing delisting price paths block venue PnL qualification",
        "status": "UNAPPROVED_CONTRACT_PROPOSAL",
    }
    proposal.write_text(yaml.safe_dump(spec,sort_keys=False))
    manifest.write_text(json.dumps(proposed_manifest,indent=2,sort_keys=True)+"\n")

    evidence_payload = {
        "contract_id": CONTRACT_ID,
        "data_sha256": sha,
        "membership_audit_sha256": _sha256(membership),
        "source_membership_root_sha256": audit["source_manifest_sha256"],
        "selected_months": selected,
        "missing_current_month_price_sources": missing_months,
        "price_sources": price_manifest,
        "funding_sources": fund_manifest,
        "quality": quality,
        "status": "PROPOSED_ONLY_NOT_WARDEN_AUTHORIZED",
        "no_protected_read": True,
    }
    with evidence.open("xb") as fh, gzip.GzipFile(fileobj=fh,mode="wb",mtime=0,filename="") as z:
        z.write((json.dumps(evidence_payload,sort_keys=True)+"\n").encode())
    return {
        "output": str(output),
        "sha256": sha,
        "rows": quality["rows_after_strict_ohlcv_validation"],
        "assets": quality["distinct_assets"],
        "funding_missing": quality["funding_missing_rows"],
        "funding_missing_settlements": quality["funding_missing_settlement_rows"],
        "missing_current_month_price_sources": len(missing_months),
        "price_shards": len(price_manifest),
        "funding_shards": len(fund_manifest),
        "proposed_contract": str(proposal),
        "evidence_sha256": _sha256(evidence),
        "do_not_promote": True,
    }


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--membership", type=Path, default=OLD_AUDIT)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    args=parser.parse_args()
    result=materialize(membership=args.membership,source_root=args.source_root)
    print(json.dumps(result,indent=2,sort_keys=True))


if __name__=="__main__":
    main()
