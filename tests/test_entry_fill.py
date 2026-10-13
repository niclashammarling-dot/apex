"""
Entry is the fill, not the quote (2026-10-13; traced 10-12: 56/56 since-reset rows
held signal["price"]). The tracker replaces entry_price with the parent order's
filled_avg_price once, keeps the quote in signal_price, and the exit rules (profit-lock
trigger, TP/SL/TSL leg classification) stay anchored to the quote until the re-anchor
ruling.
"""
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.db import get_db, init_db, insert_live_trade, record_entry_fill

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


def _row(tid):
    conn = get_db()
    try:
        return dict(conn.execute("SELECT * FROM live_trades WHERE id = ?", (tid,)).fetchone())
    finally:
        conn.close()


def _open_trade(ticker="FILL", price=100.0, qty=4.0, signal_price=None, ts=None):
    """ts defaults to an hour ago: check_live_exits compares entry age to now (time stop),
    so a fixed date would drift into another branch (CHECK 74)."""
    row = {
        "timestamp":       ts or (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        "ticker":          ticker,
        "sector":          "Industrials",
        "alpaca_order_id": f"ord-{ticker.lower()}-1",
        "entry_price":     price,
        "qty":             qty,
        "notional":        price * qty,
        "tp_price":        round(price * 1.06, 2),
        "sl_price":        round(price * 0.94, 2),
    }
    if signal_price is not None:
        row["signal_price"] = signal_price
    return insert_live_trade(row)


def test_insert_keeps_the_quote_as_signal_price():
    tid = _open_trade(price=100.0)
    r = _row(tid)
    assert r["signal_price"] == 100.0 and r["entry_price"] == 100.0 and r["entry_filled_at"] is None


def test_record_entry_fill_writes_once():
    tid = _open_trade(price=100.0)
    assert record_entry_fill(tid, 101.0, "2026-10-13T13:40:02+00:00") == 1
    assert record_entry_fill(tid, 999.0, "2026-10-13T13:45:00+00:00") == 0
    r = _row(tid)
    assert r["entry_price"] == 101.0 and r["signal_price"] == 100.0
    assert r["entry_filled_at"] == "2026-10-13T13:40:02+00:00"


def test_record_entry_fill_recomputes_a_closed_row():
    """A capture landing after the exit (backfill, regime exit in another job) leaves
    pnl and WIN/LOSS consistent with the fill: a +$2 WIN on the quote is a -$2 LOSS on
    a fill $1 above the exit."""
    tid = _open_trade(price=100.0, qty=2.0)
    conn = get_db()
    try:
        conn.execute("UPDATE live_trades SET exit_price = 101.0, pnl = 2.0, outcome = 'WIN', "
                     "exit_reason = 'TP' WHERE id = ?", (tid,))
        conn.commit()
    finally:
        conn.close()
    record_entry_fill(tid, 102.0, "2026-10-13T13:40:02+00:00")
    r = _row(tid)
    assert r["pnl"] == -2.0 and r["outcome"] == "LOSS"


def _run_exit_check(order, current=None):
    with ExitStack() as stack:
        stack.enter_context(patch("backend.live_trades_tracker.LIVE_ENABLED", True))
        stack.enter_context(patch("backend.live_config.get_live_config", return_value={"max_hold_days": 99}))
        stack.enter_context(patch("backend.brokers.alpaca.get_positions", return_value=[{"ticker": "FILL"}]))
        stack.enter_context(patch("backend.brokers.alpaca.get_order_by_id", return_value=order))
        stack.enter_context(patch("backend.live_trades_tracker._current_price", return_value=current))
        stack.enter_context(patch("backend.live_trades_tracker._log_vol_slope"))
        stack.enter_context(patch("backend.alerts.alert_position_unreconciled"))
        stack.enter_context(patch("backend.alerts.alert_position_untracked"))
        from backend.live_trades_tracker import check_live_exits
        return check_live_exits()


def test_exit_check_captures_the_fill_and_books_pnl_against_it():
    tid = _open_trade(price=100.0, qty=4.0)
    tp_leg = {"side": "sell", "order_type": "limit", "status": "filled",
              "filled_avg_price": 106.0, "filled_at": "2026-10-14T15:00:00+00:00"}
    order = {"status": "filled", "filled_avg_price": 101.0, "filled_qty": 4.0,
             "filled_at": "2026-10-13T13:40:02+00:00", "legs": [tp_leg]}
    closed = _run_exit_check(order)
    r = _row(tid)
    assert r["entry_price"] == 101.0 and r["signal_price"] == 100.0
    assert [c["ticker"] for c in closed] == ["FILL"]
    assert r["pnl"] == 20.0 and r["exit_reason"] == "TP"   # (106 - 101) * 4


def test_partial_parent_is_not_captured():
    tid = _open_trade(price=100.0)
    order = {"status": "partially_filled", "filled_avg_price": 101.0, "filled_qty": 2.0, "legs": []}
    _run_exit_check(order)
    r = _row(tid)
    assert r["entry_price"] == 100.0 and r["entry_filled_at"] is None


def test_profit_lock_trigger_measures_from_the_signal_price():
    """Signal 100, fill 103, peak 104.5, trigger 4%: 4.5% from the quote clears the
    trigger (the leg fetch happens); 1.5% from the fill would not."""
    from backend.live_trades_tracker import _maybe_ratchet_bracket_sl
    trade = {"id": 1, "ticker": "FILL", "alpaca_order_id": "ord-fill-1",
             "entry_price": 103.0, "signal_price": 100.0, "qty": 4.0}
    broker = MagicMock()
    broker.get_order_by_id.return_value = {"legs": []}
    cfg = {"profit_lock_trigger_pct": 0.04, "profit_lock_trail_pct": 0.01}
    _maybe_ratchet_bracket_sl(trade, 104.5, cfg, broker)
    broker.get_order_by_id.assert_called_once()

    # Control: the same trade anchored on the fill stays below the trigger.
    broker.reset_mock()
    _maybe_ratchet_bracket_sl({**trade, "signal_price": 103.0}, 104.5, cfg, broker)
    broker.get_order_by_id.assert_not_called()


ENTRY = "2026-10-13T13:40:00+00:00"   # CHECK 89 tests pass a fixed now, so fixed entries do not decay


class TestCheck89:
    @pytest.fixture
    def run89(self, tmp_path, monkeypatch):
        import shutil
        from datetime import datetime, timezone

        import audit.checks_data as cd
        import backend.db as db_module
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "live_config.json").write_text('{"live_account_since": "2026-07-07"}')
        monkeypatch.setattr(cd, "REPO", tmp_path)
        found = []
        monkeypatch.setattr(cd, "flag", lambda num, name, sev, where, msg: found.append((sev, msg)))

        def run():
            shutil.copy(db_module.DB_PATH, tmp_path / "data" / "apex.db")
            found.clear()
            cd.check89(now=datetime(2026, 10, 13, 15, 0, tzinfo=timezone.utc))
            return list(found)
        return run

    def test_uncaptured_entry_warns(self, run89):
        _open_trade("NOFILL", price=100.0, ts=ENTRY)   # 13:40 UTC, check at 15:00: past the grace
        f = run89()
        assert any(s == "WARNING" and "NOFILL" in m for s, m in f)

    def test_captured_entry_is_info_with_slip(self, run89):
        tid = _open_trade("GOT", price=100.0, ts=ENTRY)
        record_entry_fill(tid, 102.0, "2026-10-13T13:40:02+00:00")
        f = run89()
        assert [s for s, _ in f] == ["INFO"] and "1/1 entries at the fill" in f[0][1] and "+2.00%" in f[0][1]

    def test_entry_inside_the_grace_is_not_flagged(self, run89):
        insert_live_trade({"timestamp": "2026-10-13T14:50:00+00:00", "ticker": "NEW", "sector": "Energy",
                           "alpaca_order_id": "ord-new", "entry_price": 50.0, "qty": 1, "notional": 50.0,
                           "tp_price": 53.0, "sl_price": 47.0})
        assert run89() == []

    def test_pre_reset_rows_are_outside_the_population(self, run89):
        insert_live_trade({"timestamp": "2026-06-25T17:36:37+00:00", "ticker": "OLD", "sector": "Energy",
                           "alpaca_order_id": "ord-old", "entry_price": 50.0, "qty": 1, "notional": 50.0,
                           "tp_price": 53.0, "sl_price": 47.0})
        assert run89() == []
