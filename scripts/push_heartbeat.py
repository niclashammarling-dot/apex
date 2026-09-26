"""
Push the session heartbeat: one file, heartbeat.json, as a single parentless
commit force-pushed to the orphan branch `heartbeat`. The branch never grows.

Called by scripts/market_window.sh only after the backend answered /health
with scheduler_owner=true (2026-09-26). Read by .github/workflows/
session-heartbeat.yml (scripts/heartbeat_watch.py), which alerts when an NYSE
session has no heartbeat by 08:45 ET — the watcher lives off the host because
every in-process alert path dies with the process that did not start.

Credential path: plain `git push origin` from the repo, the same SSH remote
audit/publish_state.py pushes audit-state over. No new auth on the host.

    venv/bin/python scripts/push_heartbeat.py

Env (tests / positive control only; the launcher sets none of these):
  APEX_HEARTBEAT_REMOTE  remote name or path (default: origin)
  APEX_HEARTBEAT_DATE    session date to stamp (default: today, America/New_York)
  APEX_ROOT              repo whose object store builds the commit
Exit 0 on push, 1 on failure (the launcher logs it; the watcher alerts).
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BRANCH = "heartbeat"
NY = ZoneInfo("America/New_York")


def _git(repo: Path, *args: str, stdin: str | None = None) -> str:
    r = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, input=stdin)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


def build_payload(repo: Path, session_date: str | None = None) -> dict:
    now = datetime.now(timezone.utc)
    try:
        commit = _git(repo, "rev-parse", "HEAD")
    except RuntimeError:
        commit = "unknown"
    return {
        "session_date": session_date or now.astimezone(NY).date().isoformat(),
        "pushed_at_utc": now.isoformat(timespec="seconds"),
        "pushed_at_et": now.astimezone(NY).strftime("%H:%M:%S"),
        "host_commit": commit,
        "launcher_pid": os.getppid(),
    }


def push(payload: dict, repo: Path, remote: str = "origin") -> str:
    blob = _git(repo, "hash-object", "-w", "--stdin", stdin=json.dumps(payload, indent=1) + "\n")
    tree = _git(repo, "mktree", stdin=f"100644 blob {blob}\theartbeat.json\n")
    env_msg = f"heartbeat {payload['session_date']} {payload['pushed_at_utc']}"
    commit = _git(repo, "commit-tree", tree, "-m", env_msg)       # no -p: orphan, one commit
    _git(repo, "push", "-q", remote, f"+{commit}:refs/heads/{BRANCH}")
    return commit


def main() -> int:
    repo = Path(os.environ.get("APEX_ROOT") or Path(__file__).resolve().parent.parent)
    payload = build_payload(repo, os.environ.get("APEX_HEARTBEAT_DATE") or None)
    try:
        commit = push(payload, repo, os.environ.get("APEX_HEARTBEAT_REMOTE") or "origin")
    except Exception as e:
        print(f"heartbeat push failed: {e}", file=sys.stderr)
        return 1
    print(f"heartbeat {payload['session_date']} pushed @ {commit[:8]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
