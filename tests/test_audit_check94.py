"""CHECK 94 — stored gate codes are the register's (lock labels phase 2, scope step 6 and ruling B)."""
import sqlite3
from datetime import date

TODAY = date(2026, 11, 3)


def _run94(tmp_path, monkeypatch, rows, migrated_at="2026-10-31T10:00:00+00:00"):
    from audit import _audit_core as core
    from audit import checks_gate as cg
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True, exist_ok=True)
    (repo / "data/apex.db").unlink(missing_ok=True)
    conn = sqlite3.connect(repo / "data/apex.db")
    for t in ("live_gate_history", "demo_gate_history", "signals"):
        conn.execute(f"CREATE TABLE {t} (ticker TEXT, timestamp TEXT, gate_decision TEXT, stop_code TEXT)")
    if migrated_at:
        conn.execute("CREATE TABLE gate_code_migration (migrated_at TEXT)")
        conn.execute("INSERT INTO gate_code_migration VALUES (?)", (migrated_at,))
    conn.executemany("INSERT INTO live_gate_history VALUES (?, ?, ?, ?)", rows)
    conn.commit(); conn.close()
    monkeypatch.setattr(cg, "REPO", repo)
    monkeypatch.setattr(core, "REPO", repo)
    monkeypatch.setattr(core, "findings", [])
    monkeypatch.setattr(core, "skipped", [])
    monkeypatch.setattr(core, "triggered", set())
    monkeypatch.setattr(cg, "flag", core.flag)
    cg.check94(today=TODAY)
    return [(f[2], f[4]) for f in core.findings if f[0] == 94]


CLEAN = [("AAA", "2026-10-20T15:00:00", "FILTERED_L1_ELIGIBILITY", "FILTERED_L1_ELIGIBILITY_REGIME"),
         ("BBB", "2026-10-20T15:00:00", "FILTERED_L4_SENTIMENT", "FILTERED_L4_SENTIMENT_UNAVAILABLE_INFERRED"),
         ("CCC", "2026-11-02T15:00:00", "TRADE_EXECUTED", "TRADE_EXECUTED"),
         ("DDD", "2026-08-01T15:00:00", "FILTERED_UNKNOWN", "FILTERED_UNKNOWN")]    # history's unknowns never fire


def test_a_clean_table_is_silent(tmp_path, monkeypatch):
    assert _run94(tmp_path, monkeypatch, CLEAN) == []


def test_a_legacy_code_after_the_migration_is_critical(tmp_path, monkeypatch):
    out = _run94(tmp_path, monkeypatch, CLEAN + [("EEE", "2026-11-02T15:00:00", "FILTERED_L3", None)])
    assert any(s == "CRITICAL" and "outside the register: FILTERED_L3 ×1" in m for s, m in out)


def test_a_stop_code_that_disagrees_is_critical(tmp_path, monkeypatch):
    out = _run94(tmp_path, monkeypatch, CLEAN + [
        ("FFF", "2026-11-02T15:00:00", "FILTERED_L2_RISK", "FILTERED_L1_ELIGIBILITY_COOLOFF"),
        ("GGG", "2026-11-02T15:00:00", "FILTERED_L5_POSITIONING", None),
        ("HHH", "2026-11-02T15:00:00", "TRADE_EXECUTED", "FILTERED_L6_REVIEW_CLAUDE")])
    msg = next(m for s, m in out if "disagrees" in m)
    assert "FILTERED_L2_RISK/FILTERED_L1_ELIGIBILITY_COOLOFF" in msg and "FILTERED_L5_POSITIONING/None" in msg
    assert "TRADE_EXECUTED/FILTERED_L6_REVIEW_CLAUDE" in msg


def test_a_history_only_rule_on_a_new_row_is_critical(tmp_path, monkeypatch):
    out = _run94(tmp_path, monkeypatch, CLEAN + [
        ("III", "2026-11-02T15:00:00", "FILTERED_L6_REVIEW", "FILTERED_L6_REVIEW_UNAVAILABLE_INFERRED")])
    assert any("history-only rule" in m and "FILTERED_L6_REVIEW_UNAVAILABLE_INFERRED ×1" in m for _, m in out)


def test_a_new_unknown_row_is_critical_as_the_mail_backstop(tmp_path, monkeypatch):
    out = _run94(tmp_path, monkeypatch, CLEAN + [("JJJ", "2026-11-03T14:00:00", "FILTERED_UNKNOWN", "FILTERED_UNKNOWN")])
    assert [s for s, m in out if "FILTERED_UNKNOWN row(s)" in m] == ["CRITICAL"]
    assert any("JJJ 2026-11-03T14:00" in m for _, m in out)
