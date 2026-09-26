"""
Publish the app host's audit state to the orphan branch `audit-state`.

Runs where apex.db and the app-written data files live (the APEX host),
nightly from backend/scheduler.py. Executes the mechanical checks with the
data present, writes the findings as JSON, and pushes that single file to
`refs/heads/audit-state` with git plumbing — a one-file orphan history that
never carries the host's unpushed dev commits on master. CI fetches it and
merges it into the nightly report (mechanical_checks.py --merge-state).

Reverse-direction liveness: after publishing, look at origin/master's newest
nightly report. If CI hasn't committed one in STATE_MAX_AGE_HOURS, alert
through the app's own channel — the one side that can still speak when the
CI runner is dead. Together with CHECK 73 (CI watching this file's age) each
side watches the other from outside the other's failure domain.

Stdlib + git only on the publish path. Exit non-zero on any failure so the
scheduler logs it.
"""
from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO       = Path(__file__).parent.parent
STATE_FILE = REPO / "audit" / "state" / "latest.json"
BRANCH     = "audit-state"
MAX_AGE_H  = 36


def _git(*args: str, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


def run_checks() -> None:
    r = subprocess.run([sys.executable, "-m", "audit.mechanical_checks",
                        "--emit-state", str(STATE_FILE)],
                       cwd=REPO, capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        raise RuntimeError(f"mechanical_checks failed:\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
    print(r.stdout.strip().splitlines()[-1])


def stamp_alert_channel() -> None:
    """Add a live proof of the alert channel to the state file (no email sent)."""
    import json
    sys.path.insert(0, str(REPO))
    try:
        from backend.alerts import verify_email_channel
        ok, detail = verify_email_channel()
    except Exception as e:
        ok, detail = False, f"verify_email_channel unavailable: {e}"
    payload = json.loads(STATE_FILE.read_text())
    payload["alert_channel"] = {"ok": ok, "detail": detail,
                                "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    STATE_FILE.write_text(json.dumps(payload, indent=1))
    print(f"Alert channel: {'ok' if ok else 'DEAD'} — {detail}", file=None if ok else sys.stderr)


def push_state() -> str:
    blob   = _git("hash-object", "-w", str(STATE_FILE))
    tree   = subprocess.run(["git", "mktree"], cwd=REPO, capture_output=True, text=True,
                            input=f"100644 blob {blob}\tlatest.json\n").stdout.strip()
    _git("fetch", "-q", "origin", f"+refs/heads/{BRANCH}:refs/remotes/origin/{BRANCH}", check=False)
    parent = _git("rev-parse", "-q", "--verify", f"refs/remotes/origin/{BRANCH}", check=False)
    msg    = f"audit state {datetime.now(timezone.utc).isoformat(timespec='seconds')}"
    args   = ["commit-tree", tree, "-m", msg] + (["-p", parent] if parent else [])
    commit = _git(*args)
    _git("push", "-q", "origin", f"{commit}:refs/heads/{BRANCH}")
    return commit


def check_ci_liveness() -> None:
    """The other direction: has CI committed a nightly report recently?"""
    _git("fetch", "-q", "origin", "master")
    names = _git("ls-tree", "--name-only", "origin/master", "audit/").splitlines()
    dates = sorted(m.group(1) for n in names
                   if (m := re.search(r"nightly-report-(\d{4}-\d{2}-\d{2})\.md$", n)))
    if not dates:
        _alert("no nightly report found on origin/master at all")
        return
    age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(dates[-1]).replace(
        tzinfo=timezone.utc)).total_seconds() / 3600
    if age_h > MAX_AGE_H + 24:   # report filenames are day-granular; allow a full day of slack
        _alert(f"newest nightly report on origin/master is {dates[-1]} ({age_h:.0f}h old) — "
               f"the CI audit workflow is not running or not committing")
    else:
        print(f"CI liveness ok: newest report {dates[-1]}")


def _alert(text: str) -> None:
    print(f"CI LIVENESS ALERT: {text}", file=sys.stderr)
    ALERT_STAMP.parent.mkdir(parents=True, exist_ok=True)
    ALERT_STAMP.write_text(datetime.now(timezone.utc).isoformat(timespec="seconds"))
    try:
        sys.path.insert(0, str(REPO))
        from backend.alerts import _dispatch
        _dispatch("[APEX] Nightly audit CI is not producing reports", text)
    except Exception as e:  # alerting must never mask the publish result
        print(f"(alert dispatch failed: {e})", file=sys.stderr)


ALERT_STAMP = REPO / "audit" / "state" / "ci_liveness_alerted.txt"
CI_REALERT_DAYS = 7


def _liveness_due() -> bool:
    """Rate-limit the CI-liveness alert: once, then weekly while it holds.

    Before 2026-09-16 it dispatched the identical text on every publish (the
    2026-06-27 report was 1944 h old on the 09-16 mail and would be 1968 h on
    the next) — an alert that repeats unchanged carries no information after
    the first delivery and trains the reader out of the channel.
    """
    try:
        last = datetime.fromisoformat(ALERT_STAMP.read_text().strip())
    except (FileNotFoundError, ValueError):
        return True
    return (datetime.now(timezone.utc) - last).days >= CI_REALERT_DAYS


def route_criticals() -> None:
    """Dispatch this run's CRITICAL findings through the host's own channel.

    Until 2026-09-16 the only findings→email path was audit/alert_criticals.py
    inside the CI workflow, disabled since 08-10; a CRITICAL in latest.json went
    to the audit-state branch and nowhere else (consumer audit, ten rows, zero
    read on a kept schedule). CHECKs 77/78/79 were converted from level to event
    severities in the same commit so that what arrives here is news, not the
    same cumulative count nightly. SUPPRESSED and WARNING are not routed.
    """
    import json
    payload = json.loads(STATE_FILE.read_text())
    crits = [f for f in payload.get("findings", []) if len(f) >= 5 and f[2] == "CRITICAL"]
    if not crits:
        print("CRITICAL routing: none")
        return
    lines = [f"CHECK {f[0]} — {f[1]}\n  {f[3]}\n  {f[4]}" for f in crits]
    body = (f"{len(crits)} CRITICAL finding(s) in the host audit run "
            f"{payload.get('generated_at', '?')} (commit {str(payload.get('host_commit', '?'))[:8]}):\n\n"
            + "\n\n".join(lines)
            + "\n\nFull state: origin/audit-state audit/state/latest.json")
    print(f"CRITICAL routing: {len(crits)} finding(s) — CHECKs {', '.join(str(f[0]) for f in crits)}")
    try:
        sys.path.insert(0, str(REPO))
        from backend.alerts import _dispatch
        _dispatch(f"[APEX] Audit CRITICAL x{len(crits)}: CHECKs {', '.join(str(f[0]) for f in crits)}", body)
    except Exception as e:  # routing must never mask the publish result
        print(f"(CRITICAL dispatch failed: {e})", file=sys.stderr)


WATCH_BRANCH = "heartbeat-watch"


def watcher_gap(today: str, is_session: bool | None, record: dict | None) -> str | None:
    """The alert text if the off-host heartbeat watcher did not act on today's
    session, else None. None-session (calendar lookup failed) is checked as a session."""
    if is_session is False:
        return None
    if record is None:
        return f"no watch record on origin/{WATCH_BRANCH} at all — the watcher has never recorded a run"
    if record.get("checked_date") != today:
        return (f"the watcher's last recorded run is for {record.get('checked_date')} "
                f"({record.get('run', '?')}), not today's session {today}")
    return None


def check_watcher_ran() -> None:
    """Counter-watch for .github/workflows/session-heartbeat.yml (2026-09-26).

    The heartbeat watcher is itself a check that can go silent: GitHub drops
    delayed scheduled runs and disables scheduled workflows in a public repo
    after 60 days without activity. Each acting run writes watch.json to
    origin/heartbeat-watch; this reads it at 16:33 ET and alerts on a session
    day it is missing. Mutual, not total: a day the backend AND the watcher
    both fail is silent (this runs inside the backend's scheduler).
    """
    import json
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    try:
        import pandas_market_calendars as mcal
        is_session = len(mcal.get_calendar("NYSE").schedule(start_date=today, end_date=today)) > 0
    except Exception:
        is_session = None
    _git("fetch", "-q", "origin", f"+refs/heads/{WATCH_BRANCH}:refs/remotes/origin/{WATCH_BRANCH}", check=False)
    raw = _git("show", f"refs/remotes/origin/{WATCH_BRANCH}:watch.json", check=False)
    try:
        record = json.loads(raw) if raw else None
    except ValueError:
        record = {"checked_date": f"unparseable watch.json: {raw[:80]}"}
    gap = watcher_gap(today, is_session, record)
    if gap is None:
        print(f"Heartbeat watcher: ran for {today}" if is_session is not False
              else "Heartbeat watcher: not a session, not checked")
        return
    print(f"HEARTBEAT WATCHER GAP: {gap}", file=sys.stderr)
    try:
        sys.path.insert(0, str(REPO))
        from backend.alerts import _dispatch
        _dispatch("[APEX] Session heartbeat watcher did not run today",
                  gap + "\n\nThe 08:45 ET check (.github/workflows/session-heartbeat.yml) "
                  "is the only thing that notices a backend that never started. Check the "
                  "Actions page: a dropped scheduled run, or the workflow disabled "
                  "(60-day inactivity rule, public repo).")
    except Exception as e:  # alerting must never mask the publish result
        print(f"(watcher-gap dispatch failed: {e})", file=sys.stderr)


def main() -> int:
    run_checks()
    stamp_alert_channel()
    commit = push_state()
    print(f"Published {STATE_FILE.relative_to(REPO)} → origin/{BRANCH} @ {commit[:8]}")
    route_criticals()
    try:
        check_watcher_ran()
    except Exception as e:  # the counter-watch must never mask the publish result
        print(f"(watcher counter-check failed: {e})", file=sys.stderr)
    if _liveness_due():
        check_ci_liveness()
    else:
        print("CI liveness: alert sent within the last "
              f"{CI_REALERT_DAYS} days, not repeated")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"publish_state failed: {e}", file=sys.stderr)
        sys.exit(1)
