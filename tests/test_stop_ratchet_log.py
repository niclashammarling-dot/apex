"""Stop ratchet log (ruling Niclas 2026-10-21): every confirmed live stop move is appended, the
table refuses UPDATE and DELETE, a failed insert never blocks the ratchet, and CHECK 93 reads the
log for completeness. Trade ids are unique per test: the session's temp DB cannot be cleaned,
by design."""
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from audit import _audit_core as core
from audit import checks_gate as cg
from backend import db


def _rows(trade_id):
    conn = db.get_db()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM stop_ratchet_log WHERE trade_id = ? ORDER BY id", (trade_id,))]
    finally:
        conn.close()


def test_append_then_update_and_delete_are_refused():
    db.append_stop_ratchet(910001, "AAA", 94.0, 103.95, 105.0, "leg-1", "leg-2", moved_at="2026-10-21T14:00:00+00:00")
    db.append_stop_ratchet(910001, "AAA", 103.95, 104.94, 106.0, "leg-2", "leg-3", moved_at="2026-10-21T14:05:00+00:00")
    rows = _rows(910001)
    assert [(r["old_stop"], r["new_stop"], r["new_leg_id"]) for r in rows] == [(94.0, 103.95, "leg-2"), (103.95, 104.94, "leg-3")]
    conn = db.get_db()
    try:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("UPDATE stop_ratchet_log SET new_stop = 1 WHERE trade_id = 910001")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("DELETE FROM stop_ratchet_log WHERE trade_id = 910001")
    finally:
        conn.close()
    first, last = db.stop_ratchet_summary()
    assert last[910001] == 104.94 and first <= "2026-10-21T14:00:00+00:00"


def _ratchet(trade_id, replace=None, append=None):
    from backend.live_trades_tracker import _maybe_ratchet_bracket_sl
    broker = MagicMock()
    broker.get_order_by_id.return_value = {"legs": [
        {"id": "leg-live", "order_type": "stop", "status": "held", "stop_price": 94.0}]}
    broker.replace_stop_leg.side_effect = replace or (lambda *a: "leg-new")
    trade = {"ticker": "BBB", "id": trade_id, "alpaca_order_id": "parent", "entry_price": 100.0, "qty": 10}
    cfg = {"profit_lock_trigger_pct": 0.04, "profit_lock_trail_pct": 0.01}
    with patch("backend.live_trades_tracker.set_live_trade_profit_lock_activated") as act:
        if append:
            with patch("backend.live_trades_tracker.append_stop_ratchet", side_effect=append):
                _maybe_ratchet_bracket_sl(trade, 105.0, cfg, broker)
        else:
            _maybe_ratchet_bracket_sl(trade, 105.0, cfg, broker)
    return act


def test_confirmed_move_is_logged_with_both_legs():
    _ratchet(910002)
    (r,) = _rows(910002)
    assert (r["ticker"], r["old_stop"], r["new_stop"], r["peak"], r["old_leg_id"], r["new_leg_id"]) == \
        ("BBB", 94.0, 103.95, 105.0, "leg-live", "leg-new")


def test_failed_replace_logs_nothing():
    _ratchet(910003, replace=RuntimeError("500"))
    assert _rows(910003) == []


def test_failed_insert_never_blocks_the_ratchet():
    act = _ratchet(910004, append=sqlite3.OperationalError("locked"))
    act.assert_called_once_with(910004)


def _c93(monkeypatch, trades, orders, log):
    monkeypatch.setattr(core, "findings", [])
    monkeypatch.setattr(cg, "flag", core.flag)
    cg.check93(fetch=(trades, lambda oid: orders[oid], lambda t, d: None, log))
    return [(f[2], f[4]) for f in core.findings if f[0] == 93]


def _leg(stop):
    return {"legs": [{"order_type": "stop", "stop_price": stop, "filled_avg_price": stop - 0.02,
                      "filled_at": "2026-10-22T15:30:00+00:00"}]}


def test_check93_flags_a_tsl_exit_missing_from_the_log(monkeypatch):
    log = ("2026-10-21T14:00:00+00:00", {1: 104.94})
    trades = [{"id": 1, "ticker": "OK", "exit_reason": "TSL", "alpaca_order_id": "a", "timestamp": "2026-10-21T15:00:00"},
              {"id": 2, "ticker": "GONE", "exit_reason": "TSL", "alpaca_order_id": "b", "timestamp": "2026-10-21T15:00:00"},
              {"id": 3, "ticker": "OLD", "exit_reason": "TSL", "alpaca_order_id": "c", "timestamp": "2026-10-01T15:00:00"}]
    out = _c93(monkeypatch, trades, {"a": _leg(104.94), "b": _leg(50.0), "c": _leg(80.0)}, log)
    warn = [m for s, m in out if s == "WARNING"]
    assert len(warn) == 1 and "1 TSL exits: GONE #2 leg 50.00 log none" in warn[0]
    info = [m for s, m in out if s == "INFO"][0]
    assert "ratchet log since 2026-10-21: 1 TSL exits matched, 1 not" in info


def test_check93_flags_a_logged_stop_that_differs_from_the_fill_leg(monkeypatch):
    log = ("2026-10-21T14:00:00+00:00", {1: 103.95})
    trades = [{"id": 1, "ticker": "MOVED", "exit_reason": "TSL", "alpaca_order_id": "a", "timestamp": "2026-10-21T15:00:00"}]
    out = _c93(monkeypatch, trades, {"a": _leg(104.94)}, log)
    assert any(s == "WARNING" and "MOVED #1 leg 104.94 log 103.95" in m for s, m in out)


def test_check93_with_an_empty_log_says_so(monkeypatch):
    trades = [{"id": 1, "ticker": "X", "exit_reason": "TSL", "alpaca_order_id": "a", "timestamp": "2026-10-21T15:00:00"}]
    out = _c93(monkeypatch, trades, {"a": _leg(104.94)}, (None, {}))
    assert [s for s, _ in out] == ["INFO"] and "ratchet log: no rows yet" in out[0][1]
