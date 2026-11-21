"""The nightly PCR collector writes Alpaca's put/call open interest beside yfinance's (R126, 2026-11-21; vault
ruling-positions R126 and INDEX "APEX options source"): its own table, with Alpaca's open_interest_date, so the
20-session comparison can pair readings by the OI's date. The yfinance rows and the baseline are unchanged."""
from datetime import date
from unittest.mock import patch

from backend.data import l5_positioning_pcr_collector as col
from backend.data import options_alpaca as oa
from backend.db import get_db, get_pcr_history

ROWS = [{"symbol": "A1", "expiry": "2026-11-27", "type": "call", "oi": 300, "oi_date": "2026-11-20"},
        {"symbol": "A2", "expiry": "2026-11-27", "type": "put", "oi": 150, "oi_date": "2026-11-20"},
        {"symbol": "A3", "expiry": "2026-12-04", "type": "call", "oi": 100, "oi_date": "2026-11-20"},
        {"symbol": "A4", "expiry": "2026-12-11", "type": "put", "oi": 999, "oi_date": "2026-11-20"}]   # third expiry


def test_alpaca_pcr_over_the_two_nearest_expiries():
    with patch.object(oa, "contracts", return_value=ROWS):
        r = col._fetch_pcr_alpaca("AAA", date(2026, 11, 23))
    assert r == {"pcr": 0.375, "call_oi": 400, "put_oi": 150, "expiry_1": "2026-11-27", "expiry_2": "2026-12-04",
                 "oi_date": "2026-11-20"}


def test_alpaca_failures_return_none():
    with patch.object(oa, "contracts", side_effect=ConnectionError("down")):
        assert col._fetch_pcr_alpaca("AAA", date(2026, 11, 23)) is None
    with patch.object(oa, "contracts", return_value=[]):
        assert col._fetch_pcr_alpaca("AAA", date(2026, 11, 23)) is None
    zero = [dict(r, oi=0) for r in ROWS]
    with patch.object(oa, "contracts", return_value=zero):
        assert col._fetch_pcr_alpaca("AAA", date(2026, 11, 23)) is None


def test_collect_writes_both_sources_side_by_side(tmp_path):
    yf = {"AAA": {"pcr": 0.5, "call_oi": 200, "put_oi": 100, "expiry_1": "2026-11-27", "expiry_2": "2026-12-04"},
          "BBB": None}                                                         # yfinance fails on BBB
    alp = {"AAA": {"pcr": 0.375, "call_oi": 400, "put_oi": 150, "expiry_1": "2026-11-27", "expiry_2": "2026-12-04",
                   "oi_date": "2026-11-20"},
           "BBB": {"pcr": 1.2, "call_oi": 10, "put_oi": 12, "expiry_1": "2026-11-27", "expiry_2": None,
                   "oi_date": "2026-11-20"}}
    with patch.object(col, "get_sectors", return_value={"S": {"tickers": ["AAA", "BBB"]}}), \
         patch.object(col, "_fetch_vix", return_value=18.0), \
         patch.object(col, "_fetch_pcr", side_effect=lambda t: yf[t]), \
         patch.object(col, "_fetch_pcr_alpaca", side_effect=lambda t, d: alp[t]), \
         patch.object(col, "PCR_DONE_PATH", tmp_path / "done.json"):
        out = col.collect_pcr_snapshot(today=date(2026, 11, 23))
    assert (out["collected"], out["errors"], out["alpaca_collected"], out["alpaca_errors"]) == (1, 1, 2, 0)
    assert [r["ticker"] for r in get_pcr_history(min_date="2026-11-23")] == ["AAA"]
    conn = get_db()
    try:
        rows = conn.execute("SELECT ticker, date, oi_date, pcr FROM pcr_history_alpaca ORDER BY ticker").fetchall()
    finally:
        conn.close()
    assert [tuple(r) for r in rows] == [("AAA", "2026-11-23", "2026-11-20", 0.375), ("BBB", "2026-11-23", "2026-11-20", 1.2)]
    import json
    assert json.loads((tmp_path / "done.json").read_text())["alpaca_collected"] == 2
