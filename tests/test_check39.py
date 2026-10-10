"""
CHECK 39 corrected 2026-10-10: peak_price = entry is correct for a position that
has only fallen (ADI: entry 421.59, polls since 400.52–408.06 — flagged as
"trailing stop silently disabled" until then). The check now flags a peak that
lags the prices seen since entry, or a position with no prices at all.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def run39(tmp_path, monkeypatch):
    import audit.checks_data as cd
    dbf = tmp_path / "data" / "apex.db"
    dbf.parent.mkdir()
    c = sqlite3.connect(dbf)
    c.execute("CREATE TABLE live_trades (id INTEGER, ticker TEXT, timestamp TEXT, entry_price REAL, "
              "peak_price REAL, outcome TEXT)")
    c.execute("CREATE TABLE signals (ticker TEXT, timestamp TEXT, price REAL)")
    c.commit()
    c.close()
    monkeypatch.setattr(cd, "REPO", tmp_path)
    found = []
    monkeypatch.setattr(cd, "flag", lambda num, name, sev, where, msg: found.append((sev, msg)))

    def run(entry, peak, prices, days_ago=5):
        entry_ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
        c = sqlite3.connect(dbf)
        c.execute("DELETE FROM live_trades")
        c.execute("DELETE FROM signals")
        c.execute("INSERT INTO live_trades VALUES (1, 'TKR', ?, ?, ?, 'OPEN')", (entry_ts, entry, peak))
        for k, p in enumerate(prices):
            ts = (datetime.now(timezone.utc) - timedelta(days=days_ago) + timedelta(hours=k + 1)).isoformat()
            c.execute("INSERT INTO signals VALUES ('TKR', ?, ?)", (ts, p))
        c.commit()
        c.close()
        found.clear()
        cd.check39()
        return list(found)
    return run


def test_the_1010_adi_shape_is_clean(run39):
    """Only fell since entry: peak = entry is correct."""
    assert run39(421.59, 421.59, [408.06, 404.0, 400.52]) == []


def test_peak_lagging_a_higher_price_warns(run39):
    f = run39(100.0, 100.0, [101.0, 106.0, 104.0])
    assert f and "lags prices seen since entry" in f[0][1] and "max seen 106.00" in f[0][1]


def test_within_quote_tolerance_is_clean(run39):
    assert run39(100.0, 105.0, [105.3]) == []


def test_no_prices_since_entry_warns(run39):
    f = run39(100.0, 100.0, [], days_ago=7)
    assert f and "no price since entry" in f[0][1]


def test_null_peak_reads_as_entry(run39):
    assert run39(100.0, None, [99.0]) == []
