"""Job registration facts that decisions depend on, read from the scheduler itself (not the source)."""
import backend.scheduler as sch


def _registered(monkeypatch):
    """Run start_scheduler with the scheduler's start() and every catch-up stubbed, return its jobs."""
    # The eod_regime catch-up stays real (it is the registered job); its worker is stubbed.
    monkeypatch.setattr(sch, "run_eod_regime", lambda as_of=None: None)
    for name in ("_check_missed_calibration", "_check_missed_weekly_report",
                 "_check_missed_sentiment_prefetch", "_check_missed_live_exits",
                 "_check_missed_pcr_collect", "_check_missed_audit_publish"):
        monkeypatch.setattr(sch, name, lambda: None)
    monkeypatch.setattr(sch.scheduler, "start", lambda *a, **k: None)
    sch.start_scheduler()
    try:
        return {j.id: j for j in sch.scheduler.get_jobs()}
    finally:
        sch.scheduler.remove_all_jobs()


def test_eod_regime_runs_pre_open_for_the_previous_session(monkeypatch):
    """2026-09-22: moved from 16:15 ET same day to 08:30 ET, via the due-runner so it is
    idempotent with the startup catch-up (a missed morning is filled at window start)."""
    jobs = _registered(monkeypatch)
    trig = str(jobs["eod_regime"].trigger)
    assert "hour='8'" in trig and "minute='30'" in trig and "day_of_week='mon-fri'" in trig
    assert jobs["eod_regime"].func.__name__ == "_check_missed_eod_regime"


def test_evening_window_jobs_unchanged(monkeypatch):
    jobs = _registered(monkeypatch)
    assert "hour='16'" in str(jobs["collect_pcr"].trigger) and "minute='30'" in str(jobs["collect_pcr"].trigger)
    assert "hour='16'" in str(jobs["publish_audit_state"].trigger) and "minute='33'" in str(jobs["publish_audit_state"].trigger)
