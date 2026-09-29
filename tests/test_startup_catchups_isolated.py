"""A raising startup catch-up must not fail start_scheduler() (2026-09-29).

Before: the catch-ups ran bare in start_scheduler(), which lifespan calls bare —
one raise failed startup and skipped every later catch-up.
"""
import pytest

import backend.alerts as alerts
import backend.db as db
import backend.scheduler as sch


@pytest.fixture(autouse=True)
def _fresh_latches():
    """The latch is persisted (that is the point); clear today's catch-up latches
    so each test starts from 'never alerted', whatever ran before it."""
    from datetime import datetime
    today = datetime.now(sch.NY).date().isoformat()
    for name in sch.STARTUP_CATCHUPS:
        db.clear_alert_latch(f"startup_catchup:{name}:{today}")
    yield


def _start(monkeypatch, raising: set[str]):
    """start_scheduler with scheduler.start stubbed and every catch-up replaced by a
    recorder; the ones named in `raising` raise. Returns (ran, alerts_sent)."""
    ran, sent = [], []
    for name in sch.STARTUP_CATCHUPS:
        def fn(name=name):
            ran.append(name)
            if name in raising:
                raise RuntimeError(f"boom in {name}")
        monkeypatch.setattr(sch, name, fn)
    monkeypatch.setattr(sch.scheduler, "start", lambda *a, **k: None)
    monkeypatch.setattr(alerts, "alert_startup_catchup_failed",
                        lambda name, err: sent.append((name, err)))
    try:
        sch.start_scheduler()
    finally:
        sch.scheduler.remove_all_jobs()
    return ran, sent


def test_every_catchup_listed_runs_in_order(monkeypatch):
    ran, sent = _start(monkeypatch, raising=set())
    assert ran == list(sch.STARTUP_CATCHUPS)
    assert sent == []


@pytest.mark.parametrize("bad", ["_check_missed_eod_regime", "_check_missed_live_exits",
                                 "_check_missed_weekly_report"])
def test_a_raise_alerts_and_the_rest_still_run(monkeypatch, bad):
    ran, sent = _start(monkeypatch, raising={bad})
    assert ran == list(sch.STARTUP_CATCHUPS)          # nothing after `bad` was skipped
    assert [n for n, _ in sent] == [bad]
    assert "boom" in sent[0][1]


def test_relaunch_loop_alerts_once_per_catchup_per_day(monkeypatch):
    bad = "_check_missed_live_exits"
    _, first  = _start(monkeypatch, raising={bad})
    _, second = _start(monkeypatch, raising={bad})    # RestartOnFailure relaunch, same day
    assert len(first) == 1 and second == []


def test_alert_failure_never_fails_startup(monkeypatch):
    def broken_latch(key):
        raise RuntimeError("db locked")
    monkeypatch.setattr(db, "set_alert_latch", broken_latch)
    ran, _ = _start(monkeypatch, raising={"_check_missed_eod_regime"})
    assert ran == list(sch.STARTUP_CATCHUPS)


def test_start_scheduler_calls_nothing_outside_the_list(monkeypatch):
    """A catch-up added to start_scheduler() bare, outside STARTUP_CATCHUPS, would
    be unguarded again. Every _check_missed_* in the module must be listed."""
    defined = {n for n in dir(sch) if n.startswith("_check_missed_")}
    assert defined == set(sch.STARTUP_CATCHUPS)


def test_ops_window_surfaces_a_failed_catchup_on_its_session():
    """Dashboard surface: /api/ops/window names the catch-ups that raised, per session,
    from the latch row — the durable record the alert was deduplicated on."""
    from datetime import datetime

    from backend.routers.signals_router import get_market_window
    today = datetime.now(sch.NY).date().isoformat()
    assert db.set_alert_latch(f"startup_catchup:_check_missed_live_exits:{today}")
    sessions = {s["date"]: s for s in get_market_window(days=10)["sessions"]}
    if today in sessions:                        # today is an NYSE session
        assert sessions[today]["startup_catchups_failed"] == ["_check_missed_live_exits"]
    assert all(s["startup_catchups_failed"] == [] for d, s in sessions.items() if d != today)


def test_each_catchup_leaves_one_log_line(monkeypatch):
    """A start is only checkable if every catch-up says it ran — most log nothing
    when there is nothing to catch up."""
    from loguru import logger
    seen = []
    sink = logger.add(lambda m: seen.append(m.record["message"]))
    try:
        _start(monkeypatch, raising={"_check_missed_pcr_collect"})
    finally:
        logger.remove(sink)
    for name in sch.STARTUP_CATCHUPS:
        assert sum(name in m for m in seen) == 1, name
