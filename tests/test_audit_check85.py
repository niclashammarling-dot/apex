"""CHECK 85 — session flags reconcile with computed coverage (2026-10-03)."""
import sqlite3


def _run85(tmp_path, monkeypatch, demo_cycles: int, flag: tuple | None, day="2026-10-02"):
    from audit import _audit_core as core
    from audit import checks_gate as cg
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    conn = sqlite3.connect(repo / "data/apex.db")
    conn.execute("CREATE TABLE demo_gate_history (timestamp TEXT, cycle_started_at TEXT)")
    conn.execute("CREATE TABLE session_flags (date TEXT, kind TEXT, status TEXT, cause TEXT, source TEXT, "
                 "cycles INT, expected INT, live_cycles INT, live_expected INT, first_seen_at TEXT, finalized_at TEXT)")
    for k in range(demo_cycles):                       # demo grid :41/:01/:21 from 13:41 UTC
        m = 13 * 60 + 41 + 20 * k
        ts = f"{day}T{m // 60:02d}:{m % 60:02d}:05+00:00"
        conn.execute("INSERT INTO demo_gate_history VALUES (?, ?)", (ts, ts))
    if flag:
        conn.execute("INSERT INTO session_flags (date, kind, status, cause, source, first_seen_at) "
                     "VALUES (?, 'partial', ?, ?, 'cycle_watch', 'x')", (day, flag[0], flag[1]))
    conn.commit()
    conn.close()
    monkeypatch.setattr(cg, "REPO", repo)
    monkeypatch.setattr(core, "REPO", repo)
    monkeypatch.setattr(core, "findings", [])
    monkeypatch.setattr(core, "skipped", [])
    monkeypatch.setattr(core, "triggered", set())
    monkeypatch.setattr(cg, "flag", core.flag)
    monkeypatch.setattr(cg, "_c82_session_dates", lambda n: [day])
    monkeypatch.setattr(cg, "_c82_open_day", lambda: None)
    cg.check85()
    return [(f[2], f[4]) for f in core.findings if f[0] == 85]


def test_low_coverage_without_flag_is_a_missed_outage(tmp_path, monkeypatch):
    """10-02 as it stood before the seed: 8/19 and no flag."""
    out = _run85(tmp_path, monkeypatch, 8, None)
    assert [s for s, _ in out] == ["WARNING"] and "8/19" in out[0][1] and "no session flag" in out[0][1]


def test_final_flag_on_low_coverage_is_clean(tmp_path, monkeypatch):
    assert _run85(tmp_path, monkeypatch, 8, ("final", "console close")) == []


def test_flag_on_full_coverage_is_questioned(tmp_path, monkeypatch):
    out = _run85(tmp_path, monkeypatch, 19, ("final", "short blip"))
    assert [s for s, _ in out] == ["WARNING"] and "flag is wrong" in out[0][1]


def test_provisional_flag_on_closed_session_is_warning(tmp_path, monkeypatch):
    out = _run85(tmp_path, monkeypatch, 8, ("provisional", "console close"))
    assert [s for s, _ in out] == ["WARNING"] and "still provisional" in out[0][1]


def test_full_day_without_flag_is_clean(tmp_path, monkeypatch):
    assert _run85(tmp_path, monkeypatch, 19, None) == []
