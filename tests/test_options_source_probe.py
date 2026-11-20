"""Options source probe (scripts/options_source_probe.py): the Alpaca chain frame feeds production's
unusual_calls, which counts only contracts traded in the evaluating session (lastTradeDate, 9dfc26c).
Founding case 2026-11-20: the frame had no lastTradeDate and every Alpaca chain errored (102 of 102)."""
import importlib.util
import sys
from datetime import date
from pathlib import Path

from backend.gate.l5_positioning import _check_put_call_ratio, _check_unusual_call_volume

_spec = importlib.util.spec_from_file_location(
    "options_source_probe", Path(__file__).resolve().parents[1] / "scripts" / "options_source_probe.py")
probe = importlib.util.module_from_spec(_spec)
sys.modules["options_source_probe"] = probe
_spec.loader.exec_module(probe)

EXP = "2026-11-27"
CONTRACTS = [
    {"symbol": "X261127C00100000", "expiry": EXP, "type": "call", "oi": 300, "oi_date": "2026-11-18"},
    {"symbol": "X261127C00110000", "expiry": EXP, "type": "call", "oi": 200, "oi_date": "2026-11-18"},
    {"symbol": "X261127P00090000", "expiry": EXP, "type": "put", "oi": 400, "oi_date": "2026-11-18"},
    {"symbol": "X261204C00100000", "expiry": "2026-12-04", "type": "call", "oi": 999, "oi_date": "2026-11-18"},
]
SNAPS = {   # dailyBar.t is midnight New York of the contract's last traded day, as Alpaca returns it
    "X261127C00100000": {"dailyBar": {"t": "2026-11-20T05:00:00Z", "v": 100}},
    "X261127C00110000": {"dailyBar": {"t": "2026-11-17T05:00:00Z", "v": 500}},   # last traded Tuesday
    "X261127P00090000": {"dailyBar": {"t": "2026-11-20T05:00:00Z", "v": 10}},
}


def test_alpaca_frame_reaches_production_unusual_calls_with_todays_volume_only():
    calls, puts = probe._frames(CONTRACTS, SNAPS, EXP, "2026-11-20")
    r = _check_unusual_call_volume([(calls, puts, EXP)], session=date(2026, 11, 20))
    assert not r.get("error"), r
    assert (r["call_vol"], r["put_vol"]) == (100, 10)          # Tuesday's 500 is not today's
    assert r["call_vol_all_days"] == 600 and r["n_contracts_today"] == 2
    assert r["pass"] is True


def test_alpaca_frame_keeps_its_expiry_and_open_interest_for_pcr():
    calls, puts = probe._frames(CONTRACTS, SNAPS, EXP, "2026-11-20")
    assert list(calls["openInterest"]) == [300.0, 200.0] and list(puts["openInterest"]) == [400.0]
    r = _check_put_call_ratio([(calls, puts, EXP)], ticker="X")
    assert (r["call_oi"], r["put_oi"]) == (500, 400)


def test_contract_without_a_snapshot_counts_no_volume():
    calls, puts = probe._frames(CONTRACTS, {}, EXP, "2026-11-20")
    r = _check_unusual_call_volume([(calls, puts, EXP)], session=date(2026, 11, 20))
    assert not r.get("error") and r["reason"] == "no volume today"
