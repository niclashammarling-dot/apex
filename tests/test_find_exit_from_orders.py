"""
Regression coverage for _find_exit_from_orders' two invariants (2026-08-18,
LLY incident): row 25 (LLY, entered 2026-06-26) was silently assigned row 8's
real exit (a fill from a *different* trade, closed 2026-05-28 — a month
before row 25's own entry) because the function matched candidate fills on
ticker alone, with no check that the fill postdated the trade's entry or
that it hadn't already been consumed by an earlier-closed trade.

Also covers the 2026-08-23 activities-corroboration extension: orders is
exactly the feed that can go empty while a position genuinely closed cleanly
(2026-08-06 HON incident — canceled OCO legs carry null fill fields there,
while /account/activities stayed correct throughout), so a "no fill" verdict
now requires both feeds to agree, and the function reports *which* state
produced a None (found / both_feeds_empty / orders_unavailable /
activities_unavailable) rather than collapsing them into one signal.
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
        broker.get_activities.return_value = []

        exit_price, exit_reason, exited_at, evidence = _find_exit_from_orders(
            "LLY", broker, entry_timestamp="2026-06-26T15:50:16+00:00"
        )
        assert exit_price is None
        assert exit_reason == "MANUAL"
        assert evidence == "both_feeds_empty"

    def test_post_entry_fill_accepted(self):
        from backend.live_trades_tracker import _find_exit_from_orders

        broker = MagicMock()
        broker.get_orders.return_value = [_make_order("2026-07-01T10:00:00+00:00", 1200.0)]

        exit_price, exit_reason, exited_at, evidence = _find_exit_from_orders(
            "LLY", broker, entry_timestamp="2026-06-26T15:50:16+00:00"
        )
        assert exit_price == 1200.0
        assert exit_reason == "TP"
        assert evidence == "found"

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
        broker.get_activities.return_value = []

        exit_price, exit_reason, exited_at, evidence = _find_exit_from_orders(
            "LLY", broker, entry_timestamp="2026-06-26T15:50:16+00:00"
        )
        assert exit_price is None
        assert exit_reason == "MANUAL"
        assert evidence == "both_feeds_empty"

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

        exit_price, exit_reason, exited_at, evidence = _find_exit_from_orders(
            "LLY", broker, entry_timestamp="2026-06-26T15:50:16+00:00"
        )
        assert exit_price == 1250.0
        assert evidence == "found"


class TestActivitiesCorroboration:
    """
    2026-08-23: orders is exactly the feed that can go empty while a
    position genuinely closed cleanly (2026-08-06 HON incident — canceled
    OCO legs carry null fill fields there, while /account/activities stayed
    correct throughout). _find_exit_from_orders must corroborate against
    activities before declaring "no fill found."
    """

    def test_activities_call_scoped_by_after_not_ticker(self):
        """
        No symbol filter exists on this Alpaca endpoint (confirmed against
        the SDK's request model) — get_activities() is called with `after`
        only, and ticker narrowing happens client-side inside this function.
        Pins the call shape so a future edit doesn't reintroduce a
        server-side ticker kwarg that silently does nothing.
        """
        from backend.live_trades_tracker import _find_exit_from_orders

        broker = MagicMock()
        broker.get_orders.return_value = []
        broker.get_activities.return_value = []

        _find_exit_from_orders("AAPL", broker, entry_timestamp="2026-08-18T00:00:00+00:00")

        broker.get_activities.assert_called_once_with("FILL", after="2026-08-18T00:00:00+00:00")

    def test_activities_fill_used_when_orders_feed_is_empty(self):
        from backend.live_trades_tracker import _find_exit_from_orders

        broker = MagicMock()
        broker.get_orders.return_value = []
        broker.get_activities.return_value = [
            {"ticker": "AAPL", "side": "sell", "price": 200.0, "qty": 4.0,
             "filled_at": "2026-08-20T15:00:00+00:00"},
        ]

        exit_price, exit_reason, exited_at, evidence = _find_exit_from_orders(
            "AAPL", broker, entry_timestamp="2026-08-18T00:00:00+00:00"
        )
        assert exit_price == 200.0
        assert exited_at == "2026-08-20T15:00:00+00:00"
        assert evidence == "found"

    def test_activities_fill_for_a_different_ticker_ignored(self):
        """
        get_activities() is unfiltered/account-wide (no server-side symbol
        param exists) — a fill for some *other* ticker sitting in the same
        response must not corroborate this ticker's exit. This is the
        client-side filter the account-wide read depends on.
        """
        from backend.live_trades_tracker import _find_exit_from_orders

        broker = MagicMock()
        broker.get_orders.return_value = []
        broker.get_activities.return_value = [
            {"ticker": "MSFT", "side": "sell", "price": 300.0,
             "filled_at": "2026-08-20T15:00:00+00:00"},
        ]

        exit_price, exit_reason, exited_at, evidence = _find_exit_from_orders(
            "AAPL", broker, entry_timestamp="2026-08-18T00:00:00+00:00"
        )
        assert exit_price is None
        assert evidence == "both_feeds_empty"

    def test_activities_fill_predating_entry_rejected(self):
        """Same postdate-entry invariant applies to the activities feed."""
        from backend.live_trades_tracker import _find_exit_from_orders

        broker = MagicMock()
        broker.get_orders.return_value = []
        broker.get_activities.return_value = [
            {"ticker": "AAPL", "side": "sell", "price": 150.0, "qty": 1.0,
             "filled_at": "2026-08-01T15:00:00+00:00"},   # before entry
        ]

        exit_price, exit_reason, exited_at, evidence = _find_exit_from_orders(
            "AAPL", broker, entry_timestamp="2026-08-18T00:00:00+00:00"
        )
        assert exit_price is None
        assert evidence == "both_feeds_empty"

    def test_activities_fill_already_consumed_rejected(self):
        from backend.live_trades_tracker import _find_exit_from_orders

        trade_id = insert_live_trade({
            "timestamp": "2026-08-15T00:00:00+00:00", "ticker": "AAPL",
            "sector": "Technology", "alpaca_order_id": "existing-order-act",
            "entry_price": 190.0, "qty": 1.0, "notional": 190.0,
            "tp_price": 210.0, "sl_price": 175.0,
        })
        close_live_trade(
            trade_id=trade_id, exit_price=200.0, pnl=10.0,
            outcome="WIN", exit_reason="TP",
            exited_at="2026-08-20T15:00:00+00:00",
        )

        broker = MagicMock()
        broker.get_orders.return_value = []
        broker.get_activities.return_value = [
            {"ticker": "AAPL", "side": "sell", "price": 200.0, "qty": 1.0,
             "filled_at": "2026-08-20T15:00:00+00:00"},   # already consumed above
        ]

        exit_price, exit_reason, exited_at, evidence = _find_exit_from_orders(
            "AAPL", broker, entry_timestamp="2026-08-18T00:00:00+00:00"
        )
        assert exit_price is None
        assert evidence == "both_feeds_empty"

    def test_both_feeds_empty_returns_none(self):
        from backend.live_trades_tracker import _find_exit_from_orders

        broker = MagicMock()
        broker.get_orders.return_value = []
        broker.get_activities.return_value = []

        exit_price, exit_reason, exited_at, evidence = _find_exit_from_orders(
            "AAPL", broker, entry_timestamp="2026-08-18T00:00:00+00:00"
        )
        assert exit_price is None
        assert exit_reason == "MANUAL"
        assert evidence == "both_feeds_empty"

    def test_orders_fetch_failure_reports_orders_unavailable(self):
        """
        get_orders() itself failing must be distinguishable from a genuine
        empty read — nothing was checked at all, not "checked and empty."
        """
        from backend.live_trades_tracker import _find_exit_from_orders

        broker = MagicMock()
        broker.get_orders.side_effect = Exception("orders endpoint 500")

        exit_price, exit_reason, exited_at, evidence = _find_exit_from_orders(
            "AAPL", broker, entry_timestamp="2026-08-18T00:00:00+00:00"
        )
        assert exit_price is None
        assert evidence == "orders_unavailable"
        broker.get_activities.assert_not_called()

    def test_activities_fetch_failure_degrades_to_none_not_a_crash(self):
        """
        Corroboration itself failing must not raise out of
        _find_exit_from_orders — it degrades to a freeze-worthy None
        (fail-safe: uncertainty freezes, it doesn't fabricate or propagate
        an exception) but must be reported as "couldn't check", not
        conflated with a genuine both-feeds-empty confirmation.
        """
        from backend.live_trades_tracker import _find_exit_from_orders

        broker = MagicMock()
        broker.get_orders.return_value = []
        broker.get_activities.side_effect = Exception("activities endpoint 500")

        exit_price, exit_reason, exited_at, evidence = _find_exit_from_orders(
            "AAPL", broker, entry_timestamp="2026-08-18T00:00:00+00:00"
        )
        assert exit_price is None
        assert evidence == "activities_unavailable"


class TestActivitiesPartialFills:
    """
    2026-08-23 review: TradeActivity carries leaves_qty/cum_qty because a
    single sell order can produce multiple partial-fill rows before it's
    done. Taking the first matching row's price would book one slice's
    price instead of the volume-weighted average, and a row with
    leaves_qty > 0 (order still filling) must not read as a completed exit.
    """

    def test_two_partial_fill_rows_same_order_aggregate_to_weighted_price(self):
        """2 shares @ 100 then 2 shares @ 102, same order, final row leaves_qty=0."""
        from backend.live_trades_tracker import _find_exit_from_orders

        broker = MagicMock()
        broker.get_orders.return_value = []
        broker.get_activities.return_value = [
            {"ticker": "AAPL", "side": "sell", "price": 100.0, "qty": 2.0,
             "filled_at": "2026-08-20T15:00:00+00:00", "order_id": "ord-1", "leaves_qty": 2.0},
            {"ticker": "AAPL", "side": "sell", "price": 102.0, "qty": 2.0,
             "filled_at": "2026-08-20T15:00:05+00:00", "order_id": "ord-1", "leaves_qty": 0.0},
        ]

        exit_price, exit_reason, exited_at, evidence = _find_exit_from_orders(
            "AAPL", broker, entry_timestamp="2026-08-18T00:00:00+00:00"
        )
        assert exit_price == 101.0   # (2*100 + 2*102) / 4
        assert exited_at == "2026-08-20T15:00:05+00:00"   # the completing row's timestamp
        assert evidence == "found"

    def test_partial_fill_with_no_completing_row_reports_exit_in_progress(self):
        """Only a partial-fill row exists — the order hasn't finished filling."""
        from backend.live_trades_tracker import _find_exit_from_orders

        broker = MagicMock()
        broker.get_orders.return_value = []
        broker.get_activities.return_value = [
            {"ticker": "AAPL", "side": "sell", "price": 100.0, "qty": 2.0,
             "filled_at": "2026-08-20T15:00:00+00:00", "order_id": "ord-2", "leaves_qty": 2.0},
        ]

        exit_price, exit_reason, exited_at, evidence = _find_exit_from_orders(
            "AAPL", broker, entry_timestamp="2026-08-18T00:00:00+00:00"
        )
        assert exit_price is None
        assert evidence == "exit_in_progress"   # not both_feeds_empty — this is not an absence

    def test_completed_order_preferred_over_in_progress_order_for_same_ticker(self):
        """
        Two distinct orders for the same ticker: an older one that's fully
        filled and a newer one still in progress. The completed, newer-timed
        group must win — a still-filling order elsewhere shouldn't block a
        real, usable exit from a different order.
        """
        from backend.live_trades_tracker import _find_exit_from_orders

        broker = MagicMock()
        broker.get_orders.return_value = []
        broker.get_activities.return_value = [
            {"ticker": "AAPL", "side": "sell", "price": 200.0, "qty": 4.0,
             "filled_at": "2026-08-21T15:00:00+00:00", "order_id": "ord-complete", "leaves_qty": 0.0},
            {"ticker": "AAPL", "side": "sell", "price": 100.0, "qty": 2.0,
             "filled_at": "2026-08-20T15:00:00+00:00", "order_id": "ord-partial", "leaves_qty": 2.0},
        ]

        exit_price, exit_reason, exited_at, evidence = _find_exit_from_orders(
            "AAPL", broker, entry_timestamp="2026-08-18T00:00:00+00:00"
        )
        assert exit_price == 200.0
        assert evidence == "found"
