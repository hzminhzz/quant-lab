"""Recover absent Binance USD-M historical funding archives into an isolated sidecar.

Never edit ml4t-data, a frozen price snapshot, Warden contracts or registry.
Request only missing PIT symbol-months (plus immediately preceding month for
as-of warmup) from Binance's official public data archives, strictly before
2025-07-01 UTC. Verify every ZIP against its published .CHECKSUM where
available, and byte-pin all source archives in a write-once manifest.

A successful archive is source evidence, NOT confirmation that the perpetual
contract was executable, nor evidence of a real broker funding debit.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import yaml

BASE_URL = "https://data.binance.vision/data/futures/um/monthly/fundingRate"
CUTOFF = datetime(2025, 7, 1, tzinfo=UTC)
FIRST = datetime(2022, 12, 1, tzinfo=UTC)
CONTRACT = Path("docs/contract-proposals/CRYPTO_PERPS_PIT100_1H_V1.yaml")
DEFAULT_CACHE = Path("data/exploratory/pit_funding_vision_recovery_20261102")
ARTIFACT = Path("docs/evidence/arxiv_stage1_20261102/pit_funding_archive_recovery_20261102.json")
SOURCE_LIMIT = 8_000_000
UA = "quant-lab-research-historical-integrity/1.0"


def digest(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def hash_file(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as fh:
        for part in iter(lambda: fh.read(4 * 1024 * 1024), b""):
            sha.update(part)
    return sha.hexdigest()


def preceding_month(month: datetime) -> datetime:
    return datetime(
        month.year - 1 if month.month == 1 else month.year,
        12 if month.month == 1 else month.month - 1,
        1, tzinfo=UTC,
    )


def month_start(dt: datetime) -> datetime:
    return datetime(dt.year, dt.month, 1, tzinfo=UTC)


def archive_plan(panel: pl.DataFrame) -> tuple[list[tuple[str, datetime]], dict]:
    columns = {"timestamp", "asset", "funding_observed"}
    if columns - set(panel.columns):
        raise ValueError(f"Missing PIT provenance columns {sorted(columns - set(panel.columns))}")
    if panel.schema["timestamp"] != pl.Datetime("us", "UTC"):
        raise ValueError("PIT timestamps must be UTC microsecond observed hours")
    if panel["timestamp"].min() < datetime(2023, 1, 1, tzinfo=UTC) or panel["timestamp"].max() >= CUTOFF:
        raise ValueError("PIT panel crosses frozen exploratory boundaries")
    if panel["funding_observed"].null_count():
        raise ValueError("Unknown funding_observed mask must not be filled")
    per_asset = panel.group_by("asset").agg(
        pl.len().alias("rows"),
        pl.col("funding_observed").sum().alias("observed"),
    )
    partial = per_asset.filter((pl.col("observed") > 0) & (pl.col("observed") < pl.col("rows")))
    if not partial.is_empty():
        # A partial asset demands a separate gap classifier; never hide it.
        raise ValueError("Some source-available assets have intermittent funding observations")
    absent = set(per_asset.filter(pl.col("observed") == 0)["asset"].to_list())
    observed = set(per_asset.filter(pl.col("observed") > 0)["asset"].to_list())
    if not absent:
        raise ValueError("No missing historical source to recover")
    if any(not re.fullmatch(r"[A-Z0-9]{2,24}USDT", a) for a in absent):
        raise ValueError("Unexpected or unsafe Binance market symbol")

    missing = panel.filter(pl.col("asset").is_in(absent)).select(
        "asset", pl.col("timestamp").dt.truncate("1mo").alias("month")
    ).unique().sort("asset", "month")
    months = {(x["asset"], x["month"]) for x in missing.iter_rows(named=True)}
    requested = set(months)
    for asset, month in months:
        prev = preceding_month(month)
        if prev >= FIRST:
            requested.add((asset, prev))
    if any(d < FIRST or d >= CUTOFF for _, d in requested):
        raise ValueError("Requested archive crosses protected/unknown date")
    return sorted(requested), {
        "original_missing_rows": int(panel.filter(~pl.col("funding_observed")).height),
        "source_missing_assets": len(absent),
        "source_available_assets": len(observed),
        "selected_missing_asset_months": len(months),
        "warmup_asset_months_added": len(requested - months),
        "archive_requests": len(requested),
    }


def archive_url(asset: str, month: datetime) -> str:
    if not re.fullmatch(r"[A-Z0-9]{2,24}USDT", asset) or month != month_start(month):
        raise ValueError("Invalid Binance funding archive path")
    if not (FIRST <= month < CUTOFF):
        raise ValueError("Protected or out-of-window funding month")
    return f"{BASE_URL}/{asset}/{asset}-fundingRate-{month:%Y-%m}.zip"


def validate_archive(blob: bytes, asset: str, month: datetime) -> list[dict]:
    if len(blob) > SOURCE_LIMIT:
        raise ValueError("Funding archive exceeds size limit")
    name = f"{asset}-fundingRate-{month:%Y-%m}.csv"
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        infos = archive.infolist()
        if len(infos) != 1 or infos[0].filename != name or infos[0].file_size > SOURCE_LIMIT:
            raise ValueError("Unexpected ZIP contents, path or size")
        with archive.open(infos[0]) as fh:
            text = io.TextIOWrapper(fh, encoding="utf-8-sig", newline="")
            reader = csv.DictReader(text)
            expected = ["calc_time", "funding_interval_hours", "last_funding_rate"]
            if reader.fieldnames != expected:
                raise ValueError(f"Unexpected Binance funding CSV schema: {reader.fieldnames}")
            rows = []
            seen = set()
            for x in reader:
                if not all(x.get(k) is not None for k in expected):
                    raise ValueError("Incomplete or malformed Binance funding row")
                stamp = datetime.fromtimestamp(int(x["calc_time"]) / 1000, tz=UTC)
                rate = float(x["last_funding_rate"])
                interval = int(x["funding_interval_hours"])
                if month_start(stamp) != month or stamp >= CUTOFF:
                    raise ValueError("Funding event outside requested historical month")
                if not math.isfinite(rate) or not (-.1 <= rate <= .1) or interval not in (1, 2, 4, 8):
                    raise ValueError("Invalid funding rate or settlement interval")
                if stamp in seen:
                    raise ValueError("Duplicate funding timestamp in official archive")
                seen.add(stamp)
                rows.append({
                    "timestamp": stamp,
                    "asset": asset,
                    "funding_rate": rate,
                    "funding_interval_hours": interval,
                })
            return rows


def fetch(url: str) -> tuple[int, bytes | None]:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                if response.status != 200:
                    return response.status, None
                blob = response.read(SOURCE_LIMIT + 1)
                if len(blob) > SOURCE_LIMIT:
                    raise ValueError("Remote archive is unexpectedly large")
                return 200, blob
        except urllib.error.HTTPError as ex:
            if ex.code == 404:
                return 404, None
            if ex.code in {429, 500, 502, 503, 504} and attempt < 2:
                time.sleep(.3 * (attempt + 1))
                continue
            return ex.code, None
        except (TimeoutError, OSError, urllib.error.URLError):
            if attempt < 2:
                time.sleep(.3 * (attempt + 1))
                continue
            return 0, None
    return 0, None


def write_once(path: Path, blob: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if hash_file(path) != digest(blob):
            raise FileExistsError(f"Existing immutable source differs: {path}")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as fh:
        fh.write(blob)
        fh.flush()
        os.fsync(fh.fileno())
        tmp = Path(fh.name)
    try:
        os.link(tmp, path)
    except FileExistsError:
        if hash_file(path) != digest(blob):
            raise
    finally:
        tmp.unlink(missing_ok=True)


def checksum_from_text(blob: bytes) -> str:
    text = blob.decode("ascii", errors="strict").strip()
    match = re.match(r"^([0-9a-fA-F]{64})(?:\s+.*)?$", text)
    if not match:
        raise ValueError("Unrecognized official SHA256 sidecar format")
    return match.group(1).lower()


def acquire_one(asset: str, month: datetime, cache: Path) -> tuple[dict, list[dict]]:
    url = archive_url(asset, month)
    relative = Path(asset) / f"{asset}-fundingRate-{month:%Y-%m}.zip"
    local = cache / relative
    if local.exists():
        blob = local.read_bytes()
        source = "CACHED"
        code = 200
    else:
        code, blob = fetch(url)
        source = "OFFICIAL_REMOTE"
    record = {
        "asset": asset,
        "month": f"{month:%Y-%m}",
        "url": url,
        "http_status": code,
        "source": source,
    }
    if blob is None:
        record["status"] = "NOT_PUBLISHED" if code == 404 else "DOWNLOAD_FAILED"
        return record, []

    rows = validate_archive(blob, asset, month)
    checksum_status, checkblob = fetch(url + ".CHECKSUM")
    expected = None
    if checksum_status == 200 and checkblob is not None:
        expected = checksum_from_text(checkblob)
        if digest(blob) != expected:
            raise ValueError(f"Official checksum mismatch for {url}")
    elif checksum_status != 404:
        # Cannot silently treat timeouts or authentication failures as absence.
        raise ValueError(f"Official checksum unavailable for {url}: HTTP {checksum_status}")
    write_once(local, blob)
    record.update(
        status="VERIFIED",
        sha256=digest(blob),
        official_checksum_status="MATCH" if expected else "NOT_PUBLISHED",
        events=len(rows),
        cache_path=str(local),
    )
    return record, rows


def asof_coverage(panel: pl.DataFrame, recovered: pl.DataFrame, *, age_hours: int = 16) -> dict:
    if recovered.is_empty():
        return {"recovered_rows": 0, "remaining_unknown_rows": int((~panel["funding_observed"]).sum())}
    absent = panel.filter(~pl.col("funding_observed")).select("timestamp", "asset")
    # Only observed events at or before decision time may be used.
    result = absent.sort("timestamp", "asset").join_asof(
        recovered.select("timestamp", "asset", pl.col("funding_rate").alias("recovered_funding_rate"))
        .sort("timestamp", "asset"),
        on="timestamp", by="asset", strategy="backward", tolerance=f"{age_hours}h",
    )
    recovered_n = int(result["recovered_funding_rate"].is_not_null().sum())
    return {
        "recovered_rows": recovered_n,
        "remaining_unknown_rows": absent.height - recovered_n,
        "rows_originally_missing": absent.height,
        "coverage_after_backfill_pct": float(
            (panel.height - absent.height + recovered_n) / panel.height * 100
        ),
        "funding_rate_imputation": False,
        "asof_after_event_only": True,
        "max_funding_age_hours": age_hours,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel-path", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--evidence", type=Path, default=ARTIFACT)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, default=0, help="Only download first N archives; no final report")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.workers < 1 or args.workers > 12 or args.limit < 0:
        raise ValueError("Invalid bounded acquisition settings")
    contract = yaml.safe_load(CONTRACT.read_text())
    expected = contract["data_snapshot_sha256"]
    if hash_file(args.panel_path) != expected:
        raise ValueError("PIT source changed since proposal")
    if args.evidence.exists() and not args.dry_run:
        raise FileExistsError(f"Immutable recovery evidence already exists: {args.evidence}")
    panel = pl.read_parquet(args.panel_path)
    requested, counts = archive_plan(panel)
    if args.dry_run:
        print(json.dumps({"plan": counts, "first": [archive_url(*x) for x in requested[:8]]}, indent=2))
        return

    records: list[dict] = []
    recovered_rows: list[dict] = []
    selected = requested[:args.limit] if args.limit else requested
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        work = {pool.submit(acquire_one, asset, month, args.cache): (asset, month)
                for asset, month in selected}
        for task in as_completed(work):
            record, rows = task.result()
            records.append(record)
            recovered_rows.extend(rows)
    records.sort(key=lambda x: (x["asset"], x["month"]))
    recovered_rows.sort(key=lambda x: (x["timestamp"], x["asset"]))
    if args.limit:
        print(json.dumps({
            "partial_cache_only": True, "downloaded": len(records),
            "statuses": dict(Counter(x["status"] for x in records)),
            "event_count": len(recovered_rows),
        }, indent=2))
        return

    schema = {
        "timestamp": pl.Datetime("us", "UTC"),
        "asset": pl.String,
        "funding_rate": pl.Float64,
        "funding_interval_hours": pl.Int64,
    }
    events = pl.DataFrame(recovered_rows, schema=schema)
    if events.select(pl.struct("timestamp", "asset").is_duplicated().any()).item():
        raise ValueError("Across-month funding timestamp duplicates")
    measures = asof_coverage(panel, events)
    archive_status = dict(Counter(row["status"] for row in records))
    report = {
        "schema_version": 1,
        "status": "READ_ONLY_PIT_FUNDING_SOURCE_RECOVERY_NOT_WARDEN",
        "contract_proposal": contract["contract_id"],
        "input_panel_sha256": expected,
        "original": counts,
        "downloaded_archive_status": archive_status,
        "event_count": events.height,
        "event_asset_count": events["asset"].n_unique(),
        "rate_event_intervals": dict(Counter(str(x) for x in events["funding_interval_hours"])),
        "coverage": measures,
        "archives": records,
        "protected_holdout_read": False,
        "original_source_modified": False,
        "new_contract_signed": False,
    }
    output_data = args.cache / "recovered_funding_events.parquet"
    if output_data.exists():
        raise FileExistsError("Existing recovered event evidence is write-once")
    events.write_parquet(output_data, compression="zstd")
    report["derived_events_parquet_sha256"] = hash_file(output_data)
    report["derived_events_path"] = str(output_data)
    write_once(args.evidence, (json.dumps(report, sort_keys=True, indent=2) + "\n").encode())
    print(json.dumps({
        "original": counts, "archive_status": archive_status,
        "events": events.height, "coverage": measures,
        "evidence_path": str(args.evidence),
        "evidence_sha256": hash_file(args.evidence),
        "derived_events_sha256": report["derived_events_parquet_sha256"],
    }, indent=2))


if __name__ == "__main__":
    main()
