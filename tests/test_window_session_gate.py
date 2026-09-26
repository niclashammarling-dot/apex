"""
The window launchers refuse to start on a weekday NYSE holiday (2026-09-26).
Runs the real scripts with APEX_WINDOW_DRY_RUN so nothing can start, a temp
log dir, and this interpreter for the calendar lookup.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = ["market_window.sh", "eod_window.sh"]


def _run(script, tmp_path, session_date):
    env = {**os.environ, "APEX_ROOT": str(REPO), "APEX_PYTHON": sys.executable,
           "APEX_LOG_DIR": str(tmp_path), "APEX_SESSION_DATE": session_date,
           "APEX_WINDOW_DRY_RUN": "1"}
    env.pop("MARKET_WINDOW_TEST_SECONDS", None)
    env.pop("EOD_WINDOW_TEST_SECONDS", None)
    rc = subprocess.run(["bash", str(REPO / "scripts" / script)], env=env,
                        capture_output=True, text=True, timeout=60).returncode
    log = "".join(p.read_text() for p in tmp_path.glob("*.log"))
    return rc, log


@pytest.mark.parametrize("script", SCRIPTS)
def test_holiday_starts_nothing(tmp_path, script):
    rc, log = _run(script, tmp_path, "2026-11-26")
    assert rc == 0
    assert "not an NYSE session (2026-11-26) — nothing started" in log
    assert "dry run" not in log and "starting uvicorn" not in log


@pytest.mark.parametrize("script", SCRIPTS)
def test_session_passes_the_gate(tmp_path, script):
    rc, log = _run(script, tmp_path, "2026-11-27")      # half day is a session
    assert rc == 0
    assert "not an NYSE session" not in log
    assert "dry run" in log or "outside ET window" in log
    assert "starting uvicorn" not in log


@pytest.mark.parametrize("script", SCRIPTS)
def test_lookup_failure_starts_anyway(tmp_path, script):
    rc, log = _run(script, tmp_path, "not-a-date")
    assert "NYSE calendar lookup failed — starting anyway" in log
    assert "dry run" in log or "outside ET window" in log
