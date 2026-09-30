"""CHECK 84 — Lock 5 decided on a stale position count (cycle-start state)."""
import sqlite3
from datetime import date, timedelta

T = date.today().isoformat()
OLD = (date.today() - timedelta(days=2)).isoformat()


def _run84(tmp_path, monkeypatch, gate_rows, trades):
    from audit import _audit_core as core
    from audit import checks_gate as cg
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    conn = sqlite3.connect(repo / "data/apex.db")
    conn.execute("CREATE TABLE live_gate_history (timestamp TEXT, ticker TEXT, gate_decision TEXT, lock3_reasoning TEXT)")
    conn.execute("CREATE TABLE live_trades (id INTEGER PRIMARY KEY, ticker TEXT, timestamp TEXT, exited_at TEXT)")
    conn.executemany("INSERT INTO live_gate_history VALUES (?, ?, 'TRADE_EXECUTED', ?)", gate_rows)
    conn.executemany("INSERT INTO live_trades (ticker, timestamp, exited_at) VALUES (?, ?, ?)", trades)
    conn.commit(); conn.close()
    monkeypatch.setattr(cg, "REPO", repo)
    monkeypatch.setattr(core, "REPO", repo)
    monkeypatch.setattr(core, "findings", [])
    monkeypatch.setattr(core, "skipped", [])
    monkeypatch.setattr(core, "triggered", set())
    monkeypatch.setattr(cg, "flag", core.flag)
    cg.check84()
    return [(f[2], f[4]) for f in core.findings if f[0] == 84]


def _book(day, n):
    """n positions opened days earlier, still open."""
    return [(f"H{i}", f"{day}T10:00:0{i}+00:00", None) for i in range(n)]


SAYS7 = "Portfolio risk checks pass: 7 of 8 max positions used (one slot available)"


def test_the_0929_shape_second_same_cycle_execution_is_named(tmp_path, monkeypatch):
    # 7 open at cycle start; KLAC executes first, LRCX second — both cite 7.
    trades = _book(OLD, 7) + [("KLAC", f"{T}T14:54:00+00:00", None), ("LRCX", f"{T}T14:54:08+00:00", None)]
    gate = [(f"{T}T14:53:53+00:00", "KLAC", SAYS7), (f"{T}T14:53:49+00:00", "LRCX", SAYS7)]
    out = _run84(tmp_path, monkeypatch, gate, trades)
    assert [s for s, _ in out] == ["WARNING"]
    assert "LRCX: cited 7, actual 8" in out[0][1] and "KLAC" not in out[0][1]


def test_single_execution_with_true_count_is_silent(tmp_path, monkeypatch):
    trades = _book(OLD, 7) + [("KLAC", f"{T}T14:54:00+00:00", None)]
    assert _run84(tmp_path, monkeypatch, [(f"{T}T14:53:53+00:00", "KLAC", SAYS7)], trades) == []


def test_exit_before_execution_is_not_counted_as_open(tmp_path, monkeypatch):
    trades = _book(OLD, 7) + [("ADI", f"{OLD}T09:00:00+00:00", f"{T}T14:42:01+00:00"),
                              ("KLAC", f"{T}T14:54:00+00:00", None)]
    assert _run84(tmp_path, monkeypatch, [(f"{T}T14:53:53+00:00", "KLAC", SAYS7)], trades) == []


def test_uncited_execution_is_tallied_not_warned(tmp_path, monkeypatch):
    trades = _book(OLD, 3) + [("X", f"{T}T14:54:00+00:00", None)]
    out = _run84(tmp_path, monkeypatch, [(f"{T}T14:53:53+00:00", "X", "Strong signal, clear risk.")], trades)
    assert [s for s, _ in out] == ["INFO"] and "1 execution(s) cite no count" in out[0][1]


def test_open_positions_form_is_parsed(tmp_path, monkeypatch):
    from audit.checks_gate import _c84_cited
    assert _c84_cited("Hard rejections: open_positions (8) equals max_positions (8)") == 8
    assert _c84_cited(SAYS7) == 7
    assert _c84_cited(None) is None
