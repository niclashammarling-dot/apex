"""
unusual_calls counts only contracts traded in the evaluating session (2026-11-14, Saturday order item 5;
scope vault raw/notes/2026-11/2026-11-02-apex-saturday-scopes-11-07.md § 1). yfinance's `volume` is each
contract's last traded day's; summed without lastTradeDate, 20 of 102 unusual_calls and 9 options-group
decisions flipped at one measured minute (11-02 probe).
"""
from datetime import date

import pandas as pd

import backend.gate.l5_positioning as l5

SESSION = date(2026, 11, 2)


def _side(rows):
    """rows: (volume, lastTradeDate UTC)."""
    return pd.DataFrame({"volume": [v for v, _ in rows], "openInterest": [1] * len(rows),
                         "lastTradeDate": pd.to_datetime([t for _, t in rows], utc=True)})


def test_a_chain_mixing_days_counts_only_today():
    calls = _side([(300, "2026-11-02T15:00:00Z"), (500, "2026-10-30T15:00:00Z")])
    puts = _side([(100, "2026-11-02T16:00:00Z"), (900, "2026-10-08T15:00:00Z")])
    r = l5._check_unusual_call_volume([(calls, puts, "2026-11-06")], session=SESSION)
    assert (r["call_vol"], r["put_vol"], r["cv_ratio"], r["pass"]) == (300, 100, 3.0, True)
    assert (r["call_vol_all_days"], r["put_vol_all_days"], r["n_contracts_today"]) == (800, 1000, 2)


def test_a_late_trade_belongs_to_its_new_york_day():
    # 01:00 UTC on 11-03 is 20:00 ET on 11-02: the 11-02 session's.
    calls = _side([(40, "2026-11-03T01:00:00Z")])
    puts = _side([(10, "2026-11-02T15:00:00Z")])
    assert l5._check_unusual_call_volume([(calls, puts, "x")], session=SESSION)["call_vol"] == 40


def test_only_earlier_days_is_no_volume_today_not_an_error():
    calls = _side([(286, "2026-10-30T15:00:00Z")])
    puts = _side([(66, "2026-10-29T15:00:00Z")])
    r = l5._check_unusual_call_volume([(calls, puts, "x")], session=SESSION)
    assert r["pass"] is False and "error" not in r and r["reason"] == "no volume today"
    assert (r["call_vol_all_days"], r["put_vol_all_days"]) == (286, 66)


def test_a_chain_without_last_trade_date_is_a_data_error():
    bare = pd.DataFrame({"volume": [5], "openInterest": [1]})
    r = l5._check_unusual_call_volume([(bare, bare, "x")], session=SESSION)
    assert r["error"] is True and r["pass"] is False


def test_the_11_02_eog_chain_shape_reproduces_the_measured_ratios():
    # Probe 2026-11-02 16:23 UTC: all days calls 352 / puts 446 (0.79), today 43 / 142 (0.30).
    calls = _side([(43, "2026-11-02T14:00:00Z"), (309, "2026-10-30T15:00:00Z")])
    puts = _side([(142, "2026-11-02T14:30:00Z"), (304, "2026-10-28T15:00:00Z")])
    r = l5._check_unusual_call_volume([(calls, puts, "2026-11-06")], session=SESSION)
    assert r["cv_ratio"] == 0.3 and (r["call_vol_all_days"], r["put_vol_all_days"]) == (352, 446)
    assert round(352 / 446, 2) == 0.79                       # the old all-days reading of the same chain
