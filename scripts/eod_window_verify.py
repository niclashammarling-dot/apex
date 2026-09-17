"""
Did the Task Scheduler EOD window fire for a date, and did the two jobs that
earn a slot write from it? One command for the morning-after read.

    venv/bin/python scripts/eod_window_verify.py            # last NYSE session
    venv/bin/python scripts/eod_window_verify.py 2026-09-17

Four facts, each printed with its evidence (feedback_registry_entry_not_running_check):
  1. launcher  — logs/eod_window_<date>.log shows "starting uvicorn" inside the ET window
  2. eod_regime — sector_posterior_history rows for <date> with written_at inside 16:15–16:40 ET
  3. collect_pcr — lock4_pcr_history rows for <date>
  4. publish   — the launcher log carries publish_audit_state's result line
Exit 0 only if all four hold; the manual-instance case (port bound, window
exited) is reported as such, not as a pass.
"""
import re
import sqlite3
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "data" / "apex.db"
NY = ZoneInfo("America/New_York")


def last_session() -> date:
    import pandas_market_calendars as mcal
    today = datetime.now(NY).date()
    sched = mcal.get_calendar("NYSE").schedule(
        start_date=(today - timedelta(days=7)).isoformat(), end_date=today.isoformat())
    return sched.index[-1].date()


def main() -> int:
    d = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else last_session()
    ok = True

    def fact(name: str, held: bool, evidence: str) -> None:
        nonlocal ok
        ok &= held
        print(f"[{'ok' if held else 'NO'}] {name}: {evidence}")

    log = REPO / "logs" / f"eod_window_{d.isoformat()}.log"
    text = log.read_text(errors="replace") if log.exists() else ""
    if not log.exists():
        fact("launcher", False, f"{log.name} absent — task did not run wsl.exe, or wsl.exe never reached the script")
    elif "port 8000 already bound" in text and "starting uvicorn" not in text:
        fact("launcher", False, "window exited — a manual instance owned port 8000 (not a task-fired slot)")
    else:
        m = re.search(r"starting uvicorn \(no --reload\), ET now (\d{4})", text)
        started_in_window = bool(m) and 1605 <= int(m.group(1)) <= 1645
        fact("launcher", started_in_window, f"started at ET {m.group(1)}" + ("" if started_in_window else " — outside 16:05–16:45 (test launch?)") if m else
             "no 'starting uvicorn' line — " + "; ".join(re.findall(r"\| (outside ET window.*)", text)[:2]))
        drift = re.findall(r"clock drift vs Windows: (\S+)", text)
        if drift:
            print(f"     clock drift at launch: {drift[0]}")

    lo = datetime.combine(d, time(16, 15), tzinfo=NY).astimezone(ZoneInfo("UTC")).isoformat()
    hi = datetime.combine(d, time(16, 40), tzinfo=NY).astimezone(ZoneInfo("UTC")).isoformat()
    conn = sqlite3.connect(str(DB))
    rows = conn.execute(
        "SELECT COUNT(*), MIN(written_at), MAX(written_at) FROM sector_posterior_history WHERE date = ?",
        (d.isoformat(),)).fetchone()
    n, w_min, w_max = rows
    in_window = bool(n) and w_min is not None and lo <= w_min and w_max <= hi
    fact("eod_regime", in_window,
         f"{n} rows, written_at {w_min}..{w_max} (window {lo}..{hi})" if n else "no rows for the date")

    pcr = conn.execute("SELECT COUNT(*) FROM lock4_pcr_history WHERE date = ?", (d.isoformat(),)).fetchone()[0]
    fact("collect_pcr", pcr > 0, f"{pcr} ticker rows")

    pub = re.search(r"publish_audit_state: (.*)", text)
    err = re.search(r"publish_audit_state (failed|: timed out).*", text)
    fact("publish", bool(pub) and not err, (pub or err).group(0)[:160] if (pub or err) else "no publish_audit_state line in the launcher log")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
