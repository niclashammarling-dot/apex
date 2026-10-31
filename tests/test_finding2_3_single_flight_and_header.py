"""
Finding 2 and 3 (2026-10-07 review, built 2026-10-31): one live gate run at a time (runner file lock and a
partial unique index on held live positions), and a custom header on mutating /api routes.
"""
import fcntl
import sqlite3

import pytest

from backend import db
from backend.gate import gate_runner_live as glive
from backend.gate.cycle import GateCycle


def test_a_held_lock_makes_the_run_busy_and_places_nothing(monkeypatch):
    called = []
    monkeypatch.setattr(glive, "_run", lambda cycle: called.append(cycle) or ["ran"])
    path = glive.live_gate_lock_path()
    assert path.parent == db.DB_PATH.parent                      # the test DB's directory, never production's
    with open(path, "w") as other:                               # a second run (another thread or backend)
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        cycle = GateCycle.unrecorded()
        assert glive.run(cycle) == []
        assert cycle.reason == "busy" and called == []
    assert glive.run(GateCycle.unrecorded()) == ["ran"]         # released: the next run goes through
    assert len(called) == 1


def _trade(ticker, outcome="OPEN"):
    tid = db.insert_live_trade({"timestamp": "2026-10-31T14:00:00+00:00", "ticker": ticker, "sector": "Energy",
                                "alpaca_order_id": f"o-{ticker}-{outcome}", "entry_price": 10.0, "qty": 1,
                                "notional": 10.0, "tp_price": 10.6, "sl_price": 9.4})
    if outcome != "OPEN":
        conn = db.get_db()
        conn.execute("UPDATE live_trades SET outcome = ? WHERE id = ?", (outcome, tid))
        conn.commit()
        conn.close()
    return tid


def _clean(*tickers):
    conn = db.get_db()
    conn.executemany("DELETE FROM live_trades WHERE ticker = ?", [(t,) for t in tickers])
    conn.commit()
    conn.close()


def test_the_index_refuses_a_second_held_row_and_allows_closed_ones():
    db.init_db()
    try:
        _trade("ZZF2", "WIN")
        _trade("ZZF2")                                           # a closed row and one held row: fine
        with pytest.raises(sqlite3.IntegrityError):
            _trade("ZZF2")
        conn = db.get_db()
        conn.execute("UPDATE live_trades SET outcome = 'UNRECONCILED' WHERE ticker = 'ZZF2' AND outcome = 'OPEN'")
        conn.commit()
        conn.close()
        with pytest.raises(sqlite3.IntegrityError):              # UNRECONCILED is still held
            _trade("ZZF2")
    finally:
        _clean("ZZF2")


def test_a_refused_duplicate_is_logged_critical_not_raised(monkeypatch):
    db.init_db()
    seen = []
    monkeypatch.setattr(glive.logger, "critical", lambda m: seen.append(m))
    try:
        _trade("ZZF3")
        glive._record_live_trade({"ticker": "ZZF3", "price": 10.0, "sector": "Energy"}, 10.0, "o-dup",
                                 {"take_profit_pct": 0.06, "stop_loss_pct": 0.06})
        assert len(seen) == 1 and "o-dup" in seen[0]
    finally:
        _clean("ZZF3")


def test_existing_duplicates_leave_the_start_running(monkeypatch):
    db.init_db()
    conn = db.get_db()
    conn.execute(f"DROP INDEX {db.LIVE_OPEN_TICKER_INDEX}")
    conn.commit()
    conn.close()
    seen = []
    monkeypatch.setattr(db.logger, "critical", lambda m: seen.append(m))
    try:
        _trade("ZZF4")
        _trade("ZZF4")
        db.init_db()                                             # must not raise
        assert seen and db.LIVE_OPEN_TICKER_INDEX in seen[0]
    finally:
        _clean("ZZF4")
        db.init_db()


def test_mutating_api_calls_need_the_client_header():
    from fastapi.testclient import TestClient
    from backend.main import CLIENT_HEADER, app
    c = TestClient(app)                                          # no context manager: the lifespan does not run
    assert c.post("/api/no-such-route").status_code == 403
    assert c.delete("/api/watchlist/ZZZ").status_code == 403
    assert c.post("/api/no-such-route", headers={CLIENT_HEADER: "dashboard"}).status_code == 404
    assert c.post("/api/no-such-route", headers={CLIENT_HEADER: "x"}).status_code == 403
    assert c.get("/api/no-such-route").status_code == 404        # reads are untouched
