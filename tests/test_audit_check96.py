"""CHECK 96 — the market window's first start of a session is read against 08:15–08:30 ET (2026-11-07)."""


def _run96(tmp_path, monkeypatch, logs: dict[str, str], sessions: list[str]):
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
    cg.check96()
    return [(f[2], f[4]) for f in core.findings if f[0] == 96]


# The 2026-10-27 launch log line as written (the mismatch week, the control the scope names).
MISMATCH = "2026-10-27 14:20:04 CET | starting uvicorn (no --reload), ET now 0920\n"
NORMAL = "2026-11-05 14:20:01 CET | starting uvicorn (no --reload), ET now 0820\n"
RELAUNCH = "2026-11-05 19:53:10 CET | starting uvicorn (no --reload), ET now 1353\n"


def test_the_10_27_mismatch_start_warns(tmp_path, monkeypatch):
    out = _run96(tmp_path, monkeypatch, {"2026-10-27": MISMATCH}, ["2026-10-27"])
    assert len(out) == 1 and out[0][0] == "WARNING" and "ET 0920" in out[0][1]


def test_an_08_20_start_with_a_later_relaunch_is_silent(tmp_path, monkeypatch):
    # The first start is read, not a relaunch (CHECK 82 reads relaunches).
    assert _run96(tmp_path, monkeypatch, {"2026-11-05": NORMAL + RELAUNCH}, ["2026-11-05"]) == []


def test_a_day_without_a_log_is_left_to_check82(tmp_path, monkeypatch):
    assert _run96(tmp_path, monkeypatch, {}, ["2026-11-06"]) == []
