"""Recover the final BNXUSDT January 2023 PIT funding gap via official REST.

The Binance Vision MONTHLY fundingRate ZIPs for BNXUSDT in Dec 2022
and Jan 2023 are unpublished (HTTP 404), but the official Binance
USD-M GET /fapi/v1/fundingRate endpoint serves historical BNX events.
Use one explicit bounded Jan 2023 query, immutable raw response bytes,
strict interval/amount/timestamp checks, and preserve UNKNOWN intervals.

Materialize a new VERSION-3 research overlay; never alter prior V1/V2
panels, the protected Warden evaluator, approved contracts or trials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from scripts.build_pit_funding_overlay import overlay_prices, write_once_parquet
from scripts.recover_pit_funding_archives import hash_file, write_once

SYMBOL = "BNXUSDT"
START_MS = 1672531200000
END_MS = 1675209599999
OFFICIAL_URL = (
    "https://fapi.binance.com/fapi/v1/fundingRate?"
    f"symbol={SYMBOL}&startTime={START_MS}&endTime={END_MS}&limit=1000"
)
FIRST = datetime(2023, 1, 1, tzinfo=UTC)
END = datetime(2023, 2, 1, tzinfo=UTC)
V2_EVIDENCE = Path("docs/evidence/arxiv_stage1_20261102/pit_funding_overlay_v2_20261102.json")
RAW_RESPONSE = Path("data/exploratory/pit_funding_vision_recovery_20261102/BNXUSDT/rest_fundingRate_2023-01.json")
V3_PANEL = Path("data/exploratory/crypto_perps_pit100_funding_overlay_v3.parquet")
V3_LEDGER = Path("data/exploratory/crypto_perps_pit100_event_ledger_v3.parquet")
V3_EVIDENCE = Path("docs/evidence/arxiv_stage1_20261102/pit_funding_overlay_v3_20261102.json")


def decode_official_response(blob: bytes) -> pl.DataFrame:
    if len(blob) > 2_000_000:
        raise ValueError("REST response exceeded bounded evidence limit")
    payload = json.loads(blob)
    if not isinstance(payload, list) or not payload or len(payload) > 1000:
        raise ValueError("Unexpected Binance funding response size or type")
    out = []
    seen = set()
    for x in payload:
        if not isinstance(x, dict) or not {"symbol", "fundingTime", "fundingRate"} <= x.keys():
            raise ValueError("Malformed official Binance funding observation")
        if x["symbol"] != SYMBOL:
            raise ValueError("Wrong asset in official funding history")
        stamp_ms = x["fundingTime"]
        if type(stamp_ms) is not int or stamp_ms < START_MS or stamp_ms > END_MS:
            raise ValueError("Funding observation outside preregistered January 2023 request")
        stamp = datetime.fromtimestamp(stamp_ms / 1000, tz=UTC)
        if not (FIRST <= stamp < END):
            raise ValueError("Funding observation exceeds preserved historical window")
        amount = float(x["fundingRate"])
        if not math.isfinite(amount) or abs(amount) > .1:
            raise ValueError("Invalid historical funding rate")
        if stamp_ms in seen:
            raise ValueError("Duplicate official funding timestamp")
        seen.add(stamp_ms)
        out.append({
            "timestamp": stamp,
            "asset": SYMBOL,
            "funding_rate": amount,
            "funding_interval_hours": None,  # Official REST does not publish it.
            "event_source": "BINANCE_OFFICIAL_FUNDING_HISTORY_REST",
        })
    return pl.DataFrame(out, schema={
        "timestamp": pl.Datetime("us", "UTC"),
        "asset": pl.String,
        "funding_rate": pl.Float64,
        "funding_interval_hours": pl.Int64,
        "event_source": pl.String,
    }).sort("timestamp", "asset")


def download_exact_january(output: Path = RAW_RESPONSE) -> tuple[pl.DataFrame, dict]:
    if output.exists():
        blob = output.read_bytes()
        acquisition = "EXISTING_PINNED_CACHE"
    else:
        request = urllib.request.Request(
            OFFICIAL_URL,
            headers={"User-Agent": "quant-lab-research-historical-integrity/1.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=35) as resp:
                if resp.status != 200:
                    raise ValueError(f"Official REST HTTP {resp.status}")
                blob = resp.read(2_000_001)
        except urllib.error.HTTPError as exc:
            raise ValueError(f"Historical REST funding retrieval unavailable: {exc.code}") from exc
        acquisition = "OFFICIAL_BINANCE_REST"
    events = decode_official_response(blob)
    # Never mark the two unpublished monthly ZIPs as recovered;
    # pin the REST response as an independent source and provenance.
    write_once(output, blob)
    return events, {
        "source_url": OFFICIAL_URL,
        "source_kind": "OFFICIAL_HISTORICAL_REST_NOT_MONTHLY_ARCHIVE",
        "source_response_sha256": hashlib.sha256(blob).hexdigest(),
        "source_cache_path": str(output),
        "acquisition": acquisition,
        "source_events": events.height,
        "interval_metadata_present": False,
        "not_published_monthly_archives": ["BNXUSDT:2022-12", "BNXUSDT:2023-01"],
    }


def augment_v2(
    prior_panel: pl.DataFrame,
    prior_ledger: pl.DataFrame,
    jan_events: pl.DataFrame,
) -> tuple[pl.DataFrame, pl.DataFrame, dict]:
    if not jan_events["funding_interval_hours"].is_null().all():
        raise ValueError("Do not infer REST funding intervals")
    if jan_events["asset"].unique().to_list() != [SYMBOL]:
        raise ValueError("Supplement must be BNX January 2023 only")
    if not (jan_events["timestamp"].min() >= FIRST and jan_events["timestamp"].max() < END):
        raise ValueError("Outside frozen REST supplement")
    full = pl.concat([prior_ledger, jan_events], how="vertical").sort("timestamp", "asset")
    if full.select(pl.struct(["timestamp", "asset"]).is_duplicated().any()).item():
        raise ValueError("REST and ZIP/local event sources overlap; fail closed")
    repaired, report = overlay_prices(prior_panel, full)
    if repaired.height != prior_panel.height:
        raise ValueError("REST supplement changed source asset-hour rows")
    if not repaired.filter(~pl.col("funding_observed")).is_empty():
        # This is the exact remaining symbol/month. If REST cannot fully
        # bridge it, preserve blocked status rather than claim full recovery.
        raise ValueError(
            "Historical REST supplement does not close all remaining funding unknowns"
        )
    if not prior_panel.filter(~pl.col("funding_observed")).select("asset").unique()["asset"].to_list() == [SYMBOL]:
        raise ValueError("Unexpected additional data gaps; cannot imply complete coverage")
    report["rest_rows"] = jan_events.height
    report["rest_interval_metadata_missing_events"] = jan_events.height
    report["read_only_unsigned"] = True
    return repaired, full, report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prior-panel", type=Path, default=Path(
        "data/exploratory/crypto_perps_pit100_funding_overlay_v2.parquet"
    ))
    parser.add_argument("--prior-ledger", type=Path, default=Path(
        "data/exploratory/crypto_perps_pit100_event_ledger_v2.parquet"
    ))
    parser.add_argument("--raw-response", type=Path, default=RAW_RESPONSE)
    parser.add_argument("--out-panel", type=Path, default=V3_PANEL)
    parser.add_argument("--out-ledger", type=Path, default=V3_LEDGER)
    parser.add_argument("--evidence", type=Path, default=V3_EVIDENCE)
    args = parser.parse_args()
    if any(p.exists() for p in (args.out_panel, args.out_ledger, args.evidence)):
        raise FileExistsError("PIT v3 output already exists; never overwrite versioned evidence")
    v2 = json.loads(V2_EVIDENCE.read_text())
    if (hash_file(args.prior_panel) != v2["panel_overlay"]["sha256"]
            or hash_file(args.prior_ledger) != v2["event_ledger"]["sha256"]):
        raise ValueError("Prior PIT overlay and event ledger hashes changed")
    supplement, original = download_exact_january(args.raw_response)
    panel = pl.read_parquet(args.prior_panel)
    old_events = pl.read_parquet(args.prior_ledger)
    repaired, ledger, verification = augment_v2(panel, old_events, supplement)
    write_once_parquet(repaired, args.out_panel)
    write_once_parquet(ledger, args.out_ledger)
    report = {
        "schema": "quantlab-binance-pit-funding-rest-v3",
        "status": "RESEARCH_ONLY_UNSIGNED_NOT_WARDEN",
        "sources": {
            "v2_receipt_sha256": hash_file(V2_EVIDENCE),
            "v2_panel_sha256": v2["panel_overlay"]["sha256"],
            "v2_ledger_sha256": v2["event_ledger"]["sha256"],
            "official_rest": original,
        },
        "data": {
            **verification,
            "event_ledger_rows": ledger.height,
            "panel_rows": repaired.height,
            "panel_sha256": hash_file(args.out_panel),
            "event_ledger_sha256": hash_file(args.out_ledger),
            "panel_path": str(args.out_panel),
            "ledger_path": str(args.out_ledger),
        },
        "holdout_read": False,
        "original_data_modified": False,
        "funding_execution_qualified": False,
        "unapproved_8h_warden_model_not_used": True,
        "constraint": (
            "Observed historical RATE availability is now complete under a 16h as-of "
            "lookup. Settlement intervals for 93 API events remain UNKNOWN; no "
            "futures fill, delisting, data availability or live trading qualification."
        ),
    }
    data = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=args.evidence.parent, delete=False) as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
        tmp = Path(fh.name)
    try:
        os.link(tmp, args.evidence)
    finally:
        tmp.unlink(missing_ok=True)
    print(json.dumps({
        "result": report["status"],
        "source_rest_events": supplement.height,
        "funding_rows_recovered": verification["recovered_rows"],
        "remaining_missing_rows": verification["after_overlay_missing_rows"],
        "coverage_pct": verification["coverage_pct"],
        "event_ledger_rows": ledger.height,
        "panel_sha256": hash_file(args.out_panel),
        "event_ledger_sha256": hash_file(args.out_ledger),
        "evidence_sha256": hash_file(args.evidence),
    }, indent=2))


if __name__ == "__main__":
    main()
