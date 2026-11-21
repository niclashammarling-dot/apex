"""CHECK 81 counts as lost only candidates whose stop was L6 REVIEW failing closed (R124 a, 2026-11-21).

Founding case: on 2026-11-17 six SPG rows carried `anthropic_unavailable: JSONDecodeError` while their stop was
L1's exit cooloff on a win (pre-rotation runs L3–L6 past L1, lock labels phase 4a M5); the 11-17/18 CRITICAL said
"candidates … were rejected because the Anthropic call failed" for rows no failure had decided.
"""
import sqlite3
from datetime import date

PRE = "anthropic_unavailable: JSONDecodeError"
COLS = ("timestamp TEXT, ticker TEXT, decision_reason TEXT, stop_code TEXT, gate_decision TEXT, "
        "l5_positioning_pass INTEGER")


def _run81(tmp_path, monkeypatch, live_rows, demo_rows=(), today=date(2026, 11, 18)):
    from audit import _audit_core as core
    from audit import checks_gate as cg
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True, exist_ok=True)
    (repo / "data/apex.db").unlink(missing_ok=True)
    conn = sqlite3.connect(repo / "data/apex.db")
    for t, rows in (("live_gate_history", live_rows), ("demo_gate_history", demo_rows)):
        conn.execute(f"CREATE TABLE {t} ({COLS})")
        conn.executemany(f"INSERT INTO {t} VALUES (?, ?, ?, ?, ?, ?)", rows)
    conn.commit(); conn.close()
    monkeypatch.setattr(cg, "REPO", repo)
    monkeypatch.setattr(core, "REPO", repo)
    monkeypatch.setattr(core, "findings", [])
    monkeypatch.setattr(core, "skipped", [])
    monkeypatch.setattr(core, "triggered", set())
    monkeypatch.setattr(cg, "flag", core.flag)
    cg.check81(today=today)
    return [(f[2], f[4]) for f in core.findings if f[0] == 81]


SPG_L1 = [(f"2026-11-17T{h}:08:00+00:00", "SPG", PRE, "FILTERED_L1_ELIGIBILITY_EXIT_COOLOFF_WIN",
           "FILTERED_L1_ELIGIBILITY", 1) for h in (16, 19, 20)]
DLR_L6 = [("2026-11-17T15:08:37+00:00", "DLR", PRE, "FILTERED_L6_REVIEW_UNAVAILABLE", "FILTERED_L6_REVIEW", 1)]


def test_rows_an_earlier_rule_stopped_are_info_not_critical(tmp_path, monkeypatch):
    out = _run81(tmp_path, monkeypatch, SPG_L1)
    assert [s for s, _ in out] == ["INFO"]
    assert "no candidate lost" in out[0][1] and "SPG FILTERED_L1_ELIGIBILITY_EXIT_COOLOFF_WIN" in out[0][1]


def test_an_l6_stop_is_critical_and_names_only_the_lost_candidate(tmp_path, monkeypatch):
    out = _run81(tmp_path, monkeypatch, SPG_L1 + DLR_L6)
    sev = sorted(s for s, _ in out)
    assert sev == ["CRITICAL", "INFO"]
    crit = next(d for s, d in out if s == "CRITICAL")
    assert crit.startswith("1 L6 REVIEW fail-closed row(s)") and "DLR" in crit and "SPG" not in crit


def test_a_row_without_stop_code_counts_by_its_lock_code(tmp_path, monkeypatch):
    old = [("2026-11-17T15:00:00+00:00", "VTR", PRE, None, "FILTERED_L6_REVIEW", 1)]
    out = _run81(tmp_path, monkeypatch, old)
    assert [s for s, _ in out] == ["CRITICAL"]


def test_outside_the_event_window_only_lost_rows_reach_the_tally(tmp_path, monkeypatch):
    out = _run81(tmp_path, monkeypatch, SPG_L1 + DLR_L6, today=date(2026, 11, 25))
    assert [s for s, _ in out] == ["INFO"]
    assert out[0][1].startswith("1 fail-closed row(s) in the last 30 days")


def test_silent_without_prefixed_rows(tmp_path, monkeypatch):
    ok = [("2026-11-17T15:00:00+00:00", "AMT", "pass", "TRADE_EXECUTED", "TRADE_EXECUTED", 1)]
    assert _run81(tmp_path, monkeypatch, ok) == []
