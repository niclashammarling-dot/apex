"""
Demo exit catch-up after a missed interval (vault scope raw/notes/2026-10/2026-10-31-apex-saturday-scopes.md § 1,
option (a); built 2026-11-07). A skipped exit check cannot fire a stop: 10-12 (yfinance "no price data", 6 tickers,
2 cycles) and 10-22 (DNS outage, every demo position about 21 cycles). The next check with a price walks the gap's
5-minute bars: the first bar reaching a stop or the target decides, a bar reaching both closes at the stop, the fill
is the level or the bar's open when it gapped through.
"""
from datetime import datetime, timedelta, timezone

import pytest

from backend import wallet

ENTRY = 100.0


def _bar(minute, o, h, low):
    return {"ts": f"2026-10-22T18:{minute:02d}:00+00:00", "open": o, "high": h, "low": low}


def test_a_stop_touched_in_the_gap_closes_at_the_stop_level_and_time():
    r = wallet._walk_bars(ENTRY, ENTRY, [_bar(0, 100, 101, 99), _bar(5, 99, 99.5, 93.5), _bar(10, 94, 96, 93)],
                          tp_pct=0.06, sl_pct=0.06, trigger_pct=None, lock_trail=None)
    assert r["exit"] == {"reason": "SL", "outcome": "LOSS", "price": pytest.approx(94.0), "at": "2026-10-22T18:05:00+00:00"}


def test_target_then_stop_closes_at_the_target():
    r = wallet._walk_bars(ENTRY, ENTRY, [_bar(0, 100, 106.5, 100), _bar(5, 106, 106, 90)],
                          tp_pct=0.06, sl_pct=0.06, trigger_pct=None, lock_trail=None)
    assert (r["exit"]["reason"], r["exit"]["price"]) == ("TP", pytest.approx(106.0))


def test_a_bar_reaching_both_closes_at_the_stop():
    r = wallet._walk_bars(ENTRY, ENTRY, [_bar(0, 100, 107, 93)], tp_pct=0.06, sl_pct=0.06,
                          trigger_pct=None, lock_trail=None)
    assert r["exit"]["reason"] == "SL"


def test_a_gap_through_the_stop_fills_at_the_open():
    r = wallet._walk_bars(ENTRY, ENTRY, [_bar(0, 92, 93, 91)], tp_pct=0.06, sl_pct=0.06,
                          trigger_pct=None, lock_trail=None)
    assert r["exit"]["price"] == pytest.approx(92.0)


def test_an_armed_trail_stops_above_the_sl_and_a_bar_does_not_arm_it_against_itself():
    bars = [_bar(0, 100, 105, 100), _bar(5, 104.9, 105, 103.5)]          # peak 105 after bar 1; trail 1 % → 103.95
    r = wallet._walk_bars(ENTRY, ENTRY, bars, tp_pct=0.06, sl_pct=0.06, trigger_pct=0.04, lock_trail=0.01)
    assert (r["exit"]["reason"], r["exit"]["outcome"], r["exit"]["price"]) == ("TSL", "WIN", pytest.approx(103.95))
    r = wallet._walk_bars(ENTRY, ENTRY, [_bar(0, 100, 105, 103.5)], tp_pct=0.06, sl_pct=0.06,
                          trigger_pct=0.04, lock_trail=0.01)
    assert r["exit"] is None and r["peak"] == 105                         # same bar: armed only afterwards


def _trade(checked_minutes_ago):
    at = (datetime.now(timezone.utc) - timedelta(minutes=checked_minutes_ago)).isoformat()
    return {"id": 1, "ticker": "ZZZ", "price": ENTRY, "amount": 200.0, "peak_price": ENTRY, "exits_checked_at": at,
            "timestamp": at}


def test_no_gap_reads_no_bars(monkeypatch):
    monkeypatch.setattr(wallet, "_bars_since", lambda *a: pytest.fail("bars read without a gap"))
    assert wallet._catch_up(_trade(5), ENTRY, 0.06, 0.06, None, None) is None


def test_check_exits_closes_a_trade_whose_stop_was_touched_while_prices_were_missing(monkeypatch):
    closed = {}
    monkeypatch.setattr(wallet, "get_open_trades", lambda: [_trade(150)])
    monkeypatch.setattr(wallet, "_batch_prices", lambda t: {"ZZZ": 99.0})   # back inside the band now
    monkeypatch.setattr(wallet, "_bars_since", lambda tk, since: [_bar(0, 100, 100, 99), _bar(5, 99, 99, 93.0)])
    monkeypatch.setattr(wallet, "close_trade", lambda **k: closed.update(k))
    monkeypatch.setattr(wallet, "update_trade_peak_price", lambda *a: None)
    monkeypatch.setattr(wallet, "set_trade_exits_checked", lambda *a: pytest.fail("stamped after a close"))
    monkeypatch.setattr(wallet, "get_demo_config", lambda: {"take_profit_pct": 0.06, "stop_loss_pct": 0.06,
                                                            "profit_lock_trigger_pct": None, "profit_lock_trail_pct": None,
                                                            "max_hold_days": 25})
    out = wallet.check_exits()
    assert [o["exit_reason"] for o in out] == ["SL"]
    assert closed["price_exit"] == pytest.approx(94.0) and closed["exited_at"] == "2026-10-22T18:05:00+00:00"


def test_a_missing_price_leaves_the_stamp_to_age(monkeypatch):
    monkeypatch.setattr(wallet, "get_open_trades", lambda: [_trade(5)])
    monkeypatch.setattr(wallet, "_batch_prices", lambda t: {})
    monkeypatch.setattr(wallet, "set_trade_exits_checked", lambda *a: pytest.fail("stamped without a price"))
    monkeypatch.setattr(wallet, "get_demo_config", lambda: {"take_profit_pct": 0.06, "stop_loss_pct": 0.06,
                                                            "max_hold_days": 25})
    assert wallet.check_exits() == []


def test_bars_since_reads_5_minute_bars_after_the_gap_start(monkeypatch):
    import pandas as pd
    idx = pd.DatetimeIndex(["2026-10-22 18:00", "2026-10-22 18:05", "2026-10-22 18:10"], tz="UTC")
    df = pd.DataFrame({"Open": [100.0, 99.0, float("nan")], "High": [101.0, 99.5, 98.0],
                       "Low": [99.0, 93.5, 97.0], "Close": [100.5, 94.0, 97.5]}, index=idx)
    calls = {}
    monkeypatch.setattr(wallet.yf, "download", lambda t, **k: calls.update(k) or df)
    since = datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)
    bars = wallet._bars_since("ZZZ", since)
    assert calls["interval"] == "5m"
    assert [b["ts"] for b in bars] == ["2026-10-22T18:05:00+00:00"]   # after the gap start; a NaN bar is dropped
    assert bars[0]["low"] == 93.5


def test_bars_since_returns_none_when_nothing_can_be_read(monkeypatch):
    import pandas as pd
    monkeypatch.setattr(wallet.yf, "download", lambda t, **k: pd.DataFrame())
    assert wallet._bars_since("ZZZ", datetime(2026, 10, 22, tzinfo=timezone.utc)) is None

    def boom(t, **k):
        raise RuntimeError("no network")
    monkeypatch.setattr(wallet.yf, "download", boom)
    assert wallet._bars_since("ZZZ", datetime(2026, 10, 22, tzinfo=timezone.utc)) is None
