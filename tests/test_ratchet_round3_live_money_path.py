"""
Ratchet round 3 (R021, APEX Saturday 2026-11-28, order item 3; the five confirmed 2026-11-27, confirm list item 15):
five money-path functions from the baseline, each with a caller on the live path, get a test and leave
tests/coverage/unexercised.txt.

  alerts.alert_position_untracked     the broker holds a ticker live_trades has no OPEN row for (live_trades_tracker:387)
  alpaca.get_prior_close              the reference for a same-day exit's daily P&L (gate_runner_live:104)
  db.get_live_trades_exited_since     the day's booked exits for the loss cap (gate_runner_live:169)
  alpaca.get_portfolio_history        the live equity curve (brokers/alpaca.py:418, the dashboard)
  alpaca.get_fee_activities           the fill reconcile's fee read (audit/fill_reconcile.py:75)
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend import alerts, db
from backend.brokers import alpaca

db.init_db()


@pytest.fixture(autouse=True)
def _clean():
    yield
    conn = db.get_db()
    try:
        conn.execute("DELETE FROM live_trades WHERE ticker LIKE 'RR3%'")
        conn.commit()
    finally:
        conn.close()


def _open(ticker: str) -> int:
    return db.insert_live_trade({
        "timestamp": "2026-11-27T15:00:00+00:00", "ticker": ticker, "sector": "Industrials",
        "alpaca_order_id": f"ord-{ticker.lower()}", "entry_price": 100.0, "qty": 2.0, "notional": 200.0,
        "tp_price": 106.0, "sl_price": 94.0})


# ── alerts.alert_position_untracked ─────────────────────────────────────────────────────────────────────────────

def test_untracked_position_mail_names_the_ticker_and_the_action(monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "_dispatch", lambda title, body: sent.append((title, body)))
    alerts.alert_position_untracked("RR3A")
    assert len(sent) == 1
    title, body = sent[0]
    assert "Position/DB mismatch — RR3A" in title
    assert "held at the broker but has no OPEN row in live_trades" in body and "before the next live cycle" in body


# ── db.get_live_trades_exited_since ─────────────────────────────────────────────────────────────────────────────

def test_exited_since_returns_booked_exits_at_or_after_the_cutoff_in_order():
    a, b, c, d = (_open(t) for t in ("RR3B", "RR3C", "RR3D", "RR3E"))
    assert db.close_live_trade(a, 101.0, 2.0, "WIN", "tp", "2026-11-27T16:00:00+00:00") == 1
    assert db.close_live_trade(b, 99.0, -2.0, "LOSS", "sl", "2026-11-27T14:30:00+00:00") == 1   # at the cutoff
    assert db.close_live_trade(c, 98.0, -4.0, "LOSS", "sl", "2026-11-26T19:00:00+00:00") == 1   # before it
    # d stays OPEN: no exit, no pnl
    got = [r for r in db.get_live_trades_exited_since("2026-11-27T14:30:00+00:00") if r["ticker"].startswith("RR3")]
    assert [r["ticker"] for r in got] == ["RR3C", "RR3B"]                     # ascending exited_at
    assert all(r["pnl"] is not None for r in got)


def test_exited_since_leaves_out_a_row_without_booked_pnl():
    a = _open("RR3F")
    conn = db.get_db()
    try:   # an UNRECONCILED freeze: an exit time but no booked pnl, by design
        conn.execute("UPDATE live_trades SET outcome='UNRECONCILED', exited_at=? WHERE id=?",
                     ("2026-11-27T16:00:00+00:00", a))
        conn.commit()
    finally:
        conn.close()
    assert not [r for r in db.get_live_trades_exited_since("2026-11-27T00:00:00+00:00") if r["ticker"] == "RR3F"]


# ── alpaca.get_fee_activities ───────────────────────────────────────────────────────────────────────────────────

def test_fee_activities_pages_through_and_keeps_the_signed_amount(monkeypatch):
    pages = [[{"id": "f1", "date": "2026-11-26", "activity_sub_type": "CAT", "net_amount": "-0.01"},
              {"id": "f2", "date": "2026-11-27", "activity_sub_type": "TAF", "net_amount": None}],
             []]
    calls = []
    client = MagicMock()
    client.get.side_effect = lambda path, params: (calls.append((path, dict(params))), pages[len(calls) - 1])[1]
    monkeypatch.setattr(alpaca, "_client", lambda: client)
    got = alpaca.get_fee_activities("2026-11-25")
    assert got == [{"date": "2026-11-26", "sub_type": "CAT", "net_amount": -0.01},
                   {"date": "2026-11-27", "sub_type": "TAF", "net_amount": 0.0}]
    assert calls[0] == ("/account/activities/FEE", {"page_size": 100, "after": "2026-11-25"})
    assert calls[1][1]["page_token"] == "f2"                                  # the next page starts after the last id


# ── alpaca.get_portfolio_history ────────────────────────────────────────────────────────────────────────────────

def test_portfolio_history_drops_zero_equity_days_and_raises_on_a_broker_error(monkeypatch):
    day = int(datetime(2026, 11, 26, tzinfo=timezone.utc).timestamp())
    hist = SimpleNamespace(base_value=100000.0, timestamp=[day - 86400, day], equity=[0.0, 101234.567])
    client = MagicMock()
    client.get_portfolio_history.return_value = hist
    monkeypatch.setattr(alpaca, "_client", lambda: client)
    assert alpaca.get_portfolio_history("1M") == [{"ts": "Start", "balance": 100000.0},
                                                  {"ts": "2026-11-26", "balance": 101234.57}]
    assert client.get_portfolio_history.call_args.kwargs["history_filter"].period == "1M"
    client.get_portfolio_history.side_effect = RuntimeError("503")
    with pytest.raises(RuntimeError, match="503"):
        alpaca.get_portfolio_history()


# ── alpaca.get_prior_close ──────────────────────────────────────────────────────────────────────────────────────

class _Bars:
    def __init__(self, rows):
        self.data = {"RR3P": rows} if rows is not None else {}

    def __getitem__(self, k):
        return self.data[k]


def _bar(day, close):
    return SimpleNamespace(timestamp=datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc), close=close)


def _prior_close(monkeypatch, rows=None, raises=None):
    seen = {}

    class Client:
        def __init__(self, *a, **k):
            pass

        def get_stock_bars(self, req):
            seen["req"] = req
            if raises:
                raise raises
            return _Bars(rows)
    monkeypatch.setattr(alpaca, "StockHistoricalDataClient", Client)
    return alpaca.get_prior_close("RR3P"), seen


def test_prior_close_is_the_last_bar_before_today_not_todays_forming_bar(monkeypatch):
    today = datetime.now(timezone.utc).date()
    got, seen = _prior_close(monkeypatch, [_bar(today - timedelta(days=2), 98.0), _bar(today - timedelta(days=1), 99.5),
                                           _bar(today, 101.0)])
    assert got == 99.5
    end = seen["req"].end                                                 # alpaca-py stores it as naive UTC
    end = end.replace(tzinfo=timezone.utc) if end.tzinfo is None else end
    assert end <= datetime.now(timezone.utc) - timedelta(minutes=15)      # outside the free plan's SIP window


def test_prior_close_is_none_on_too_few_bars_no_prior_bar_or_an_error(monkeypatch):
    today = datetime.now(timezone.utc).date()
    assert _prior_close(monkeypatch, [_bar(today, 101.0)])[0] is None
    assert _prior_close(monkeypatch, [_bar(today, 100.0), _bar(today, 101.0)])[0] is None
    assert _prior_close(monkeypatch, None)[0] is None
    assert _prior_close(monkeypatch, raises=RuntimeError("subscription"))[0] is None
