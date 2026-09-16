"""
EOD regime catch-up: the missed-date arithmetic, the as_of= plumbing and the
provenance column. Background 2026-09-16: the old catch-up computed `expected`
and then discarded it, calling run_eod_regime() with no date — the row was
stamped with the restart date, and INSERT OR IGNORE then dropped the genuine
16:15 write for that date. Weekday arithmetic also fired for Labor Day.
"""
from datetime import date, datetime, time
from unittest.mock import patch

import pandas as pd
import pytest

import backend.db as db
import backend.scheduler as sched
from backend.scheduler import NY, _last_eod_due, _nyse_sessions_between


# ── missed-date arithmetic ────────────────────────────────────────────────────

@pytest.mark.parametrize("now, expected", [
    (datetime(2026, 9, 3, 11, 20, tzinfo=NY), date(2026, 9, 2)),   # Thu before close → Wed
    (datetime(2026, 9, 3, 16, 20, tzinfo=NY), date(2026, 9, 3)),   # Thu after 16:15 → Thu
    (datetime(2026, 9, 8, 10, 45, tzinfo=NY), date(2026, 9, 4)),   # Tue after Labor Day → Fri, not Mon
    (datetime(2026, 9, 7, 18,  0, tzinfo=NY), date(2026, 9, 4)),   # Labor Day evening → Fri
    (datetime(2026, 9, 13, 9,  0, tzinfo=NY), date(2026, 9, 11)),  # Sunday → Fri
])
def test_last_eod_due_uses_exchange_calendar(now, expected):
    assert _last_eod_due(now) == expected


def test_sessions_between_skips_holiday_and_weekend():
    assert _nyse_sessions_between(date(2026, 9, 1), date(2026, 9, 8)) == [
        date(2026, 9, 2), date(2026, 9, 3), date(2026, 9, 4), date(2026, 9, 8),
    ]


# ── catch-up passes the missed date and fills the whole range in order ───────

def _seed_history(last_date: str):
    db.init_db()
    conn = db.get_db()
    conn.execute("DELETE FROM sector_posterior_history")
    conn.execute(
        "INSERT INTO sector_posterior_history (date, sector, posterior) VALUES (?, 'Technology', 0.5)",
        (last_date,),
    )
    conn.commit()
    conn.close()


def test_catchup_runs_each_missed_session_as_of_that_date():
    _seed_history("2026-09-01")
    calls = []
    fake_now = datetime(2026, 9, 8, 10, 45, tzinfo=NY)   # Tue morning after Labor Day
    with patch.object(sched, "run_eod_regime", side_effect=lambda as_of=None: calls.append(as_of)), \
         patch.object(sched, "datetime") as dt:
        dt.now.return_value = fake_now
        dt.combine = datetime.combine
        dt.fromisoformat = datetime.fromisoformat
        sched._check_missed_eod_regime()
    assert calls == [date(2026, 9, 2), date(2026, 9, 3), date(2026, 9, 4)]


def test_catchup_noop_when_history_current():
    _seed_history("2026-09-04")
    calls = []
    with patch.object(sched, "run_eod_regime", side_effect=lambda as_of=None: calls.append(as_of)), \
         patch.object(sched, "datetime") as dt:
        dt.now.return_value = datetime(2026, 9, 8, 10, 45, tzinfo=NY)
        dt.fromisoformat = datetime.fromisoformat
        sched._check_missed_eod_regime()
    assert calls == []


# ── as_of plumbing: the row is stamped with the target date, inputs read as-of ─

def test_run_eod_regime_as_of_stamps_target_and_truncates_inputs():
    target = date(2026, 9, 2)
    idx = pd.date_range("2026-08-01", "2026-09-04", freq="B")
    frame = pd.DataFrame({"Close": range(len(idx))}, index=idx)
    seen = {}

    class FakeRB:
        def update(self, today, raw_data, snapshots, ipo_shares):
            seen["today"] = today
            seen["max_bar"] = raw_data.index.max().date()
            seen["snap_as_of"] = snapshots
            class R: leader = "Technology"; qualifiers = []
            return R()

    class FakeIpo:
        def __init__(self, *_): pass
        def compute(self, reference_date=None):
            seen["ipo_ref"] = reference_date
            class R: ipo_shares = {}; total_ipos = 0; risk_off = False
            return R()

    with patch("yfinance.download", return_value=frame) as dl, \
         patch.object(sched, "_get_regime_bayes", return_value=FakeRB()), \
         patch("backend.regime.ipo_sentiment.IpoSentiment", FakeIpo), \
         patch("backend.db.get_latest_sector_scores", side_effect=lambda as_of=None: {"as_of": as_of}), \
         patch("backend.ticker_config.get_sectors", return_value={"Technology": {"tickers": ["AAPL"], "etf": "XLK"}}):
        sched.run_eod_regime(as_of=target)

    assert seen["today"] == target
    assert seen["max_bar"] <= target                      # _compute_rank_lrs reads iloc[-1]; must not see 09-03/04
    assert seen["ipo_ref"] == target
    assert seen["snap_as_of"]["as_of"] == sched._eod_cutoff_utc(target)
    assert "end" in dl.call_args.kwargs and "period" not in dl.call_args.kwargs


def test_insert_history_stamps_written_at():
    db.init_db()
    conn = db.get_db(); conn.execute("DELETE FROM sector_posterior_history"); conn.commit(); conn.close()
    db.insert_sector_posterior_history("2026-09-02", {"Technology": 0.5})
    conn = db.get_db()
    wa = conn.execute("SELECT written_at FROM sector_posterior_history WHERE date='2026-09-02'").fetchone()[0]
    conn.close()
    assert wa and datetime.fromisoformat(wa).tzinfo is not None


def test_run_eod_regime_refuses_as_of_before_close():
    with patch.object(sched, "_get_regime_bayes") as rb, patch("yfinance.download") as dl:
        sched.run_eod_regime(as_of=date(2099, 1, 2))
    rb.assert_not_called(); dl.assert_not_called()


def test_run_eod_regime_live_refuses_before_close_today():
    fake_now = datetime(2026, 9, 15, 11, 51, tzinfo=NY)
    with patch.object(sched, "datetime") as dt, patch.object(sched, "_get_regime_bayes") as rb, patch("yfinance.download") as dl:
        dt.now.return_value = fake_now
        dt.combine = datetime.combine
        sched.run_eod_regime()
    rb.assert_not_called(); dl.assert_not_called()


def test_preview_persists_nothing():
    """preview_eod_regime returns a result and leaves DB, result cache and singleton untouched."""
    import backend.regime.regime_bayes as rbm
    db.init_db()
    conn = db.get_db(); conn.execute("DELETE FROM sector_posterior_history"); conn.commit(); conn.close()
    idx = pd.date_range("2026-08-01", "2026-09-15", freq="B")
    frame = pd.DataFrame({"Close": range(len(idx))}, index=idx)
    saved = []
    with patch.object(sched, "_eod_inputs", return_value=(frame, {"Technology": 0.5}, {"Technology": 1.0})), \
         patch.object(sched, "_build_transition_priors", return_value={}), \
         patch.object(sched, "_get_regime_bayes") as singleton, \
         patch("backend.ticker_config.get_sectors", return_value={"Technology": {"tickers": ["AAPL"], "etf": "XLK"}}), \
         patch.object(rbm, "write_json_atomic", side_effect=lambda *a, **k: saved.append(a)), \
         patch.object(rbm.RegimeBayes, "update", autospec=True,
                      side_effect=lambda self, *a, **k: (self._save_posteriors({}), self._save_posterior_history("2026-09-15", {}), self._save_result(None), "R")[-1]):
        out = sched.preview_eod_regime()
    assert out == "R"
    assert saved == []
    conn = db.get_db()
    assert conn.execute("SELECT COUNT(*) FROM sector_posterior_history").fetchone()[0] == 0
    conn.close()
    singleton.assert_not_called()   # preview never touches the scheduler singleton
