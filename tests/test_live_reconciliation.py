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


class TestOrderStatusExtraction:
    """
    Regression coverage for the str(enum) gotcha: alpaca-py's OrderStatus is a
    str-mixin Enum, but str(member) returns "OrderStatus.CANCELED" (Enum's
    __str__), not the plain "canceled" value every consumer expects. This is
    what let a canceled parent order be silently misread as "still pending"
    forever in the reconciliation branch (2026-08-06 HON incident diagnosis).
    """

    def test_enum_value_helper_unwraps_alpaca_enums(self):
        from alpaca.trading.enums import OrderStatus, OrderSide
        from backend.brokers.alpaca import _enum_value

        assert _enum_value(OrderStatus.CANCELED) == "canceled"
        assert str(OrderStatus.CANCELED) != "canceled"  # pin the gotcha itself
        assert _enum_value(OrderSide.SELL) == "sell"
        assert _enum_value(None) is None
        assert _enum_value("already-plain") == "already-plain"  # passthrough

    def test_get_order_by_id_returns_plain_values_not_enum_repr(self):
        from alpaca.trading.enums import OrderStatus, OrderSide, OrderType
        import uuid as uuid_mod

        class _FakeOrder:
            id = uuid_mod.uuid4()
            symbol = "HON"
            status = OrderStatus.CANCELED
            filled_avg_price = None
            filled_qty = 0
            legs = []

        with patch("backend.brokers.alpaca._client") as client_mock:
            client_mock.return_value.get_order_by_id.return_value = _FakeOrder()
            from backend.brokers.alpaca import get_order_by_id
            result = get_order_by_id(str(_FakeOrder.id))

        assert result["status"] == "canceled"
        assert "OrderStatus" not in result["status"]

    def test_terminal_and_pending_status_sets_pin_exact_alpaca_values(self):
        """
        Pins the exact strings so a future edit (or an Alpaca SDK bump renaming
        a member) is a visible test failure, not a silent reclassification.
        """
        from backend.live_trades_tracker import (
            _PENDING_ORDER_STATUSES, _TERMINAL_ORDER_STATUSES,
        )

        assert _TERMINAL_ORDER_STATUSES == {
            "filled", "canceled", "expired", "replaced", "done_for_day", "rejected",
        }
        assert _PENDING_ORDER_STATUSES == {
            "new", "partially_filled", "accepted", "pending_new", "pending_cancel",
            "pending_replace", "pending_review", "accepted_for_bidding", "held",
            "stopped", "calculated",
        }
        # The two sets must never overlap — a status can't be simultaneously
        # "still might fill" and "definitely won't fill again".
        assert _PENDING_ORDER_STATUSES & _TERMINAL_ORDER_STATUSES == set()
        # The exact typo that caused the incident must not be present anywhere.
        assert "cancelled" not in _TERMINAL_ORDER_STATUSES
        assert "cancelled" not in _PENDING_ORDER_STATUSES


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

    def test_canceled_parent_order_does_not_wait_forever(self):
        """
        The exact bug scenario: the *parent* buy order itself was canceled
        (filled_qty=0, status="canceled" — real Alpaca spelling), and the
        position never opened. Before the enum-vocabulary fix, the substring
        check for "cancelled" (typo) never matched real Alpaca's "canceled",
        so this order was misread as "still pending" and the trade would
        `continue` (skip reconciliation) on every cycle, forever. It must
        instead proceed straight to reconciliation and freeze/alert.
        """
        trade_id = _open_trade(ticker="ZOMB", order_id="ord-zombie-1")

        with ExitStack() as stack:
            stack.enter_context(patch("backend.live_trades_tracker.LIVE_ENABLED", True))
            stack.enter_context(patch("backend.live_config.get_live_config",
                                       return_value={"max_hold_days": 25}))
            stack.enter_context(patch("backend.brokers.alpaca.get_positions", return_value=[]))
            stack.enter_context(patch("backend.brokers.alpaca.get_order_by_id",
                                       return_value={"status": "canceled", "filled_qty": 0, "legs": []}))
            stack.enter_context(patch("backend.brokers.alpaca.cancel_open_orders", return_value=0))
            stack.enter_context(patch("backend.brokers.alpaca.get_orders", return_value=[]))
            stack.enter_context(patch("backend.live_trades_tracker._current_price", return_value=None))
            alert_mock = stack.enter_context(patch("backend.alerts.alert_position_unreconciled"))
            stack.enter_context(patch("backend.alerts.alert_position_untracked"))

            from backend.live_trades_tracker import check_live_exits
            check_live_exits()

        # Reached reconciliation and froze — did NOT `continue` and wait forever.
        alert_mock.assert_called_once()
        assert any(t["id"] == trade_id for t in get_unreconciled_live_trades())

    def test_unrecognized_status_warns_and_proceeds(self):
        """
        A status Alpaca might add in the future that isn't in either known
        set must log a warning and proceed to reconciliation — not silently
        fall into "still pending" (the exact failure mode being fixed) or
        get misclassified without a trace.
        """
        trade_id = _open_trade(ticker="NEWSTAT", order_id="ord-newstatus-1")

        with ExitStack() as stack:
            stack.enter_context(patch("backend.live_trades_tracker.LIVE_ENABLED", True))
            stack.enter_context(patch("backend.live_config.get_live_config",
                                       return_value={"max_hold_days": 25}))
            stack.enter_context(patch("backend.brokers.alpaca.get_positions", return_value=[]))
            stack.enter_context(patch("backend.brokers.alpaca.get_order_by_id",
                                       return_value={"status": "some_future_status", "filled_qty": 0, "legs": []}))
            stack.enter_context(patch("backend.brokers.alpaca.cancel_open_orders", return_value=0))
            stack.enter_context(patch("backend.brokers.alpaca.get_orders", return_value=[]))
            stack.enter_context(patch("backend.live_trades_tracker._current_price", return_value=None))
            stack.enter_context(patch("backend.alerts.alert_position_unreconciled"))
            stack.enter_context(patch("backend.alerts.alert_position_untracked"))
            warn_mock = stack.enter_context(patch("backend.live_trades_tracker.logger.warning"))

            from backend.live_trades_tracker import check_live_exits
            check_live_exits()

        assert any("unrecognized order status" in str(c.args[0]).lower()
                   for c in warn_mock.call_args_list)
        assert any(t["id"] == trade_id for t in get_unreconciled_live_trades())


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
