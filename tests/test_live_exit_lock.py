"""
Item 5 (2026-10-01): the live exit lock and close_live_trade's OPEN guard.

Two callers reach scheduler.check_live_exit_conditions: the 5-min interval job
and the startup catch-up _check_missed_live_exits, which since bdbedb2 (09-30)
runs alongside the jobs. The 09-21 incident (a second backend running its own
scheduler against the same book, three live orders) is the precedent for two
exit passes on one book. In-process, EXIT_LOCK serialises the passes; across
processes, close_live_trade's `AND outcome = 'OPEN'` is the guard, and its
rowcount tells the caller whether it actually booked.
"""
import threading
import time
from contextlib import ExitStack, nullcontext
from unittest.mock import patch

import pytest

from backend.db import (
    close_live_trade,
    get_db,
    init_db,
    insert_live_trade,
    mark_live_trade_unreconciled,
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


def _open_trade(ticker="LOCK", entry_price=100.0, qty=4.0, order_id="ord-lock-1"):
    return insert_live_trade({
        "timestamp":       "2026-09-30T14:00:00+00:00",
        "ticker":          ticker,
        "sector":          "Industrials",
        "alpaca_order_id": order_id,
        "entry_price":     entry_price,
        "qty":             qty,
        "notional":        entry_price * qty,
        "tp_price":        entry_price * 1.06,
        "sl_price":        entry_price * 0.94,
    })


def _row(trade_id):
    conn = get_db()
    try:
        return dict(conn.execute("SELECT * FROM live_trades WHERE id = ?", (trade_id,)).fetchone())
    finally:
        conn.close()


class TestCloseLiveTradeOpenGuard:

    def test_books_an_open_row_and_returns_one(self):
        tid = _open_trade()
        assert close_live_trade(tid, 106.0, 24.0, "WIN", "TP", "2026-10-01T14:00:00+00:00") == 1
        assert _row(tid)["outcome"] == "WIN"

    def test_second_booking_returns_zero_warns_and_keeps_the_first(self):
        tid = _open_trade()
        close_live_trade(tid, 106.0, 24.0, "WIN", "TP", "2026-10-01T14:00:00+00:00")
        with patch("backend.db.logger.warning") as warn:
            n = close_live_trade(tid, 94.0, -24.0, "LOSS", "TIME", "2026-10-01T14:00:05+00:00")
        assert n == 0
        row = _row(tid)
        assert (row["outcome"], row["exit_price"], row["exit_reason"]) == ("WIN", 106.0, "TP")
        assert warn.call_count == 1 and "NOT booked" in warn.call_args[0][0]

    def test_unreconciled_freeze_is_not_overwritten_by_a_booking(self):
        tid = _open_trade()
        mark_live_trade_unreconciled(tid, "test freeze")
        assert close_live_trade(tid, 106.0, 24.0, "WIN", "TP", "2026-10-01T14:00:00+00:00") == 0
        row = _row(tid)
        assert row["outcome"] == "UNRECONCILED" and row["exit_price"] is None


class TestStaleSnapshotBooking:

    def test_pass_whose_row_was_booked_meanwhile_does_not_report_a_close(self):
        """
        The cross-process shape: this pass read the row OPEN, and another
        process booked it before this pass's own booking. The second booking
        must not land, and the trade must not appear in this pass's `closed`
        (which feeds logs and the regime-exit alert).
        """
        tid = _open_trade(ticker="STALE", order_id="ord-stale-1")
        filled_tp = {"side": "sell", "status": "filled", "order_type": "limit",
                     "filled_avg_price": 106.0, "filled_at": "2026-10-01T14:00:00+00:00"}

        def order_after_other_process_booked(order_id):
            close_live_trade(tid, 106.0, 24.0, "WIN", "TP", "2026-10-01T14:00:00+00:00")
            return {"status": "filled", "filled_qty": 4, "legs": [filled_tp]}

        with ExitStack() as stack:
            stack.enter_context(patch("backend.live_trades_tracker.LIVE_ENABLED", True))
            stack.enter_context(patch("backend.live_config.get_live_config",
                                      return_value={"max_hold_days": 25}))
            stack.enter_context(patch("backend.brokers.alpaca.get_positions", return_value=[]))
            stack.enter_context(patch("backend.brokers.alpaca.get_order_by_id",
                                      side_effect=order_after_other_process_booked))
            stack.enter_context(patch("backend.live_trades_tracker._current_price", return_value=None))
            stack.enter_context(patch("backend.live_trades_tracker._log_vol_slope"))
            stack.enter_context(patch("backend.alerts.alert_position_untracked"))

            from backend.live_trades_tracker import check_live_exits
            closed = check_live_exits()

        assert closed == []
        assert _row(tid)["outcome"] == "WIN"


def _run_two_concurrent_passes(lock):
    """Two callers into check_live_exit_conditions at once; returns peak overlap.
    lock=None runs the shipped EXIT_LOCK."""
    active, peak, guard = [0], [0], threading.Lock()
    start = threading.Barrier(2)

    def inner_pass():
        with guard:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.2)
        with guard:
            active[0] -= 1
        return []

    with ExitStack() as stack:
        stack.enter_context(patch("backend.scheduler.is_market_open", return_value=True))
        if lock is not None:
            stack.enter_context(patch("backend.live_trades_tracker.EXIT_LOCK", lock))
        stack.enter_context(patch("backend.live_trades_tracker.cancel_orphan_brackets", return_value=0))
        stack.enter_context(patch("backend.live_trades_tracker.check_live_exits", side_effect=inner_pass))
        stack.enter_context(patch("backend.live_trades_tracker.check_live_regime_exits", return_value=[]))
        from backend.scheduler import check_live_exit_conditions

        def caller():
            start.wait()
            check_live_exit_conditions()

        threads = [threading.Thread(target=caller) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
    return peak[0]


class TestTwoConcurrentCallers:

    def test_positive_control_without_the_lock_the_passes_overlap(self):
        """Holds the guard open: proves this harness can see an overlap at all."""
        assert _run_two_concurrent_passes(nullcontext()) == 2

    def test_exit_lock_serialises_interval_job_and_startup_catchup(self):
        assert _run_two_concurrent_passes(None) == 1


class TestUnreconciledFreezeOpenGuard:
    """2026-10-02: the mirror of close_live_trade's guard — a freeze lands on OPEN rows only."""

    def test_freezes_an_open_row_and_returns_one(self):
        tid = _open_trade()
        assert mark_live_trade_unreconciled(tid, "test freeze") == 1
        assert _row(tid)["outcome"] == "UNRECONCILED"

    def test_booked_exit_is_not_overwritten_by_a_freeze(self):
        tid = _open_trade()
        close_live_trade(tid, 106.0, 24.0, "WIN", "TP", "2026-10-01T14:00:00+00:00")
        with patch("backend.db.logger.warning") as warn:
            n = mark_live_trade_unreconciled(tid, "late freeze")
        assert n == 0
        row = _row(tid)
        assert (row["outcome"], row["exit_price"], row["exit_reason"]) == ("WIN", 106.0, "TP")
        assert warn.call_count == 1 and "NOT applied" in warn.call_args[0][0]

    def test_second_freeze_keeps_the_first_note(self):
        tid = _open_trade()
        mark_live_trade_unreconciled(tid, "first freeze")
        assert mark_live_trade_unreconciled(tid, "second freeze") == 0
        assert _row(tid)["exit_reason"] == "first freeze"


def _pass_where_other_process_books_during_exit_lookup(tid):
    """Reconciliation path (position gone, no fill found): another process books
    the row while this pass is looking for the exit. Returns the alert mock."""
    def find_exit_after_other_process_booked(ticker, broker, entry_ts):
        close_live_trade(tid, 106.0, 24.0, "WIN", "TP", "2026-10-01T14:00:00+00:00")
        return None, "", "", "both_feeds_empty"

    with ExitStack() as stack:
        stack.enter_context(patch("backend.live_trades_tracker.LIVE_ENABLED", True))
        stack.enter_context(patch("backend.live_config.get_live_config",
                                  return_value={"max_hold_days": 25}))
        stack.enter_context(patch("backend.brokers.alpaca.get_positions", return_value=[]))
        stack.enter_context(patch("backend.brokers.alpaca.get_order_by_id",
                                  return_value={"status": "filled", "filled_qty": 4, "legs": []}))
        stack.enter_context(patch("backend.live_trades_tracker._find_exit_from_orders",
                                  side_effect=find_exit_after_other_process_booked))
        stack.enter_context(patch("backend.live_trades_tracker._current_price", return_value=None))
        stack.enter_context(patch("backend.live_trades_tracker._log_vol_slope"))
        stack.enter_context(patch("backend.alerts.alert_position_untracked"))
        alert = stack.enter_context(patch("backend.alerts.alert_position_unreconciled"))

        from backend.live_trades_tracker import check_live_exits
        closed = check_live_exits()
    assert closed == []
    return alert


class TestStaleSnapshotFreeze:

    def test_positive_control_unguarded_freeze_overwrites_the_booking_and_alerts(self):
        """Holds the guard open (the pre-10-02 UPDATE): proves the harness reaches the freeze."""
        def unguarded(trade_id, note):
            conn = get_db()
            try:
                conn.execute("UPDATE live_trades SET outcome = 'UNRECONCILED', exit_reason = ? "
                             "WHERE id = ?", (note, trade_id))
                conn.commit()
            finally:
                conn.close()
            return 1

        tid = _open_trade(ticker="FRZC", order_id="ord-frz-c")
        with patch("backend.live_trades_tracker.mark_live_trade_unreconciled", side_effect=unguarded):
            alert = _pass_where_other_process_books_during_exit_lookup(tid)
        assert _row(tid)["outcome"] == "UNRECONCILED"
        assert alert.call_count == 1

    def test_pass_whose_row_was_booked_meanwhile_does_not_freeze_or_alert(self):
        tid = _open_trade(ticker="FRZ", order_id="ord-frz-1")
        alert = _pass_where_other_process_books_during_exit_lookup(tid)
        row = _row(tid)
        assert (row["outcome"], row["exit_price"]) == ("WIN", 106.0)
        assert alert.call_count == 0
