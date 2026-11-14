"""
Earnings rules: the date source fixed, shadow mode, CHECK 99 (2026-11-14, Saturday order item 2; ruling 2026-10-18).

- _get_earnings_date reads yfinance's dict calendar ({"Earnings Date": [date, ...]}, 0.2.66); the old code
  tested `cal.empty` on it and returned None for every ticker, so the rule read no_data on every row.
- In shadow mode a report inside the near window is recorded as days_to_earnings; nothing fails and no
  penalty key reaches the score re-test. With the shadow off, the blackout fails as before (the control).
- CHECK 99 warns when the rule's no_data share is the dead source's, counts nothing below its row floor, and
  counts distinct ticker-reports for the shadow read, not candidate-days.
"""
import sqlite3
from datetime import date, datetime, timezone

import pandas as pd
import pytest

import backend.config as config_module
import backend.gate.l2_risk as l2
from backend.gate.lock_codes import NO_DATA, PASS

TODAY = date(2026, 11, 16)
CFG = {"l2_risk_earnings_days": 2, "l2_risk_earnings_near_days": 14, "l2_risk_earnings_near_penalty": 0.15}


def test_dict_calendar_gives_the_date():
    assert l2._earnings_from_calendar({"Earnings Date": [date(2026, 11, 17)]}) == date(2026, 11, 17)


def test_a_range_gives_its_earlier_date():
    cal = {"Earnings Date": [date(2026, 11, 20), date(2026, 11, 18)]}
    assert l2._earnings_from_calendar(cal) == date(2026, 11, 18)


def test_datetimes_and_the_old_dataframe_shape():
    assert l2._earnings_from_calendar({"Earnings Date": [datetime(2026, 11, 17, 21, 0)]}) == date(2026, 11, 17)
    df = pd.DataFrame({"Earnings Date": [pd.Timestamp("2026-11-19")]})
    assert l2._earnings_from_calendar(df) == date(2026, 11, 19)


def test_no_date_is_none():
    assert l2._earnings_from_calendar({}) is None
    assert l2._earnings_from_calendar({"Earnings Date": []}) is None
    assert l2._earnings_from_calendar(None) is None


def test_the_date_source_reads_a_dict_calendar(monkeypatch):
    # Fails on the parent: `cal.empty` on a dict raised, was debug-logged, and None was cached.
    import yfinance as yf
    monkeypatch.setattr(yf, "Ticker", lambda t: type("T", (), {"calendar": {"Earnings Date": [date(2026, 11, 17)]}})())
    l2._earnings_cache.clear()
    try:
        assert l2._get_earnings_date("NVDA") == date(2026, 11, 17)
    finally:
        l2._earnings_cache.clear()


def _with_date(monkeypatch, d, shadow=True):
    monkeypatch.setattr(l2, "_get_earnings_date", lambda t: d)
    monkeypatch.setattr(config_module, "L2_RISK_EARNINGS_SHADOW", shadow)
    return l2.check_earnings("NVDA", CFG, TODAY)


def test_shadow_records_a_blackout_day_and_blocks_nothing(monkeypatch):
    fail, data, state = _with_date(monkeypatch, date(2026, 11, 17))
    assert fail is None and state == PASS
    assert data == {"earnings_shadow": {"days_to_earnings": 1, "earnings_date": "2026-11-17"}}


def test_shadow_records_the_near_window_without_a_penalty(monkeypatch):
    fail, data, state = _with_date(monkeypatch, date(2026, 11, 23))
    assert fail is None and data["earnings_shadow"]["days_to_earnings"] == 7
    assert set(data) == {"earnings_shadow"}               # no penalty key, nothing the chain or L6 reads


def test_shadow_beyond_the_window_records_nothing(monkeypatch):
    assert _with_date(monkeypatch, date(2027, 1, 29)) == (None, {}, PASS)


def test_no_date_is_no_data(monkeypatch):
    assert _with_date(monkeypatch, None)[2] == NO_DATA


def test_with_the_shadow_off_the_blackout_fails(monkeypatch):
    fail, _, state = _with_date(monkeypatch, date(2026, 11, 17), shadow=False)
    assert fail["fail_type"] == "earnings" and fail["days_to_earnings"] == 1 and state == "fail"


def test_the_shipped_value_is_shadow():
    assert config_module.L2_RISK_EARNINGS_SHADOW is True


def _l1_with(data):
    from backend.gate.types import LockResult
    return LockResult.pass_(lock_id=1, score=1.0, reason="qualifies", data={"adjusted_score": 0.6, **data})


def test_l6_does_not_see_the_shadow_report_date():
    # Niclas 2026-11-14: a reader outside the shadow would let the rule act through Claude's review unruled.
    from backend.gate import l6_review
    shadow = {"earnings_shadow": {"days_to_earnings": 1, "earnings_date": "2026-11-17"}}
    payload = l6_review._build_context_payload("NVDA", "Technology", {1: _l1_with(shadow)}, {})
    l1 = payload["lock_results"]["l1_eligibility"]["data"]
    assert "earnings_shadow" not in l1 and "days_to_earnings" not in l1 and "earnings_date" not in l1
    assert l1["adjusted_score"] == 0.6                    # control: the rest of L1's data still goes


def test_the_gate_row_records_days_to_earnings_from_the_shadow():
    from backend.gate import gate_runner
    from backend.gate.chain import ChainResult
    chain = ChainResult(ticker="NVDA", sector="Technology", approved=False,
                        lock_results={1: _l1_with({"earnings_shadow": {"days_to_earnings": 3, "earnings_date": "2026-11-19"}})})
    row = gate_runner._chain_to_gate_result({"id": 1, "ticker": "NVDA", "signal_score": 0.7}, chain)
    assert row["days_to_earnings"] == 3 and row["earnings_near"] == 0


# ── CHECK 99 ──────────────────────────────────────────────────────────────────

NOW = datetime(2026, 11, 20, 21, 0, tzinfo=timezone.utc)


@pytest.fixture
def run99(tmp_path, monkeypatch):
    import audit.checks_data as cd
    (tmp_path / "data").mkdir()
    db = tmp_path / "data" / "apex.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE live_gate_history (id INTEGER PRIMARY KEY, timestamp TEXT, ticker TEXT, "
                 "days_to_earnings INTEGER, rule_states TEXT)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(cd, "REPO", tmp_path)
    found = []
    monkeypatch.setattr(cd, "flag", lambda num, name, sev, where, msg: found.append((sev, msg)))

    def add(n, state, day="2026-11-18", ticker="T{i}", dte=None):
        c = sqlite3.connect(db)
        for i in range(n):
            c.execute("INSERT INTO live_gate_history (timestamp, ticker, days_to_earnings, rule_states) VALUES (?, ?, ?, ?)",
                      (f"{day}T15:00:00+00:00", ticker.format(i=i), dte, f'{{"L2_RISK_EARNINGS": "{state}"}}'))
        c.commit()
        c.close()

    def run(since=None):
        found.clear()
        cd.check99(now=NOW, since=since)
        return list(found)
    return add, run


def test_dead_source_warns(run99):
    add, run = run99
    add(60, "no_data")
    assert [m for s, m in run() if s == "WARNING" and "60/60" in m]


def test_the_historical_rows_raise_when_read(run99):
    # The control the ruling asked for: the dead rule's history (100 % no_data) must raise.
    add, run = run99
    add(60, "no_data", day="2026-11-12")
    assert not [s for s, m in run() if s == "WARNING"]              # before C99_FROM: not today's population
    assert [s for s, m in run(since="2026-11-10") if s == "WARNING"]


def test_a_live_source_does_not_warn(run99):
    add, run = run99
    add(55, "pass")
    add(5, "no_data")
    assert not [s for s, m in run() if s == "WARNING"]


def test_below_the_row_floor_nothing_warns(run99):
    add, run = run99
    add(10, "no_data")
    assert not [s for s, m in run() if s == "WARNING"]


def test_the_counter_counts_reports_not_candidate_days(run99):
    add, run = run99
    add(1, "pass", day="2026-11-16", ticker="NVDA", dte=3)          # report 11-19
    add(1, "pass", day="2026-11-17", ticker="NVDA", dte=2)          # the same report, a second candidate-day
    add(1, "pass", day="2026-11-17", ticker="HD", dte=2)            # report 11-19, another ticker
    info = [m for s, m in run() if s == "INFO"][0]
    assert "2 of 20 distinct ticker-reports, 3 candidate-days" in info


def test_check99_runs_in_the_data_checks(monkeypatch):
    # CHECK 99 has a runner: checks_data.run() calls it (a check with no runner is the 11-02 four-LLM-checks shape).
    import audit.checks_data as cd
    called = []
    for name in [n for n in dir(cd) if n.startswith("check") and n[5:].isdigit()]:
        monkeypatch.setattr(cd, name, lambda *a, _n=name, **k: called.append(_n))
    cd.run()
    assert "check99" in called and "check89" in called
