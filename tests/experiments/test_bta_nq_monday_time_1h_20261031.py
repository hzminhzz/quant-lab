"""NQ Monday time-only selection and Chicago DST causality tests."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl
import pytest

from warden.strategy_loader import load_module_from_path

mod=load_module_from_path(Path("strategies/candidates/bta_nq_monday_time_1h_20261031.py"))
CHI=ZoneInfo("America/Chicago")


def contract(cid=mod.CONTRACT_ID, delay=1):
    return SimpleNamespace(contract_id=cid, execution_delay_bars=delay)


def frame(start,n=300):
    rows=[]
    for i in range(n):
        t=start+timedelta(hours=i)
        for sym in ("ES","NQ"):
            rows.append({"timestamp":t,"asset":sym,"open":100.,"high":101.,
                         "low":99.,"close":100.,"volume":100.})
    return pl.DataFrame(rows).sort("timestamp","asset")


@pytest.mark.parametrize("start,expected_date", [
    (datetime(2026,10,24,tzinfo=timezone.utc),"2026-10-26"),
    (datetime(2026,10,31,tzinfo=timezone.utc),"2026-11-02"),
])
def test_chicago_window_and_no_lookahead(start,expected_date):
    b=frame(start)
    full=mod.generate_positions(b,contract(),["NQ"])["positions"]
    stamps=sorted(b["timestamp"].unique().to_list())
    seq=[(t.astimezone(CHI),full[i,1]) for i,t in enumerate(stamps)]
    on=[(t,wt) for t,wt in seq if t.strftime("%Y-%m-%d")==expected_date]
    assert sum(w for _,w in on)==12.
    assert on[9][1]==1.0
    assert on[20][1]==1.0
    assert on[21][1]==0.0
    assert np.count_nonzero(full[:,0])==0
    prefix=b.filter(pl.col("timestamp")<=stamps[120])
    p=mod.generate_positions(prefix,contract(),["NQ"])["positions"]
    assert np.array_equal(p,full[:len(p)])


def test_rejected_invocations():
    b=frame(datetime(2026,10,24,tzinfo=timezone.utc))
    with pytest.raises(ValueError):
        mod.generate_positions(b,contract(delay=0),["NQ"])
    with pytest.raises(ValueError):
        mod.generate_positions(b,contract(),["ES"])
    with pytest.raises(ValueError,match="Unreviewed"):
        mod.generate_positions(b,contract(),["NQ"],start_hour=8)
