"""
Finding 1 (2026-10-10): gate candidates are bounded to the current NYSE session.

2026-10-07 audit: 25 of 55 live entries since 07-07 were decided on a signal not
written in that session — 15 on the same day's pre-open poll (the 14:20 CEST
startup poll, read by the first live cycle before the first in-session poll),
10 on a signal from an earlier day. The shapes below are those three classes,
plus a ticker outside the universe (TMHC's frozen 07-31 signal, evaluated five
times in August while it produced no new signals).
"""
import sqlite3
from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from backend.db import get_db, get_lock1_candidates, init_db
from backend.gate.cycle import GateCycle
from backend.gate.freshness import (BEFORE_OPEN, ENFORCE_BAR_DATE, NO_SESSION, NOT_IN_UNIVERSE,
                                    STALE_BAR_DATE, bar_date_mismatch, stale_reason)

init_db()

# 2031-03-07 is a Friday NYSE session; open 09:30 ET = 14:30 UTC (EST, before DST).
SESSION = "2031-03-07"
OPEN    = datetime(2031, 3, 7, 14, 30, tzinfo=timezone.utc)
NOW     = datetime(2031, 3, 7, 15, 0, tzinfo=timezone.utc)


class TestStaleReason:
    def test_in_session_signal_is_fresh(self):
        assert stale_reason("2031-03-07T14:45:00+00:00", SESSION, SESSION, OPEN) is None

    def test_pre_open_poll_on_the_prior_bar(self):
        """The 14:20 CEST startup poll: written before the open, newest bar is yesterday's."""
        assert stale_reason("2031-03-07T13:20:00+00:00", "2031-03-06", SESSION, OPEN) == BEFORE_OPEN

    def test_written_before_open_even_with_todays_bar_date(self):
        """A poll five minutes before the open passes any age limit; write time still refuses it."""
        assert stale_reason("2031-03-07T14:25:00+00:00", SESSION, SESSION, OPEN) == BEFORE_OPEN

    def test_earlier_day_signal(self):
        assert stale_reason("2031-02-14T15:00:00+00:00", "2031-02-14", SESSION, OPEN) == BEFORE_OPEN

    def test_post_open_poll_on_a_prior_bar_is_observed_not_refused(self):
        """bar_date is observe-only until the 2026-10-17 review (Niclas 2026-10-10)."""
        assert stale_reason("2031-03-07T14:45:00+00:00", "2031-03-06", SESSION, OPEN) is None
        assert bar_date_mismatch("2031-03-06", SESSION)

    def test_enforcing_bar_date_refuses_it(self):
        assert stale_reason("2031-03-07T14:45:00+00:00", "2031-03-06", SESSION, OPEN,
                            enforce_bar_date=True) == STALE_BAR_DATE
        assert stale_reason("2031-03-07T14:45:00+00:00", None, SESSION, OPEN,
                            enforce_bar_date=True) == STALE_BAR_DATE

    def test_enforcement_is_off(self):
        """Switching it on is a ruling after the review, not a default."""
        assert ENFORCE_BAR_DATE is False


def _signal(ticker, ts, bar_date, score=0.9):
    conn = get_db()
    try:
        conn.execute("INSERT INTO signals (timestamp, ticker, sector, price, signal_score, bar_date) "
                     "VALUES (?, ?, 'Technology', 100.0, ?, ?)", (ts, ticker, score, bar_date))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def universe(monkeypatch):
    def set_(tickers):
        monkeypatch.setattr("backend.ticker_config.get_sectors",
                            lambda: {"Technology": {"etf": "XLK", "tickers": list(tickers)}})
    return set_


class TestCandidateBound:
    def test_only_the_in_session_signal_survives(self, universe):
        universe(["FRA", "FRB", "FRC", "FRD", "FRF", "FRI"])
        _signal("FRA", "2031-03-07T14:45:00+00:00", SESSION)        # fresh
        _signal("FRB", "2031-03-07T13:20:00+00:00", "2031-03-06")   # 14:20 CEST startup poll
        _signal("FRC", "2031-03-07T14:25:00+00:00", SESSION)        # five minutes before the open
        _signal("FRD", "2031-02-14T15:00:00+00:00", "2031-02-14")   # earlier day
        _signal("FRE", "2031-03-07T14:45:00+00:00", SESSION)        # fresh but not in the universe
        _signal("FRF", "2031-03-07T14:50:00+00:00", SESSION, score=0.10)  # fresh, below threshold
        _signal("FRI", "2031-03-07T14:50:00+00:00", "2031-03-06")   # post-open, prior bar: observed
        stale = []
        got = get_lock1_candidates(threshold=0.5, stale_out=stale, now=NOW)
        mine = {c["ticker"] for c in got} & {"FRA", "FRB", "FRC", "FRD", "FRE", "FRF", "FRI"}
        assert mine == {"FRA", "FRI"}
        reasons = {s["ticker"]: s["reason"] for s in stale}
        assert reasons["FRB"] == BEFORE_OPEN
        assert reasons["FRC"] == BEFORE_OPEN
        assert reasons["FRD"] == BEFORE_OPEN
        assert reasons["FRE"] == NOT_IN_UNIVERSE
        assert "FRF" not in reasons   # refusals count only would-be candidates

    def test_latest_signal_decides_not_any_signal(self, universe):
        """A fresh signal followed by a newer stale one cannot happen in order, but a
        newer in-session poll must replace the pre-open one: the newest row is read."""
        universe(["FRG"])
        _signal("FRG", "2031-03-07T13:20:00+00:00", "2031-03-06")
        _signal("FRG", "2031-03-07T14:50:00+00:00", SESSION)
        got = get_lock1_candidates(threshold=0.5, now=NOW)
        assert "FRG" in {c["ticker"] for c in got}

    def test_non_session_day_has_no_fresh_candidate(self, universe):
        universe(["FRH"])
        _signal("FRH", "2031-03-08T15:00:00+00:00", "2031-03-08")   # Saturday
        stale = []
        got = get_lock1_candidates(threshold=0.5, stale_out=stale,
                                   now=datetime(2031, 3, 8, 15, 30, tzinfo=timezone.utc))
        assert got == []
        assert {s["reason"] for s in stale} == {NO_SESSION}


class TestRunnerLabelsTheRefusal:
    def _stale_side_effect(self, *a, stale_out=None, **k):
        stale_out.append({"ticker": "FRX", "sector": "Technology", "signal_score": 0.9,
                          "timestamp": "2031-03-07T13:20:00+00:00", "bar_date": "2031-03-06",
                          "reason": BEFORE_OPEN})
        return []

    def test_live_first_cycle_on_pre_open_signals_is_no_fresh_candidates(self):
        from backend.gate import gate_runner_live
        from tests.test_gate_runners import _live_patches
        cycle = GateCycle("run_live_gate", "scheduler", NOW.isoformat())
        with ExitStack() as stack:
            mocks = [stack.enter_context(p) for p in _live_patches(candidates=[])]
            mocks[6].side_effect = self._stale_side_effect
            assert gate_runner_live.run(cycle=cycle) == []
        assert cycle.reason == "no_fresh_candidates"
        assert cycle.stale_excluded == 1

    def test_demo_runner_same_label(self):
        from backend.gate import gate_runner
        from tests.test_gate_runners import _demo_patches
        cycle = GateCycle("run_gate", "scheduler", NOW.isoformat())
        with ExitStack() as stack:
            mocks = [stack.enter_context(p) for p in _demo_patches(candidates=[])]
            lock1 = next(m for m, p in zip(mocks, _demo_patches(candidates=[]))
                         if getattr(p, "attribute", "") == "get_lock1_candidates")
            lock1.side_effect = self._stale_side_effect
            assert gate_runner.run(cycle=cycle) == []
        assert cycle.reason == "no_fresh_candidates"
        assert cycle.stale_excluded == 1

    def test_no_signal_at_all_stays_no_candidates(self):
        from backend.gate import gate_runner_live
        from tests.test_gate_runners import _live_patches
        cycle = GateCycle("run_live_gate", "scheduler", NOW.isoformat())
        with ExitStack() as stack:
            [stack.enter_context(p) for p in _live_patches(candidates=[])]
            gate_runner_live.run(cycle=cycle)
        assert cycle.reason == "no_candidates"
        assert cycle.stale_excluded == 0


class TestCheck87:
    @pytest.fixture
    def run87(self, tmp_path, monkeypatch):
        import audit.checks_gate as cg
        dbf = tmp_path / "data" / "apex.db"
        dbf.parent.mkdir()
        conn = sqlite3.connect(dbf)
        for t in ("live_gate_history", "demo_gate_history"):
            conn.execute(f"CREATE TABLE {t} (ticker TEXT, sector TEXT, gate_decision TEXT, "
                         "cycle_started_at TEXT, signal_ts TEXT, bar_date TEXT)")
        conn.execute("CREATE TABLE gate_cycles (started_at TEXT, trigger TEXT, reason TEXT, "
                     "stale_excluded INTEGER)")
        conn.commit()
        conn.close()
        monkeypatch.setattr(cg, "REPO", tmp_path)
        monkeypatch.setattr(cg, "_c82_session_dates", lambda n: [SESSION])
        found = []
        monkeypatch.setattr(cg, "flag", lambda num, name, sev, where, msg: found.append((num, sev, msg)))

        def run(live=(), demo=(), earlier_live=()):
            c = sqlite3.connect(dbf)
            for t, rows in (("live_gate_history", list(live) + list(earlier_live)),
                            ("demo_gate_history", demo)):
                c.execute(f"DELETE FROM {t}")
                c.executemany(f"INSERT INTO {t} VALUES (?, 'Technology', ?, ?, ?, ?)", rows)
            c.commit()
            c.close()
            found.clear()
            cg.check87()
            return list(found)
        return run

    CYC = "2031-03-07T15:04:00+00:00"

    def test_clean_session_is_info_with_its_population(self, run87):
        f = run87(live=[("FRA", "TRADE_EXECUTED", self.CYC, "2031-03-07T14:50:00+00:00", SESSION)])
        assert {x[1] for x in f} == {"INFO"}
        assert "live: 1 rows checked" in f[0][2]
        assert "live: 0 of 1 in-session rows on a non-session bar" in f[1][2]

    def test_post_open_prior_bar_is_reported_per_sector_not_critical(self, run87):
        f = run87(live=[("FRI", "TRADE_EXECUTED", self.CYC, "2031-03-07T14:50:00+00:00", "2031-03-06")])
        assert {x[1] for x in f} == {"INFO"}
        assert "live: 1 of 1 in-session rows on a non-session bar (Technology 1/1 entered)" in f[1][2]

    def test_the_1007_shape_is_critical(self, run87):
        """TT/NOW 10-07: entered 15:34 CEST on the 14:20 CEST signal."""
        f = run87(live=[("FRB", "TRADE_EXECUTED", self.CYC, "2031-03-07T13:20:00+00:00", "2031-03-06")])
        crit = [x for x in f if x[1] == "CRITICAL"]
        assert crit and "1 entered" in crit[0][2] and "FRB" in crit[0][2]

    def test_unrecorded_rows_after_the_writer_existed_warn(self, run87):
        f = run87(live=[("FRC", "FILTERED_L2", self.CYC, None, None)],
                  earlier_live=[("FRZ", "FILTERED_L2", "2031-03-06T15:04:00+00:00",
                                 "2031-03-06T14:50:00+00:00", "2031-03-06")])
        assert any(x[1] == "WARNING" and "no signal_ts" in x[2] for x in f)

    def test_rows_from_before_the_columns_are_not_findings(self, run87):
        f = run87(live=[("FRD", "FILTERED_L2", self.CYC, None, None)])
        assert {x[1] for x in f} == {"INFO"}
