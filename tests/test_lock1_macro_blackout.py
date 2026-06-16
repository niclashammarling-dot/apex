"""
Tests for Lock 1 macro event blackout — asymmetric behaviour.

Key invariants (post penalty refactor):
  - FOMC: hard_block for 0–2 days before the event
  - CPI/NFP event day: hard_block (days_ahead = 0)
  - CPI/NFP day-before: pre_event (score penalty, not a hard block)
  - Post-event: clear (days_ahead < 0)
"""
from datetime import date, timedelta

import pytest

from backend.gate.lock1_eligibility import (
    _FOMC_DATES,
    _CPI_DATES,
    _NFP_DATES,
    _MACRO_DATES,
    _FOMC_PRE_EVENT_DAYS,
    _macro_status,
    _check_calendar_expiry,
)


# ── helpers ────────────────────────────────────────────────────────────────────

def _any_fomc() -> date:
    return min(d for d in _FOMC_DATES if d.year >= 2026)

def _any_cpi() -> date:
    return min(d for d in _CPI_DATES if d.year >= 2026)

def _any_nfp() -> date:
    return min(d for d in _NFP_DATES if d.year >= 2026)


# ── post-event is always clear ─────────────────────────────────────────────────

def test_cpi_day_after_is_clear():
    day_after = _any_cpi() + timedelta(days=1)
    status, _ = _macro_status(day_after, fomc_blackout=_FOMC_PRE_EVENT_DAYS)
    assert status == "clear", "Post-CPI day must be clear"


def test_fomc_day_after_is_clear():
    day_after = _any_fomc() + timedelta(days=1)
    status, _ = _macro_status(day_after, fomc_blackout=_FOMC_PRE_EVENT_DAYS)
    assert status == "clear", "Post-FOMC day must be clear"


def test_nfp_day_after_is_clear():
    day_after = _any_nfp() + timedelta(days=1)
    status, _ = _macro_status(day_after, fomc_blackout=_FOMC_PRE_EVENT_DAYS)
    assert status == "clear", "Post-NFP day must be clear"


# ── event day is hard_block ────────────────────────────────────────────────────

def test_cpi_day_itself_is_hard_block():
    cpi = _any_cpi()
    status, desc = _macro_status(cpi, fomc_blackout=_FOMC_PRE_EVENT_DAYS)
    assert status == "hard_block"
    assert "CPI" in desc


def test_fomc_day_itself_is_hard_block():
    fomc = _any_fomc()
    status, desc = _macro_status(fomc, fomc_blackout=_FOMC_PRE_EVENT_DAYS)
    assert status == "hard_block"
    assert "FOMC" in desc


def test_nfp_day_itself_is_hard_block():
    nfp = _any_nfp()
    status, desc = _macro_status(nfp, fomc_blackout=_FOMC_PRE_EVENT_DAYS)
    assert status == "hard_block"
    assert "NFP" in desc


# ── CPI/NFP day-before is pre_event (not hard_block) ──────────────────────────

def test_cpi_one_day_before_is_pre_event():
    cpi = _any_cpi()
    status, desc = _macro_status(cpi - timedelta(days=1), fomc_blackout=_FOMC_PRE_EVENT_DAYS)
    assert status == "pre_event"
    assert "CPI" in desc


def test_nfp_one_day_before_is_pre_event():
    nfp = _any_nfp()
    status, desc = _macro_status(nfp - timedelta(days=1), fomc_blackout=_FOMC_PRE_EVENT_DAYS)
    assert status == "pre_event"
    assert "NFP" in desc


def test_cpi_two_days_before_is_clear():
    cpi = _any_cpi()
    status, _ = _macro_status(cpi - timedelta(days=2), fomc_blackout=_FOMC_PRE_EVENT_DAYS)
    assert status == "clear"


# ── FOMC pre-window is hard_block throughout ───────────────────────────────────

def test_fomc_two_days_before_is_hard_block():
    fomc = _any_fomc()
    status, desc = _macro_status(fomc - timedelta(days=2), fomc_blackout=_FOMC_PRE_EVENT_DAYS)
    assert status == "hard_block"
    assert "FOMC" in desc


def test_fomc_one_day_before_is_hard_block():
    fomc = _any_fomc()
    status, desc = _macro_status(fomc - timedelta(days=1), fomc_blackout=_FOMC_PRE_EVENT_DAYS)
    assert status == "hard_block"
    assert "FOMC" in desc


def test_fomc_three_days_before_is_clear():
    fomc = _any_fomc()
    status, _ = _macro_status(fomc - timedelta(days=3), fomc_blackout=2)
    assert status == "clear"


def test_fomc_pre_event_constant_is_2():
    assert _FOMC_PRE_EVENT_DAYS == 2


# ── calendar expiry ────────────────────────────────────────────────────────────

def test_calendar_expiry_no_error_with_valid_dates(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="backend.gate.lock1_eligibility"):
        _check_calendar_expiry()
    assert "exhausted" not in caplog.text


def test_calendar_expiry_warns_when_near(monkeypatch):
    from unittest.mock import patch as _patch
    last = max(_MACRO_DATES)
    fake_today = last - timedelta(days=30)

    import backend.gate.lock1_eligibility as m
    monkeypatch.setattr(m, "_MACRO_DATES", {last})

    with _patch.object(m.logger, "warning") as mock_warn:
        with _patch("backend.gate.lock1_eligibility.date") as mock_date:
            mock_date.today.return_value = fake_today
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            m._check_calendar_expiry()
        assert mock_warn.called, "logger.warning should fire when calendar near expiry"
        msg = mock_warn.call_args[0][0]
        assert "expires in" in msg
