"""
Ratchet round 1 (vault 11-14 order item 11): the two money-path functions that went unexercised on 2026-11-05.
The exit check stores a new peak only when the quote is above the stored one (the profit-lock ratchet and the TSL
anchor read it), and the quote reader returns the last close, or None on an empty or failing download.
"""
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pandas as pd
import pytest

import backend.live_trades_tracker as ltt
from backend.db import get_db, init_db, insert_live_trade, update_live_trade_peak_price

init_db()


@pytest.fixture(autouse=True)
def _clean():
    yield
    conn = get_db()
    try:
        conn.execute("DELETE FROM live_trades WHERE ticker LIKE 'PEAK%'")
        conn.commit()
    finally:
        conn.close()


def _open(price=100.0, ticker="PEAK") -> int:
    # An hour old: check_live_exits compares entry age to now (time stop), so a fixed date would drift (CHECK 74).
    return insert_live_trade({
        "timestamp": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), "ticker": ticker,
        "sector": "Industrials", "alpaca_order_id": f"ord-{ticker.lower()}-1", "entry_price": price, "qty": 2.0,
        "notional": price * 2, "tp_price": round(price * 1.06, 2), "sl_price": round(price * 0.94, 2)})


def _peak(tid: int):
    conn = get_db()
    try:
        return conn.execute("SELECT peak_price FROM live_trades WHERE id = ?", (tid,)).fetchone()[0]
    finally:
        conn.close()


def _cycle(current):
    order = {"status": "filled", "filled_avg_price": 100.0, "filled_qty": 2.0,
             "filled_at": (datetime.now(timezone.utc) - timedelta(minutes=59)).isoformat(), "legs": [{"side": "sell", "order_type": "stop", "status": "new"}]}
    with ExitStack() as stack:
        stack.enter_context(patch("backend.live_trades_tracker.LIVE_ENABLED", True))
        stack.enter_context(patch("backend.live_config.get_live_config", return_value={"max_hold_days": 99}))
        stack.enter_context(patch("backend.brokers.alpaca.get_positions", return_value=[{"ticker": "PEAK"}]))
        stack.enter_context(patch("backend.brokers.alpaca.get_order_by_id", return_value=order))
        stack.enter_context(patch("backend.live_trades_tracker._current_price", return_value=current))
        stack.enter_context(patch("backend.live_trades_tracker._maybe_ratchet_bracket_sl"))
        stack.enter_context(patch("backend.live_trades_tracker._log_vol_slope"))
        stack.enter_context(patch("backend.alerts.alert_position_unreconciled"))
        stack.enter_context(patch("backend.alerts.alert_position_untracked"))
        return ltt.check_live_exits()


def test_the_writer_stores_the_peak_on_its_row():
    tid, other = _open(), _open(ticker="PEAK2")
    update_live_trade_peak_price(tid, 107.25)
    assert _peak(tid) == 107.25 and _peak(other) is None


def test_a_quote_above_the_peak_is_stored_and_a_lower_one_is_not():
    tid = _open()
    _cycle(103.0)
    assert _peak(tid) == 103.0
    _cycle(101.5)                                   # below the stored peak: no write
    assert _peak(tid) == 103.0
    _cycle(None)                                    # no quote: the stored peak stands
    assert _peak(tid) == 103.0


def test_a_quote_at_or_under_entry_writes_nothing():
    tid = _open()
    _cycle(100.0)
    assert _peak(tid) is None


class _Ticker:
    def __init__(self, frame=None, exc=None):
        self.frame, self.exc = frame, exc

    def history(self, period):
        assert period == "2d"
        if self.exc:
            raise self.exc
        return self.frame


def test_the_quote_is_the_last_close(monkeypatch):
    frame = pd.DataFrame({"Close": [98.5, 99.75]})
    monkeypatch.setattr(ltt.yf, "Ticker", lambda t: _Ticker(frame))
    assert ltt._current_price("PEAK") == 99.75


def test_an_empty_or_failing_download_is_none(monkeypatch):
    monkeypatch.setattr(ltt.yf, "Ticker", lambda t: _Ticker(pd.DataFrame({"Close": []})))
    assert ltt._current_price("PEAK") is None
    monkeypatch.setattr(ltt.yf, "Ticker", lambda t: _Ticker(exc=RuntimeError("rate limited")))
    assert ltt._current_price("PEAK") is None
