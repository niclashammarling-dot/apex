"""
RegimeBayes.update's in-memory result must follow the persist (2026-09-26).

_last_result is what every consumer reads (both gate runners, Lock 1, both
trackers). It was assigned before _save_session_posteriors, so a failed persist
restored _posteriors but left the gate trading on the unpersisted session's
leader and allocation, while DB, cache and history stayed on the previous one.
"""
from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest

from backend import db
from backend.regime.regime_bayes import RegimeBayes

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
