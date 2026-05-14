"""
Tests for Lock 1 macro event blackout — asymmetric behaviour.

Key invariants:
  - Pre-event: blocked for N days before the event (N=1 for CPI/NFP, N=2 for FOMC)
  - Event day: blocked (days_ahead = 0)
  - Post-event: NOT blocked (days_ahead < 0)
  - FOMC pre-window is 2, not 1
"""
from datetime import date

import pytest

from backend.gate.lock1_eligibility import (
    _FOMC_DATES,
    _CPI_DATES,
    _NFP_DATES,
    _MACRO_DATES,
    _FOMC_PRE_EVENT_DAYS,
    _days_to_nearest_event,
    _check_calendar_expiry,
)


# ── helpers ────────────────────────────────────────────────────────────────────

def _any_fomc() -> date:
    return min(d for d in _FOMC_DATES if d.year >= 2026)

def _any_cpi() -> date:
    return min(d for d in _CPI_DATES if d.year >= 2026)

def _any_nfp() -> date:
    return min(d for d in _NFP_DATES if d.year >= 2026)


# ── post-event is never blocked ────────────────────────────────────────────────

def test_cpi_day_after_is_not_blocked():
    cpi = _any_cpi()
    today = date(cpi.year, cpi.month, cpi.day).__class__(
        cpi.year, cpi.month, cpi.day
    )
    # one day after
    from datetime import timedelta
    day_after = cpi + timedelta(days=1)
    blocked, _ = _days_to_nearest_event(day_after, event_blackout=1, fomc_blackout=_FOMC_PRE_EVENT_DAYS)
    assert not blocked, "Post-CPI day must not be blocked"


def test_fomc_day_after_is_not_blocked():
    from datetime import timedelta
    fomc = _any_fomc()
    day_after = fomc + timedelta(days=1)
    blocked, _ = _days_to_nearest_event(day_after, event_blackout=1, fomc_blackout=_FOMC_PRE_EVENT_DAYS)
    assert not blocked, "Post-FOMC day must not be blocked"


def test_nfp_day_after_is_not_blocked():
    from datetime import timedelta
    nfp = _any_nfp()
    day_after = nfp + timedelta(days=1)
    blocked, _ = _days_to_nearest_event(day_after, event_blackout=1, fomc_blackout=_FOMC_PRE_EVENT_DAYS)
    assert not blocked, "Post-NFP day must not be blocked"


# ── event day is blocked ───────────────────────────────────────────────────────

def test_cpi_day_itself_is_blocked():
    cpi = _any_cpi()
    blocked, desc = _days_to_nearest_event(cpi, event_blackout=1, fomc_blackout=_FOMC_PRE_EVENT_DAYS)
    assert blocked
    assert "CPI" in desc


def test_fomc_day_itself_is_blocked():
    fomc = _any_fomc()
    blocked, desc = _days_to_nearest_event(fomc, event_blackout=1, fomc_blackout=_FOMC_PRE_EVENT_DAYS)
    assert blocked
    assert "FOMC" in desc


# ── pre-event window ───────────────────────────────────────────────────────────

def test_cpi_one_day_before_is_blocked():
    from datetime import timedelta
    cpi = _any_cpi()
    blocked, desc = _days_to_nearest_event(cpi - timedelta(days=1), event_blackout=1, fomc_blackout=_FOMC_PRE_EVENT_DAYS)
    assert blocked
    assert "CPI" in desc


def test_cpi_two_days_before_is_not_blocked_with_window_1():
    from datetime import timedelta
    cpi = _any_cpi()
    blocked, _ = _days_to_nearest_event(cpi - timedelta(days=2), event_blackout=1, fomc_blackout=_FOMC_PRE_EVENT_DAYS)
    assert not blocked


def test_fomc_two_days_before_is_blocked():
    from datetime import timedelta
    fomc = _any_fomc()
    blocked, desc = _days_to_nearest_event(fomc - timedelta(days=2), event_blackout=1, fomc_blackout=_FOMC_PRE_EVENT_DAYS)
    assert blocked
    assert "FOMC" in desc


def test_fomc_three_days_before_is_not_blocked():
    from datetime import timedelta
    fomc = _any_fomc()
    blocked, _ = _days_to_nearest_event(fomc - timedelta(days=3), event_blackout=1, fomc_blackout=2)
    assert not blocked


def test_fomc_pre_event_constant_is_2():
    assert _FOMC_PRE_EVENT_DAYS == 2


# ── calendar expiry ────────────────────────────────────────────────────────────

def test_calendar_expiry_no_error_with_valid_dates(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="backend.gate.lock1_eligibility"):
        _check_calendar_expiry()
    # calendar extends to end of 2026 — today is 2026-05-14; >60 days remain
    assert "exhausted" not in caplog.text


def test_calendar_expiry_warns_when_near(monkeypatch):
    from datetime import timedelta
    from unittest.mock import patch as _patch
    last = max(_MACRO_DATES)
    fake_today = last - timedelta(days=30)

    import backend.gate.lock1_eligibility as m
    monkeypatch.setattr(m, "_MACRO_DATES", {last})

    with _patch.object(m.logger, "warning") as mock_warn:
        # inject today via monkeypatching date.today in the module
        with _patch("backend.gate.lock1_eligibility.date") as mock_date:
            mock_date.today.return_value = fake_today
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            m._check_calendar_expiry()
        assert mock_warn.called, "logger.warning should fire when calendar near expiry"
        msg = mock_warn.call_args[0][0]
        assert "expires in" in msg
