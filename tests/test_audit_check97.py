"""CHECK 97 — an open demo trade whose last priced exit check is older than 3 cycles on a session day warns
(2026-11-07, the rate count beside the demo exit catch-up)."""
import sqlite3
from datetime import datetime, timedelta, timezone

NOW = datetime(2026, 10, 22, 19, 0, tzinfo=timezone.utc)          # a session day, 15:00 EDT


def _run97(tmp_path, monkeypatch, trades, now=NOW):
    from audit import _audit_core as core
    from audit import checks_gate as cg
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    conn = sqlite3.connect(repo / "data/apex.db")
    conn.execute("CREATE TABLE trades (id INTEGER, ticker TEXT, timestamp TEXT, outcome TEXT, exits_checked_at TEXT)")
    conn.executemany("INSERT INTO trades VALUES (?, ?, ?, 'OPEN', ?)", trades)
    conn.commit()
    conn.close()
    monkeypatch.setattr(cg, "REPO", repo)
    monkeypatch.setattr(core, "REPO", repo)
    monkeypatch.setattr(core, "findings", [])
    monkeypatch.setattr(cg, "flag", core.flag)
    cg.check97(now=now)
    return [f[4] for f in core.findings if f[0] == 97]


def _iso(minutes_before):
    return (NOW - timedelta(minutes=minutes_before)).isoformat()


def test_the_10_22_shape_warns(tmp_path, monkeypatch):
    # DNS outage: the last priced check 2.5 h before, the trade open since the morning
    out = _run97(tmp_path, monkeypatch, [(1, "AAA", _iso(400), _iso(150))])
    assert len(out) == 1 and "demo AAA" in out[0] and "could not fire" in out[0]


def test_a_fresh_stamp_and_a_young_trade_are_silent(tmp_path, monkeypatch):
    assert _run97(tmp_path, monkeypatch, [(1, "AAA", _iso(400), _iso(5)), (2, "BBB", _iso(5), None)]) == []


def test_never_stamped_warns(tmp_path, monkeypatch):
    out = _run97(tmp_path, monkeypatch, [(1, "AAA", _iso(400), None)])
    assert len(out) == 1 and "never stamped" in out[0]


def test_a_non_session_day_is_silent(tmp_path, monkeypatch):
    saturday = datetime(2026, 11, 7, 19, 0, tzinfo=timezone.utc)
    assert _run97(tmp_path, monkeypatch, [(1, "AAA", "2026-11-05T15:00:00+00:00", None)], now=saturday) == []
