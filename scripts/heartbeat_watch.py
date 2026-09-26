"""
Session heartbeat watcher — runs in GitHub Actions (session-heartbeat.yml),
never on the host. Alerts when an NYSE session has no heartbeat from the
market-window launcher.

SCOPE — what a quiet run proves, and nothing more:
  "the backend STARTED and HELD THE SCHEDULER LOCK by 08:45 ET today."
It does NOT prove the backend was alive at the open, served the gate, or
survived the session. A crash after the heartbeat is uncovered by this check;
the next thing that notices is the 16:30 ET collect_pcr / 16:33 ET publish
(CHECK 80 counts gate cycles per session, next day). Do not read more
coverage into a quiet day than the line above.

Why off-host (2026-09-26): the launcher runs from Windows Task Scheduler via
wsl.exe on the desk PC. A watcher on the same host shares every failure that
stops the launch — task disabled, wsl.exe broken, PC off or asleep — and
every in-process alert path dies with the process that did not start.

Window gate instead of dedupe state: two crons (12:45 and 13:45 UTC) cover
EDT and EST; a run acts only if ET now is inside 08:30–09:30, so exactly one
run acts per day, up to 45 min of GitHub schedule lateness still lands inside,
and "one alert per missing day" needs no stored state. GitHub documents that
a delayed schedule run can be dropped entirely — the host counter-watch
(audit/publish_state.py check_watcher_ran) reads the record this watcher
writes to the `heartbeat-watch` branch and alerts on a session day it missed.

    python scripts/heartbeat_watch.py                      # scheduled
    python scripts/heartbeat_watch.py --date 2026-09-25    # forced: no window gate

Writes GITHUB_OUTPUT: action=<skip-window|skip-holiday|ok|alert>, subject,
body file. The workflow sends the mail and fails the job on alert.
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
WINDOW = (time(8, 30), time(9, 30))


def evaluate(now_utc: datetime, heartbeat: dict | None, is_session: bool | None,
             force_date: date | None = None) -> tuple[str, str]:
    """(action, detail). is_session None = calendar lookup failed → treated as a session."""
    now_et = now_utc.astimezone(NY)
    if force_date is None:
        if not (WINDOW[0] <= now_et.time() <= WINDOW[1]):
            return "skip-window", f"ET now {now_et:%H:%M} outside {WINDOW[0]:%H:%M}–{WINDOW[1]:%H:%M}"
        day = now_et.date()
    else:
        day = force_date
    if is_session is False:
        return "skip-holiday", f"{day} is not an NYSE session"
    cal = "" if is_session else " (NYSE calendar lookup failed — treated as a session)"
    if heartbeat is None:
        return "alert", f"no heartbeat branch/file found — nothing has ever been pushed, or it was deleted{cal}"
    got = heartbeat.get("session_date")
    if got != day.isoformat():
        return "alert", f"heartbeat is for {got} (pushed {heartbeat.get('pushed_at_utc')}), not {day}{cal}"
    return "ok", (f"heartbeat {got} pushed {heartbeat.get('pushed_at_et')} ET, "
                  f"commit {str(heartbeat.get('host_commit'))[:8]}")


def session_status(d: date) -> bool | None:
    try:
        import pandas_market_calendars as mcal
        return len(mcal.get_calendar("NYSE").schedule(start_date=d.isoformat(),
                                                      end_date=d.isoformat())) > 0
    except Exception as e:
        print(f"calendar lookup failed: {e}", file=sys.stderr)
        return None


def read_heartbeat() -> dict | None:
    f = subprocess.run(["git", "fetch", "-q", "--depth=1", "origin", "heartbeat"],
                       capture_output=True, text=True)
    if f.returncode != 0:
        print(f"fetch heartbeat: {f.stderr.strip()}", file=sys.stderr)
        return None
    s = subprocess.run(["git", "show", "FETCH_HEAD:heartbeat.json"], capture_output=True, text=True)
    if s.returncode != 0:
        return None
    try:
        return json.loads(s.stdout)
    except ValueError:
        return {"session_date": None, "pushed_at_utc": "unparseable heartbeat.json"}


def send_mail(subject: str, body: str) -> None:
    """Same ALERT_* variables and SMTP shape as backend/alerts.py. Raises on a
    missing variable or a failed send — the workflow step must go red, never
    print-and-pass (audit/alert_criticals.py's stdout fallback is the wrong
    default for a check whose only output is this mail)."""
    import smtplib
    from email.mime.text import MIMEText
    env = {k: os.environ.get(k, "") for k in
           ("ALERT_EMAIL_TO", "ALERT_EMAIL_FROM", "ALERT_SMTP_USER", "ALERT_SMTP_PASS")}
    missing = [k for k, v in env.items() if not v]
    if missing:
        raise RuntimeError(f"mail not configured — empty secrets: {', '.join(missing)}")
    msg = MIMEText(body)
    msg["Subject"], msg["From"], msg["To"] = subject, env["ALERT_EMAIL_FROM"], env["ALERT_EMAIL_TO"]
    host = os.environ.get("ALERT_SMTP_HOST") or "smtp.gmail.com"
    port = int(os.environ.get("ALERT_SMTP_PORT") or "587")
    with smtplib.SMTP(host, port, timeout=30) as server:
        server.ehlo()
        server.starttls()
        server.login(env["ALERT_SMTP_USER"], env["ALERT_SMTP_PASS"])
        server.sendmail(env["ALERT_EMAIL_FROM"], [env["ALERT_EMAIL_TO"]], msg.as_string())
    print(f"alert sent via {host}:{port}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    ap.add_argument("--send", nargs=2, metavar=("SUBJECT", "BODY_FILE"))
    a = ap.parse_args()
    if a.send:
        with open(a.send[1]) as fh:
            send_mail(a.send[0], fh.read())
        return 0
    force = date.fromisoformat(a.date) if a.date else None
    now = datetime.now(timezone.utc)
    day = force or now.astimezone(NY).date()

    action, detail = evaluate(now, None, True, force)     # window gate first: no fetch outside it
    if action != "skip-window":
        action, detail = evaluate(now, read_heartbeat(), session_status(day), force)
    print(f"{action}: {detail}")

    tag = " [FORCED TEST RUN]" if force else ""
    subject = f"[APEX] No session heartbeat — {day} backend not confirmed started{tag}"
    body = (f"{detail}\n\n"
            f"The market-window launcher pushes a heartbeat to origin/heartbeat only after the "
            f"backend answers /health with scheduler_owner=true. None for {day} by the time "
            f"this check ran ({now.astimezone(NY):%H:%M} ET).\n\n"
            f"Look at: logs/market_window_{day}.log on the desk PC (missing file = the task never "
            f"reached the script; 'not an NYSE session' = the session gate answered wrong; "
            f"'not ready' = started but never held the scheduler lock).\n\n"
            f"Scope: this check proves 'started with lock by 08:45 ET', not 'alive at the open'."
            + ("\n\nThis is a FORCED test run (--date), not a scheduled check." if force else ""))
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        body_path = os.path.join(os.environ.get("RUNNER_TEMP", "/tmp"), "heartbeat_alert.txt")
        with open(body_path, "w") as fh:
            fh.write(body)
        with open(out, "a") as fh:
            fh.write(f"action={action}\nday={day}\nforced={'true' if force else 'false'}\n"
                     f"subject={subject}\nbody_file={body_path}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
