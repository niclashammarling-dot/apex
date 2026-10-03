"""scripts/cycle_watch.py — off-process alert when a gate job stops starting in market hours."""
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import cycle_watch as cw

OPEN  = datetime(2026, 10, 2, 13, 30, tzinfo=timezone.utc)   # 09:30 ET
CLOSE = datetime(2026, 10, 2, 20, 0, tzinfo=timezone.utc)    # 16:00 ET
BOUNDS = (OPEN, CLOSE)


def _iso(dt):
    return dt.isoformat()


def test_fresh_stamps_are_ok():
    now = OPEN + timedelta(hours=2)
    rows = cw.evaluate(now, BOUNDS, {"run_gate": _iso(now - timedelta(minutes=12)),
                                     "run_live_gate": _iso(now - timedelta(minutes=3))})
    assert [r["stale"] for r in rows] == [False, False]


def test_10_02_outage_is_stale_on_both_sides():
    """Replay: last cycles 16:01 / 16:13 UTC (before the 16:09 UTC HUP), watch at 17:00 UTC."""
    now = datetime(2026, 10, 2, 17, 0, tzinfo=timezone.utc)
    rows = cw.evaluate(now, BOUNDS, {"run_gate": "2026-10-02T16:01:00+00:00",
                                     "run_live_gate": "2026-10-02T16:13:00+00:00"})
    assert [(r["side"], r["stale"]) for r in rows] == [("demo", True), ("live", True)]
    assert rows[0]["age_min"] == 59


def test_outside_window_and_holiday_evaluate_nothing():
    assert cw.evaluate(OPEN + timedelta(minutes=44), BOUNDS, {}) == []
    assert cw.evaluate(CLOSE + timedelta(minutes=1), BOUNDS, {}) == []
    assert cw.evaluate(OPEN + timedelta(hours=1), None, {}) == []


def test_yesterdays_stamp_counts_from_the_open():
    now = OPEN + timedelta(minutes=46)
    rows = cw.evaluate(now, BOUNDS, {"run_gate": "2026-10-01T19:41:00+00:00"})
    assert rows[0]["age_min"] == 46 and rows[0]["stale"]          # never started today
    rows = cw.evaluate(OPEN + timedelta(minutes=45), BOUNDS, {"run_gate": "2026-10-01T19:41:00+00:00"})
    assert not rows[0]["stale"]                                   # at the threshold, not past it


def test_read_stamps_without_table_is_empty(tmp_path):
    db = tmp_path / "apex.db"
    sqlite3.connect(db).close()
    assert cw.read_stamps(db) == {}


def test_process_state_free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    assert "free" in cw.process_state(free)


@pytest.fixture
def watch_repo(tmp_path, monkeypatch):
    """main() against a temp repo: stamp DB, fixed clock, captured alerts, real latch table."""
    from backend import alerts, db
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)          # main() chdirs to REPO; restore the cwd after the test
    sent = []
    monkeypatch.setattr(cw, "REPO", tmp_path)
    monkeypatch.setattr(cw, "process_state", lambda port=8000: "port 8000 free — no backend running")
    monkeypatch.setattr(cw, "session_bounds", lambda day: (BOUNDS, ""))
    monkeypatch.setattr(alerts, "_dispatch", lambda title, body: sent.append(title))
    db.init_db()

    def run(at, stamps):
        conn = sqlite3.connect(tmp_path / "data" / "apex.db")
        conn.execute("CREATE TABLE IF NOT EXISTS job_runs (job TEXT PRIMARY KEY, started_at TEXT NOT NULL, "
                     "finished_at TEXT, outcome TEXT)")
        conn.execute("DELETE FROM job_runs")
        conn.executemany("INSERT INTO job_runs (job, started_at) VALUES (?, ?)", stamps.items())
        conn.commit()
        conn.close()

        class _At(datetime):
            @classmethod
            def now(cls, tz=None):
                return at
        monkeypatch.setattr(cw, "datetime", _At)
        cw.main()
    return run, sent, tmp_path


def test_main_alerts_once_per_side_per_day_and_logs_each_run(watch_repo):
    """Positive control: stale stamps → one mail per side; a second run re-logs but does not re-mail."""
    run, sent, repo = watch_repo
    stale = {"run_gate": "2026-10-02T16:01:00+00:00", "run_live_gate": "2026-10-02T16:13:00+00:00"}
    run(datetime(2026, 10, 2, 17, 0, tzinfo=timezone.utc), stale)
    run(datetime(2026, 10, 2, 17, 15, tzinfo=timezone.utc), stale)
    assert [t.split("] ", 1)[1] for t in sent] == ["No demo gate cycle for 59 min", "No live gate cycle for 47 min"]
    lines = (repo / "logs" / "cycle_watch_2026-10-02.log").read_text().splitlines()
    assert len(lines) == 2 and "alert sent: demo, live" in lines[0] and "alert sent" not in lines[1]
    assert "STALE" in lines[1] and "port 8000 free" in lines[1]


def test_main_fresh_is_silent_but_logged(watch_repo):
    run, sent, repo = watch_repo
    at = datetime(2026, 10, 2, 17, 0, tzinfo=timezone.utc)
    run(at, {"run_gate": _iso(at - timedelta(minutes=5)), "run_live_gate": _iso(at - timedelta(minutes=15))})
    assert sent == []
    assert "STALE" not in (repo / "logs" / "cycle_watch_2026-10-02.log").read_text()


# ── the producer: backend/scheduler.py stamps every scheduled gate run ──────────

def _job_row(job):
    from backend.db import get_db
    with get_db() as conn:
        return conn.execute("SELECT started_at, finished_at, outcome FROM job_runs WHERE job = ?",
                            (job,)).fetchone()


def test_stamped_records_start_end_and_ok():
    from backend import db, scheduler
    db.init_db()
    seen = []
    scheduler._stamped("t_ok", lambda: seen.append(_job_row("t_ok")))
    assert seen[0][0] and seen[0][1] is None           # started, not finished, while running
    started, finished, outcome = _job_row("t_ok")
    assert outcome == "ok", (started, finished, outcome)
    assert datetime.fromisoformat(finished) >= datetime.fromisoformat(started), (started, finished)


def test_stamped_records_error_and_reraises():
    from backend import db, scheduler
    db.init_db()

    def boom():
        raise RuntimeError("alpaca down")
    with pytest.raises(RuntimeError):
        scheduler._stamped("t_err", boom)
    assert _job_row("t_err")[2].startswith("error: RuntimeError('alpaca down'")


def test_stamp_failure_never_stops_the_job(monkeypatch):
    from backend import db, scheduler

    def broken(*a, **k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(db, "stamp_job_run", broken)
    ran = []
    scheduler._stamped("t_locked", lambda: ran.append(1))
    assert ran == [1]


def test_both_gate_jobs_are_stamped(monkeypatch):
    """The two scheduled jobs the watch reads go through _stamped under their watch names."""
    from backend import scheduler
    from backend.gate import gate_runner, gate_runner_live
    monkeypatch.setattr(scheduler, "is_market_open", lambda: True)
    calls = []
    monkeypatch.setattr(scheduler, "_stamped", lambda job, fn: calls.append((job, fn)))
    scheduler.run_gate_candidates()
    scheduler.run_live_gate_candidates()
    assert calls == [("run_gate", gate_runner.run), ("run_live_gate", gate_runner_live.run)]
    assert set(cw.JOBS) == {job for job, _ in calls}


# ── the surface: /api/ops/window reads the watch's own log ──────────────────────

def test_ops_window_surfaces_the_watch_log(tmp_path, monkeypatch):
    import backend.routers.signals_router as sr
    monkeypatch.setattr(sr, "_LOG_DIR", tmp_path)
    d = sr.get_market_window(days=10)["sessions"][-1]["date"]
    assert sr.get_market_window(days=10)["sessions"][-1]["cycle_watch"] is None   # no log: "—"
    (tmp_path / f"cycle_watch_{d}.log").write_text(
        f"{d} 16:15:01 CEST | demo last 16:01 CEST (14 min) ok · live last 16:13 CEST (2 min) ok · /health answers\n"
        f"{d} 19:00:02 CEST | demo last 18:01 CEST (59 min) STALE · live last 18:13 CEST (47 min) STALE · "
        f"port 8000 free — no backend running · alert sent: demo, live\n")
    w = next(s for s in sr.get_market_window(days=10)["sessions"] if s["date"] == d)["cycle_watch"]
    assert (w["runs"], w["stale_runs"], w["alerted"], w["last_at"]) == (2, 1, True, "19:00")
    assert w["last"].startswith("demo last 18:01")


# ── session flags (2026-10-03): written by the watch, outside the backend ───────

def test_stale_run_flags_session_provisionally_once(watch_repo, monkeypatch):
    from backend.db import get_session_flags
    run, sent, repo = watch_repo
    # its own date: the suite's DB is shared, and 10-02 is flagged by the alert test above
    monkeypatch.setattr(cw, "session_bounds", lambda day: ((datetime(2026, 9, 29, 13, 30, tzinfo=timezone.utc),
                                                             datetime(2026, 9, 29, 20, 0, tzinfo=timezone.utc)), ""))
    stale = {"run_gate": "2026-09-29T16:01:00+00:00", "run_live_gate": "2026-09-29T16:13:00+00:00"}
    run(datetime(2026, 9, 29, 17, 0, tzinfo=timezone.utc), stale)
    run(datetime(2026, 9, 29, 17, 15, tzinfo=timezone.utc), stale)
    flags = [f for f in get_session_flags("2026-09-29", "2026-09-30")]
    assert len(flags) == 1 and flags[0]["status"] == "provisional" and flags[0]["source"] == "cycle_watch"
    assert "no demo/live gate start for 59 min" in flags[0]["cause"]
    lines = (repo / "logs" / "cycle_watch_2026-09-29.log").read_text().splitlines()
    assert "session flagged partial (provisional)" in lines[0] and "flagged" not in lines[1]


def test_post_close_run_finalizes_the_flag(watch_repo, monkeypatch):
    from backend.db import flag_session, get_session_flags
    run, sent, repo = watch_repo
    flag_session("2026-10-01", "partial", "test outage", "cycle_watch")
    monkeypatch.setattr(cw, "session_bounds", lambda day: ((datetime(2026, 10, 1, 13, 30, tzinfo=timezone.utc),
                                                             datetime(2026, 10, 1, 20, 0, tzinfo=timezone.utc)), ""))
    monkeypatch.setattr(cw, "session_cycles", lambda db, day, bounds: (8, 19, 8, 20))
    run(datetime(2026, 10, 1, 20, 15, tzinfo=timezone.utc), {})
    f = get_session_flags("2026-10-01", "2026-10-02")[0]
    assert (f["status"], f["cycles"], f["expected"], f["live_cycles"], f["live_expected"]) == ("final", 8, 19, 8, 20)
    assert "finalized: demo 8/19, live 8/20" in (repo / "logs" / "cycle_watch_2026-10-01.log").read_text()


def test_post_close_run_without_flag_writes_nothing(watch_repo, monkeypatch):
    run, sent, repo = watch_repo
    monkeypatch.setattr(cw, "session_bounds", lambda day: ((datetime(2026, 9, 30, 13, 30, tzinfo=timezone.utc),
                                                             datetime(2026, 9, 30, 20, 0, tzinfo=timezone.utc)), ""))
    run(datetime(2026, 9, 30, 20, 15, tzinfo=timezone.utc), {})
    assert not (repo / "logs" / "cycle_watch_2026-09-30.log").exists()


def test_weekly_report_leads_with_partial_session(monkeypatch, tmp_path):
    import backend.weekly_report as wr
    from backend.db import finalize_session_flag, flag_session
    day = wr._week_start_iso()[:10]
    flag_session(day, "partial", "console close, no relaunch", "cycle_watch")
    finalize_session_flag(day, "partial", 8, 19, 8, 20)
    monkeypatch.setattr(wr, "_SWEEP_PATH", tmp_path / "none.json")
    monkeypatch.setattr(wr, "_OPT_PATH", tmp_path / "none.json")
    monkeypatch.setattr(wr, "_gpt4o_commentary", lambda *a, **k: None)
    monkeypatch.setattr(wr, "_fetch_prices", lambda tickers: {})
    _, html, plain = wr.build_report()
    for body in (html, plain):
        assert f"PARTIAL SESSION {day} (demo 8/19, live 8/20 cycles) — console close, no relaunch" in body
    assert plain.index("PARTIAL SESSION") < plain.index("Demo:")


def test_ops_window_carries_the_flag(tmp_path, monkeypatch):
    import backend.routers.signals_router as sr
    from backend.db import flag_session
    monkeypatch.setattr(sr, "_LOG_DIR", tmp_path)
    from backend.db import get_db
    d = sr.get_market_window(days=10)["sessions"][0]["date"]     # oldest row: no other test flags it
    with get_db() as conn:
        conn.execute("DELETE FROM session_flags WHERE date = ?", (d,))
        conn.commit()
    assert next(x for x in sr.get_market_window(days=10)["sessions"] if x["date"] == d)["partial"] is None
    flag_session(d, "partial", "test", "cycle_watch")
    s = next(x for x in sr.get_market_window(days=10)["sessions"] if x["date"] == d)
    assert s["partial"]["status"] == "provisional" and s["partial"]["cause"] == "test"
