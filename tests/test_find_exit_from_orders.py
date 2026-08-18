"""
Regression coverage for _find_exit_from_orders' two invariants (2026-08-18,
LLY incident): row 25 (LLY, entered 2026-06-26) was silently assigned row 8's
real exit (a fill from a *different* trade, closed 2026-05-28 — a month
before row 25's own entry) because the function matched candidate fills on
ticker alone, with no check that the fill postdated the trade's entry or
that it hadn't already been consumed by an earlier-closed trade.
"""
from unittest.mock import MagicMock

from backend.db import init_db, insert_live_trade, close_live_trade

init_db()


def _make_order(filled_at: str, filled_price: float, order_type: str = "limit"):
    return {
        "ticker": "LLY", "side": "sell", "type": order_type,
        "filled_price": filled_price, "filled_at": filled_at,
        "submitted_at": filled_at,
    }


class TestExitFromOrdersInvariants:
    def test_stale_pre_entry_fill_rejected(self):
        """
        The only candidate fill predates this trade's entry — must not be
        used. Reproduces the LLY shape: a real fill exists for the ticker,
        it's just not this trade's fill.
        """
        from backend.live_trades_tracker import _find_exit_from_orders

        broker = MagicMock()
        broker.get_orders.return_value = [_make_order("2026-05-28T14:29:18+00:00", 1144.23)]

        exit_price, exit_reason, exited_at = _find_exit_from_orders(
            "LLY", broker, entry_timestamp="2026-06-26T15:50:16+00:00"
        )
        assert exit_price is None
        assert exit_reason == "MANUAL"

    def test_post_entry_fill_accepted(self):
        from backend.live_trades_tracker import _find_exit_from_orders

        broker = MagicMock()
        broker.get_orders.return_value = [_make_order("2026-07-01T10:00:00+00:00", 1200.0)]

        exit_price, exit_reason, exited_at = _find_exit_from_orders(
            "LLY", broker, entry_timestamp="2026-06-26T15:50:16+00:00"
        )
        assert exit_price == 1200.0
        assert exit_reason == "TP"

    def test_already_consumed_fill_rejected(self):
        """
        The fill postdates entry but is already recorded as another trade's
        exit (same exit_price + exited_at) — must not be reused even though
        the timestamp invariant alone would have accepted it.
        """
        from backend.live_trades_tracker import _find_exit_from_orders

        trade_id = insert_live_trade({
            "timestamp": "2026-06-01T00:00:00+00:00", "ticker": "LLY",
            "sector": "Healthcare", "alpaca_order_id": "existing-order",
            "entry_price": 1000.0, "qty": 1.0, "notional": 1000.0,
            "tp_price": 1100.0, "sl_price": 900.0,
        })
        close_live_trade(
            trade_id=trade_id, exit_price=1200.0, pnl=200.0,
            outcome="WIN", exit_reason="TP",
            exited_at="2026-07-01T10:00:00+00:00",
        )

        broker = MagicMock()
        broker.get_orders.return_value = [_make_order("2026-07-01T10:00:00+00:00", 1200.0)]

        exit_price, exit_reason, exited_at = _find_exit_from_orders(
            "LLY", broker, entry_timestamp="2026-06-26T15:50:16+00:00"
        )
        assert exit_price is None
        assert exit_reason == "MANUAL"

    def test_newer_unconsumed_fill_used_when_older_one_is_consumed(self):
        """A stale/consumed fill doesn't disqualify a genuinely newer, unconsumed one."""
        from backend.live_trades_tracker import _find_exit_from_orders

        trade_id = insert_live_trade({
            "timestamp": "2026-06-01T00:00:00+00:00", "ticker": "LLY",
            "sector": "Healthcare", "alpaca_order_id": "existing-order-2",
            "entry_price": 1000.0, "qty": 1.0, "notional": 1000.0,
            "tp_price": 1100.0, "sl_price": 900.0,
        })
        close_live_trade(
            trade_id=trade_id, exit_price=1200.0, pnl=200.0,
            outcome="WIN", exit_reason="TP",
            exited_at="2026-07-01T10:00:00+00:00",
        )

        broker = MagicMock()
        broker.get_orders.return_value = [
            _make_order("2026-07-01T10:00:00+00:00", 1200.0),   # consumed
            _make_order("2026-07-15T10:00:00+00:00", 1250.0),   # genuinely new
        ]

        exit_price, exit_reason, exited_at = _find_exit_from_orders(
            "LLY", broker, entry_timestamp="2026-06-26T15:50:16+00:00"
        )
        assert exit_price == 1250.0
