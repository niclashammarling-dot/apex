"""
The three Windows tasks fire inside their scripts' ET guards in both EDT and EST (2026-10-23).

Model: a CalendarTrigger whose StartBoundary carries an offset fires at a fixed UTC time,
whatever the host's DST (probed 2026-10-19: `14:20:00+02:00` on a task dated 10-27 gave
NextRunTime 13:20 +01:00; the offset-free control gave 14:20 +01:00). So the fire times
below depend only on the US clock. Found 10-19: from 2026-11-02 (EST) the EOD fallback never
ran and the cycle watch stopped before the close. Guards read from the scripts:
market_window.sh:92 (start 08:15-15:30 ET), eod_window.sh:67 (16:05-16:45 ET),
cycle_watch.py session_bounds + CLOSE_FINALIZE_MIN (watch to the 16:00 close, then finalize).
"""
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

WIN = Path(__file__).resolve().parent.parent / "scripts" / "windows"
NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
NY = ZoneInfo("America/New_York")
# 10-21 EDT with Stockholm on CEST; 10-27 EDT with Stockholm on CET (mismatch week); 11-03 EST
DAYS = [date(2026, 10, 21), date(2026, 10, 27), date(2026, 11, 3)]


def _duration(s):
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?", s)
    return timedelta(hours=int(m[1] or 0), minutes=int(m[2] or 0))


def _fires(name, day):
    """ET times the task's calendar triggers fire on `day` (repetition end inclusive)."""
    text = (WIN / name).read_text(encoding="utf-8")
    root = ET.fromstring(re.sub(r"^<\?xml[^>]*\?>", "", text))
    return [t for trig in root.iterfind("t:Triggers/t:CalendarTrigger", NS) for t in _trigger_fires(trig, day)]


def _trigger_fires(trig, day):
    start = datetime.fromisoformat(trig.findtext("t:StartBoundary", namespaces=NS))
    assert start.tzinfo, "StartBoundary without offset breaks the fixed-UTC model"
    first = datetime.combine(day, start.astimezone(timezone.utc).time(), tzinfo=timezone.utc)
    rep = trig.find("t:Repetition", NS)
    step = _duration(rep.findtext("t:Interval", namespaces=NS)) if rep is not None else None
    end = first + (_duration(rep.findtext("t:Duration", namespaces=NS)) if rep is not None else timedelta())
    out, t = [], first
    while t <= end:
        out.append(t.astimezone(NY))
        if step is None:
            break
        t += step
    return out


def _et(day, hh, mm):
    return datetime.combine(day, time(hh, mm), tzinfo=NY)


@pytest.mark.parametrize("day", DAYS, ids=str)
def test_market_window_starts_before_the_watcher_and_relaunches_to_the_guard(day):
    f = _fires("APEX-market-window.xml", day)
    assert any(_et(day, 8, 15) <= t <= _et(day, 8, 45) for t in f), "no start before the 08:45 ET watcher"
    # relaunch: a repeat at most 5 min after the guard's last start (15:30), so a death before 15:30 relaunches
    assert any(_et(day, 15, 30) <= t <= _et(day, 15, 35) for t in f)


@pytest.mark.parametrize("day", DAYS, ids=str)
def test_eod_fallback_fires_inside_its_guard(day):
    f = _fires("APEX-EOD-window.xml", day)
    assert any(_et(day, 16, 5) <= t <= _et(day, 16, 45) for t in f), [t.strftime("%H:%M") for t in f]


@pytest.mark.parametrize("day", DAYS, ids=str)
def test_cycle_watch_covers_the_session_and_a_finalize_run(day):
    from scripts.cycle_watch import CLOSE_FINALIZE_MIN
    f = _fires("APEX-cycle-watch.xml", day)
    close = _et(day, 16, 0)
    assert any(close - timedelta(minutes=15) <= t <= close for t in f), "last 15 min of the session unwatched"
    assert any(close < t <= close + timedelta(minutes=CLOSE_FINALIZE_MIN) for t in f), "no finalize run"


GUARDS = {"APEX-EOD-window.xml": ((16, 5), (16, 45)), "APEX-market-window.xml": ((8, 15), (15, 30))}


@pytest.mark.parametrize("name", sorted(GUARDS))
def test_every_trigger_fires_inside_its_guard_on_some_day(name):
    """2026-10-23 (Niclas): a trigger that never fires inside the guard is removed, not described."""
    (h0, m0), (h1, m1) = GUARDS[name]
    text = (WIN / name).read_text(encoding="utf-8")
    root = ET.fromstring(re.sub(r"^<\?xml[^>]*\?>", "", text))
    for trig in root.iterfind("t:Triggers/t:CalendarTrigger", NS):
        start = trig.findtext("t:StartBoundary", namespaces=NS)
        hits = [d for d in DAYS for t in _trigger_fires(trig, d) if _et(d, h0, m0) <= t <= _et(d, h1, m1)]
        assert hits, f"{name}: trigger {start} never fires inside {h0:02}:{m0:02}-{h1:02}:{m1:02} ET"
