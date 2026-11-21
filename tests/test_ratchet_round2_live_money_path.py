"""
Ratchet round 2 (R021, APEX Saturday 2026-11-21, order item 4): five money-path functions from the baseline, each
on the live path, get a test and leave tests/coverage/unexercised.txt.

  db.set_live_trade_profit_lock_activated   the profit-lock ratchet's flag on its own row (live_trades_tracker:264)
  db.get_open_live_tickers                  the live gate's held-ticker set (gate_runner_live:461)
  alpaca.close_position                     a live close: bracket legs cancelled first, then the position
  alpaca.cancel_open_orders                 the OCO legs cancelled before a manual or rule close
  live_trades_tracker._ticker_consecutive_down_days   the regime exit's ticker condition (live_trades_tracker:819)
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

import backend.live_trades_tracker as ltt
from backend import db
from backend.brokers import alpaca


db.init_db()


@pytest.fixture(autouse=True)
def _clean():
    yield
    conn = db.get_db()
    try:
        conn.execute("DELETE FROM live_trades WHERE ticker LIKE 'RR2%'")
        conn.commit()
    finally:
        conn.close()


def _open(ticker: str) -> int:
    return db.insert_live_trade({
        "timestamp": "2026-11-20T15:00:00+00:00", "ticker": ticker, "sector": "Industrials",
        "alpaca_order_id": f"ord-{ticker.lower()}", "entry_price": 100.0, "qty": 2.0, "notional": 200.0,
        "tp_price": 106.0, "sl_price": 94.0})


def _flag(tid: int):
    conn = db.get_db()
    try:
        return conn.execute("SELECT profit_lock_activated FROM live_trades WHERE id = ?", (tid,)).fetchone()[0]
    finally:
        conn.close()


# --- db ---------------------------------------------------------------------------------------------------

def test_profit_lock_flag_is_set_on_its_own_row_only():
    a, b = _open("RR2A"), _open("RR2B")
    assert not _flag(a) and not _flag(b)
    db.set_live_trade_profit_lock_activated(a)
    assert _flag(a) == 1 and not _flag(b)
    db.set_live_trade_profit_lock_activated(a)          # a second ratchet on the same trade keeps it set
    assert _flag(a) == 1


def test_open_live_tickers_holds_open_rows_and_drops_a_closed_one():
    a, _ = _open("RR2C"), _open("RR2D")
    with pytest.raises(db.sqlite3.IntegrityError):       # one open row per ticker is the schema's guard
        _open("RR2D")
    held = {t for t in db.get_open_live_tickers() if t.startswith("RR2")}
    assert held == {"RR2C", "RR2D"}
    assert db.close_live_trade(a, 101.0, 2.0, "WIN", "tp", "2026-11-20T16:00:00+00:00") == 1
    held = {t for t in db.get_open_live_tickers() if t.startswith("RR2")}
    assert held == {"RR2D"}


# --- Alpaca broker ----------------------------------------------------------------------------------------

def _client(orders=(), cancel_fails=(), close_order=None, close_raises=None):
    c = MagicMock()
    c.get_orders.return_value = [SimpleNamespace(id=o) for o in orders]

    def cancel(oid):
        if oid in cancel_fails:
            raise RuntimeError("422 order not cancelable")
    c.cancel_order_by_id.side_effect = cancel
    if close_raises:
        c.close_position.side_effect = close_raises
    else:
        c.close_position.return_value = close_order
    return c


def test_cancel_open_orders_counts_only_what_it_cancelled(monkeypatch):
    c = _client(orders=["tp-1", "sl-1", "x-1"], cancel_fails={"x-1"})
    monkeypatch.setattr(alpaca, "_client", lambda: c)
    assert alpaca.cancel_open_orders("RR2E") == 2
    req = c.get_orders.call_args.kwargs["filter"]
    assert req.symbols == ["RR2E"] and str(req.status.value) == "open"
    assert [call.args[0] for call in c.cancel_order_by_id.call_args_list] == ["tp-1", "sl-1", "x-1"]


def test_cancel_open_orders_raises_when_the_order_list_cannot_be_read(monkeypatch):
    c = MagicMock()
    c.get_orders.side_effect = RuntimeError("503")
    monkeypatch.setattr(alpaca, "_client", lambda: c)
    with pytest.raises(RuntimeError):
        alpaca.cancel_open_orders("RR2F")


def test_close_position_cancels_the_legs_before_closing(monkeypatch):
    calls = []
    c = _client(orders=["tp-1", "sl-1"],
                close_order=SimpleNamespace(id="close-1", filled_avg_price=None))
    c.cancel_order_by_id.side_effect = lambda oid: calls.append(("cancel", oid))
    c.close_position.side_effect = lambda t: calls.append(("close", t)) or SimpleNamespace(id="close-1", filled_avg_price=None)
    monkeypatch.setattr(alpaca, "_client", lambda: c)
    out = alpaca.close_position("RR2G")
    assert calls == [("cancel", "tp-1"), ("cancel", "sl-1"), ("close", "RR2G")]
    assert out == {"order_id": "close-1", "ticker": "RR2G", "filled_avg_price": None}   # not filled yet: None, not 0


def test_close_position_reports_a_fill_price_and_raises_on_a_broker_error(monkeypatch):
    c = _client(close_order=SimpleNamespace(id="close-2", filled_avg_price="101.25"))
    monkeypatch.setattr(alpaca, "_client", lambda: c)
    assert alpaca.close_position("RR2H")["filled_avg_price"] == 101.25
    c = _client(close_raises=RuntimeError("40410000 position not found"))
    monkeypatch.setattr(alpaca, "_client", lambda: c)
    with pytest.raises(RuntimeError):
        alpaca.close_position("RR2H")


def test_close_position_is_not_attempted_when_the_legs_cannot_be_read(monkeypatch):
    c = MagicMock()
    c.get_orders.side_effect = RuntimeError("503")
    monkeypatch.setattr(alpaca, "_client", lambda: c)
    with pytest.raises(RuntimeError):
        alpaca.close_position("RR2I")
    c.close_position.assert_not_called()                 # legs still holding the qty would reject the close


# --- regime exit's ticker condition -------------------------------------------------------------------------

def _hist(closes):
    return pd.DataFrame({"Close": closes})


@pytest.mark.parametrize("closes, n, expected", [
    ([10, 9.9, 9.8, 9.7, 9.6, 9.5], 5, True),           # five down closes in a row
    ([10, 9.9, 9.8, 9.8, 9.6, 9.5], 5, False),          # one flat close breaks the run
    ([9, 10, 9.9, 9.8, 9.7, 9.6, 9.5], 5, True),        # only the last n returns count
    ([10, 9.9, 9.8, 9.7, 9.6], 5, False),               # n closes give n-1 returns: not enough
    ([], 5, False),                                      # empty history
])
def test_consecutive_down_days(monkeypatch, closes, n, expected):
    seen = {}

    def ticker(sym):
        seen["sym"] = sym
        return SimpleNamespace(history=lambda period: seen.setdefault("period", period) and _hist(closes))
    monkeypatch.setattr(ltt.yf, "Ticker", ticker)
    assert ltt._ticker_consecutive_down_days("RR2J", n) is expected
    assert seen == {"sym": "RR2J", "period": f"{n + 5}d"}


def test_consecutive_down_days_is_false_when_the_download_fails(monkeypatch):
    # The regime exit keeps the position when yfinance fails, with no log line: recorded as a finding
    # (vault INDEX, 2026-11-21), pinned here so a change to it is a decision, not a drift.
    def ticker(sym):
        raise RuntimeError("yfinance down")
    monkeypatch.setattr(ltt.yf, "Ticker", ticker)
    assert ltt._ticker_consecutive_down_days("RR2K", 5) is False
