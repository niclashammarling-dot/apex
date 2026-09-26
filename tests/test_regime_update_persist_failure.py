"""
RegimeBayes.update's in-memory result must follow the persist (2026-09-26).

_last_result is what every consumer reads (both gate runners, Lock 1, both
trackers). It was assigned before _save_session_posteriors, so a failed persist
restored _posteriors but left the gate trading on the unpersisted session's
leader and allocation, while DB, cache and history stayed on the previous one.
"""
from datetime import date, datetime
from unittest.mock import patch

import pandas as pd
import pytest

import backend.scheduler as sched
from backend import db
from backend.regime.regime_bayes import RegimeBayes
from backend.scheduler import NY

SECTORS = {"Technology": {"tickers": ["AAPL"], "etf": "XLK"},
           "Energy": {"tickers": ["XOM"], "etf": "XLE"}}


def _fresh_rb():
    conn = db.get_db()
    conn.execute("DELETE FROM sector_posterior_history")
    conn.execute("DELETE FROM sector_posteriors")
    conn.commit()
    conn.close()
    rb = RegimeBayes(SECTORS, {s: c["etf"] for s, c in SECTORS.items()}, {})
    rb._last_result = None
    return rb


def _update(rb, d):
    return rb.update(d, pd.DataFrame(), {"Technology": 0.6, "Energy": 0.4},
                     {"Technology": 0.5, "Energy": 0.5})


def test_successful_persist_moves_last_result_to_the_new_session():
    rb = _fresh_rb()
    out = _update(rb, date(2026, 9, 24))
    assert rb.last_result() is out and out.date == "2026-09-24"


def test_failed_persist_leaves_last_result_on_the_previous_session():
    rb = _fresh_rb()
    prev = _update(rb, date(2026, 9, 24))
    with patch("backend.db.persist_session_posteriors", side_effect=RuntimeError("disk")):
        with pytest.raises(RuntimeError):
            _update(rb, date(2026, 9, 25))
    assert rb.last_result() is prev
    assert rb.last_result().date == "2026-09-24"


# ── Alert on a failed run (2026-09-26) ───────────────────────────────────────

def _failing_run(monkeypatch, alert_side_effect=None, sent=None, clear=True):
    if clear:
        conn = db.get_db()
        conn.execute("DELETE FROM sector_posterior_history")
        conn.execute("DELETE FROM alert_latches")
        conn.commit()
        conn.close()
    sent = [] if sent is None else sent
    monkeypatch.setattr("backend.alerts.alert_eod_regime_failed",
                        alert_side_effect or (lambda s, e: sent.append((s, e))))
    monkeypatch.setattr(sched, "_eod_inputs", lambda target, live: (pd.DataFrame(), {}, {}))

    class _RB:
        def update(self, *a, **k):
            raise RuntimeError("persist failed")
    monkeypatch.setattr(sched, "_get_regime_bayes", lambda: _RB())
    status = sched.run_eod_regime(as_of=date(2026, 9, 25),
                                  now_ny=datetime(2026, 9, 28, 8, 30, tzinfo=NY))
    return status, sent


def test_failed_run_sends_the_alert_once_per_session(monkeypatch):
    status, sent = _failing_run(monkeypatch)
    assert status == "failed"
    assert sent == [("2026-09-25", "persist failed")]
    status, sent = _failing_run(monkeypatch, sent=sent, clear=False)   # retry, same session
    assert status == "failed"
    assert len(sent) == 1                                              # latched: no second email


def test_alert_that_raises_leaves_the_eod_return_intact(monkeypatch):
    def boom(s, e):
        raise RuntimeError("smtp down")
    status, _ = _failing_run(monkeypatch, alert_side_effect=boom)
    assert status == "failed"


# ── no_inputs (2026-09-26) ───────────────────────────────────────────────────

def _no_inputs_run(monkeypatch, download, as_of=date(2026, 9, 25)):
    conn = db.get_db()
    conn.execute("DELETE FROM sector_posterior_history")
    conn.execute("DELETE FROM alert_latches")
    conn.commit()
    conn.close()
    sent, updated = [], []
    monkeypatch.setattr("backend.alerts.alert_eod_regime_failed", lambda s, e: sent.append((s, e)))
    monkeypatch.setattr("yfinance.download", download)
    monkeypatch.setattr("backend.regime.ipo_sentiment.IpoSentiment.compute",
                        lambda self, reference_date=None: (_ for _ in ()).throw(RuntimeError("offline")))

    class _RB:
        def update(self, *a, **k):
            updated.append(a)
    monkeypatch.setattr(sched, "_get_regime_bayes", lambda: _RB())
    status = sched.run_eod_regime(as_of=as_of, now_ny=datetime(2026, 9, 28, 8, 30, tzinfo=NY))
    return status, sent, updated


def test_empty_download_is_no_inputs_and_alerts(monkeypatch):
    """yfinance returns an empty frame on a total failure instead of raising;
    before, that frame reached rb.update and a row was persisted as "ok"."""
    # yfinance 1.2.2's real total-failure frame: empty, DatetimeIndex, (ticker, field)
    # columns — a bare pd.DataFrame() has a RangeIndex, which the replay slice's
    # .index.date happens to reject, masking the defect on the parent.
    empty = pd.DataFrame(index=pd.DatetimeIndex([]),
                         columns=pd.MultiIndex.from_product([["XLK"], ["Open", "Close"]]))
    status, sent, updated = _no_inputs_run(monkeypatch, lambda *a, **k: empty)
    assert status == "no_inputs"
    assert updated == []
    assert [s for s, _ in sent] == ["2026-09-25"]


def test_raising_download_is_no_inputs_and_alerts(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("yahoo down")
    status, sent, updated = _no_inputs_run(monkeypatch, boom)
    assert status == "no_inputs" and updated == [] and len(sent) == 1


def test_no_inputs_alert_is_gated_on_a_session(monkeypatch):
    sent = []
    monkeypatch.setattr("backend.alerts.alert_eod_regime_failed", lambda s, e: sent.append(s))
    sched._alert_eod_regime_failed(date(2026, 11, 26), "x", key="no_inputs")   # Thanksgiving
    assert sent == []
