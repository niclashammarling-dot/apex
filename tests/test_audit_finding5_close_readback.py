"""
Codebase audit finding 5 (R064 a, Niclas 2026-11-14): a time-stop or regime close reads its
own order back by id for up to CLOSE_FILL_READBACK_S and books the fill. Still unfilled:
the quote is booked as before, and exit_confidence = QUOTE_EXIT names the close order.
CHECK 89 counts those rows: WARNING for this week's, INFO for the total.

Niclas's conditions: the fallback is tested, not only the readback that succeeds; CHECK 89
alarms on such a row; the window is a registered parameter (audit/PARAMETERS.json).
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

import backend.live_trades_tracker as ltt
import backend.market_calendar as mc
import backend.scheduler as scheduler
from backend.db import QUOTE_EXIT, close_live_trade, get_db, init_db, insert_live_trade

init_db()


@pytest.fixture(autouse=True)
def _clean_and_fast(monkeypatch):
    monkeypatch.setattr(ltt, "CLOSE_FILL_POLL_S", 0.0, raising=False)   # raising=False: the parent control
    monkeypatch.setattr(ltt, "CLOSE_FILL_READBACK_S", 0.05, raising=False)
    yield
    conn = get_db()
    try:
        conn.execute("DELETE FROM live_trades")
        conn.commit()
    finally:
        conn.close()


def _row(tid):
    conn = get_db()
    try:
        return dict(conn.execute("SELECT * FROM live_trades WHERE id = ?", (tid,)).fetchone())
    finally:
        conn.close()


def _open(ticker="RGM", price=100.0, ts=None):
    return insert_live_trade({
        "timestamp": ts or (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        "ticker": ticker, "sector": "Industrials", "alpaca_order_id": f"ord-{ticker.lower()}-1",
        "entry_price": price, "qty": 4.0, "notional": price * 4.0,
        "tp_price": round(price * 1.06, 2), "sl_price": round(price * 0.94, 2)})


def _broker(reads):
    """get_order_by_id answers from `reads` in turn, then repeats the last one."""
    b = MagicMock()
    seq = list(reads)
    b.get_order_by_id.side_effect = lambda oid: seq.pop(0) if len(seq) > 1 else seq[0]
    return b


FILLED = {"status": "filled", "filled_avg_price": 98.40, "filled_qty": 4.0, "qty": 4.0,
          "filled_at": "2026-11-16T15:30:01+00:00"}
PENDING = {"status": "new", "filled_avg_price": None, "filled_qty": None, "qty": 4.0, "filled_at": None}


def test_readback_books_the_close_orders_fill():
    b = _broker([PENDING, FILLED])
    price, at, conf = ltt._book_close(b, "RGM", {"order_id": "close-1", "filled_avg_price": None}, 97.0)
    assert (price, at, conf) == (98.40, "2026-11-16T15:30:01+00:00", None)
    assert b.get_order_by_id.call_args.args == ("close-1",)


def test_fill_after_the_window_books_the_quote_and_names_the_order():
    b = _broker([PENDING])                     # the fill arrives after the window
    price, _, conf = ltt._book_close(b, "RGM", {"order_id": "close-2", "filled_avg_price": None}, 97.0)
    assert price == 97.0
    assert conf.startswith(QUOTE_EXIT) and "close-2" in conf


def test_a_partial_fill_is_not_booked():
    part = {**FILLED, "status": "partially_filled", "filled_qty": 2.0}
    _, _, conf = ltt._book_close(_broker([part]), "RGM", {"order_id": "close-3", "filled_avg_price": None}, 97.0)
    assert conf is not None and "close-3" in conf


def _regime_exit(monkeypatch, reads):
    class _Res:
        date = "2026-11-13"
        leaderboard: list = []
    monkeypatch.setattr(ltt, "LIVE_ENABLED", True)   # the live check returns before its guard otherwise (CI 329)
    monkeypatch.setattr(scheduler, "_get_regime_bayes", lambda: MagicMock(last_result=lambda: _Res()))
    monkeypatch.setattr(mc, "sessions_since", lambda d: 1)
    monkeypatch.setattr(ltt, "_ticker_consecutive_down_days", lambda t, n: True)
    monkeypatch.setattr(ltt, "_current_price", lambda t: 97.0)
    entry = {"status": "filled", "filled_avg_price": 100.0, "filled_qty": 4.0, "qty": 4.0,
             "filled_at": "2026-11-10T14:31:00+00:00", "legs": []}
    seq = [entry] + list(reads)
    with patch("backend.brokers.alpaca.close_position",
               return_value={"order_id": "close-9", "ticker": "RGM", "filled_avg_price": None}), \
         patch("backend.brokers.alpaca.get_order_by_id",
               side_effect=lambda oid: seq.pop(0) if len(seq) > 1 else seq[0]), \
         patch("backend.alerts.alert_regime_exits"):
        return ltt.check_live_regime_exits()


def test_regime_close_with_a_late_fill_writes_quote_exit_on_the_row(monkeypatch):
    tid = _open()
    closed = _regime_exit(monkeypatch, [PENDING])
    row = _row(tid)
    assert closed and row["outcome"] != "OPEN" and row["exit_reason"] == "REGIME"
    assert row["exit_price"] == 97.0
    assert row["exit_confidence"].startswith(QUOTE_EXIT) and "close-9" in row["exit_confidence"]


def test_regime_close_read_back_in_time_books_the_fill(monkeypatch):
    tid = _open()
    _regime_exit(monkeypatch, [FILLED])
    row = _row(tid)
    assert row["exit_price"] == 98.40 and row["exit_confidence"] == "confirmed"


class TestCheck89QuoteExit:
    NOW = datetime(2026, 11, 20, 21, 0, tzinfo=timezone.utc)

    @pytest.fixture
    def run89(self, tmp_path, monkeypatch):
        import shutil

        import audit.checks_data as cd
        import backend.db as db_module
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "live_config.json").write_text('{"live_account_since": "2026-07-07"}')
        monkeypatch.setattr(cd, "REPO", tmp_path)
        found = []
        monkeypatch.setattr(cd, "flag", lambda num, name, sev, where, msg: found.append((sev, msg)))
        monkeypatch.setattr(cd, "_check89_reconcile", lambda now: None)

        def run():
            shutil.copy(db_module.DB_PATH, tmp_path / "data" / "apex.db")
            found.clear()
            cd.check89(now=self.NOW)
            return list(found)
        return run

    def _quoted(self, exited_at):
        tid = _open("QTE", ts="2026-11-02T14:40:00+00:00")
        close_live_trade(tid, 97.0, -12.0, "LOSS", "TIME", exited_at,
                         exit_confidence=f"{QUOTE_EXIT}: close order close-7 unfilled after 3s readback, booked at the quote")
        return tid

    def test_this_weeks_quote_exit_warns_with_the_order(self, run89):
        self._quoted("2026-11-19T15:30:00+00:00")
        warn = [m for s, m in run89() if s == "WARNING" and QUOTE_EXIT in m]
        assert len(warn) == 1 and "QTE" in warn[0] and "close-7" in warn[0]

    def test_an_older_quote_exit_is_counted_not_warned(self, run89):
        self._quoted("2026-11-02T15:30:00+00:00")
        f = run89()
        assert not [m for s, m in f if s == "WARNING" and QUOTE_EXIT in m]
        assert [m for s, m in f if s == "INFO" and f"1 close(s) booked at the quote" in m]

    def test_a_confirmed_close_is_not_counted(self, run89):
        tid = _open("OK", ts="2026-11-02T14:40:00+00:00")
        close_live_trade(tid, 98.4, -6.4, "LOSS", "TIME", "2026-11-19T15:30:00+00:00")
        assert not [m for s, m in run89() if QUOTE_EXIT in m]
