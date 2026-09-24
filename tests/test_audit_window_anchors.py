"""
last_due_session() — the trailing-window anchor convention (_audit_core).

Why this exists (2026-09-24). CHECK 78 read `_most_recent_trading_day(today-1)`,
copied from CHECK 77 whose producer runs the NEXT morning, while collect_pcr
runs 16:30 ET on the session itself. The window was exactly three sessions and
still named a session four back, because the anchor ended one session early.
The fix declared the convention — an anchor is justified by when the check's
own producer becomes due — and nothing tested the arithmetic.

`now_et` is injectable precisely so these claims are checkable without waiting
for a clock. It was added after a comment asserting "same behaviour as before"
turned out to be false at times outside the audit slot.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from audit._audit_core import last_due_session

ET = ZoneInfo("America/New_York")


def at(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


# 2026-09-21 Mon, 09-22 Tue, 09-23 Wed — all NYSE sessions.

def test_same_day_producer_counts_today_after_its_slot():
    """collect_pcr (16:30 ET, offset 0): today counts from 16:30, not before."""
    assert last_due_session("1630", 0, at(2026, 9, 23, 16, 34)) .isoformat() == "2026-09-23"
    assert last_due_session("1630", 0, at(2026, 9, 23, 16, 29)) .isoformat() == "2026-09-22"


def test_next_morning_producer_lags_by_one_session():
    """eod_regime (08:30 ET next session, offset 1): a session is due the morning after."""
    assert last_due_session("0830", 1, at(2026, 9, 23, 16, 34)).isoformat() == "2026-09-22"
    # Before 08:30 the previous session is not due yet either.
    assert last_due_session("0830", 1, at(2026, 9, 23, 1, 31)).isoformat() == "2026-09-21"


def test_the_two_conventions_disagree_and_that_is_the_point():
    """
    The bug was 78 using 77's anchor. At the audit slot they differ by exactly
    one session — which is the gap that let a same-day collection failure go
    unreported on the night it happened.
    """
    now = at(2026, 9, 23, 16, 34)
    assert last_due_session("1630", 0, now) != last_due_session("0830", 1, now)


def test_weekend_rolls_back_to_the_last_session():
    """Saturday: the most recent due session is Friday, under either convention."""
    sat = at(2026, 9, 26, 12, 0)   # 09-25 is a Friday session
    assert last_due_session("1630", 0, sat).isoformat() == "2026-09-25"


def test_offset_one_is_the_previous_session_at_the_audit_slot():
    """
    CHECK 78's completion gate drops back a session by asking for offset 1 when
    the collector has not reported finishing. At/after the producer's own slot
    that is exactly one session back — the property the gate relies on.
    """
    now = at(2026, 9, 23, 16, 34)
    assert last_due_session("1630", 0, now).isoformat() == "2026-09-23"
    assert last_due_session("1630", 1, now).isoformat() == "2026-09-22"
