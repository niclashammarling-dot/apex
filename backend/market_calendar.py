"""NYSE sessions for hold counts (2026-11-14, codebase audit finding 7, R013).

The live and demo time stops counted weekdays (`pd.bdate_range`), so a holiday inside a
hold counted as a session and the stop fired a session early; Thanksgiving 2026-11-26 is
the next case. One count for both books, on the NYSE calendar the scheduler uses, in New
York dates. A half day (2026-11-27, close 13:00 ET) is one session. The regime exits'
staleness guards (live and demo) count the same way: a result from the last session before
a holiday is one session old, not two.
"""
from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

from loguru import logger

NY = ZoneInfo("America/New_York")


def _today_ny() -> date:
    return datetime.now(NY).date()


@lru_cache(maxsize=256)
def sessions_after(start: date, through: date) -> int:
    """NYSE sessions strictly after `start`, up to and including `through`.

    If the calendar cannot be read, weekdays are counted instead (holidays not skipped)
    and a WARNING says so, once per date pair (the result is cached)."""
    if through <= start:
        return 0
    try:
        import pandas_market_calendars as mcal
        sched = mcal.get_calendar("NYSE").schedule(start_date=start.isoformat(), end_date=through.isoformat())
        return sum(1 for ts in sched.index if ts.date() > start)
    except Exception as e:
        import pandas as pd
        logger.warning(f"NYSE calendar unavailable ({e}): hold count {start}..{through} counts weekdays, "
                       f"holidays not skipped")
        return max(0, len(pd.bdate_range(start, through)) - 1)


def sessions_since(d: date) -> int:
    """NYSE sessions after date `d`, up to and including today's New York date."""
    return sessions_after(d, _today_ny())


def trading_days_since(iso_timestamp: str) -> int:
    """NYSE sessions held since an entry timestamp: sessions after the entry's New York
    date, up to and including today's New York date."""
    ts = datetime.fromisoformat(iso_timestamp)
    entry = ts.astimezone(NY).date() if ts.tzinfo else ts.date()
    return sessions_after(entry, _today_ny())
