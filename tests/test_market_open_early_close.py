"""
is_market_open() reads open/close from the NYSE calendar (2026-09-17): a 13:00 ET
early close ends the session at 13:00, not 16:00. Before this, the gate kept
evaluating a closed market for three hours on the day after Thanksgiving and on
Christmas Eve.
"""
from datetime import datetime

import backend.scheduler as sched
from backend.scheduler import NY


class _Now:
    def __init__(self, fixed: datetime):
        self._fixed = fixed

    def now(self, tz=None):
        return self._fixed


def _at(y, m, d, hh, mm, monkeypatch):
    monkeypatch.setattr(sched, "datetime", _Now(datetime(y, m, d, hh, mm, tzinfo=NY)))
    return sched.is_market_open()


def test_full_day_bounds(monkeypatch):
    assert _at(2026, 9, 17, 9, 29, monkeypatch) is False
    assert _at(2026, 9, 17, 9, 30, monkeypatch) is True
    assert _at(2026, 9, 17, 15, 59, monkeypatch) is True
    assert _at(2026, 9, 17, 16, 1, monkeypatch) is False


def test_early_close_ends_at_13(monkeypatch):
    # 2026-11-27, day after Thanksgiving: NYSE closes 13:00 ET
    assert _at(2026, 11, 27, 12, 59, monkeypatch) is True
    assert _at(2026, 11, 27, 13, 1, monkeypatch) is False
    assert _at(2026, 11, 27, 15, 0, monkeypatch) is False


def test_holiday_and_weekend_closed(monkeypatch):
    assert _at(2026, 9, 7, 12, 0, monkeypatch) is False    # Labor Day
    assert _at(2026, 9, 13, 12, 0, monkeypatch) is False   # Sunday


def test_sessions_for_date_still_answers():
    assert sched._nyse_sessions_for_date("2026-09-17") == 1
    assert sched._nyse_sessions_for_date("2026-09-07") == 0
