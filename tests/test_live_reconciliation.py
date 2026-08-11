"""
Sanity tests for the UNRECONCILED position-wipe path (2026-08-06 HON incident,
CHECK 66). Confirms the full chain fires before the mechanism is relied on
for real: freeze (no fabricated exit) → alert → gate refusal → audit CRITICAL.
Also covers the mirror direction (broker holds an untracked position) and
the resolve_unreconciled() sign-off path.
"""
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from backend.db import (
    close_live_trade,
    get_open_live_trades,
    get_unreconciled_live_trades,
    init_db,
    insert_live_trade,
    mark_live_trade_unreconciled,
    reopen_unreconciled,
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

    def test_get_account_get_positions_get_orders_return_plain_values(self):
        """
        2026-08-07 audit sweep: get_order_by_id() was fixed for this gotcha on
        2026-08-06, but get_account()/get_positions()/get_orders() — same
        module, same enum fields — were not. That left "status": "AccountStatus.ACTIVE"
        and "side": "PositionSide.LONG" flowing to every consumer, silently
        breaking cancel_orphan_brackets()'s exact-match status guard (the
        mechanism added 2026-07-07 specifically to stop orphaned bracket legs
        creating short exposure — it could never match and had been a no-op
        since it was written) and the frontend order-side buy/sell coloring.
        """
        from alpaca.trading.enums import AccountStatus, OrderSide, OrderStatus, OrderType
        from alpaca.trading.enums import PositionSide
        import uuid as uuid_mod

        class _FakeAccount:
            equity = "1000.0"
            cash = "500.0"
            buying_power = "2000.0"
            last_equity = "990.0"
            pattern_day_trader = False
            trading_blocked = False
            account_blocked = False
            status = AccountStatus.ACTIVE

        class _FakePosition:
            symbol = "HON"
            qty = "4"
            side = PositionSide.LONG
            avg_entry_price = "249.42"
            current_price = "245.0"
            market_value = "980.0"
            cost_basis = "997.68"
            unrealized_pl = "-17.68"
            unrealized_plpc = "-0.0177"
            change_today = "0.01"

        class _FakeLeg:
            id = uuid_mod.uuid4()
            side = OrderSide.SELL
            order_type = OrderType.STOP
            status = OrderStatus.CANCELED
            filled_avg_price = None
            filled_at = None

        class _FakeOrder:
            id = uuid_mod.uuid4()
            symbol = "HON"
            side = OrderSide.BUY
            order_type = OrderType.MARKET
            qty = "4"
            filled_qty = "4"
            filled_avg_price = "249.42"
            status = OrderStatus.FILLED
            submitted_at = None
            filled_at = None
            legs = [_FakeLeg()]

        with patch("backend.brokers.alpaca._client") as client_mock:
            client_mock.return_value.get_account.return_value = _FakeAccount()
            client_mock.return_value.get_all_positions.return_value = [_FakePosition()]
            client_mock.return_value.get_orders.return_value = [_FakeOrder()]

            from backend.brokers.alpaca import get_account, get_orders, get_positions
            acct  = get_account()
            poss  = get_positions()
            ords  = get_orders(nested=True)

        for value in (acct["status"], poss[0]["side"], ords[0]["side"],
                      ords[0]["type"], ords[0]["status"],
                      ords[0]["legs"][0]["side"], ords[0]["legs"][0]["order_type"],
                      ords[0]["legs"][0]["status"]):
            assert "." not in value, f"leaked Enum repr: {value!r}"

        # AccountStatus's own vocabulary is uppercase ("ACTIVE"); every other
        # enum here (side/type/order status) is lowercase — both are .value
        # as Alpaca defines it, not a normalization this helper should impose.
        assert acct["status"] == "ACTIVE"
        assert poss[0]["side"] == "long"
        assert ords[0]["side"] == "buy"
        assert ords[0]["status"] == "filled"
        assert ords[0]["legs"][0]["status"] == "canceled"

    def test_place_oco_exit_submits_linked_tp_sl_no_entry_leg(self):
        """
        place_oco_exit() (added alongside reopen_unreconciled(), 2026-08-10 HON
        incident) places a standalone TP/SL pair on an already-held position —
        no entry leg, order_class=OCO. First call went straight to a live
        broker with no test coverage; this pins the request shape so a second
        use doesn't repeat that.
        """
        import uuid as uuid_mod
        from alpaca.trading.enums import OrderClass

        fake_id = uuid_mod.uuid4()

        with patch("backend.brokers.alpaca._client") as client_mock:
            submitted = {}

            def _fake_submit_order(req):
                submitted["req"] = req
                order = MagicMock()
                order.id = fake_id
                return order

            client_mock.return_value.submit_order.side_effect = _fake_submit_order

            from backend.brokers.alpaca import place_oco_exit
            order_id = place_oco_exit("HON", 4, 264.39, 234.45)

        assert order_id == str(fake_id)
        req = submitted["req"]
        assert req.symbol == "HON"
        assert req.qty == 4
        assert req.side.value == "sell"
        assert req.order_class == OrderClass.OCO
        assert req.take_profit.limit_price == 264.39
        assert req.stop_loss.stop_price == 234.45
        # No entry leg — this must not be a bracket order (which would try to
        # buy more shares on top of the position already held).
        assert req.order_class != OrderClass.BRACKET

    def test_cancel_orphan_brackets_actually_matches_now(self):
        """
        Direct regression for the silenced mechanism: with real Alpaca-shaped
        status/side strings (not the mangled Enum repr), an orphaned sell leg
        on a ticker with no open position, corroborated by a filled sell on
        the same ticker, must be recognized and cancelled.
        """
        from backend.live_trades_tracker import cancel_orphan_brackets

        with patch("backend.live_trades_tracker.LIVE_ENABLED", True), \
             patch("backend.brokers.alpaca.get_positions", return_value=[]), \
             patch("backend.brokers.alpaca.get_orders", return_value=[
                 {"ticker": "TOL", "side": "sell", "status": "new", "legs": []},
                 {"ticker": "TOL", "side": "sell", "status": "filled",
                  "filled_price": 42.0, "legs": []},
             ]), \
             patch("backend.brokers.alpaca.cancel_open_orders", return_value=1) as cancel_mock:
            n = cancel_orphan_brackets()

        assert n == 1
        cancel_mock.assert_called_once_with("TOL")

    def test_cancel_orphan_brackets_leaves_uncorroborated_order_in_place(self):
        """
        2026-08-11 HON: absence from get_positions() alone, with no filled
        sell anywhere corroborating an actual close, is a suspected bad
        broker read, not a confirmed orphan. Must NOT cancel — the resting
        order is the only protection left on a position that may still be
        genuinely held.
        """
        from backend.live_trades_tracker import cancel_orphan_brackets

        with patch("backend.live_trades_tracker.LIVE_ENABLED", True), \
             patch("backend.brokers.alpaca.get_positions", return_value=[]), \
             patch("backend.brokers.alpaca.get_orders", return_value=[
                 {"ticker": "HON", "side": "sell", "status": "new", "legs": []},
             ]), \
             patch("backend.brokers.alpaca.cancel_open_orders") as cancel_mock:
            n = cancel_orphan_brackets()

        assert n == 0
        cancel_mock.assert_not_called()

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


class TestProfitLockRatchetLegSelection:
    """
    _maybe_ratchet_bracket_sl operates on existing open positions (BA, CVX,
    PFE — not new entries, which the gate halt already covers), so a wrong
    belief about whether the SL leg is alive here is a live-position risk,
    not just a bookkeeping one. Same defect family as the reconciliation
    fallback fixed in 49dfe30: exact-match against a hand-typed tuple
    containing "cancelled" (never a real Alpaca status) instead of
    membership against the real vocabulary — inert before that commit
    (str(enum) never matched any tuple member regardless of spelling),
    live afterward.
    """

    def _trade(self, peak=110.0, entry_price=100.0):
        return {"ticker": "TEST", "id": 1, "alpaca_order_id": "parent-1",
                "entry_price": entry_price, "qty": 10}

    def _cfg(self):
        return {"profit_lock_trigger_pct": 0.05, "profit_lock_trail_pct": 0.02}

    def test_canceled_stop_leg_not_selected_as_open(self):
        """
        Only stop leg present is genuinely canceled (real Alpaca spelling).
        Before the fix, "cancelled" (typo) never matched "canceled" so this
        leg was wrongly treated as still open — the ratchet believed a dead
        leg was live protection.
        """
        from backend.live_trades_tracker import _maybe_ratchet_bracket_sl

        order = {"legs": [
            {"id": "leg-dead", "order_type": "stop", "status": "canceled",
             "stop_price": 95.0},
        ]}
        broker = MagicMock()
        broker.get_order_by_id.return_value = order

        with patch("backend.live_trades_tracker.logger.warning") as warn_mock:
            _maybe_ratchet_bracket_sl(self._trade(), peak=110.0, cfg=self._cfg(), broker=broker)

        broker.replace_stop_leg.assert_not_called()
        assert any("no open stop leg" in str(c.args[0]).lower()
                   for c in warn_mock.call_args_list)

    def test_live_stop_leg_selected_over_canceled_one(self):
        """A dead leg and a live leg both present — the live one must be picked."""
        from backend.live_trades_tracker import _maybe_ratchet_bracket_sl

        order = {"legs": [
            {"id": "leg-dead", "order_type": "stop", "status": "canceled",
             "stop_price": 95.0},
            {"id": "leg-live", "order_type": "stop", "status": "new",
             "stop_price": 96.0},
        ]}
        broker = MagicMock()
        broker.get_order_by_id.return_value = order
        broker.replace_stop_leg.return_value = "new-order-id"

        with patch("backend.live_trades_tracker.set_live_trade_profit_lock_activated"):
            _maybe_ratchet_bracket_sl(self._trade(), peak=110.0, cfg=self._cfg(), broker=broker)

        broker.replace_stop_leg.assert_called_once()
        called_leg_id = broker.replace_stop_leg.call_args[0][0]
        assert called_leg_id == "leg-live"

    def test_oco_exit_stop_leg_selected_after_order_id_repoint(self):
        """
        2026-08-10 HON reopen: place_oco_exit() replaces canceled bracket legs
        with a standalone OCO pair, and update_live_trade_order_id() repoints
        alpaca_order_id at it so this gate can find the leg at all — before
        the repoint, get_order_by_id(parent_id) returns the old canceled
        bracket order and finds nothing (warns, silently never ratchets).

        Fixture below is the verbatim get_order_by_id() response captured
        live for HON's actual OCO order (id ec9b01a9-...) on 2026-08-10, not
        a hand-typed guess at the shape — pins the ratchet against what
        Alpaca actually returns (SL leg nested under `legs`, status "held",
        not "new") rather than against our belief about it.
        """
        from backend.live_trades_tracker import _maybe_ratchet_bracket_sl

        order = {
            "id": "ec9b01a9-dc04-463c-9473-2c384b881b03",
            "ticker": "HON",
            "status": "new",
            "filled_avg_price": None,
            "filled_qty": 0.0,
            "legs": [
                {
                    "id": "d9c160d8-b2af-4a77-96d5-d9003b17ebe8",
                    "side": "sell",
                    "order_type": "stop",
                    "status": "held",
                    "limit_price": None,
                    "stop_price": 234.45,
                    "filled_avg_price": None,
                    "filled_at": None,
                },
            ],
        }
        broker = MagicMock()
        broker.get_order_by_id.return_value = order
        broker.replace_stop_leg.return_value = "new-order-id"

        trade = self._trade(entry_price=249.42)
        trade["alpaca_order_id"] = "ec9b01a9-dc04-463c-9473-2c384b881b03"  # post-repoint
        with patch("backend.live_trades_tracker.set_live_trade_profit_lock_activated"):
            _maybe_ratchet_bracket_sl(trade, peak=270.0, cfg=self._cfg(), broker=broker)

        broker.get_order_by_id.assert_called_once_with("ec9b01a9-dc04-463c-9473-2c384b881b03")
        broker.replace_stop_leg.assert_called_once()
        called_leg_id = broker.replace_stop_leg.call_args[0][0]
        assert called_leg_id == "d9c160d8-b2af-4a77-96d5-d9003b17ebe8"


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
            cancel_mock = stack.enter_context(patch("backend.brokers.alpaca.cancel_open_orders", return_value=2))
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

        # No fill corroborates the position actually closed — this is a
        # suspected bad broker read, not a confirmed close. The resting
        # order must be left alone, not cancelled (2026-08-11 HON: cancelling
        # here is what stripped protection off a still-held position).
        cancel_mock.assert_not_called()

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

    def test_corroborated_exit_still_cancels_remaining_leg(self):
        """
        The corroboration requirement gates cancellation on evidence, not on
        disabling it outright — when a filled sell genuinely explains the
        close, the leftover bracket leg must still be cancelled (prevents a
        stale TP/SL firing against a position that no longer exists).

        close_live_trade runs for real here (not mocked) — mocking it would
        leave the row permanently OPEN in the shared test DB and pollute
        every later test's get_open_live_trades() with a phantom MSFT row.
        """
        trade_id = _open_trade(ticker="MSFT", order_id="ord-msft-1")

        with ExitStack() as stack:
            stack.enter_context(patch("backend.live_trades_tracker.LIVE_ENABLED", True))
            stack.enter_context(patch("backend.live_config.get_live_config",
                                       return_value={"max_hold_days": 25}))
            stack.enter_context(patch("backend.brokers.alpaca.get_positions", return_value=[]))
            stack.enter_context(patch("backend.brokers.alpaca.get_order_by_id",
                                       return_value={"status": "filled", "filled_qty": 4, "legs": []}))
            cancel_mock = stack.enter_context(patch("backend.brokers.alpaca.cancel_open_orders", return_value=1))
            # A genuine filled TP sell corroborates the position actually closed.
            stack.enter_context(patch("backend.brokers.alpaca.get_orders", return_value=[
                {"ticker": "MSFT", "side": "sell", "type": "limit",
                 "filled_price": 270.0, "filled_at": "2026-08-11T14:00:00+00:00", "legs": []},
            ]))

            from backend.live_trades_tracker import check_live_exits
            closed = check_live_exits()

        cancel_mock.assert_called_once_with("MSFT")
        assert any(c["ticker"] == "MSFT" for c in closed)
        assert not any(t["id"] == trade_id for t in get_unreconciled_live_trades())

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


class TestAlertLatches:
    """Direct coverage of the persisted latch primitives (backend.db)."""

    def test_set_alert_latch_is_one_shot_and_persists(self):
        from backend.db import clear_alert_latch, set_alert_latch
        clear_alert_latch("test:one-shot")
        assert set_alert_latch("test:one-shot") is True   # newly set → caller alerts
        assert set_alert_latch("test:one-shot") is False  # already set → caller doesn't
        assert set_alert_latch("test:one-shot") is False  # still latched, no expiry
        clear_alert_latch("test:one-shot")
        assert set_alert_latch("test:one-shot") is True   # cleared → re-arms

    def test_clear_alert_latches_except_keeps_only_named_keys(self):
        from backend.db import clear_alert_latch, clear_alert_latches_except, set_alert_latch
        for t in ("AAA", "BBB", "CCC"):
            clear_alert_latch(f"untracked:{t}")
            set_alert_latch(f"untracked:{t}")

        # Only BBB is still mismatched — AAA and CCC should clear, BBB should not.
        clear_alert_latches_except("untracked:", {"untracked:BBB"})

        assert set_alert_latch("untracked:AAA") is True   # cleared → re-arms
        assert set_alert_latch("untracked:BBB") is False  # kept latched → still suppressed
        assert set_alert_latch("untracked:CCC") is True   # cleared → re-arms

        for t in ("AAA", "BBB", "CCC"):
            clear_alert_latch(f"untracked:{t}")


class TestUntrackedPosition:
    """The mirror direction: broker holds a ticker with no matching internal OPEN row.

    Dedup is a DB-persisted latch (backend.db.alert_latches), not a module-level
    set — 2026-08-07 confirmed a plain in-memory set gets wiped by any process
    restart (uvicorn --reload, a deploy), re-sending an already-latched alert
    with no relation to whether the mismatch actually recurred. These tests
    exercise the persisted table directly so a restart can't silently
    reintroduce that failure mode.
    """

    def test_broker_position_with_no_open_row_alerts(self):
        from backend.db import clear_alert_latch
        from backend.live_trades_tracker import _check_untracked_positions
        clear_alert_latch("untracked:XOM")

        with patch("backend.alerts.alert_position_untracked") as alert_mock:
            _check_untracked_positions({"XOM"}, open_trades=[])
            alert_mock.assert_called_once_with("XOM")

        # Second call with the same mismatch does not re-alert (latch persists
        # across calls — and would persist across a process restart too,
        # since it's read from the DB each time, not a process-lifetime set).
        with patch("backend.alerts.alert_position_untracked") as alert_mock2:
            _check_untracked_positions({"XOM"}, open_trades=[])
            alert_mock2.assert_not_called()

        # Once it clears, a future recurrence re-alerts.
        _check_untracked_positions(set(), open_trades=[])
        with patch("backend.alerts.alert_position_untracked") as alert_mock3:
            _check_untracked_positions({"XOM"}, open_trades=[])
            alert_mock3.assert_called_once_with("XOM")

        clear_alert_latch("untracked:XOM")

    def test_latch_survives_simulated_process_restart(self):
        """
        The exact defect class the in-memory set had: a restart between two
        checks of the same still-standing mismatch must NOT re-alert. Simulated
        by reloading live_trades_tracker (fresh module globals) between calls —
        the old _untracked_alerted set would have reset to empty here and
        re-alerted; the DB latch must not.
        """
        import importlib
        from backend.db import clear_alert_latch
        import backend.live_trades_tracker as ltt

        clear_alert_latch("untracked:BA")
        with patch("backend.alerts.alert_position_untracked") as alert_mock:
            ltt._check_untracked_positions({"BA"}, open_trades=[])
            alert_mock.assert_called_once_with("BA")

        importlib.reload(ltt)  # fresh module-level state, as a restart would produce

        with patch("backend.alerts.alert_position_untracked") as alert_mock2:
            ltt._check_untracked_positions({"BA"}, open_trades=[])
            alert_mock2.assert_not_called()

        clear_alert_latch("untracked:BA")


class TestGateRefusal:
    """gate_runner_live.run() must refuse all new entries while UNRECONCILED rows exist."""

    def test_gate_halts_on_unreconciled(self):
        from backend.db import clear_alert_latch
        clear_alert_latch("unreconciled:2026-08-07")

        with ExitStack() as stack:
            stack.enter_context(patch("backend.gate.gate_runner_live.LIVE_ENABLED", True))
            stack.enter_context(patch(
                "backend.gate.gate_runner_live.get_unreconciled_live_trades",
                return_value=[{"ticker": "HON", "id": 1}],
            ))
            stack.enter_context(patch("backend.gate.gate_runner_live._ny_today", return_value="2026-08-07"))
            alert_mock = stack.enter_context(patch("backend.alerts.alert_gate_blocked"))
            # If the halt didn't short-circuit, run() would reach broker.get_account()
            # next and this mock absence would raise — proving the halt fired first.
            account_mock = stack.enter_context(patch("backend.brokers.alpaca.get_account"))

            from backend.gate import gate_runner_live
            result = gate_runner_live.run()

        assert result == []
        alert_mock.assert_called_once()
        account_mock.assert_not_called()  # halted before touching the broker at all
        clear_alert_latch("unreconciled:2026-08-07")

    def test_unreconciled_alert_is_day_capped_not_permanently_suppressed(self):
        """
        The dedup key must include the NY trading date. Without it, a
        DB-persisted latch (unlike the old in-memory set, which reset on every
        restart) would suppress this alert forever after the first firing —
        the next day's genuine halt would alert zero times instead of once.
        """
        from backend.db import clear_alert_latch
        from backend.gate import gate_runner_live
        clear_alert_latch("unreconciled:2026-08-07")
        clear_alert_latch("unreconciled:2026-08-08")

        def run_on(day: str):
            with ExitStack() as stack:
                stack.enter_context(patch("backend.gate.gate_runner_live.LIVE_ENABLED", True))
                stack.enter_context(patch(
                    "backend.gate.gate_runner_live.get_unreconciled_live_trades",
                    return_value=[{"ticker": "HON", "id": 1}],
                ))
                stack.enter_context(patch("backend.gate.gate_runner_live._ny_today", return_value=day))
                alert_mock = stack.enter_context(patch("backend.alerts.alert_gate_blocked"))
                stack.enter_context(patch("backend.brokers.alpaca.get_account"))
                gate_runner_live.run()
                return alert_mock.call_count

        assert run_on("2026-08-07") == 1  # first firing on day 1: alerts
        assert run_on("2026-08-07") == 0  # same day, still halted: latched, no re-alert
        assert run_on("2026-08-08") == 1  # new trading day, still halted: alerts again

        clear_alert_latch("unreconciled:2026-08-07")
        clear_alert_latch("unreconciled:2026-08-08")

    def test_loss_cap_alert_is_day_capped_not_permanently_suppressed(self):
        from backend.db import clear_alert_latch
        from backend.gate import gate_runner_live
        clear_alert_latch("loss_cap:2026-08-07")
        clear_alert_latch("loss_cap:2026-08-08")

        def run_on(day: str):
            with ExitStack() as stack:
                stack.enter_context(patch("backend.gate.gate_runner_live.LIVE_ENABLED", True))
                stack.enter_context(patch(
                    "backend.gate.gate_runner_live.get_unreconciled_live_trades", return_value=[],
                ))
                stack.enter_context(patch(
                    "backend.brokers.alpaca.get_account",
                    return_value={"trading_blocked": False, "account_blocked": False, "day_pnl": -500.0},
                ))
                stack.enter_context(patch("backend.brokers.alpaca.get_positions", return_value=[]))
                # APEX-side reconstruction agrees with the broker (within the
                # data-quality threshold) — this is a genuine loss, not a
                # divergence, so it must classify as a loss-cap halt, not a
                # data-quality halt (see TestGateRefusal's data-quality tests).
                stack.enter_context(patch("backend.gate.gate_runner_live._compute_apex_day_pnl",
                                           return_value=(-480.0, [])))
                stack.enter_context(patch(
                    "backend.live_config.get_live_config",
                    return_value={"daily_loss_cap": 100.0},
                ))
                stack.enter_context(patch("backend.db.get_ticker_thresholds", return_value={}))
                stack.enter_context(patch("backend.gate.gate_runner_live._ny_today", return_value=day))
                alert_mock = stack.enter_context(patch("backend.alerts.alert_daily_loss_cap"))
                gate_runner_live.run()
                return alert_mock.call_count

        assert run_on("2026-08-07") == 1
        assert run_on("2026-08-07") == 0
        assert run_on("2026-08-08") == 1

        clear_alert_latch("loss_cap:2026-08-07")
        clear_alert_latch("loss_cap:2026-08-08")


class TestDataQualityDivergence:
    """
    2026-08-11 HON third snapshot-omission: broker day P&L hit the loss cap
    on a phantom $978 drop while APEX's own ledger (realized+unrealized) said
    $0. gate_runner_live must relabel a divergent loss-cap trip as a
    data-quality halt instead of a genuine one — same halt, honest reason.
    """

    def _run(self, day_pnl: float, apex_pnl: float, missing: list[str],
              cap: float = 100.0, latch_day: str = "2026-08-11"):
        """Runs gate_runner_live.run() once under the given inputs. Does not
        touch alert latches — callers own latch setup/teardown so day-capping
        can be exercised across repeated calls (see test_data_quality_alert_is_day_capped)."""
        from backend.gate import gate_runner_live

        with ExitStack() as stack:
            stack.enter_context(patch("backend.gate.gate_runner_live.LIVE_ENABLED", True))
            stack.enter_context(patch(
                "backend.gate.gate_runner_live.get_unreconciled_live_trades", return_value=[],
            ))
            stack.enter_context(patch(
                "backend.brokers.alpaca.get_account",
                return_value={"trading_blocked": False, "account_blocked": False, "day_pnl": day_pnl},
            ))
            stack.enter_context(patch("backend.brokers.alpaca.get_positions", return_value=[]))
            stack.enter_context(patch("backend.gate.gate_runner_live._compute_apex_day_pnl",
                                       return_value=(apex_pnl, missing)))
            stack.enter_context(patch(
                "backend.live_config.get_live_config", return_value={"daily_loss_cap": cap},
            ))
            stack.enter_context(patch("backend.db.get_ticker_thresholds", return_value={}))
            stack.enter_context(patch("backend.gate.gate_runner_live._ny_today", return_value=latch_day))
            loss_cap_mock = stack.enter_context(patch("backend.alerts.alert_daily_loss_cap"))
            dq_mock = stack.enter_context(patch("backend.alerts.alert_data_quality_divergence"))
            result = gate_runner_live.run()

        return result, loss_cap_mock, dq_mock

    def test_large_divergence_reclassifies_as_data_quality(self):
        """Broker says -$978 loss, APEX's own ledger says $0 — HON's exact shape."""
        from backend.db import clear_alert_latch
        clear_alert_latch("data_quality:2026-08-11")
        result, loss_cap_mock, dq_mock = self._run(day_pnl=-978.32, apex_pnl=0.0, missing=[])
        clear_alert_latch("data_quality:2026-08-11")
        assert result == []
        dq_mock.assert_called_once_with(-978.32, 0.0, [])
        loss_cap_mock.assert_not_called()

    def test_missing_from_broker_forces_data_quality_even_if_dollar_gap_small(self):
        """A DB-open ticker absent from the broker snapshot is itself the
        defect signature, independent of how large the dollar divergence is."""
        from backend.db import clear_alert_latch
        clear_alert_latch("data_quality:2026-08-11")
        result, loss_cap_mock, dq_mock = self._run(day_pnl=-110.0, apex_pnl=-100.0, missing=["HON"])
        clear_alert_latch("data_quality:2026-08-11")
        assert result == []
        dq_mock.assert_called_once_with(-110.0, -100.0, ["HON"])
        loss_cap_mock.assert_not_called()

    def test_agreeing_ledger_stays_a_genuine_loss_cap_halt(self):
        """Broker and APEX agree (within threshold) — this is a real loss,
        must not be relabeled as a data-quality event."""
        from backend.db import clear_alert_latch
        clear_alert_latch("loss_cap:2026-08-11")
        result, loss_cap_mock, dq_mock = self._run(day_pnl=-500.0, apex_pnl=-480.0, missing=[])
        clear_alert_latch("loss_cap:2026-08-11")
        assert result == []
        loss_cap_mock.assert_called_once_with(500.0, 100.0)
        dq_mock.assert_not_called()

    def test_data_quality_alert_is_day_capped(self):
        from backend.db import clear_alert_latch
        clear_alert_latch("data_quality:2026-08-11")
        clear_alert_latch("data_quality:2026-08-12")

        _, _, dq_mock1 = self._run(day_pnl=-978.32, apex_pnl=0.0, missing=[], latch_day="2026-08-11")
        assert dq_mock1.call_count == 1
        _, _, dq_mock2 = self._run(day_pnl=-978.32, apex_pnl=0.0, missing=[], latch_day="2026-08-11")
        assert dq_mock2.call_count == 0  # same day, still halted: latched, no re-alert
        _, _, dq_mock3 = self._run(day_pnl=-978.32, apex_pnl=0.0, missing=[], latch_day="2026-08-12")
        assert dq_mock3.call_count == 1  # new trading day: alerts again

        clear_alert_latch("data_quality:2026-08-11")
        clear_alert_latch("data_quality:2026-08-12")


class TestApexDayPnl:
    """_compute_apex_day_pnl: realized (booked today) + unrealized (matched
    open rows), with broker-missing tickers surfaced rather than zero-filled."""

    def test_realized_and_unrealized_sum(self):
        from backend.gate import gate_runner_live
        with ExitStack() as stack:
            stack.enter_context(patch(
                "backend.gate.gate_runner_live.get_live_trades_exited_since",
                return_value=[{"pnl": 12.5}, {"pnl": -4.0}],
            ))
            stack.enter_context(patch(
                "backend.gate.gate_runner_live.get_open_live_trades",
                return_value=[{"ticker": "MSFT"}, {"ticker": "CRM"}],
            ))
            pnl, missing = gate_runner_live._compute_apex_day_pnl([
                {"ticker": "MSFT", "unrealized_pnl": -4.8},
                {"ticker": "CRM", "unrealized_pnl": -1.6},
            ])
        assert pnl == pytest.approx(12.5 - 4.0 - 4.8 - 1.6)
        assert missing == []

    def test_open_trade_missing_from_broker_is_surfaced_not_zero_filled(self):
        from backend.gate import gate_runner_live
        with ExitStack() as stack:
            stack.enter_context(patch(
                "backend.gate.gate_runner_live.get_live_trades_exited_since", return_value=[],
            ))
            stack.enter_context(patch(
                "backend.gate.gate_runner_live.get_open_live_trades",
                return_value=[{"ticker": "HON"}],
            ))
            pnl, missing = gate_runner_live._compute_apex_day_pnl([])  # HON absent from broker
        assert pnl == 0.0
        assert missing == ["HON"]


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


class TestReopenUnreconciled:
    """
    reopen_unreconciled(): a third resolution path for the case neither
    CONFIRMED_EXIT nor WRITTEN_OFF describes — the position was confirmed
    still held at the broker, so the UNRECONCILED freeze was a reporting-layer
    false alarm, not a real exit or a real loss (2026-08-06/07 HON incident).
    """

    def test_reopen_clears_halt_and_restores_open_with_no_fabricated_exit(self):
        trade_id = _open_trade(ticker="TESTR", order_id="ord-reopen-1")
        mark_live_trade_unreconciled(trade_id, "test: simulated snapshot omission")
        assert any(t["id"] == trade_id for t in get_unreconciled_live_trades())

        reopen_unreconciled(
            trade_id,
            "portfolio-history replay: position held continuously, no dip",
        )

        assert not any(t["id"] == trade_id for t in get_unreconciled_live_trades())
        reopened = next(t for t in get_open_live_trades() if t["id"] == trade_id)
        assert reopened["outcome"] == "OPEN"
        assert reopened["exit_price"] is None
        assert reopened["pnl"] is None
        assert reopened["exited_at"] is None

    def test_reopen_requires_evidence(self):
        trade_id = _open_trade(ticker="TESTQ", order_id="ord-reopen-2")
        mark_live_trade_unreconciled(trade_id, "test: simulated snapshot omission")
        with pytest.raises(ValueError):
            reopen_unreconciled(trade_id, "")

    def test_reopen_refuses_a_row_that_is_not_unreconciled(self):
        trade_id = _open_trade(ticker="TESTP", order_id="ord-reopen-3")
        # Never frozen — still plain OPEN.
        with pytest.raises(ValueError):
            reopen_unreconciled(trade_id, "should not apply to an already-open row")
