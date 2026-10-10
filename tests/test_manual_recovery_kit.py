"""
The manual recovery kit must stay importable (2026-10-10). No code calls it — an
operator runs it by hand when a position is still held after an UNRECONCILED
freeze (HON, 2026-08-10): reopen the row, re-arm broker-side protection with a
standalone OCO pair, repoint the ratchet at the new order. A whole-codebase
"referenced nowhere" sweep deleted the last step on 2026-10-10; it was restored
the same day. Without it a recovered position's profit-lock ratchet silently
reads a terminal parent order.
"""
import inspect


def test_recovery_kit_is_present():
    from backend.brokers.alpaca import place_oco_exit
    from backend.db import reopen_unreconciled, update_live_trade_order_id
    assert {"trade_id", "evidence"} <= set(inspect.signature(reopen_unreconciled).parameters)
    assert {"ticker", "qty", "tp_price", "sl_price"} <= set(inspect.signature(place_oco_exit).parameters)
    assert {"trade_id", "alpaca_order_id"} <= set(inspect.signature(update_live_trade_order_id).parameters)


def test_order_id_repoint_writes_the_row():
    from backend.db import get_db, insert_live_trade, update_live_trade_order_id
    tid = insert_live_trade({"timestamp": "2031-03-07T15:00:00+00:00", "ticker": "KITX", "sector": "Technology",
                             "alpaca_order_id": "old", "entry_price": 10.0, "qty": 1, "notional": 10.0,
                             "tp_price": 11.0, "sl_price": 9.0})
    conn = get_db()
    try:
        update_live_trade_order_id(tid, "oco-new")
        assert conn.execute("SELECT alpaca_order_id FROM live_trades WHERE id = ?", (tid,)).fetchone()[0] == "oco-new"
    finally:
        # The test DB is shared across the session: an OPEN row left here is read by every
        # later test that scans open live positions (found 2026-10-10).
        conn.execute("DELETE FROM live_trades WHERE id = ?", (tid,))
        conn.commit()
        conn.close()
