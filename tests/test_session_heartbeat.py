"""
Session heartbeat (2026-09-26): launcher readiness → push, the off-host
watcher's decision, and the host counter-watch. Nothing here touches origin:
pushes go to a temp bare repo, the watcher is tested as a pure function.
"""
import json
import os
import socket
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import heartbeat_watch as hw  # noqa: E402
from audit.publish_state import watcher_gap  # noqa: E402

UTC = timezone.utc


def _hb(d: str) -> dict:
    return {"session_date": d, "pushed_at_utc": f"{d}T12:25:00+00:00",
            "pushed_at_et": "08:25:00", "host_commit": "abcdef1234"}


# ── window gate: exactly one of the two crons acts, in both DST regimes ──────

@pytest.mark.parametrize("utc_hm, acts", [
    ((12, 45), True),    # EDT: 08:45 ET
    ((13, 45), False),   # EDT: 09:45 ET
])
def test_edt_window(utc_hm, acts):
    now = datetime(2026, 9, 28, *utc_hm, tzinfo=UTC)          # Monday, EDT
    action, _ = hw.evaluate(now, None, True)
    assert (action != "skip-window") is acts


@pytest.mark.parametrize("utc_hm, acts", [
    ((12, 45), False),   # EST: 07:45 ET
    ((13, 45), True),    # EST: 08:45 ET
])
def test_est_window(utc_hm, acts):
    now = datetime(2026, 11, 30, *utc_hm, tzinfo=UTC)         # Monday, EST
    action, _ = hw.evaluate(now, None, True)
    assert (action != "skip-window") is acts


def test_forty_five_minutes_late_still_acts():
    assert hw.evaluate(datetime(2026, 9, 28, 13, 30, tzinfo=UTC), None, True)[0] == "alert"


def test_exactly_one_acting_cron_every_weekday_of_the_year():
    d = date(2026, 1, 1)
    while d.year == 2026:
        if d.weekday() < 5:
            acting = [h for h in (12, 13)
                      if hw.evaluate(datetime(d.year, d.month, d.day, h, 45, tzinfo=UTC),
                                     None, True)[0] != "skip-window"]
            assert len(acting) == 1, d
        d += timedelta(days=1)


# ── decision inside the window ───────────────────────────────────────────────

NOW = datetime(2026, 9, 28, 12, 45, tzinfo=UTC)


def test_missing_heartbeat_alerts():
    assert hw.evaluate(NOW, None, True)[0] == "alert"


def test_stale_heartbeat_alerts():
    action, detail = hw.evaluate(NOW, _hb("2026-09-25"), True)
    assert action == "alert" and "2026-09-25" in detail


def test_matching_heartbeat_is_quiet():
    assert hw.evaluate(NOW, _hb("2026-09-28"), True)[0] == "ok"


def test_holiday_is_quiet():
    thanksgiving = datetime(2026, 11, 26, 13, 45, tzinfo=UTC)
    assert hw.evaluate(thanksgiving, None, False)[0] == "skip-holiday"


def test_calendar_failure_is_checked_as_a_session():
    action, detail = hw.evaluate(NOW, None, None)
    assert action == "alert" and "lookup failed" in detail


def test_forced_date_skips_the_window_gate():
    saturday = datetime(2026, 9, 26, 18, 0, tzinfo=UTC)
    assert hw.evaluate(saturday, None, True, date(2026, 9, 25))[0] == "alert"
    assert hw.evaluate(saturday, _hb("2026-09-25"), True, date(2026, 9, 25))[0] == "ok"


# ── host counter-watch ───────────────────────────────────────────────────────

def test_counter_watch():
    today = "2026-09-28"
    assert watcher_gap(today, True, None) is not None
    assert watcher_gap(today, True, {"checked_date": "2026-09-25"}) is not None
    assert watcher_gap(today, True, {"checked_date": today}) is None
    assert watcher_gap(today, None, {"checked_date": "2026-09-25"}) is not None
    assert watcher_gap(today, False, None) is None


# ── push: orphan, one commit, one file, never grows ─────────────────────────

def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def remote(tmp_path):
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    return bare


def _push(remote, session_date):
    env = {**os.environ, "APEX_HEARTBEAT_REMOTE": str(remote), "APEX_HEARTBEAT_DATE": session_date,
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run([sys.executable, str(REPO / "scripts" / "push_heartbeat.py")],
                          env=env, capture_output=True, text=True, timeout=60)


def test_push_is_a_single_orphan_commit(remote):
    assert _push(remote, "2026-09-25").returncode == 0
    assert _push(remote, "2026-09-28").returncode == 0
    assert _git(remote, "rev-list", "--count", "heartbeat") == "1"
    assert _git(remote, "ls-tree", "--name-only", "heartbeat") == "heartbeat.json"
    hb = json.loads(_git(remote, "show", "heartbeat:heartbeat.json"))
    assert hb["session_date"] == "2026-09-28"


# ── launcher: push only after scheduler_owner=true, bounded wait ────────────

def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _launch(tmp_path, remote, owner, timeout="20", with_remote=True):
    env = {**os.environ, "APEX_ROOT": str(REPO), "APEX_PYTHON": sys.executable,
           "APEX_LOG_DIR": str(tmp_path), "APEX_SESSION_DATE": "2026-11-27",
           "APEX_PORT": str(_free_port()), "APEX_READY_TIMEOUT": timeout,
           "APEX_UVICORN": str(REPO / "tests" / "fixtures" / "fake_uvicorn.sh"),
           "MARKET_WINDOW_TEST_SECONDS": "1", "FAKE_DELAY": "1", "FAKE_OWNER": owner,
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    env.pop("APEX_WINDOW_DRY_RUN", None)
    env.pop("APEX_HEARTBEAT_REMOTE", None)
    if with_remote:
        env["APEX_HEARTBEAT_REMOTE"] = str(remote)
    subprocess.run(["bash", str(REPO / "scripts" / "market_window.sh")], env=env,
                   capture_output=True, text=True, timeout=120)
    return "".join(p.read_text() for p in tmp_path.glob("market_window_*.log"))


def _has_branch(remote):
    return subprocess.run(["git", "rev-parse", "-q", "--verify", "refs/heads/heartbeat"],
                          cwd=remote, capture_output=True).returncode == 0


def test_ready_launch_pushes(tmp_path, remote):
    log = _launch(tmp_path, remote, owner="1")
    assert "ready: scheduler owner confirmed" in log
    assert "heartbeat" in log and "pushed @" in log
    assert _has_branch(remote)


def test_no_lock_pushes_nothing(tmp_path, remote):
    # 12 s = polls at ~0, 5, 10 s against a server up at 1 s. A timeout shorter
    # than the 5 s poll interval passed vacuously (one poll, before the server
    # was up) — found by loosening the grep, 2026-09-26.
    log = _launch(tmp_path, remote, owner="0", timeout="12")
    assert "not ready: no scheduler_owner=true from /health within 12s" in log
    assert "pushed @" not in log
    assert not _has_branch(remote)


def test_test_hold_never_pushes_to_origin(tmp_path, remote):
    log = _launch(tmp_path, remote, owner="1", with_remote=False)
    assert "test hold — heartbeat not pushed" in log
