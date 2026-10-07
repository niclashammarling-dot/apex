"""
2026-10-07: the /api/sectors freeze. Two causes that compounded:
- get_prev_ticker_prices / prev_signals_by_ticker ran a correlated
  ORDER BY ... OFFSET 1 subquery per row (43–49 s on 48,880 rows); rewritten
  as window functions with a defined tie-break.
- prune_signals stopped running on 2026-09-16 (02:00 ET slot outside the
  market window); it now has a startup catch-up, stamped in job_runs and read
  by CHECK 86.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import backend.db as db
import backend.scheduler as sched
from backend.db import get_db, get_prev_ticker_prices, init_db, prev_signals_by_ticker

init_db()


def _signal(ticker, ts, price, score=0.5, gate=None):
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO signals (timestamp, ticker, sector, price, signal_score, gate_decision) "
            "VALUES (?, ?, 'Technology', ?, ?, ?)", (ts, ticker, price, score, gate))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


class TestPrevQueries:
    def test_second_most_recent_price(self):
        _signal("PQA", "2031-01-05T15:00:00+00:00", 10.0)
        _signal("PQA", "2031-01-05T15:20:00+00:00", 11.0)
        _signal("PQA", "2031-01-05T15:40:00+00:00", 12.0)
        _signal("PQA", "2031-01-05T16:00:00+00:00", None)   # unpriced rows don't count
        assert get_prev_ticker_prices()["PQA"] == 11.0

    def test_duplicate_timestamp_resolves_to_the_higher_id(self):
        """Rows sharing the latest timestamp: row order is (timestamp DESC, id DESC),
        so the second row is the lower-id duplicate — one defined answer."""
        _signal("PQB", "2031-01-05T15:00:00+00:00", 20.0)
        _signal("PQB", "2031-01-05T15:20:00+00:00", 21.0)   # lower id
        _signal("PQB", "2031-01-05T15:20:00+00:00", 22.0)   # higher id: newest
        assert get_prev_ticker_prices()["PQB"] == 21.0

    def test_prev_poll_is_the_second_distinct_timestamp(self):
        _signal("PQC", "2031-01-05T15:00:00+00:00", 1.0, score=0.40)
        _signal("PQC", "2031-01-05T15:20:00+00:00", 1.0, score=0.50)
        _signal("PQC", "2031-01-05T15:40:00+00:00", 1.0, score=0.60)
        _signal("PQC", "2031-01-05T15:40:00+00:00", 1.0, score=0.61)   # same poll as above
        assert prev_signals_by_ticker()["PQC"] == 0.50

    def test_duplicate_in_the_previous_poll_takes_the_higher_id(self):
        _signal("PQD", "2031-01-05T15:00:00+00:00", 1.0, score=0.30)   # lower id
        _signal("PQD", "2031-01-05T15:00:00+00:00", 1.0, score=0.35)   # higher id
        _signal("PQD", "2031-01-05T15:20:00+00:00", 1.0, score=0.70)
        assert prev_signals_by_ticker()["PQD"] == 0.35

    def test_dead_quadratic_reader_is_gone(self):
        assert not hasattr(db, "prev_signals_avg_by_sector")


def _set_prune_run(finished_at, outcome="ok"):
    conn = get_db()
    try:
        conn.execute("DELETE FROM job_runs WHERE job = 'prune_signals'")
        if finished_at is not None:
            conn.execute("INSERT INTO job_runs (job, started_at, finished_at, outcome) VALUES "
                         "('prune_signals', ?, ?, ?)", (finished_at, finished_at, outcome))
        conn.commit()
    finally:
        conn.close()


class TestPruneCatchup:
    def test_never_recorded_runs_and_stamps(self, monkeypatch):
        calls = []
        monkeypatch.setattr(sched, "prune_signals", lambda keep_per_ticker: calls.append(keep_per_ticker) or 0)
        monkeypatch.setattr(sched, "prune_sector_snapshots", lambda keep_days: 0)
        _set_prune_run(None)
        sched._check_missed_prune()
        assert calls == [10]
        row = next(r for r in db.get_job_runs() if r["job"] == "prune_signals")
        assert row["outcome"] == "ok" and row["finished_at"]

    def test_recent_prune_is_not_repeated(self, monkeypatch):
        calls = []
        monkeypatch.setattr(sched, "prune_signals", lambda keep_per_ticker: calls.append(1) or 0)
        _set_prune_run((datetime.now(timezone.utc) - timedelta(hours=2)).isoformat())
        sched._check_missed_prune()
        assert calls == []

    def test_old_or_failed_prune_runs_again(self, monkeypatch):
        calls = []
        monkeypatch.setattr(sched, "prune_signals", lambda keep_per_ticker: calls.append(1) or 0)
        monkeypatch.setattr(sched, "prune_sector_snapshots", lambda keep_days: 0)
        _set_prune_run((datetime.now(timezone.utc) - timedelta(hours=30)).isoformat())
        sched._check_missed_prune()
        _set_prune_run((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), outcome="error: x")
        sched._check_missed_prune()
        assert calls == [1, 1]

    def test_prune_keeps_gated_rows_and_newest_ungated(self):
        for i in range(14):
            _signal("PRN", f"2031-02-01T15:{i:02d}:00+00:00", 1.0)
        _signal("PRN", "2031-01-01T15:00:00+00:00", 1.0, gate="FILTERED_L2")   # oldest, but gated
        db.prune_signals(keep_per_ticker=10)
        conn = get_db()
        try:
            n_ungated = conn.execute("SELECT COUNT(*) FROM signals WHERE ticker='PRN' AND gate_decision IS NULL").fetchone()[0]
            n_gated = conn.execute("SELECT COUNT(*) FROM signals WHERE ticker='PRN' AND gate_decision IS NOT NULL").fetchone()[0]
        finally:
            conn.close()
        assert (n_ungated, n_gated) == (10, 1)


class TestCheck86:
    @pytest.fixture
    def run86(self, tmp_path, monkeypatch):
        import audit.checks_gate as cg
        dbf = tmp_path / "data" / "apex.db"
        dbf.parent.mkdir()
        conn = sqlite3.connect(dbf)
        conn.execute("CREATE TABLE job_runs (job TEXT PRIMARY KEY, started_at TEXT NOT NULL, "
                     "finished_at TEXT, outcome TEXT)")
        conn.commit()
        conn.close()
        monkeypatch.setattr(cg, "REPO", tmp_path)
        monkeypatch.setattr(cg, "_c82_session_dates", lambda n: ["2031-03-06", "2031-03-07"])
        found = []
        monkeypatch.setattr(cg, "flag", lambda num, name, sev, where, msg: found.append((num, sev, msg)))

        def run(row=None):
            c = sqlite3.connect(dbf)
            c.execute("DELETE FROM job_runs")
            if row:
                c.execute("INSERT INTO job_runs VALUES ('prune_signals', ?, ?, ?)", row)
            c.commit()
            c.close()
            found.clear()
            cg.check86()
            return list(found)
        return run

    def test_never_run_is_a_warning(self, run86):
        assert "has not run" in run86()[0][2]

    def test_failed_prune_is_a_warning(self, run86):
        f = run86(("2031-03-07T12:00:00+00:00", "2031-03-07T12:00:30+00:00", "error: locked"))
        assert "error: locked" in f[0][2]

    def test_the_1007_shape_last_prune_weeks_ago(self, run86):
        f = run86(("2031-02-14T12:00:00+00:00", "2031-02-14T12:00:30+00:00", "ok"))
        assert f and f[0][1] == "WARNING" and "2031-02-14" in f[0][2]

    def test_prune_at_previous_session_is_clean(self, run86):
        assert run86(("2031-03-06T12:20:00+00:00", "2031-03-06T12:20:30+00:00", "ok")) == []


def test_ops_jobs_marks_an_old_prune_stale():
    from backend.routers.signals_router import get_job_runs_status
    _set_prune_run((datetime.now(timezone.utc) - timedelta(hours=40)).isoformat())
    row = next(j for j in get_job_runs_status()["jobs"] if j["job"] == "prune_signals")
    assert row["stale"] and row["age_h"] >= 39
    _set_prune_run((datetime.now(timezone.utc) - timedelta(hours=3)).isoformat())
    row = next(j for j in get_job_runs_status()["jobs"] if j["job"] == "prune_signals")
    assert not row["stale"]
