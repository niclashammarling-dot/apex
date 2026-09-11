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
from datetime import date, datetime, timezone
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
    try:
        sys.path.insert(0, str(REPO))
        from backend.alerts import _dispatch
        _dispatch("[APEX] Nightly audit CI is not producing reports", text)
    except Exception as e:  # alerting must never mask the publish result
        print(f"(alert dispatch failed: {e})", file=sys.stderr)


def main() -> int:
    run_checks()
    stamp_alert_channel()
    commit = push_state()
    print(f"Published {STATE_FILE.relative_to(REPO)} → origin/{BRANCH} @ {commit[:8]}")
    check_ci_liveness()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"publish_state failed: {e}", file=sys.stderr)
        sys.exit(1)
