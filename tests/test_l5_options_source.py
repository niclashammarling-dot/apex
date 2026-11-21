"""L5 options inputs: Alpaca primary for open interest and volume, yfinance fallback per field (2026-11-21, the 11-21
order's item 1; vault INDEX "APEX options source": Niclas 2026-11-04 confirmed Alpaca as primary).

The 11-20 probe found an Alpaca chain without lastTradeDate errors on every ticker in production's unusual_calls
(102 of 102), so the adapter carries dailyBar.t as lastTradeDate: the positive control below is the column-less
frame, which still errors.
"""
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

from backend.data import options_alpaca as oa
from backend.gate import l5_positioning as l5

TODAY = date(2026, 11, 23)
EXP = ["2026-11-27", "2026-12-04"]
ROWS = [  # contracts endpoint: two expiries, OI dated one session behind
    {"symbol": "AMT261127C00200000", "expiry": EXP[0], "type": "call", "oi": 300, "oi_date": "2026-11-20"},
    {"symbol": "AMT261127P00200000", "expiry": EXP[0], "type": "put", "oi": 100, "oi_date": "2026-11-20"},
    {"symbol": "AMT261204C00200000", "expiry": EXP[1], "type": "call", "oi": 200, "oi_date": "2026-11-20"},
    {"symbol": "AMT261204P00200000", "expiry": EXP[1], "type": "put", "oi": None, "oi_date": None},
    {"symbol": "AMT261211C00200000", "expiry": "2026-12-11", "type": "call", "oi": 50, "oi_date": "2026-11-20"},
]
SNAPS = {  # chain snapshot: dailyBar is the contract's last traded day
    "AMT261127C00200000": {"dailyBar": {"t": "2026-11-23T20:59:00Z", "v": 60}},
    "AMT261127P00200000": {"dailyBar": {"t": "2026-11-23T20:58:00Z", "v": 20}},
    "AMT261204C00200000": {"dailyBar": {"t": "2026-11-18T20:00:00Z", "v": 999}},   # an earlier day: not today's
}


def _yf_chains():
    def side(oi, vol):
        return pd.DataFrame({"openInterest": [oi], "volume": [vol],
                             "lastTradeDate": [pd.Timestamp("2026-11-23T20:00:00Z")]})
    return [(side(900, 5), side(100, 50), "2026-11-27")]


def _fetch(contracts=ROWS, snaps=SNAPS, yf=None):
    kw = {}
    with patch.object(oa, "contracts", side_effect=contracts if isinstance(contracts, Exception) else None,
                      return_value=None if isinstance(contracts, Exception) else contracts), \
         patch.object(oa, "snapshots", side_effect=snaps if isinstance(snaps, Exception) else None,
                      return_value=None if isinstance(snaps, Exception) else snaps), \
         patch.object(l5, "_fetch_options_chains",
                      side_effect=yf if isinstance(yf, Exception) else None,
                      return_value=None if isinstance(yf, Exception) else (yf or _yf_chains())) as yfm:
        out = l5._fetch_options("AMT", today=TODAY, **kw)
        return (*out, yfm.call_count)


def test_r126_pcr_decision_reads_yfinance_with_alpaca_beside_it():
    """R126 (c): for the 20-session comparison the decision's open interest stays on yfinance; Alpaca's is recorded."""
    assert l5.PCR_OI_SOURCE == "yfinance"
    oi, vol, meta, yf_calls = _fetch()
    assert yf_calls == 1 and meta["oi_source"] == "yfinance" and meta["volume_source"] == "alpaca"
    assert l5._check_put_call_ratio(oi, ticker="AMT")["call_oi"] == 900                 # yfinance's chain
    assert meta["alpaca_shadow"] == {"pc_ratio": 0.2, "call_oi": 500, "put_oi": 100, "oi_date": "2026-11-20",
                                     "expiries": EXP}
    assert l5._source_fields(meta, "oi")["alpaca_shadow"]["pc_ratio"] == 0.2
    assert l5._check_unusual_call_volume(vol, session=TODAY)["call_vol"] == 60          # volume is Alpaca's


def test_r126_a_yfinance_failure_is_still_a_data_error_with_the_shadow_kept():
    oi, vol, meta, _ = _fetch(yf=RuntimeError("curl: (35) TLS connect error"))
    assert oi == [] and meta["oi_error"] == "curl TLS error" and meta["alpaca_shadow"]["call_oi"] == 500
    assert meta["volume_source"] == "alpaca" and "volume_error" not in meta


def test_both_fields_from_alpaca_and_yfinance_not_called(monkeypatch):
    monkeypatch.setattr(l5, "PCR_OI_SOURCE", "alpaca")                               # after the switch
    oi, vol, meta, yf_calls = _fetch()
    assert yf_calls == 0 and meta["oi_source"] == meta["volume_source"] == "alpaca"
    assert meta["oi_date"] == "2026-11-20" and [e for *_, e in oi] == EXP          # the two nearest expiries
    pcr = l5._check_put_call_ratio(oi, ticker="AMT")
    assert (pcr["call_oi"], pcr["put_oi"]) == (500, 100)                            # None OI counts as zero
    ucv = l5._check_unusual_call_volume(vol, session=TODAY)
    assert not ucv.get("error") and (ucv["call_vol"], ucv["put_vol"]) == (60, 20)  # the 11-18 bar is not today's


def test_a_failed_snapshot_takes_volume_from_yfinance_only(monkeypatch):
    monkeypatch.setattr(l5, "PCR_OI_SOURCE", "alpaca")
    oi, vol, meta, yf_calls = _fetch(snaps=ConnectionError("snapshot 503"))
    assert yf_calls == 1 and meta["oi_source"] == "alpaca" and meta["volume_source"] == "yfinance"
    assert "snapshot" in meta["alpaca_error"] and l5._check_unusual_call_volume(vol, session=TODAY)["call_vol"] == 5
    assert l5._source_fields(meta, "volume")["alpaca_error"].startswith("snapshot")
    assert "alpaca_error" not in l5._source_fields(meta, "oi")


def test_failed_contracts_take_both_fields_from_one_yfinance_fetch():
    oi, vol, meta, yf_calls = _fetch(contracts=ConnectionError("contracts 500"))
    assert yf_calls == 1 and meta["oi_source"] == meta["volume_source"] == "yfinance" and oi is vol


def test_no_contract_listed_falls_back_and_says_so():
    oi, vol, meta, yf_calls = _fetch(contracts=[])
    assert meta["oi_source"] == "yfinance" and meta["alpaca_error"] == "no contract within the horizon"


def test_both_sources_down_is_a_data_error_on_both_checks():
    oi, vol, meta, _ = _fetch(contracts=ConnectionError("down"), yf=RuntimeError("curl: (35) TLS connect error"))
    assert oi == [] and meta["oi_error"] == meta["volume_error"] == "curl TLS error"
    assert l5._check_put_call_ratio(oi, meta["oi_error"])["error"] is True


def test_the_adapter_frame_carries_lastTradeDate_and_the_column_less_frame_still_errors():
    calls, puts = oa.frames(ROWS, SNAPS, EXP[0])
    assert "lastTradeDate" in calls.columns and list(calls["volume"]) == [60.0]
    ok = l5._check_unusual_call_volume([(calls, puts, EXP[0])], session=TODAY)
    assert not ok.get("error")
    bare = [(calls.drop(columns=["lastTradeDate"]), puts.drop(columns=["lastTradeDate"]), EXP[0])]
    assert l5._check_unusual_call_volume(bare, session=TODAY)["error"] is True      # positive control
    calls_novol, _ = oa.frames(ROWS, None, EXP[0])
    assert calls_novol["volume"].isna().all() and "lastTradeDate" in calls_novol.columns


def test_evaluate_records_the_source_on_each_options_check():
    with patch.object(oa, "contracts", return_value=ROWS), patch.object(oa, "snapshots", return_value=SNAPS), \
         patch.object(l5, "_check_relative_strength", return_value={"pass": True, "reason": "rs"}), \
         patch.object(l5, "_check_volume_accumulation", return_value={"pass": True, "reason": "va"}), \
         patch.object(l5, "_fetch_options_chains", return_value=_yf_chains()):
        r = l5.evaluate("AMT", "RealEstate")
    checks = r.data["checks"]
    assert checks["put_call_ratio"]["source"] == "yfinance" and checks["put_call_ratio"]["call_oi"] == 900
    assert checks["put_call_ratio"]["alpaca_shadow"]["oi_date"] == "2026-11-20"
    assert checks["unusual_calls"]["source"] == "alpaca" and "alpaca_shadow" not in checks["unusual_calls"]


def test_contracts_pages_and_keeps_the_fields():
    pages = [SimpleNamespace(option_contracts=[SimpleNamespace(symbol="A1", expiration_date=date(2026, 11, 27),
                                                               type=SimpleNamespace(value="call"), open_interest="12",
                                                               open_interest_date=date(2026, 11, 20))],
                             next_page_token="p2"),
             SimpleNamespace(option_contracts=[SimpleNamespace(symbol="A2", expiration_date=date(2026, 12, 4),
                                                               type=SimpleNamespace(value="put"), open_interest=None,
                                                               open_interest_date=None)],
                             next_page_token=None)]
    tc = SimpleNamespace(get_option_contracts=lambda req: pages.pop(0))
    rows = oa.contracts("AMT", TODAY, tc=tc)
    assert rows == [{"symbol": "A1", "expiry": "2026-11-27", "type": "call", "oi": 12, "oi_date": "2026-11-20"},
                    {"symbol": "A2", "expiry": "2026-12-04", "type": "put", "oi": None, "oi_date": None}]
    assert oa.nearest_expiries(ROWS) == EXP and oa.oi_date(ROWS, EXP) == "2026-11-20"


def test_snapshots_reads_the_raw_chain():
    seen = {}
    dc = SimpleNamespace(get_option_chain=lambda req: seen.update(req=req) or {"S": {"dailyBar": {}}})
    assert oa.snapshots("AMT", EXP, dc=dc) == {"S": {"dailyBar": {}}}
    assert seen["req"].underlying_symbol == "AMT"
    dc2 = SimpleNamespace(get_option_chain=lambda req: ["not", "a", "dict"])
    assert oa.snapshots("AMT", EXP, dc=dc2) == {}


def test_clients_are_built_once_with_raw_data(monkeypatch):
    built = []

    class Fake:
        def __init__(self, *a, **k):
            built.append((type(self).__name__, k))
    monkeypatch.setattr(oa, "_trading", None)
    monkeypatch.setattr(oa, "_data", None)
    with patch("alpaca.trading.client.TradingClient", type("TradingClient", (Fake,), {})), \
         patch("alpaca.data.historical.option.OptionHistoricalDataClient", type("DataClient", (Fake,), {})):
        a = oa._clients()
        b = oa._clients()
    assert a is b or a == b
    assert [n for n, _ in built] == ["TradingClient", "DataClient"] and built[1][1]["raw_data"] is True
