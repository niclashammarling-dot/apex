"""
Item 6 (2026-10-01): a fresh positions read decides CONTRADICTION on the time-stop path.

The race no lock closes (09-30 audit): a bracket leg fills while the time stop
calls close_position(). The cycle-start snapshot still says held, and the
2026-09-01 rule turned that clean exit into a CONTRADICTION freeze. Now:
  snapshot held, re-read held        → CONTRADICTION (the 09-01 rule, unchanged)
  snapshot held, re-read gone + fill covering the whole position → booked
  snapshot held, re-read gone + no fill / partial fill → UNRECONCILED, not CONTRADICTION
  re-read fails                      → the snapshot stands (CONTRADICTION)
"""
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from backend.db import (
    get_db,
    get_open_live_trades,
    get_unreconciled_live_trades,
    init_db,
    insert_live_trade,
)

init_db()


@pytest.fixture(autouse=True)
def _clean_live_trades():
    yield
    conn = get_db()
    try:
        conn.execute("DELETE FROM live_trades")
        conn.execute("DELETE FROM alert_latches")
        conn.commit()
    finally:
        conn.close()


def _open_trade(ticker, qty=4.0):
    return insert_live_trade({
        "timestamp":       "2026-08-04T17:14:31+00:00",
        "ticker":          ticker,
        "sector":          "Industrials",
        "alpaca_order_id": f"ord-{ticker.lower()}-1",
        "entry_price":     100.0,
        "qty":             qty,
        "notional":        100.0 * qty,
        "tp_price":        106.0,
        "sl_price":        94.0,
    })


def _run_time_stop(ticker, positions, activities):
    """positions: get_positions side_effect (first call = cycle snapshot, second = re-read)."""
    with ExitStack() as stack:
        stack.enter_context(patch("backend.live_trades_tracker.LIVE_ENABLED", True))
        stack.enter_context(patch("backend.live_config.get_live_config", return_value={"max_hold_days": 0}))
        stack.enter_context(patch("backend.brokers.alpaca.get_positions", side_effect=positions))
        stack.enter_context(patch("backend.brokers.alpaca.get_order_by_id",
                                  return_value={"status": "filled", "filled_qty": 4, "legs": []}))
        stack.enter_context(patch("backend.brokers.alpaca.close_position",
                                  side_effect=Exception("position not found")))
        stack.enter_context(patch("backend.brokers.alpaca.get_orders", return_value=[]))
        stack.enter_context(patch("backend.brokers.alpaca.get_activities", return_value=activities))
        stack.enter_context(patch("backend.live_trades_tracker._current_price", return_value=None))
        alert = stack.enter_context(patch("backend.alerts.alert_position_unreconciled"))
        stack.enter_context(patch("backend.alerts.alert_position_untracked"))
        from backend.live_trades_tracker import check_live_exits
        closed = check_live_exits()
    return closed, alert


def _fill(ticker, qty):
    return [{"ticker": ticker, "side": "sell", "price": 105.0, "qty": qty,
             "filled_at": "2026-10-01T14:00:00+00:00"}]


def test_gone_on_reread_with_whole_fill_books_the_exit():
    tid = _open_trade("RACE")
    closed, alert = _run_time_stop("RACE", [[{"ticker": "RACE"}], []], _fill("RACE", 4.0))
    alert.assert_not_called()
    assert [c["ticker"] for c in closed] == ["RACE"] and closed[0]["pnl"] == 20.0
    assert tid not in [t["id"] for t in get_open_live_trades()]
    assert tid not in [t["id"] for t in get_unreconciled_live_trades()]


def test_gone_on_reread_with_no_fill_is_unreconciled_not_contradiction():
    tid = _open_trade("RACENOFILL")
    closed, alert = _run_time_stop("RACENOFILL", [[{"ticker": "RACENOFILL"}], []], [])
    assert closed == []
    alert.assert_called_once()
    note = alert.call_args[0][3]
    assert "CONTRADICTION" not in note and "CONFIRMED ABSENT" in note
    assert tid in [t["id"] for t in get_unreconciled_live_trades()]


def test_gone_on_reread_with_partial_fill_is_not_booked():
    tid = _open_trade("RACEPART")
    closed, alert = _run_time_stop("RACEPART", [[{"ticker": "RACEPART"}], []], _fill("RACEPART", 2.0))
    assert closed == []
    note = alert.call_args[0][3]
    assert "PARTIAL" in note and "2 of 4" in note and "NOT booked" in note
    frozen = [t for t in get_unreconciled_live_trades() if t["id"] == tid]
    assert frozen and frozen[0]["exit_price"] is None


def test_held_on_reread_is_still_a_contradiction():
    tid = _open_trade("STILLHELD")
    closed, alert = _run_time_stop("STILLHELD", [[{"ticker": "STILLHELD"}], [{"ticker": "STILLHELD"}]],
                                   _fill("STILLHELD", 4.0))
    assert closed == []
    note = alert.call_args[0][3]
    assert "CONTRADICTION" in note and "fresh re-read" in note and "NOT booked" in note
    assert tid in [t["id"] for t in get_unreconciled_live_trades()]


def test_failed_reread_leaves_the_snapshot_standing():
    tid = _open_trade("REREADFAIL")
    closed, alert = _run_time_stop("REREADFAIL", [[{"ticker": "REREADFAIL"}], Exception("timeout")],
                                   _fill("REREADFAIL", 4.0))
    assert closed == []
    note = alert.call_args[0][3]
    assert "CONTRADICTION" in note and "re-read failed" in note
    assert tid in [t["id"] for t in get_unreconciled_live_trades()]
