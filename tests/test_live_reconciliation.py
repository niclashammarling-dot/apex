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

    def test_cancel_orphan_brackets_actually_matches_now(self):
        """
        Direct regression for the silenced mechanism: with real Alpaca-shaped
        status/side strings (not the mangled Enum repr), an orphaned sell leg
        on a ticker with no open position must be recognized and cancelled.
        """
        from backend.live_trades_tracker import cancel_orphan_brackets

        with patch("backend.live_trades_tracker.LIVE_ENABLED", True), \
             patch("backend.brokers.alpaca.get_positions", return_value=[]), \
             patch("backend.brokers.alpaca.get_orders", return_value=[{
                 "ticker": "TOL", "side": "sell", "status": "new", "legs": [],
             }]), \
             patch("backend.brokers.alpaca.cancel_open_orders", return_value=1) as cancel_mock:
            n = cancel_orphan_brackets()

        assert n == 1
        cancel_mock.assert_called_once_with("TOL")

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
