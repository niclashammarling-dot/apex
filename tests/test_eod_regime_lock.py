"""Two concurrent EOD callers run the session once (2026-09-30).

The startup catch-up runs concurrently with the scheduled jobs; a launch still
catching up at 08:30 ET would have both call run_eod_regime for the same session
on one RegimeBayes singleton. In-process only — a second process is the flock's job.
"""
import threading
import time
from datetime import date

import backend.db as db
import backend.scheduler as sch

D_PREV, D = date(2026, 9, 24), date(2026, 9, 25)


def _seed_prev():
    conn = db.get_db()
    try:
        conn.execute("DELETE FROM sector_posterior_history WHERE date >= ?", (D_PREV.isoformat(),))
        conn.execute("INSERT INTO sector_posterior_history (date, sector, posterior, written_at) "
                     "VALUES (?, 'Technology', 0.5, '2026-09-25T12:30:00+00:00')", (D_PREV.isoformat(),))
        conn.commit()
    finally:
        conn.close()


def _slow_run(calls):
    """Stands in for the computation: slow (the real one fetches ~1.5 min), then persists D."""
    def run(as_of=None, *, now_ny=None):
        calls.append(as_of)
        time.sleep(0.3)
        conn = db.get_db()
        try:
            conn.execute("INSERT INTO sector_posterior_history (date, sector, posterior, written_at) "
                         "VALUES (?, 'Technology', 0.6, '2026-09-28T12:30:00+00:00')", (as_of.isoformat(),))
            conn.commit()
        finally:
            conn.close()
        return "ok"
    return run


def test_concurrent_catchup_and_cron_run_the_session_once(monkeypatch):
    _seed_prev()
    calls = []
    monkeypatch.setattr(sch, "_last_eod_due", lambda now: D)
    monkeypatch.setattr(sch, "_run_eod_regime_unlocked", _slow_run(calls))
    threads = [threading.Thread(target=sch._check_missed_eod_regime) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert calls == [D]                         # the waiting caller re-read and found D written


def test_direct_callers_are_serialised(monkeypatch):
    """The manual endpoint and replay go through run_eod_regime too."""
    inside, overlap = [0], [False]

    def run(as_of=None, *, now_ny=None):
        inside[0] += 1
        overlap[0] |= inside[0] > 1
        time.sleep(0.2)
        inside[0] -= 1
        return "ok"
    monkeypatch.setattr(sch, "_run_eod_regime_unlocked", run)
    threads = [threading.Thread(target=sch.run_eod_regime, kwargs={"as_of": D}) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert overlap[0] is False
