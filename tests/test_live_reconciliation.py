"""
Sanity tests for the UNRECONCILED position-wipe path (2026-08-06 HON incident,
CHECK 66). Confirms the full chain fires before the mechanism is relied on
for real: freeze (no fabricated exit) → alert → gate refusal → audit CRITICAL.
Also covers the mirror direction (broker holds an untracked position) and
the resolve_unreconciled() sign-off path.
"""
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from backend.db import (
    close_live_trade,
    get_unreconciled_live_trades,
    init_db,
    insert_live_trade,
    mark_live_trade_unreconciled,
    resolve_unreconciled,
)

init_db()


def _open_trade(ticker="HON", entry_price=249.13, qty=4.0, order_id="ord-hon-1"):
    trade_id = insert_live_trade({
        "timestamp":       "2026-08-04T17:14:31+00:00",
        "ticker":          ticker,
        "sector":          "Industrials",
        "alpaca_order_id": order_id,
        "entry_price":     entry_price,
        "qty":             qty,
        "notional":        entry_price * qty,
        "tp_price":        entry_price * 1.06,
        "sl_price":        entry_price * 0.94,
    })
    return trade_id


class TestReconciliationFreeze:
    """
    check_live_exits(): a position gone from the broker with no fill found
    anywhere must freeze, not fabricate an exit at market price.
    """

    def test_no_fill_found_freezes_not_fabricates(self):
        trade_id = _open_trade()

        with ExitStack() as stack:
            stack.enter_context(patch("backend.live_trades_tracker.LIVE_ENABLED", True))
            stack.enter_context(patch("backend.live_config.get_live_config",
                                       return_value={"max_hold_days": 25}))
            get_positions_mock = stack.enter_context(
                patch("backend.brokers.alpaca.get_positions", return_value=[]))  # HON gone
            # Parent buy order filled normally (mirrors the real HON order) — the
            # bracket TP/SL legs are what's gone; get_orders() below (used by
            # _find_exit_from_orders) is what actually returns nothing.
            stack.enter_context(patch("backend.brokers.alpaca.get_order_by_id",
                                       return_value={"status": "filled", "filled_qty": 4, "legs": []}))
            stack.enter_context(patch("backend.brokers.alpaca.cancel_open_orders", return_value=2))
            stack.enter_context(patch("backend.brokers.alpaca.get_orders", return_value=[]))  # no fill anywhere
            stack.enter_context(patch("backend.live_trades_tracker._current_price", return_value=248.12))
            close_mock = stack.enter_context(patch("backend.live_trades_tracker.close_live_trade"))
            alert_mock = stack.enter_context(patch("backend.alerts.alert_position_unreconciled"))
            stack.enter_context(patch("backend.alerts.alert_position_untracked"))

            from backend.live_trades_tracker import check_live_exits
            closed = check_live_exits()

        # No exit was fabricated — the trade is not in the returned closed list
        # and close_live_trade (which would write a real exit_price/pnl) was
        # never called for it.
        assert closed == []
        close_mock.assert_not_called()

        # It fired the alert exactly once...
        alert_mock.assert_called_once()
        ticker_arg = alert_mock.call_args[0][0]
        assert ticker_arg == "HON"

        # ...and froze the DB row as UNRECONCILED, not LOSS.
        unreconciled = get_unreconciled_live_trades()
        assert len(unreconciled) == 1
        assert unreconciled[0]["id"] == trade_id
        assert unreconciled[0]["outcome"] == "UNRECONCILED"
        assert unreconciled[0]["exit_price"] is None  # nothing fabricated
        assert unreconciled[0]["pnl"] is None


class TestUntrackedPosition:
    """The mirror direction: broker holds a ticker with no matching internal OPEN row."""

    def test_broker_position_with_no_open_row_alerts(self):
        from backend.live_trades_tracker import _check_untracked_positions, _untracked_alerted
        _untracked_alerted.clear()

        with patch("backend.alerts.alert_position_untracked") as alert_mock:
            _check_untracked_positions({"XOM"}, open_trades=[])
            alert_mock.assert_called_once_with("XOM")

        # Second call with the same mismatch does not re-alert (latch).
        with patch("backend.alerts.alert_position_untracked") as alert_mock2:
            _check_untracked_positions({"XOM"}, open_trades=[])
            alert_mock2.assert_not_called()

        # Once it clears, a future recurrence re-alerts.
        _check_untracked_positions(set(), open_trades=[])
        with patch("backend.alerts.alert_position_untracked") as alert_mock3:
            _check_untracked_positions({"XOM"}, open_trades=[])
            alert_mock3.assert_called_once_with("XOM")

        _untracked_alerted.clear()


class TestGateRefusal:
    """gate_runner_live.run() must refuse all new entries while UNRECONCILED rows exist."""

    def test_gate_halts_on_unreconciled(self):
        with ExitStack() as stack:
            stack.enter_context(patch("backend.gate.gate_runner_live.LIVE_ENABLED", True))
            stack.enter_context(patch(
                "backend.gate.gate_runner_live.get_unreconciled_live_trades",
                return_value=[{"ticker": "HON", "id": 1}],
            ))
            alert_mock = stack.enter_context(patch("backend.alerts.alert_gate_blocked"))
            # If the halt didn't short-circuit, run() would reach broker.get_account()
            # next and this mock absence would raise — proving the halt fired first.
            account_mock = stack.enter_context(patch("backend.brokers.alpaca.get_account"))

            from backend.gate import gate_runner_live
            gate_runner_live._unreconciled_alerted.clear()
            result = gate_runner_live.run()

        assert result == []
        alert_mock.assert_called_once()
        account_mock.assert_not_called()  # halted before touching the broker at all
        gate_runner_live._unreconciled_alerted.clear()


class TestAuditCheck66:
    """CHECK 66's dynamic sub-check flags any standing UNRECONCILED row."""

    def test_check66_flags_unreconciled_row(self):
        from backend import db as db_module
        trade_id = _open_trade(ticker="TESTX", order_id="ord-check66")
        mark_live_trade_unreconciled(trade_id, "test: simulated wipe, no fill found")

        from audit import _audit_core
        from audit import checks_gate

        _audit_core.findings.clear()
        _audit_core.triggered.clear()
        with patch("audit.checks_gate.REPO") as repo_mock:
            # Point CHECK 66's DB lookup at the same (temp) DB the rest of this
            # test file uses, and let the static file-based sub-checks run
            # against the real repo tree.
            import pathlib
            repo_mock.__truediv__ = lambda self, other: (
                db_module.DB_PATH if other == "data/apex.db"
                else pathlib.Path(__file__).resolve().parent.parent / other
            )
            checks_gate.check66()

        matches = [f for f in _audit_core.findings
                   if f[0] == 66 and "TESTX" in f[4]]
        assert len(matches) == 1
        assert matches[0][2] == "CRITICAL"

        # Clean up so this row doesn't linger in the shared test DB for other tests.
        resolve_unreconciled(trade_id, "WRITTEN_OFF", "test cleanup")


class TestResolveUnreconciled:
    def test_written_off_clears_the_halt(self):
        trade_id = _open_trade(ticker="TESTY", order_id="ord-resolve-1")
        mark_live_trade_unreconciled(trade_id, "test: simulated wipe")
        assert any(t["id"] == trade_id for t in get_unreconciled_live_trades())

        resolve_unreconciled(trade_id, "WRITTEN_OFF", "Alpaca support confirmed the wipe, ticket #123")

        assert not any(t["id"] == trade_id for t in get_unreconciled_live_trades())

    def test_confirmed_exit_requires_price(self):
        trade_id = _open_trade(ticker="TESTZ", order_id="ord-resolve-2")
        mark_live_trade_unreconciled(trade_id, "test: simulated wipe")
        with pytest.raises(ValueError):
            resolve_unreconciled(trade_id, "CONFIRMED_EXIT", "evidence without a price")

    def test_resolve_requires_evidence(self):
        trade_id = _open_trade(ticker="TESTW", order_id="ord-resolve-3")
        mark_live_trade_unreconciled(trade_id, "test: simulated wipe")
        with pytest.raises(ValueError):
            resolve_unreconciled(trade_id, "WRITTEN_OFF", "")
