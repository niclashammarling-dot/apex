"""
Job registration check for the live gate / exit tracker phase (2026-09-25).

The live gate must fire half an exit-check interval after a check_live_exits
tick, never in the same second (the race behind the 09-16 and 09-25 false
data-quality halts). Nothing else tests job registration, so a mistake in
start_scheduler()'s add_job arguments would only show up at launch, live.

Runs the real start_scheduler() against a fresh, never-started
BackgroundScheduler whose start() raises: every add_job call executes with
its real arguments, and nothing after start() (the _check_missed_* catch-up
runs) is reached. No lock, no APEX_SERVE, no job ever executes.
"""
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

from backend.config import EXIT_CHECK_INTERVAL, GATE_INTERVAL


class _Registered(Exception):
    pass


def _registered_jobs() -> dict:
    from backend import scheduler as sched_module

    fresh = BackgroundScheduler(timezone=sched_module.scheduler.timezone)
    with patch.object(sched_module, "scheduler", fresh), \
         patch.object(fresh, "start", side_effect=_Registered):
        with pytest.raises(_Registered):
            sched_module.start_scheduler()
    return {job.id: job for job in fresh.get_jobs()}


def _fire_times(job, start: datetime, n: int) -> list[datetime]:
    times, prev, now = [], None, start
    for _ in range(n):
        t = job.trigger.get_next_fire_time(prev, now)
        times.append(t)
        prev, now = t, t + timedelta(microseconds=1)
    return times


def test_live_gate_fires_half_an_exit_interval_after_a_tracker_tick():
    jobs = _registered_jobs()
    gate, tracker = jobs["run_live_gate"], jobs["check_live_exits"]

    # Monday session open, 15:30 CEST — times are compared as instants.
    ref = datetime(2026, 9, 28, 15, 30, tzinfo=ZoneInfo("Europe/Stockholm"))
    gate_times = _fire_times(gate, ref, 20)                     # ~6.5h of gate runs
    tracker_times = _fire_times(tracker, ref - timedelta(minutes=EXIT_CHECK_INTERVAL),
                                20 * GATE_INTERVAL // EXIT_CHECK_INTERVAL + 2)

    want = EXIT_CHECK_INTERVAL * 60 / 2
    for g in gate_times:
        preceding = max(t for t in tracker_times if t <= g)
        lag = (g - preceding).total_seconds()
        # start_date for the gate and the tracker's default start are taken
        # from two datetime.now() calls microseconds apart — allow 1s.
        assert abs(lag - want) < 1.0, (
            f"gate fires at {g.isoformat()} {lag:.1f}s after the tracker tick at "
            f"{preceding.isoformat()}; expected {want:.0f}s")
