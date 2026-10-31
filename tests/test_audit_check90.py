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
    conn.execute("CREATE TABLE live_gate_history (timestamp TEXT, lock3_pass INTEGER, stop_code TEXT)")
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


# Rows as stored from lock labels phase 2 (the history migrated the same way): Claude's approval is lock3_pass 1,
# Claude's rejection stop_code L6 claude; a fail-closed row (L6 unavailable) and a row that never reached L6 are
# not Claude's judgement.
APPROVED = (1, "TRADE_EXECUTED")
HOLD = (0, "FILTERED_L6_REVIEW_CLAUDE")
GATE = [("2026-10-01T15:00:00", *APPROVED), ("2026-10-02T15:00:00", *HOLD),
        ("2026-10-05T15:00:00", 0, "FILTERED_L6_REVIEW_UNAVAILABLE"),                         # excluded
        ("2026-09-01T15:00:00", *APPROVED),                                                    # before window
        ("2026-10-20T15:00:00", *APPROVED), ("2026-10-21T15:00:00", *APPROVED),
        ("2026-10-21T16:00:00", None, "FILTERED_L5_POSITIONING_LEADING")]                      # never reached L6
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


# The lock labels prompt (Niclas 10-26, review in two weeks): the check moves to the latest change, and
# its "before" window stops at the previous change instead of mixing two prompts.
GATE2 = GATE + [("2026-11-02T15:00:00", *HOLD), ("2026-11-03T15:00:00", *APPROVED),
                ("2026-11-03T16:00:00", 0, "FILTERED_L6_REVIEW_UNAVAILABLE")]
TRADES2 = TRADES + [("2026-11-03T15:00:00", 700.0)]


def test_the_lock_labels_prompt_is_read_against_the_window_since_the_previous_change(tmp_path, monkeypatch):
    out = _run90(tmp_path, monkeypatch, GATE2, TRADES2, date(2026, 11, 5))
    assert len(out) == 1 and out[0][0] == "WARNING"
    # 10-01/10-02 sit before the 10-19 change and stay out; 10-20 and 10-21 are the before-window
    assert "2/2 (100%) in the 14 days before 2026-11-02 vs 1/2 (50%) since" in out[0][1]
    assert "$1,000 (n=2) vs $700 (n=1)" in out[0][1]
    assert "Review 2026-11-09" in out[0][1] and "lock labels" in out[0][1]
    assert [s for s, _ in _run90(tmp_path, monkeypatch, GATE2, TRADES2, date(2026, 11, 10))] == ["INFO"]
