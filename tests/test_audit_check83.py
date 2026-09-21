"""CHECK 83 — two schedulers on one book: off-phase gate cycles on either table."""
import sqlite3
from datetime import date


def _run83(tmp_path, monkeypatch, live_minutes, demo_minutes):
    from audit import _audit_core as core
    from audit import checks_gate as cg
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    conn = sqlite3.connect(repo / "data/apex.db")
    for t, mins in (("live_gate_history", live_minutes), ("demo_gate_history", demo_minutes)):
        conn.execute(f"CREATE TABLE {t} (timestamp TEXT, ticker TEXT)")
        conn.executemany(f"INSERT INTO {t} VALUES (?, ?)",
                         [(f"{date.today().isoformat()}T{m}:{s}+00:00", "X") for m in mins for s in ("05", "44")])
    conn.commit(); conn.close()
    monkeypatch.setattr(cg, "REPO", repo)
    monkeypatch.setattr(core, "REPO", repo)
    monkeypatch.setattr(core, "findings", [])
    monkeypatch.setattr(core, "skipped", [])
    monkeypatch.setattr(core, "triggered", set())
    monkeypatch.setattr(cg, "flag", core.flag)
    cg.check83()
    return [(f[2], f[4]) for f in core.findings if f[0] == 83]


ONE = ["14:34", "14:54", "15:14", "15:34"]                       # one 20-min scheduler
TWO = ["14:34", "14:37", "14:54", "14:57", "15:14", "15:17"]     # a second phase 3 min behind


def test_single_scheduler_is_silent(tmp_path, monkeypatch):
    assert _run83(tmp_path, monkeypatch, ONE, ONE) == []


def test_cycle_straddling_a_minute_boundary_is_not_a_second_phase(tmp_path, monkeypatch):
    assert _run83(tmp_path, monkeypatch, ["14:34", "14:35", "14:54"], ONE) == []


def test_second_phase_is_critical_and_names_the_table(tmp_path, monkeypatch):
    out = _run83(tmp_path, monkeypatch, ONE, TWO)
    assert [s for s, _ in out] == ["CRITICAL"]
    assert out[0][1].startswith("demo:") and "3 off-phase" in out[0][1] and "14:34→14:37 (3m)" in out[0][1]


def test_both_tables_flag_separately(tmp_path, monkeypatch):
    out = _run83(tmp_path, monkeypatch, TWO, TWO)
    assert [m[:4] for _, m in out] == ["live", "demo"]
