"""
Codebase audit finding 7 (R013, built 2026-11-14, before Thanksgiving 2026-11-26).

- The live and demo time stops count NYSE sessions, not weekdays: a hold across
  Thanksgiving is one session shorter than the weekday count, so the stop no longer fires
  a session early. Both books give the same count.
- The half day 2026-11-27 (close 13:00 ET) is one session in the count, and
  is_market_open is false at 13:30 ET that day (Niclas 2026-11-07: "lås fyndet med ett test").
- The regime exits' staleness guard counts the same way: Wednesday's result on the Friday
  after Thanksgiving is one session old, so the exit check runs.
- The orders feed labels a stop filled above the exit anchor TSL (168 h cooloff), as the
  bracket-leg path does; below it, or with no anchor, SL.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

import backend.live_trades_tracker as ltt
import backend.market_calendar as mc
import backend.scheduler as scheduler
import backend.wallet as wallet

NY = ZoneInfo("America/New_York")
ENTRY = "2026-11-20T15:00:00+00:00"   # Friday before Thanksgiving week


def _today(monkeypatch, d: date):
    monkeypatch.setattr(mc, "_today_ny", lambda: d)


def test_hold_across_thanksgiving_counts_sessions_not_weekdays(monkeypatch):
    _today(monkeypatch, date(2026, 11, 30))
    weekdays = len(pd.bdate_range(date(2026, 11, 20), date(2026, 11, 30))) - 1
    assert weekdays == 6                                   # the old count: 23, 24, 25, 26, 27, 30
    assert ltt._trading_days_since(ENTRY) == 5             # 26 is a holiday
    assert wallet._trading_days_since(ENTRY) == 5          # the demo book counts the same


def test_the_half_day_is_one_whole_session(monkeypatch):
    _today(monkeypatch, date(2026, 11, 27))
    assert ltt._trading_days_since(ENTRY) == 4             # 23, 24, 25, 27
    assert mc.sessions_after(date(2026, 11, 26), date(2026, 11, 27)) == 1


def test_no_holiday_gives_the_weekday_count():
    # Control: the two counts agree where there is no holiday.
    assert mc.sessions_after(date(2026, 11, 2), date(2026, 11, 13)) == len(pd.bdate_range("2026-11-02", "2026-11-13")) - 1


def _clock(monkeypatch, hh, mm):
    class _Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 27, hh, mm, tzinfo=NY)
    monkeypatch.setattr(scheduler, "datetime", _Fixed)


def test_market_closed_at_1330_et_on_the_half_day(monkeypatch):
    _clock(monkeypatch, 13, 30)
    assert scheduler.is_market_open() is False


def test_market_open_at_1230_et_on_the_half_day(monkeypatch):
    _clock(monkeypatch, 12, 30)                            # control: the session itself is open
    assert scheduler.is_market_open() is True


def test_wednesdays_regime_result_is_fresh_on_the_friday_after_thanksgiving(monkeypatch):
    _today(monkeypatch, date(2026, 11, 27))
    assert mc.sessions_since(date(2026, 11, 25)) == 1      # the guard skips only above 1
    assert len(pd.bdate_range(date(2026, 11, 25), date(2026, 11, 27))) - 1 == 2   # the old count: stale


class _Broker:
    def __init__(self, fill_price):
        self._fill = fill_price

    def get_orders(self, limit=100, nested=True):
        return [{"ticker": "XOM", "side": "sell", "type": "stop", "filled_avg_price": self._fill,
                 "filled_at": "2026-11-12T18:00:00+00:00", "filled_qty": 10, "legs": []}]


def test_orders_feed_labels_a_ratcheted_stop_tsl():
    f = ltt.find_exit_fill("XOM", _Broker(112.0), "2026-11-10T15:00:00+00:00", anchor=110.0)
    assert (f["evidence"], f["reason"]) == ("found", "TSL")


def test_orders_feed_labels_a_stop_below_the_anchor_sl():
    f = ltt.find_exit_fill("XOM", _Broker(104.0), "2026-11-10T15:00:00+00:00", anchor=110.0)
    assert f["reason"] == "SL"


def test_orders_feed_without_an_anchor_keeps_sl():
    # The live gate's day-P&L check passes no anchor; its label is unchanged.
    f = ltt.find_exit_fill("XOM", _Broker(112.0), "2026-11-10T15:00:00+00:00")
    assert f["reason"] == "SL"


class _Result:
    date = "2026-11-25"
    leaderboard: list = []


class _RB:
    def last_result(self):
        return _Result()


def _guard_reached(monkeypatch, module, open_fn, sessions):
    """Whether a regime exit check got past its staleness guard (it then reads open trades)."""
    reached = []
    monkeypatch.setattr(ltt, "LIVE_ENABLED", True)   # the live check returns before its guard otherwise (CI 329)
    monkeypatch.setattr(scheduler, "_get_regime_bayes", lambda: _RB())
    monkeypatch.setattr(module, open_fn, lambda: reached.append(1) or [])
    if sessions is not None:
        monkeypatch.setattr(mc, "sessions_since", lambda d: sessions)
    (wallet.check_regime_exits if module is wallet else ltt.check_live_regime_exits)()
    return bool(reached)


def test_both_regime_guards_read_the_session_count(monkeypatch):
    # Fails on the parent: its weekday count ignores market_calendar.
    assert not _guard_reached(monkeypatch, wallet, "get_open_trades", sessions=2)
    assert not _guard_reached(monkeypatch, ltt, "get_open_live_trades", sessions=2)
    assert _guard_reached(monkeypatch, wallet, "get_open_trades", sessions=1)
    assert _guard_reached(monkeypatch, ltt, "get_open_live_trades", sessions=1)


def test_regime_check_runs_on_the_half_day_with_wednesdays_result(monkeypatch):
    _today(monkeypatch, date(2026, 11, 27))
    assert _guard_reached(monkeypatch, wallet, "get_open_trades", sessions=None)
    assert _guard_reached(monkeypatch, ltt, "get_open_live_trades", sessions=None)


def test_today_is_the_new_york_date():
    assert mc._today_ny() == datetime.now(NY).date()
