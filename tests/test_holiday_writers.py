"""
Writers that stamp market data with today's date must not run on a weekday
NYSE holiday (2026-09-26). The startup poll (force=True) and the 16:30
collect_pcr job had no calendar check; market_window.sh keeps the backend up
to 16:40 ET on every weekday. The real NYSE calendar is used — not stubbed —
because the holiday list is the thing under test.
"""
from datetime import datetime, time

import pytest

import backend.scheduler as sch
from backend.scheduler import NY

THANKSGIVING = "2026-11-26"
DAY_BEFORE   = "2026-11-25"
HALF_DAY     = "2026-11-27"      # early close 13:00 ET — still a session


def _freeze(monkeypatch, d: str, hhmm: str = "16:30"):
    fixed = datetime.combine(datetime.fromisoformat(d).date(), time.fromisoformat(hhmm), tzinfo=NY)

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz is None else fixed.astimezone(tz)
    monkeypatch.setattr(sch, "datetime", _DT)


@pytest.mark.parametrize("d, expected", [(THANKSGIVING, False), (DAY_BEFORE, True),
                                         (HALF_DAY, True), ("2026-09-07", False)])
def test_is_session_today_reads_the_exchange_calendar(monkeypatch, d, expected):
    _freeze(monkeypatch, d)
    assert sch.is_session_today("t") is expected


def test_calendar_failure_runs_anyway(monkeypatch):
    _freeze(monkeypatch, THANKSGIVING)
    def boom(_):
        raise RuntimeError("calendar down")
    monkeypatch.setattr(sch, "_nyse_sessions_for_date", boom)
    assert sch.is_session_today("t") is True


@pytest.mark.parametrize("d, collects", [(THANKSGIVING, False), (DAY_BEFORE, True)])
def test_registered_collect_pcr_job_skips_holiday(monkeypatch, d, collects):
    monkeypatch.setattr(sch, "run_eod_regime", lambda as_of=None: None)
    for name in ("_check_missed_calibration", "_check_missed_weekly_report",
                 "_check_missed_sentiment_prefetch", "_check_missed_live_exits",
                 "_check_missed_pcr_collect", "_check_missed_audit_publish"):
        monkeypatch.setattr(sch, name, lambda: None)
    monkeypatch.setattr(sch.scheduler, "start", lambda *a, **k: None)
    sch.start_scheduler()
    try:
        job = {j.id: j for j in sch.scheduler.get_jobs()}["collect_pcr"]
    finally:
        sch.scheduler.remove_all_jobs()
    calls = []
    monkeypatch.setattr(sch, "collect_pcr_snapshot", lambda today=None: calls.append(today))
    _freeze(monkeypatch, d)
    job.func()
    assert bool(calls) is collects


@pytest.mark.parametrize("d, polls", [(THANKSGIVING, False), (DAY_BEFORE, True)])
def test_startup_poll_skips_holiday(tmp_path, monkeypatch, d, polls):
    from fastapi.testclient import TestClient

    import backend.main as m
    monkeypatch.setattr(m, "SCHEDULER_LOCK", tmp_path / "scheduler.lock")
    monkeypatch.setenv("APEX_SERVE", "1")
    polled = []
    monkeypatch.setattr(m, "poll_all_sectors", lambda force=False: polled.append(force))
    monkeypatch.setattr(m, "start_scheduler", lambda: None)
    _freeze(monkeypatch, d, "08:20")
    with TestClient(m.app):
        pass
    assert bool(polled) is polls
