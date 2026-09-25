"""
get_latest_sector_scores — the "latest row per sector" read that feeds
compute_dynamic_caps (every gate cycle) and the EOD RegimeBayes update.

Found 2026-09-26: the query had no exclusion filter and no lower bound, so the
four EXCLUDED_SECTORS — no longer polled — were returned forever from their
last rows (2025-06-13, 2026-07-07) and pulled compute_dynamic_caps' mean.
"""
from datetime import datetime
from unittest.mock import patch

from loguru import logger

from backend.config import EXCLUDED_SECTORS
from backend.db import (
    _sector_score_floor,
    get_db,
    get_latest_sector_scores,
    get_sector_score,
    insert_sector_snapshots,
)


# Fri 2026-09-25 20:00 ET — after that session's last poll.
AS_OF = "2026-09-26T00:00:00+00:00"


def _row(ts: str, sector: str, score: float) -> dict:
    return {"timestamp": ts, "sector": sector, "avg_score": score,
            "top_ticker": None, "top_score": None, "ticker_count": 1}


def _clear():
    conn = get_db()
    try:
        conn.execute("DELETE FROM sector_snapshots")
        conn.commit()
    finally:
        conn.close()


def test_excluded_sector_fossil_not_returned():
    _clear()
    fossil = next(iter(EXCLUDED_SECTORS))
    insert_sector_snapshots([
        _row("2025-06-13T16:00:00", fossil, 0.9),
        _row("2026-09-25T19:51:26+00:00", "Technology", 0.5),
    ])
    scores = get_latest_sector_scores(as_of=AS_OF)
    assert "Technology" in scores          # positive control: the read works
    assert fossil not in scores


def test_excluded_sector_not_returned_even_when_fresh():
    """The filter is by membership, not by age — an excluded sector's row
    written today (e.g. a manual poll) still must not reach the caps mean."""
    _clear()
    fossil = next(iter(EXCLUDED_SECTORS))
    insert_sector_snapshots([
        _row("2026-09-25T19:51:26+00:00", fossil, 0.9),
        _row("2026-09-25T19:51:26+00:00", "Technology", 0.5),
    ])
    assert get_latest_sector_scores(as_of=AS_OF) == {"Technology": 0.5}


def test_excluded_filter_applies_on_replay_path():
    _clear()
    fossil = next(iter(EXCLUDED_SECTORS))
    insert_sector_snapshots([
        _row("2026-07-07T16:34:25+00:00", fossil, 0.53),
        _row("2026-09-24T19:51:00+00:00", "Technology", 0.5),
    ])
    scores = get_latest_sector_scores(as_of="2026-09-24T20:15:00+00:00")
    assert scores == {"Technology": 0.5}


# ── Staleness bound (SECTOR_SCORE_MAX_SESSIONS = 2) ─────────────────────────
# Pinned definition: the bound is the open of the 2nd most recent NYSE session
# whose OPEN is at or before as_of. Before 09:30 ET the day's own session has
# not begun and does not count — so the window is two begun sessions whatever
# the time of day, not "today plus yesterday" by date.

def _dt(s):
    return datetime.fromisoformat(s)


def test_floor_pre_open_counts_begun_sessions_only():
    # Mon 2026-09-28 08:30 ET: Monday not begun → Fri 09-25, Thu 09-24.
    assert _sector_score_floor(_dt("2026-09-28T12:30:00+00:00")) == "2026-09-24T13:30:00+00:00"


def test_floor_after_open_includes_today():
    # Mon 2026-09-28 10:00 ET: Mon 09-28, Fri 09-25.
    assert _sector_score_floor(_dt("2026-09-28T14:00:00+00:00")) == "2026-09-25T13:30:00+00:00"


def test_floor_walks_past_weekday_holiday():
    # Tue 2026-09-08 10:00 ET: Tue 09-08, then Mon 09-07 is Labor Day → Fri 09-04.
    assert _sector_score_floor(_dt("2026-09-08T14:00:00+00:00")) == "2026-09-04T13:30:00+00:00"


def test_floor_calendar_failure_falls_back_to_weekdays_and_says_so():
    msgs = []
    sink = logger.add(lambda m: msgs.append(str(m)), level="WARNING")
    try:
        with patch("pandas_market_calendars.get_calendar", side_effect=RuntimeError("boom")):
            floor = _sector_score_floor(_dt("2026-09-08T14:00:00+00:00"))
    finally:
        logger.remove(sink)
    assert floor == "2026-09-07T13:30:00+00:00"      # holiday counted: the labelled cost
    assert any("weekday fallback" in m for m in msgs)


def test_pre_first_poll_read_sees_previous_session():
    _clear()
    insert_sector_snapshots([
        _row("2026-09-25T19:51:26+00:00", "Technology", 0.5),   # Fri last poll
        _row("2026-09-23T19:51:26+00:00", "Energy", 0.3),       # Wed — outside
    ])
    scores = get_latest_sector_scores(as_of="2026-09-28T12:30:00+00:00")
    assert scores == {"Technology": 0.5}


def test_stale_live_sector_dropped_and_logged():
    _clear()
    insert_sector_snapshots([
        _row("2026-09-25T19:51:26+00:00", "Technology", 0.5),
        _row("2026-09-22T19:51:26+00:00", "Energy", 0.3),       # Tue — two sessions stale
    ])
    msgs = []
    sink = logger.add(lambda m: msgs.append(str(m)), level="WARNING")
    try:
        with patch("backend.ticker_config.get_sectors",
                   return_value={"Technology": {}, "Energy": {}}):
            scores = get_latest_sector_scores(as_of=AS_OF)
    finally:
        logger.remove(sink)
    assert scores == {"Technology": 0.5}
    assert any("Energy" in m and "omitted" in m for m in msgs)


def test_duplicate_timestamp_resolves_to_highest_id():
    _clear()
    ts = "2026-09-25T19:51:26+00:00"
    insert_sector_snapshots([_row(ts, "Technology", 0.1)])
    insert_sector_snapshots([_row(ts, "Technology", 0.9)])
    insert_sector_snapshots([_row(ts, "Technology", 0.5)])
    assert get_latest_sector_scores(as_of=AS_OF) == {"Technology": 0.5}


def test_get_sector_score_applies_the_bound():
    _clear()
    insert_sector_snapshots([_row("2026-07-01T19:51:26+00:00", "Technology", 0.5)])
    assert get_sector_score("Technology", as_of=AS_OF) is None
    insert_sector_snapshots([_row("2026-09-25T19:51:26+00:00", "Technology", 0.6)])
    assert get_sector_score("Technology", as_of=AS_OF) == 0.6
