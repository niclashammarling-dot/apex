"""/api/ops/window: the launcher's heartbeat outcome per session, and W greyed while the mail is off."""
import backend.routers.signals_router as sr


def _session_with_log(tmp_path, monkeypatch, lines):
    monkeypatch.setattr(sr, "_LOG_DIR", tmp_path)
    d = sr.get_market_window(days=10)["sessions"][-1]["date"]      # latest NYSE session in range
    (tmp_path / f"market_window_{d}.log").write_text("\n".join(lines) + "\n")
    out = sr.get_market_window(days=10)
    return next(s for s in out["sessions"] if s["date"] == d), out


def test_pushed_line_reads_true(tmp_path, monkeypatch):
    s, _ = _session_with_log(tmp_path, monkeypatch, [
        "2026-09-29 14:20:05 CEST | starting uvicorn (no --reload), ET now 0820",
        "2026-09-29 14:22:35 CEST | heartbeat 2026-09-29 pushed @ fd83065e"])
    assert s["heartbeat_pushed"] is True


def test_failed_push_reads_false(tmp_path, monkeypatch):
    s, _ = _session_with_log(tmp_path, monkeypatch, [
        "2026-09-29 14:22:35 CEST | heartbeat push FAILED — the 08:45 ET watcher will alert: 403"])
    assert s["heartbeat_pushed"] is False


def test_not_ready_reads_false_and_no_line_reads_unknown(tmp_path, monkeypatch):
    s, _ = _session_with_log(tmp_path, monkeypatch, [
        "2026-09-29 14:25:05 CEST | not ready: no scheduler_owner=true from /health within 240s — no heartbeat pushed"])
    assert s["heartbeat_pushed"] is False
    s2, _ = _session_with_log(tmp_path, monkeypatch, ["2026-09-29 14:20:05 CEST | starting uvicorn (no --reload)"])
    assert s2["heartbeat_pushed"] is None


def test_watcher_alert_flag_is_reported(tmp_path, monkeypatch):
    import audit.publish_state as ps
    _, out = _session_with_log(tmp_path, monkeypatch, [])
    assert out["watcher_alert_enabled"] is False
    monkeypatch.setattr(ps, "WATCHER_GAP_ALERT", True)
    assert sr.get_market_window(days=1)["watcher_alert_enabled"] is True
