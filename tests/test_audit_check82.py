"""CHECK 82 — market window interrupted: the launcher's self-log read at event severity."""
import pytest


def _run82(tmp_path, monkeypatch, logs: dict[str, str], sessions: list[str]):
    from audit import _audit_core as core
    from audit import checks_gate as cg
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    (repo / "data/apex.db").write_bytes(b"")
    (repo / "logs").mkdir()
    for d, text in logs.items():
        (repo / "logs" / f"market_window_{d}.log").write_text(text)
    monkeypatch.setattr(cg, "REPO", repo)
    monkeypatch.setattr(core, "REPO", repo)
    monkeypatch.setattr(core, "findings", [])
    monkeypatch.setattr(core, "skipped", [])
    monkeypatch.setattr(core, "triggered", set())
    monkeypatch.setattr(cg, "flag", core.flag)
    monkeypatch.setattr(cg, "_c82_session_dates", lambda n: sessions[-n:])
    cg.check82()
    return [(f[2], f[4]) for f in core.findings if f[0] == 82]


START  = "2026-09-22 14:15:01 CEST | starting uvicorn (no --reload), ET now 0815\n"
CLOSE  = "2026-09-22 22:40:00 CEST | window closed (ET now 1640) — SIGTERM 100\n2026-09-22 22:40:01 CEST | exit\n"
EARLYCLOSE = "2026-09-22 22:17:24 CEST | window closed (ET now 1617) — SIGTERM 100\n2026-09-22 22:17:24 CEST | exit\n"
EARLY  = "2026-09-22 19:53:10 CEST | ended before window close (uvicorn exited rc=143), ET now 1353\n"
DRIFT  = "2026-09-22 14:15:00 CEST | clock drift vs Windows: 0s\n"


def test_clean_day_is_silent(tmp_path, monkeypatch):
    assert _run82(tmp_path, monkeypatch, {"2026-09-22": DRIFT + START + CLOSE}, ["2026-09-22"]) == []


def test_early_end_is_critical_and_relaunch_is_named(tmp_path, monkeypatch):
    out = _run82(tmp_path, monkeypatch, {"2026-09-22": START + EARLY + START + CLOSE}, ["2026-09-22"])
    assert [s for s, _ in out] == ["CRITICAL"]
    assert "rc=143" in out[0][1] and "relaunched (2 starts)" in out[0][1]


def test_early_end_without_relaunch_says_so(tmp_path, monkeypatch):
    out = _run82(tmp_path, monkeypatch, {"2026-09-22": START + EARLY}, ["2026-09-22"])
    assert [s for s, _ in out] == ["CRITICAL"] and "no relaunch logged" in out[0][1]


def test_start_without_exit_is_critical(tmp_path, monkeypatch):
    out = _run82(tmp_path, monkeypatch, {"2026-09-22": START}, ["2026-09-22"])
    assert [s for s, _ in out] == ["CRITICAL"] and "no `exit` line" in out[0][1]


def test_missing_log_on_session_day_is_warning(tmp_path, monkeypatch):
    out = _run82(tmp_path, monkeypatch, {}, ["2026-09-22"])
    assert [s for s, _ in out] == ["WARNING"] and "never fired" in out[0][1]


def test_sessions_before_first_window_day_are_skipped(tmp_path, monkeypatch):
    assert _run82(tmp_path, monkeypatch, {}, ["2026-09-16", "2026-09-17"]) == []


def test_two_starts_clean_is_info(tmp_path, monkeypatch):
    out = _run82(tmp_path, monkeypatch, {"2026-09-22": START + START + CLOSE}, ["2026-09-22"])
    assert [s for s, _ in out] == ["INFO"]


def test_skipped_without_db(tmp_path, monkeypatch):
    from audit import _audit_core as core
    from audit import checks_gate as cg
    repo = tmp_path / "empty"
    repo.mkdir()
    monkeypatch.setattr(cg, "REPO", repo)
    monkeypatch.setattr(core, "REPO", repo)
    monkeypatch.setattr(core, "findings", [])
    monkeypatch.setattr(core, "skipped", [])
    cg.check82()
    assert core.findings == [] and any(s[0] == 82 for s in core.skipped)


def test_window_closed_before_1640_is_critical(tmp_path, monkeypatch):
    out = _run82(tmp_path, monkeypatch, {"2026-09-22": START + EARLYCLOSE}, ["2026-09-22"])
    assert [s for s, _ in out] == ["CRITICAL"] and "ET 1617" in out[0][1]


def test_legacy_close_line_without_clock_is_not_flagged(tmp_path, monkeypatch):
    legacy = "2026-09-22 22:17:24 CEST | window closed — SIGTERM 100\n2026-09-22 22:17:24 CEST | exit\n"
    assert _run82(tmp_path, monkeypatch, {"2026-09-22": START + legacy}, ["2026-09-22"]) == []
