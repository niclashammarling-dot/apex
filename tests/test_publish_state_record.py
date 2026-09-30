"""The host keeps its own record of what the nightly publish and counter-watch did (2026-09-29).

09-28 22:34: the counter-watch alerted correctly, and the host log held nothing —
publish_audit_state logged stdout[-4:] and dropped stderr on rc 0.
"""
import json
import subprocess
from datetime import datetime

from loguru import logger

import audit.publish_state as ps
import backend.scheduler as sch


def test_publish_logs_every_stdout_line_and_stderr_on_success(monkeypatch):
    out = "\n".join(f"line {i}" for i in range(1, 7))
    err = "HEARTBEAT WATCHER GAP: no watch record"
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=out, stderr=err))
    seen = []
    sink = logger.add(lambda m: seen.append((m.record["level"].name, m.record["message"])))
    try:
        sch.publish_audit_state()
    finally:
        logger.remove(sink)
    msgs = [m for _, m in seen]
    assert all(f"publish_audit_state: line {i}" in msgs for i in range(1, 7))   # not just the last 4
    assert ("WARNING", f"publish_audit_state (stderr): {err}") in seen


def _run_watch(monkeypatch, tmp_path, gap):
    monkeypatch.setattr(ps, "WATCHER_RECORD", tmp_path / "watcher.json")
    monkeypatch.setattr(ps, "_git", lambda *a, **k: "")
    monkeypatch.setattr(ps, "watcher_gap", lambda today, is_session, record: gap)
    import backend.alerts as alerts
    monkeypatch.setattr(alerts, "_dispatch", lambda title, body: None)
    ps.check_watcher_ran()
    return json.loads((tmp_path / "watcher.json").read_text())


def test_counter_watch_records_a_gap(monkeypatch, tmp_path):
    rec = _run_watch(monkeypatch, tmp_path, "no watch record on origin/heartbeat-watch at all")
    assert rec["status"] == "gap" and "no watch record" in rec["detail"]


def test_counter_watch_records_a_run(monkeypatch, tmp_path):
    rec = _run_watch(monkeypatch, tmp_path, None)
    assert rec["status"] in ("ran", "not_session")


def test_ops_window_surfaces_the_watcher_result(monkeypatch, tmp_path):
    import backend.routers.signals_router as sr
    today = datetime.now(sch.NY).date().isoformat()
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "watcher.json").write_text(json.dumps(
        {"date": today, "status": "gap", "detail": "no watch record"}))
    monkeypatch.setattr(sr, "_AUDIT_DIR", tmp_path)
    sessions = {s["date"]: s for s in sr.get_market_window(days=10)["sessions"]}
    if today in sessions:
        assert sessions[today]["eod"]["watcher"] is False
        assert sessions[today]["eod"]["watcher_detail"] == "no watch record"
    assert all(s["eod"]["watcher"] is None for d, s in sessions.items() if d != today)


def test_gap_is_recorded_but_not_mailed_while_alert_is_off(monkeypatch, tmp_path):
    """Decision 2026-09-29: gap mail off until the dead-man's switch; the record stays."""
    assert ps.WATCHER_GAP_ALERT is False
    import backend.alerts as alerts
    sent = []
    monkeypatch.setattr(ps, "WATCHER_RECORD", tmp_path / "watcher.json")
    monkeypatch.setattr(ps, "_git", lambda *a, **k: "")
    monkeypatch.setattr(ps, "watcher_gap", lambda today, is_session, record: "no watch record")
    monkeypatch.setattr(alerts, "_dispatch", lambda title, body: sent.append(title))
    ps.check_watcher_ran()
    assert sent == []
    assert json.loads((tmp_path / "watcher.json").read_text())["status"] == "gap"


def test_gap_mails_when_alert_is_switched_back_on(monkeypatch, tmp_path):
    import backend.alerts as alerts
    sent = []
    monkeypatch.setattr(ps, "WATCHER_GAP_ALERT", True)
    monkeypatch.setattr(ps, "WATCHER_RECORD", tmp_path / "watcher.json")
    monkeypatch.setattr(ps, "_git", lambda *a, **k: "")
    monkeypatch.setattr(ps, "watcher_gap", lambda today, is_session, record: "no watch record")
    monkeypatch.setattr(alerts, "_dispatch", lambda title, body: sent.append(title))
    ps.check_watcher_ran()
    assert sent == ["[APEX] Session heartbeat watcher did not run today"]


def test_stderr_keeps_the_subprocess_log_level(monkeypatch):
    err = ("2026-09-29 22:34:28.392 | INFO     | backend.alerts:_send_email:338 - Email alert sent: [APEX] x\n"
           "HEARTBEAT WATCHER GAP: no watch record")
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="ok", stderr=err))
    seen = []
    sink = logger.add(lambda m: seen.append((m.record["level"].name, m.record["message"])))
    try:
        sch.publish_audit_state()
    finally:
        logger.remove(sink)
    stderr = [(lvl, msg.split("(stderr): ", 1)[1]) for lvl, msg in seen if "(stderr)" in msg]
    assert [lvl for lvl, m in stderr if "Email alert sent" in m] == ["INFO"]
    assert [lvl for lvl, m in stderr if m.startswith("HEARTBEAT WATCHER GAP")] == ["WARNING"]
