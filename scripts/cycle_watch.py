"""
Cycle watch — alerts when a scheduled gate job has not started for STALE_MIN
minutes during NYSE market hours. Runs from its own Windows task ("APEX cycle
watch", scripts/windows/APEX-cycle-watch.xml) every 15 min, outside the backend.

Why (2026-10-03): on 10-02 the market window's console got a close event at
18:09 CEST; the backend died, RestartOnFailure did not relaunch (it never fires
on a non-zero exit — measured 10-03), and nothing said so for 3 h 51 m of
market hours. The 5-min repeat on the launcher's trigger now relaunches a dead
instance; this covers what a relaunch cannot: a backend that is alive but hung,
one that dies again after each start, and a launcher that did not run at all.
An in-process alert dies with the process, and CHECK 82 reads the day only at
16:33 ET — so the watch is a separate process on a separate task.

Signal: job_runs.started_at for run_gate (demo) and run_live_gate (live),
stamped by backend/scheduler.py at every scheduled run whatever its outcome.
Gate history is not used: a cycle with no Lock 1 candidates, or a live halt
(unreconciled, data quality, loss cap, Alpaca unreachable), writes no row.

Evaluates only inside [open + STALE_MIN, close] on an NYSE session (early
closes from the calendar); outside it, exits without writing anything. Inside,
writes one line per run to logs/cycle_watch_<ET date>.log (read by
/api/ops/window) and sends at most one mail per job per day (alert_latches).

Session flags (2026-10-03, Niclas: "the writer has to live outside the process
that dies"): the first stale run of a day writes a provisional `partial` row to
session_flags; the first run after the close (to CLOSE_FINALIZE_MIN) fills in the
final demo/live cycles/expected from audit/gate_cycles. Both writes happen here,
not in the backend — the EOD audit is a backend job and would be dead on exactly
the day that needs the mark. CHECK 85 reconciles the flags against computed
coverage (a low ratio with no flag, a flag on a normal ratio, a flag never finalized).

Not covered: the PC off or asleep, or WSL unable to start — nothing on the host
runs then; the off-host heartbeat (session-heartbeat.yml) is the only cover.

Review by 2026-10-31 (Niclas, 10-03): false alarms vs catches, threshold.

    venv/bin/python scripts/cycle_watch.py
"""
import os
import socket
import sqlite3
import sys
import urllib.request
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
NY = ZoneInfo("America/New_York")
STHLM = ZoneInfo("Europe/Stockholm")
STALE_MIN = 45          # > two missed 20-min cycles (GATE_INTERVAL)
JOBS = {"run_gate": "demo", "run_live_gate": "live"}
PORT = 8000
CLOSE_FINALIZE_MIN = 150   # runs this long after the close may finalize today's flag


def evaluate(now_utc: datetime, bounds: tuple[datetime, datetime] | None,
             stamps: dict[str, str]) -> list[dict]:
    """One dict per job, or [] when not a session or outside [open + STALE_MIN, close].

    Age runs from the later of the last start and the open, so the previous
    session's last cycle never counts against today.
    """
    if bounds is None:
        return []
    open_dt, close_dt = bounds
    if not (open_dt + timedelta(minutes=STALE_MIN) <= now_utc <= close_dt):
        return []
    out = []
    for job, side in JOBS.items():
        raw = stamps.get(job)
        last = datetime.fromisoformat(raw) if raw else None
        ref = max(last, open_dt) if last else open_dt
        age = (now_utc - ref).total_seconds() / 60
        out.append({"job": job, "side": side, "last": last, "age_min": int(age),
                    "stale": age > STALE_MIN})
    return out


def session_bounds(day) -> tuple[tuple[datetime, datetime] | None, str]:
    """((open, close) in UTC or None for no session, note). Lookup failure → regular hours."""
    try:
        import pandas_market_calendars as mcal
        s = mcal.get_calendar("NYSE").schedule(start_date=day.isoformat(), end_date=day.isoformat())
        if s.empty:
            return None, ""
        return (s.iloc[0]["market_open"].to_pydatetime(), s.iloc[0]["market_close"].to_pydatetime()), ""
    except Exception as e:
        o = datetime.combine(day, time(9, 30), tzinfo=NY).astimezone(timezone.utc)
        c = datetime.combine(day, time(16, 0), tzinfo=NY).astimezone(timezone.utc)
        return (o, c), f" (NYSE calendar lookup failed, regular hours assumed: {e})"


def read_stamps(db: Path) -> dict[str, str]:
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
        try:
            return dict(conn.execute("SELECT job, started_at FROM job_runs").fetchall())
        finally:
            conn.close()
    except sqlite3.Error:
        return {}           # no table yet (backend never started since 10-03) = no stamp


def process_state(port: int = PORT) -> str:
    """What is on the serving port: free / bound but not answering / answering."""
    with socket.socket() as s:
        s.settimeout(2)
        if s.connect_ex(("127.0.0.1", port)) != 0:
            return f"port {port} free — no backend running"
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
            body = r.read(500).decode(errors="replace")
        owner = '"scheduler_owner":true' in body.replace(" ", "")
        return f"/health answers (scheduler_owner={'true' if owner else 'false'})"
    except Exception as e:
        return f"port {port} bound but /health not answering ({type(e).__name__}) — hung"


def session_cycles(db: Path, day: str, bounds: tuple[datetime, datetime]) -> tuple[int, int, int, int]:
    """(demo cycles, demo expected, live cycles, live expected) for one session — the
    same count CHECK 80 and /api/ops/window use (audit/gate_cycles.py)."""
    from audit.gate_cycles import cycle_starts, expected_cycles
    interval = 20
    try:
        from backend.config import GATE_INTERVAL
        interval = GATE_INTERVAL
    except Exception:
        pass
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    try:
        demo = cycle_starts(conn, "demo_gate_history", day).get(day, [])
        live = cycle_starts(conn, "live_gate_history", day).get(day, [])
    finally:
        conn.close()
    return (len(demo), expected_cycles(bounds[0], bounds[1], demo, interval),
            len(live), expected_cycles(bounds[0], bounds[1], live, interval))


def _finalize(day: str, bounds: tuple[datetime, datetime]) -> str | None:
    """After the close: write the final ratio into today's flag, if one was written."""
    from backend.db import finalize_session_flag, get_session_flags
    flags = [f for f in get_session_flags(day) if f["date"] == day and f["status"] == "provisional"]
    if not flags:
        return None
    c, e, lc, le = session_cycles(REPO / "data" / "apex.db", day, bounds)
    for f in flags:
        finalize_session_flag(day, f["kind"], c, e, lc, le)
    return f"session flag finalized: demo {c}/{e}, live {lc}/{le}"


def _local(dt: datetime | None) -> str:
    return dt.astimezone(STHLM).strftime("%H:%M %Z") if dt else "none today"


def main() -> int:
    # backend.config's load_dotenv() finds .env from the cwd; the task starts in
    # /mnt/c, where it found none and the mail channel read as unconfigured
    # (found 10-03 before the first run). The launchers `cd "$APEX"` the same way.
    os.chdir(REPO)
    now = datetime.now(timezone.utc)
    day = now.astimezone(NY).date()
    bounds, note = session_bounds(day)
    sys.path.insert(0, str(REPO))
    if bounds and bounds[1] < now <= bounds[1] + timedelta(minutes=CLOSE_FINALIZE_MIN):
        done = _finalize(day.isoformat(), bounds)
        if done:
            _log(now, day, done)
        return 0
    rows = evaluate(now, bounds, read_stamps(REPO / "data" / "apex.db"))
    if not rows:
        return 0
    state = process_state()
    parts = [f"{r['side']} last {_local(r['last'])} ({r['age_min']} min) {'STALE' if r['stale'] else 'ok'}"
             for r in rows]
    stale = [r for r in rows if r["stale"]]
    sent = []
    if stale:
        from backend.alerts import alert_cycles_stale
        from backend.db import flag_session, set_alert_latch
        if flag_session(day.isoformat(), "partial",
                        f"no {'/'.join(r['side'] for r in stale)} gate start for "
                        f"{max(r['age_min'] for r in stale)} min at {now.astimezone(STHLM):%H:%M %Z}; {state}",
                        "cycle_watch"):
            parts.append("session flagged partial (provisional)")
        for r in stale:
            if set_alert_latch(f"cycle_watch:{r['job']}:{day.isoformat()}"):
                alert_cycles_stale(r["side"], _local(r["last"]), r["age_min"], state, STALE_MIN)
                sent.append(r["side"])
    _log(now, day, " · ".join(parts) + f" · {state}" + (f" · alert sent: {', '.join(sent)}" if sent else "") + note)
    return 0


def _log(now: datetime, day, line: str) -> None:
    log = REPO / "logs" / f"cycle_watch_{day.isoformat()}.log"
    log.parent.mkdir(exist_ok=True)
    with log.open("a") as f:
        f.write(f"{now.astimezone(STHLM):%Y-%m-%d %H:%M:%S %Z} | {line}\n")


if __name__ == "__main__":
    sys.exit(main())
