"""CHECK 90 — Lock 5 output after the 2026-10-19 prompt change (2026-10-17)."""
import sqlite3
from datetime import date


def _run90(tmp_path, monkeypatch, gate_rows, trade_rows, today):
    from audit import _audit_core as core
    from audit import checks_gate as cg
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True, exist_ok=True)
    (repo / "data/apex.db").unlink(missing_ok=True)
    conn = sqlite3.connect(repo / "data/apex.db")
    conn.execute("CREATE TABLE live_gate_history (timestamp TEXT, gate_decision TEXT, lock3_reasoning TEXT)")
    conn.execute("CREATE TABLE live_trades (timestamp TEXT, notional REAL)")
    conn.executemany("INSERT INTO live_gate_history VALUES (?, ?, ?)", gate_rows)
    conn.executemany("INSERT INTO live_trades VALUES (?, ?)", trade_rows)
    conn.commit(); conn.close()
    monkeypatch.setattr(cg, "REPO", repo)
    monkeypatch.setattr(core, "REPO", repo)
    monkeypatch.setattr(core, "findings", [])
    monkeypatch.setattr(core, "skipped", [])
    monkeypatch.setattr(core, "triggered", set())
    monkeypatch.setattr(cg, "flag", core.flag)
    cg.check90(today=today)
    return [(f[2], f[4]) for f in core.findings if f[0] == 90]


GATE = [("2026-10-01T15:00:00", "TRADE_EXECUTED", "ok"), ("2026-10-02T15:00:00", "FILTERED_L3", "hold"),
        ("2026-10-05T15:00:00", "FILTERED_L3", "anthropic_unavailable: credit_exhausted"),   # excluded
        ("2026-09-01T15:00:00", "TRADE_EXECUTED", "ok"),                                       # before window
        ("2026-10-20T15:00:00", "TRADE_EXECUTED", "ok"), ("2026-10-21T15:00:00", "TRADE_EXECUTED", "ok"),
        ("2026-10-21T16:00:00", "FILTERED_LEADING", None)]                                     # never reached L5
TRADES = [("2026-10-01T15:00:00", 1200.0), ("2026-10-20T15:00:00", 900.0), ("2026-10-21T15:00:00", 1100.0)]


def test_compares_before_and_after_and_excludes_fail_closed(tmp_path, monkeypatch):
    out = _run90(tmp_path, monkeypatch, GATE, TRADES, date(2026, 10, 23))
    assert len(out) == 1 and out[0][0] == "WARNING"
    assert "1/2 (50%) in the 4 weeks before 2026-10-19 vs 2/2 (100%) since" in out[0][1]
    assert "$1,200 (n=1) vs $1,000 (n=2)" in out[0][1]


def test_silent_before_the_change_and_info_after_the_review(tmp_path, monkeypatch):
    assert _run90(tmp_path, monkeypatch, GATE, TRADES, date(2026, 10, 18)) == []
    out = _run90(tmp_path, monkeypatch, GATE, TRADES, date(2026, 10, 26))
    assert [s for s, _ in out] == ["INFO"]
