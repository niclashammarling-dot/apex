"""
get_latest_sector_scores — the "latest row per sector" read that feeds
compute_dynamic_caps (every gate cycle) and the EOD RegimeBayes update.

Found 2026-09-26: the query had no exclusion filter and no lower bound, so the
four EXCLUDED_SECTORS — no longer polled — were returned forever from their
last rows (2025-06-13, 2026-07-07) and pulled compute_dynamic_caps' mean.
"""
from backend.config import EXCLUDED_SECTORS
from backend.db import get_db, get_latest_sector_scores, insert_sector_snapshots


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
    scores = get_latest_sector_scores()
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
    assert get_latest_sector_scores() == {"Technology": 0.5}


def test_excluded_filter_applies_on_replay_path():
    _clear()
    fossil = next(iter(EXCLUDED_SECTORS))
    insert_sector_snapshots([
        _row("2026-07-07T16:34:25+00:00", fossil, 0.53),
        _row("2026-09-24T19:51:00+00:00", "Technology", 0.5),
    ])
    scores = get_latest_sector_scores(as_of="2026-09-24T20:15:00+00:00")
    assert scores == {"Technology": 0.5}
