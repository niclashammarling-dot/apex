"""
Every cron job fires inside the host's window or has a startup catch-up (2026-10-07).

prune_signals was a 02:00 ET cron in a process that, from 2026-09-18, runs only
in the market window (scripts/market_window.sh). Its slot never fired; the last
prune was 2026-09-16 and un-gated signals rows grew until /api/sectors froze.
The 09-16 amendment (a job on a schedule the host doesn't keep) existed as
prose and was never applied to the jobs already registered. This applies it
to every registration, now and later.

The window is 09:30–16:40 ET on weekdays: the launcher stops at 16:40 ET, and
its start (14:20 CEST, 08:20 ET) is not before 09:30 ET in every DST-mismatch
week, so market hours are the part it always covers. Registrations are read
from scheduler.py's source (ast), so a job added later is checked without
anyone listing it here. CHECK 86 is the runtime half: it catches a catch-up
that exists but does not run.
"""
import ast
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import backend.scheduler as sched

SRC = Path(sched.__file__)
NY = ZoneInfo("America/New_York")
WINDOW = (time(9, 30), time(16, 40))
WEEKDAYS = {"mon-fri", "mon", "tue", "wed", "thu", "fri"}


def cron_jobs(source: str) -> list[dict]:
    """Every scheduler.add_job(<fn>, "cron", ...) call, with constant keywords."""
    out = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "add_job"):
            continue
        if len(node.args) < 2 or not (isinstance(node.args[1], ast.Constant) and node.args[1].value == "cron"):
            continue
        kw = {k.arg: ast.literal_eval(k.value) for k in node.keywords
              if k.arg in ("id", "hour", "minute", "day_of_week", "timezone")}
        out.append(kw)
    return out


def outside_window(job: dict) -> str | None:
    """Why the job's slot is outside the window, or None if it is inside."""
    dow = str(job.get("day_of_week", "*"))
    if dow not in WEEKDAYS:
        return f"day_of_week={dow} includes days the host is not up"
    h, m = int(job["hour"]), int(job.get("minute", 0))
    tz = ZoneInfo(job.get("timezone", "America/New_York"))
    # Both DST states, so a job inside the window only in some weeks fails.
    for d in ("2026-01-14", "2026-03-11", "2026-07-15", "2026-10-28"):
        local = datetime.fromisoformat(d).replace(hour=h, minute=m, tzinfo=tz)
        et = local.astimezone(NY).time()
        if not (WINDOW[0] <= et <= WINDOW[1]):
            return f"fires {et.strftime('%H:%M')} ET on {d}"
    return None


def violations(jobs: list[dict]) -> list[str]:
    bad = []
    for j in jobs:
        why = outside_window(j)
        if why is None:
            continue
        catchup = sched.CATCHUP_FOR.get(j["id"])
        if catchup is None or catchup not in sched.STARTUP_CATCHUPS or not hasattr(sched, catchup):
            bad.append(f"{j['id']}: {why}, and no startup catch-up (CATCHUP_FOR / STARTUP_CATCHUPS)")
    return bad


def test_every_cron_job_is_in_the_window_or_caught_up():
    jobs = cron_jobs(SRC.read_text())
    assert any(j["id"] == "prune_signals" for j in jobs), "parser found no prune_signals — the read is broken"
    assert violations(jobs) == []


def test_the_0916_shape_is_caught():
    """Positive control: prune_signals as it stood until 10-07 (02:00 daily, no catch-up)."""
    src = 'scheduler.add_job(prune_old_signals, "cron", hour=2, minute=0, id="prune_without_catchup")'
    assert violations(cron_jobs(src)) == [
        "prune_without_catchup: day_of_week=* includes days the host is not up, "
        "and no startup catch-up (CATCHUP_FOR / STARTUP_CATCHUPS)"]


def test_weekday_slot_before_the_open_is_caught():
    src = 'scheduler.add_job(f, "cron", day_of_week="mon-fri", hour=8, minute=0, id="early")'
    assert violations(cron_jobs(src)) == [
        "early: fires 08:00 ET on 2026-01-14, and no startup catch-up (CATCHUP_FOR / STARTUP_CATCHUPS)"]


def test_stockholm_slot_is_converted_in_both_dst_states():
    # weekly_research, Monday 17:00 Stockholm: 11:00 ET, 12:00 ET in mismatch weeks
    assert outside_window({"id": "w", "day_of_week": "mon", "hour": 17, "minute": 0,
                           "timezone": "Europe/Stockholm"}) is None
    # 23:00 Stockholm is 17:00 or 18:00 ET — after the window
    assert outside_window({"id": "w", "day_of_week": "mon", "hour": 23, "minute": 0,
                           "timezone": "Europe/Stockholm"}) is not None
