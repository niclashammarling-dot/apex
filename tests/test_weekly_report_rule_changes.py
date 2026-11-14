"""
The weekly report's prior-week reference marks rules that changed place or name inside its window (R012, R070 a,
2026-11-14): a moved or added rule (Rule.since) is netted from its lock on both sides, a split (Rule.split_from) is
summed with its parent, and a rule with rows this week and none before, with no registered change, is listed without
netting. The founding case is W45 (vault raw/notes/2026-11/2026-11-07-apex-weekly-read-2026-W45.md).
"""
from dataclasses import replace

import pytest

import backend.weekly_report as wr
from backend.db import get_db, init_db
from backend.gate import lock_codes as lc

init_db()
L1, L2 = lc.lock_label(1), lc.lock_label(2)
EXE = lc.TRADE_EXECUTED


def _c(lock: int, rule: str) -> str:
    return lc.stop_code(lock, rule)


def _ref(prior: dict[str, dict[str, int]], oldest: str) -> dict:
    """Eight reference weeks, oldest first at `oldest`, all prior rows in the newest."""
    from datetime import datetime, timedelta
    start = datetime.fromisoformat(oldest)
    closed = {"closed_total": 0, "wins": 0, "realized_pnl": 0.0}
    weeks = [{"since": (start + timedelta(weeks=7 - k)).isoformat(), "partial": 0,
              "closed": {b: closed for b in wr.BOOKS},
              "funnel": {b: wr._funnel_shape(prior[b] if k == 0 else {}) for b in wr.BOOKS}} for k in range(8)]
    return {"weeks": weeks}


def _read(week: dict, prior: dict, oldest: str, until: str):
    ref = _ref(prior, oldest)
    dfun, lfun = wr._funnel_shape(week["demo"]), wr._funnel_shape(week["live"])
    rdemo, rlive = wr._ref_book(ref, "demo"), wr._ref_book(ref, "live")
    changes = wr._rule_changes(dfun, lfun, rdemo, rlive, ref["weeks"][-1]["since"], until)
    stats = {"closed_total": 0, "wins": 0, "realized_pnl": 0.0}
    rows = {r[0]: r[1:] for r in wr._reference_rows(stats, stats, dfun, lfun, rdemo, rlive, changes)}
    return changes, rows


def test_w45_reproduces_with_l1_net_of_notional_too_small():
    week = {"live": {_c(1, "regime"): 2680, _c(1, "notional_too_small"): 277, _c(5, "leading"): 113, EXE: 6},
            "demo": {_c(1, "regime"): 2793, EXE: 214}}
    prior = {"live": {_c(1, "regime"): 9242, _c(1, "notional_too_small"): 11, _c(5, "leading"): 900, EXE: 35},
             "demo": {_c(1, "regime"): 8968, EXE: 769}}
    changes, rows = _read(week, prior, "2026-09-07T00:00:00+00:00", "2026-11-09T00:00:00+00:00")
    assert [(c["book"], c["rule"], c["kind"], c["week"], c["prior"]) for c in changes] == \
        [("live", "notional_too_small", "new", 277, 11)]
    assert rows[f"{L1} stopped / candidates"] == ("2793/3007 (93%)", "8968/9737 (92%)", "2957/3076 (96%)", "9253/10188 (91%)")
    assert rows[f"{L1} net of new rules"] == ("2793/3007 (93%)", "8968/9737 (92%)", "2680/3076 (87%)", "9242/10188 (91%)")
    assert wr._rule_changes_text(changes) == [
        f"Live {L1} notional_too_small: moved or added 2026-11-02 (this week 277, prior weeks 11); its lock's rate is "
        "also shown net of it."]


def test_a_split_is_summed_with_its_parent_and_not_netted():
    week = {"live": {_c(1, "exit_cooloff"): 207, _c(1, "exit_cooloff_win"): 139, _c(1, "regime"): 2000, EXE: 100},
            "demo": {}}
    prior = {"live": {_c(1, "exit_cooloff"): 1446, _c(1, "regime"): 8000, EXE: 742}, "demo": {}}
    changes, rows = _read(week, prior, "2026-09-14T00:00:00+00:00", "2026-11-16T00:00:00+00:00")
    assert [(c["rule"], c["kind"], c["parent"], c["parent_week"], c["week"]) for c in changes] == \
        [("exit_cooloff_win", "split", "exit_cooloff", 207, 139)]
    assert rows[f"{L1} exit_cooloff + exit_cooloff_win"][2:] == ("346/2446 (14%)", "1446/10188 (14%)")
    assert f"{L1} net of new rules" not in rows
    assert "split from exit_cooloff 2026-11-09; the two are summed (this week 207 + 139, prior weeks 1446 + 0)" \
        in wr._rule_changes_text(changes)[0]


def test_a_rule_without_prior_rows_or_a_date_is_listed_not_netted():
    week = {"live": {_c(2, "sector_cap"): 8, _c(1, "regime"): 100, EXE: 10}, "demo": {}}
    prior = {"live": {_c(1, "regime"): 400, EXE: 40}, "demo": {}}
    changes, rows = _read(week, prior, "2026-09-14T00:00:00+00:00", "2026-11-16T00:00:00+00:00")
    assert [(c["rule"], c["kind"]) for c in changes] == [("sector_cap", "unmarked")]
    assert not any("net of new rules" in m for m in rows)
    assert wr._rule_changes_text(changes) == [
        f"Live {L2} sector_cap: 8 this week, none in the prior weeks and no registered change; not netted."]


def test_a_change_older_than_the_window_is_not_marked():
    week = {"live": {_c(1, "notional_too_small"): 40, _c(1, "regime"): 100, EXE: 10}, "demo": {}}
    prior = {"live": {_c(1, "notional_too_small"): 300, _c(1, "regime"): 800, EXE: 80}, "demo": {}}
    changes, rows = _read(week, prior, "2026-11-09T00:00:00+00:00", "2027-01-11T00:00:00+00:00")
    assert changes == [] and not any("net of new rules" in m for m in rows)


@pytest.fixture
def _rows():
    yield
    conn = get_db()
    try:
        conn.execute("DELETE FROM live_gate_history WHERE ticker LIKE 'RC-%'")
        conn.commit()
    finally:
        conn.close()


def test_both_parts_of_the_mail_carry_the_marking(monkeypatch, _rows):
    import backend.config as config_module
    monkeypatch.setattr(config_module, "OPENAI_API_KEY", "")
    monkeypatch.setattr(wr, "_week_start_iso", lambda: "2034-03-06T00:00:00+00:00")
    monkeypatch.setattr(wr, "_fetch_prices", lambda tickers: {})
    monkeypatch.setattr(lc, "RULES", tuple(replace(r, since="2034-03-06") if r.name == "notional_too_small" else r
                                           for r in lc.RULES))
    conn = get_db()
    try:
        for ts, t, code in (("2034-03-07T15:00:00+00:00", "RC-1", _c(1, "notional_too_small")),
                            ("2034-03-07T15:00:00+00:00", "RC-2", _c(1, "regime")),
                            ("2034-02-28T15:00:00+00:00", "RC-3", _c(1, "regime"))):
            conn.execute("INSERT INTO live_gate_history (timestamp, ticker, sector, gate_decision, stop_code) "
                         "VALUES (?, ?, 'Tech', 'FILTERED', ?)", (ts, t, code))
        conn.commit()
    finally:
        conn.close()
    _, html, plain = wr.build_report(recal_changes=None)
    line = f"Live {L1} notional_too_small: moved or added 2034-03-06 (this week 1, prior weeks 0)"
    for part in (html, plain):
        assert "Rules changed inside the window (R070)" in part and line in part and f"{L1} net of new rules" in part
