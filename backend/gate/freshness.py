"""
Session-anchored freshness for gate candidates (Finding 1, 2026-10-10).

Found 2026-10-07: get_lock1_candidates took the newest signal per ticker with no
bound, so 25 of 55 live entries since 07-07 were decided on a signal not written
in that session: 15 on the same day's pre-open poll (the 14:20 CEST startup
poll, read by the first live cycle at start + 10.5 min, before the first
in-session poll), 10 on a signal from an earlier day. Earlier-day entries won
3 of 10 against 20 of 26 in session (n small, no significance claim).

Enforced: timestamp >= today's session open. Written after the open; a poll
five minutes before the open passes any age limit and still holds the prior
close. All 25 measured rows fail this test.

Observed, not enforced (Niclas 2026-10-10, reversing the 10-07 "bound is
bar_date == today's session" until tests decide): bar_date == today's NYSE
session date, i.e. the newest daily bar the fetcher saw is this session's.
It would also catch a post-open poll still holding yesterday's bar (Yahoo
lag, halted or frozen ticker), which write time cannot. That case is
unmeasured, and enforcing it blind risks a no-trade day if yfinance omits the
forming bar in session. Every gate row stores bar_date. CHECK 87 and
/api/ops/freshness count in-session mismatches per sector and book, and
which of them were entered. Review 2026-10-17: 0 mismatches means the
property is never violated, so drop it; >0 means compare the mismatched
entries' outcomes, per sector, then decide. ENFORCE_BAR_DATE switches it on.

This module is the single definition, read by get_lock1_candidates (both
runners, main and pre-rotation candidates), by CHECK 87 and by
scripts/finding1_snapshot_test.py.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")

ENFORCE_BAR_DATE  = False

STALE_BAR_DATE    = "stale_bar_date"
BEFORE_OPEN       = "written_before_open"
NO_SESSION        = "no_session"
NOT_IN_UNIVERSE   = "not_in_universe"


def session_for(now: datetime | None = None) -> tuple[str, datetime] | None:
    """(session date YYYY-MM-DD, open as UTC datetime) for the ET date of now,
    or None on a non-session day."""
    from backend.scheduler import _nyse_session_bounds
    now = now or datetime.now(timezone.utc)
    d = now.astimezone(NY).date().isoformat()
    bounds = _nyse_session_bounds(d)
    if bounds is None:
        return None
    return d, bounds[0].astimezone(timezone.utc)


def bar_date_mismatch(bar_date: str | None, session_date: str) -> bool:
    """The observed half: the signal's newest bar is not this session's."""
    return bar_date != session_date


def stale_reason(signal_ts: str | None, bar_date: str | None,
                 session_date: str, open_utc: datetime,
                 enforce_bar_date: bool | None = None) -> str | None:
    """None if the signal is a valid input for session_date, else why not.
    Write time is enforced; bar_date only when ENFORCE_BAR_DATE (or the argument) is set."""
    if not signal_ts or datetime.fromisoformat(signal_ts) < open_utc:
        return BEFORE_OPEN
    enforce = ENFORCE_BAR_DATE if enforce_bar_date is None else enforce_bar_date
    if enforce and bar_date_mismatch(bar_date, session_date):
        return STALE_BAR_DATE
    return None


IN_SESSION        = "in_session"
SAME_DAY_PRE_OPEN = "same_day_pre_open"
EARLIER_DAY       = "earlier_day"


def signal_class(signal_ts: str | None, entry_ts: str) -> str:
    """Which input an entry was decided on (live_trades.signal_class): in_session,
    same_day_pre_open or earlier_day. Read by the live stats, which separate
    in-session entries from the rest."""
    if not signal_ts:
        return "no_signal"
    if not written_before_open(signal_ts, entry_ts):
        return IN_SESSION
    same_day = (datetime.fromisoformat(signal_ts).astimezone(NY).date()
                == datetime.fromisoformat(entry_ts).astimezone(NY).date())
    return SAME_DAY_PRE_OPEN if same_day else EARLIER_DAY


def written_before_open(signal_ts: str, entry_ts: str) -> bool:
    """Write-time half only, for rows that carry no bar_date (history before
    2026-10-10): was the signal written before the open of the session the
    entry was made in? Used by the snapshot test and the live_trades flagging."""
    from backend.scheduler import _nyse_session_bounds
    entry_day = datetime.fromisoformat(entry_ts).astimezone(NY).date().isoformat()
    bounds = _nyse_session_bounds(entry_day)
    if bounds is None:
        return True
    return datetime.fromisoformat(signal_ts) < bounds[0].astimezone(timezone.utc)
