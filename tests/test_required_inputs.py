"""Required inputs on the money path are read so that their absence shows (2026-11-28).

Vault: raw/notes/2026-11/2026-11-28-apex-required-input-defaults-money-path-scope.md § Build 1, M12's principle (one
value per fact, R090 a, R150 b) beyond the config files. Each input here is guaranteed by its producer, so a missing
one is a defect and now raises at its reader, where until then a default stood in for it:
- a sector's tickers (data/tickers.json defines them for every sector) dropped the sector from the candidate universe;
- a sector's ETF (every RegimeBayes constructor builds the map from sectors_cfg's "etf") gave an ETF streak of 0 and
  flat decline stats;
- the rotation regime and leader streak (compute_sector_regime sets both on its one available path) became "neutral"
  and 0 days.
The drawdown guard's inputs are in tests/test_gate_runners.py (TestMechanicalDrawdownGuard). The runners' execution sort
reads signal_score as every other reader of it does; _evaluate already subscripts it, so the sort's old default 0 was
the one place a missing score would have ranked a candidate last.
"""
from datetime import date, datetime, timezone
from unittest.mock import patch

import pandas as pd
import pytest

from backend.db import get_db, get_l3_score_candidates, init_db

init_db()
NOW = datetime(2031, 3, 7, 15, 0, tzinfo=timezone.utc)            # a Friday NYSE session, after the open


def _signal(ticker: str) -> None:
    conn = get_db()
    try:
        conn.execute("INSERT INTO signals (timestamp, ticker, sector, price, signal_score, bar_date) "
                     "VALUES ('2031-03-07T14:45:00+00:00', ?, 'Technology', 100.0, 0.9, '2031-03-07')", (ticker,))
        conn.commit()
    finally:
        conn.close()


def test_a_sector_without_tickers_raises_in_the_candidate_universe(monkeypatch):
    _signal("RQA")
    monkeypatch.setattr("backend.ticker_config.get_sectors",
                        lambda: {"Technology": {"etf": "XLK", "tickers": ["RQA"]}})
    assert "RQA" in {c["ticker"] for c in get_l3_score_candidates(threshold=0.5, now=NOW)}     # control
    monkeypatch.setattr("backend.ticker_config.get_sectors",
                        lambda: {"Technology": {"etf": "XLK", "tickers": ["RQA"]}, "Energy": {"etf": "XLE"}})
    with pytest.raises(KeyError, match="tickers"):
        get_l3_score_candidates(threshold=0.5, now=NOW)


def test_a_sector_missing_from_the_etf_map_raises_in_the_regime_signals():
    from backend.regime.regime_bayes import RegimeBayes
    cfg = {"Technology": {"etf": "XLK", "tickers": ["AAA"]}, "Energy": {"etf": "XLE", "tickers": ["BBB"]}}
    rb = RegimeBayes(cfg, {"Technology": "XLK"}, persisted=False)
    with pytest.raises(KeyError, match="Energy"):
        rb._etf_signed_streak("Energy", pd.DataFrame(), date(2031, 3, 7))
    with pytest.raises(KeyError, match="Energy"):
        rb._sector_decline_stats("Energy", pd.DataFrame(), date(2031, 3, 7))


AVAILABLE = {"available": True, "leader": "Technology", "leader_streak": 4, "regime": "risk_on",
             "sectors": {"Technology": {"score": 0.7, "signal": "rising", "streak_days": 4}}}


@pytest.mark.parametrize("drop", ["regime", "leader_streak"])
def test_the_rotation_forecast_needs_the_regime_and_the_leader_streak(drop):
    from backend import sector_transitions as st
    regime = {k: v for k, v in AVAILABLE.items() if k != drop}
    with patch("backend.sector_regime.compute_sector_regime", return_value=regime):
        with pytest.raises(KeyError, match=drop):
            st.get_rotation_forecast()


def test_the_ticker_rotation_scores_need_the_regime():
    from backend import sector_transitions as st
    regime = {k: v for k, v in AVAILABLE.items() if k != "regime"}
    signals = {"AAA": {"sector": "Technology", "velocity_5d": 0.01, "signal": "rising"}}
    with patch("backend.sector_regime.compute_sector_regime", return_value=regime), \
         patch("backend.sector_regime.compute_ticker_signals", return_value=signals):
        with pytest.raises(KeyError, match="regime"):
            st.compute_ticker_rotation_scores()
    with patch("backend.sector_regime.compute_sector_regime", return_value={"available": False}):
        assert st.compute_ticker_rotation_scores() == {}                       # the unavailable path is unchanged


def test_the_rotation_reads_velocity_and_signal_as_the_producer_writes_them():
    """compute_ticker_signals writes velocity_5d and signal for every ticker; the defaults (0.0, "weak") were dead
    code that would have scored a malformed ticker as neutral momentum and weak breadth."""
    from backend import sector_transitions as st
    full = {"AAA": {"sector": "Technology", "velocity_5d": 0.01, "signal": "rising"}}
    with patch("backend.sector_regime.compute_sector_regime", return_value=AVAILABLE), \
         patch("backend.sector_regime.compute_ticker_signals", return_value=full):
        assert "AAA" in st.compute_ticker_rotation_scores()                    # control: complete input scores
        assert st.compute_sector_breadth() == {"Technology": 1.0}
    for drop, call in (("velocity_5d", st.compute_ticker_rotation_scores), ("signal", st.compute_sector_breadth)):
        broken = {"AAA": {k: v for k, v in full["AAA"].items() if k != drop}}
        with patch("backend.sector_regime.compute_sector_regime", return_value=AVAILABLE), \
             patch("backend.sector_regime.compute_ticker_signals", return_value=broken):
            with pytest.raises(KeyError, match=drop):
                call()
