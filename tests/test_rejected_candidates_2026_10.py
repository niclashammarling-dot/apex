"""T7 rejected-candidate card: ticker-day labelling, the within-day permutation, Holm and the MDE."""
import numpy as np
import pandas as pd

import backend.backtest.rejected_candidates_2026_10 as rc


def test_label_is_the_furthest_stage_of_the_day_and_entered_wins():
    rows = [("AAA", "2026-08-03T14:00:00+00:00", "FILTERED_L3"),
            ("AAA", "2026-08-03T18:00:00+00:00", "TRADE_EXECUTED"),     # rejected by L5, entered later: entered
            ("BBB", "2026-08-03T14:00:00+00:00", "FILTERED_L2"),
            ("BBB", "2026-08-03T15:00:00+00:00", "FILTERED_LEADING"),   # reached leading: leading
            ("BBB", "2026-08-04T15:00:00+00:00", "FILTERED_L1"),        # a new day, a new label
            ("CCC", "2026-08-03T14:00:00+00:00", "SKIPPED_COOLOFF")]    # no judgment: dropped
    td = rc.label_ticker_days(rows)
    got = {(r.ticker, r.d): r.layer for r in td.itertuples()}
    assert got == {("AAA", "2026-08-03"): "entered", ("BBB", "2026-08-03"): "leading", ("BBB", "2026-08-04"): "quant"}


def test_codes_map_to_the_funnel_layers_not_their_numbers():
    # The old FILTERED_L3 was L6 REVIEW (Claude) in weekly_report._FUNNEL_AFTER_LEADING, FILTERED_L2 L4 SENTIMENT.
    assert rc.STAGES["FILTERED_L3"][1] == "l5" and rc.STAGES["FILTERED_L2"][1] == "sentiment"
    assert rc.STAGES["FILTERED_L1"][1] == "quant"


def _days(rng, shift):
    rows = []
    for k in range(40):
        day_move = rng.normal(0, 0.03)                         # shared by every ticker that day
        for lab in ["entered"] * 3 + ["l5"] * 3:
            rows.append({"d": f"d{k}", "layer": lab, "pnl": day_move + rng.normal(0, 0.01) + (shift if lab == "entered" else 0)})
    return pd.DataFrame(rows)


def test_within_day_perm_finds_a_shift_and_not_a_null():
    rng = np.random.default_rng(1)
    assert rc.within_day_perm(_days(rng, 0.01), "entered", "l5", "pnl", 500, rng)["p"] < 0.01
    ps = [rc.within_day_perm(_days(rng, 0.0), "entered", "l5", "pnl", 200, rng)["p"] for _ in range(40)]
    assert 0.25 < np.mean(ps) < 0.75                            # roughly uniform under the null


def test_single_label_days_cannot_move():
    df = pd.DataFrame({"d": ["a", "a", "b", "b"], "layer": ["entered", "entered", "l5", "l5"], "pnl": [0.05, 0.04, -0.02, -0.03]})
    r = rc.within_day_perm(df, "entered", "l5", "pnl", 100, np.random.default_rng(2))
    assert r["mixed_days"] == 0 and r["p"] == 1.0               # every permutation equals the observation


def test_holm_and_mde():
    adj = rc.holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert adj == {"a": 0.03, "c": 0.06, "b": 0.06}
    assert abs(rc.mde(0.045, 57, 44) - 2.487 * 0.045 * np.sqrt(1 / 57 + 1 / 44)) < 1e-12


def test_events_join_consecutive_sessions_of_one_ticker_and_label():
    sessions = ["2026-08-13", "2026-08-14", "2026-08-17", "2026-08-18", "2026-08-19", "2026-08-20"]
    td = pd.DataFrame([
        ("MRNA", "2026-08-13", "sentiment"), ("MRNA", "2026-08-14", "sentiment"),
        ("MRNA", "2026-08-17", "sentiment"),                      # next session after 08-14 (a weekend): same event
        ("MRNA", "2026-08-18", "leading"),                        # label changes: new event
        ("MRNA", "2026-08-20", "sentiment"),                      # a session skipped: new event
        ("AAPL", "2026-08-14", "sentiment")], columns=["ticker", "d", "layer"])
    ev = rc.to_events(td, sessions)
    got = sorted((r.ticker, r.d, r.layer, r.event_days) for r in ev.itertuples())
    assert got == [("AAPL", "2026-08-14", "sentiment", 1), ("MRNA", "2026-08-13", "sentiment", 3),
                   ("MRNA", "2026-08-18", "leading", 1), ("MRNA", "2026-08-20", "sentiment", 1)]


def test_until_leaves_min_forward_sessions():
    days = [f"2026-11-{d:02d}" for d in range(2, 21)]
    u = rc.until_for(days)
    assert u == days[-rc.MIN_FORWARD] and len([d for d in days if d >= u]) == rc.MIN_FORWARD


def test_dated_window_builds_from_the_battery_start_to_end(monkeypatch):
    seen = []
    monkeypatch.setattr(rc.ef, "precompute", lambda a, b: seen.append((a, b)) or {"trading_days": [1, 2]})
    rc.dated_window("2026-11-19")
    rc.dated_window("latest")
    assert seen[0] == (rc.WINDOW_START, "2026-11-19")
    latest = pd.Timestamp(seen[1][1])
    assert seen[1][0] == rc.WINDOW_START and latest.weekday() < 5 and latest < pd.Timestamp.now().normalize()


def test_gate_rows_read_the_old_code_from_the_migration_record_and_new_rows_by_stop_code():
    # Lock labels phase 2: after the 2026-10-31 migration the stored codes are owner codes, so the frozen layers
    # are read from gate_code_migration.old_code (the archived result reproduces) and new rows by stop_code.
    import sqlite3
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE live_gate_history (id INTEGER PRIMARY KEY, ticker TEXT, timestamp TEXT, "
                "gate_decision TEXT, stop_code TEXT)")
    con.execute("CREATE TABLE gate_code_migration (tbl TEXT, row_id INTEGER, old_code TEXT)")
    con.executemany("INSERT INTO live_gate_history VALUES (?, ?, ?, ?, ?)", [
        (1, "AAA", "2026-08-03T14:00:00", "FILTERED_L1_ELIGIBILITY", "FILTERED_L1_ELIGIBILITY_MACRO_PRE_EVENT"),
        (2, "BBB", "2026-11-03T14:00:00", "FILTERED_L4_SENTIMENT", "FILTERED_L4_SENTIMENT_GROK")])
    con.execute("INSERT INTO gate_code_migration VALUES ('live_gate_history', 1, 'FILTERED_L1')")
    rows = rc.gate_rows(con, "2026-01-01", "2026-12-31")
    assert sorted(rows) == [("AAA", "2026-08-03T14:00:00", "FILTERED_L1"),
                            ("BBB", "2026-11-03T14:00:00", "FILTERED_L4_SENTIMENT_GROK")]
    td = rc.label_ticker_days(rows)
    assert dict(zip(td.ticker, td.layer)) == {"AAA": "quant", "BBB": "sentiment"}


def test_every_registered_rule_has_a_frozen_layer_or_is_dropped_on_purpose():
    from backend.gate import lock_codes
    rules = {(r.owner, r.name) for r in lock_codes.RULES}
    assert rules == set(rc._LAYER_OF_RULE) | rc.DROPPED_RULES and not set(rc._LAYER_OF_RULE) & rc.DROPPED_RULES


# ── T7 line (scope vault raw/notes/2026-11/2026-11-10-apex-t7-line-build-scope.md, built 2026-11-14) ─────────

def _trade(i, ticker, d, sector="Energy", entry=100.0):
    return {"id": i, "ticker": ticker, "timestamp": f"{d}T15:00:00+00:00", "sector": sector, "notional": 400.0,
            "pnl": 8.0, "entry_price": entry, "tp_price": entry * 1.06, "sl_price": entry * 0.94, "signal_class": "in_session"}


def test_t7_line_filters_by_until_and_ids_and_runs_r9_then_pre_r9(monkeypatch):
    import backend.backtest.live_vs_engine_2026_10_20 as lve
    calls = []

    def fake_compare(P, pre, trades, flag, rng):
        calls.append(([t["id"] for t in trades], flag))
        by = {"all": {"n": len(trades), "live_mean": 0.02, "rnd_mean": 0.005}}
        return {"n": len(trades), "by": by, "rows": [{"id": t["id"], "ticker": t["ticker"], "d": t["timestamp"][:10],
                                                     "cls": "in_session", "live": 0.02, "rnd_mean": 0.005} for t in trades]}
    monkeypatch.setattr(lve, "compare", fake_compare)
    monkeypatch.setattr(rc, "sector_baseline", lambda P, pre, trades, rows: {"stub": True})
    live = [_trade(1, "XOM", "2026-10-01"), _trade(2, "CVX", "2026-10-02"), _trade(3, "EOG", "2026-11-06")]
    out = rc.t7_line({}, {}, live, "2026-11-06")
    assert [c[1] for c in calls] == [True, False] and calls[0][0] == [1, 2]
    assert abs(out["r9"]["by"]["all"]["excess"] - 0.015) < 1e-12
    calls.clear()
    rc.t7_line({}, {}, live, None, ids=[3])
    assert calls[0][0] == [3]


def test_group3_removes_the_best_sector_and_the_best_three():
    rows = [{"id": i, "live": v, "rnd_mean": 0.0} for i, v in enumerate([0.10, 0.08, 0.06, 0.01, 0.00, -0.01])]
    sector_of = {0: "Energy", 1: "Energy", 2: "Tech", 3: "Tech", 4: "Health", 5: "Health"}
    g = rc.group3(rows, sector_of)["live"]
    assert g["best_sector"] == "Energy" and g["without_best_sector"]["n"] == 4
    assert g["without_best_3"]["n"] == 3 and abs(g["without_best_3"]["mean"] - 0.0) < 1e-12
    assert g["without_both"]["n"] == 3                                  # Energy holds two of the best three


def _panels(days, tickers, sectors, atr_rows, mask_rows):
    return {"days": days, "tickers": tickers, "sector": dict(zip(tickers, sectors)),
            "atr": pd.DataFrame(atr_rows, index=days, columns=tickers),
            "mask": pd.DataFrame(mask_rows, index=days, columns=tickers)}


def test_sector_comparator_excludes_own_and_other_live_entries_and_takes_the_nearest_atr(monkeypatch):
    import backend.backtest.ladder_2026_10 as ld
    picked = []
    monkeypatch.setattr(ld, "_simulate", lambda P, pre, i, k, cfg: picked.append(P["tickers"][k]) or {"pnl": 0.01})
    P = _panels(["2026-10-01"], ["XOM", "CVX", "EOG", "OXY", "AAPL"], ["Energy"] * 4 + ["Tech"],
                [[0.020, 0.021, 0.030, 0.019, 0.020]], [[True] * 5])
    trades = [_trade(1, "XOM", "2026-10-01"), _trade(2, "CVX", "2026-10-01")]          # CVX is another live entry
    rows = [{"id": 1, "ticker": "XOM", "d": "2026-10-01", "cls": "in_session", "live": 0.02, "rnd_mean": 0.0}]
    out = rc.sector_baseline(P, {}, trades, rows)
    assert picked == ["OXY"]                         # nearest ATR in Energy, own ticker and CVX excluded, AAPL other sector
    assert out["candidates"]["before_exclusion"] == {3: 1} and out["candidates"]["after_exclusion"] == {2: 1}


def test_a_trade_with_one_candidate_is_flagged_and_none_is_counted(monkeypatch):
    import backend.backtest.ladder_2026_10 as ld
    monkeypatch.setattr(ld, "_simulate", lambda P, pre, i, k, cfg: {"pnl": 0.0})
    P = _panels(["2026-10-01"], ["XOM", "CVX", "AAPL"], ["Energy", "Energy", "Tech"],
                [[0.02, 0.02, 0.02]], [[True, True, True]])
    trades = [_trade(1, "XOM", "2026-10-01"), _trade(3, "AAPL", "2026-10-01", sector="Tech")]
    rows = [{"id": 1, "ticker": "XOM", "d": "2026-10-01", "cls": "in_session", "live": 0.02, "rnd_mean": 0.0},
            {"id": 3, "ticker": "AAPL", "d": "2026-10-01", "cls": "in_session", "live": 0.01, "rnd_mean": 0.0}]
    out = rc.sector_baseline(P, {}, trades, rows)
    assert out["flagged_le1"] == [1, 3]                                # one candidate (CVX); none in Tech
    assert out["all"]["with_comparator"] == 1 and out["all"]["no_comparator"] == 1


def test_day_cluster_p_flips_whole_days():
    days = pd.Series(["d1", "d1", "d2", "d3", "d4", "d5", "d6", "d7", "d8"])
    d = pd.Series([0.05, 0.05, 0.04, 0.03, 0.06, 0.02, 0.05, 0.04, 0.03])
    assert rc.day_cluster_p(days, d) < 0.02                              # every day positive: strong
    mixed = pd.Series([0.05, 0.05, -0.04, 0.03, -0.06, 0.02, -0.05, 0.04, -0.03])
    assert rc.day_cluster_p(days, mixed) > 0.2                           # control: days of both signs
    assert rc.day_cluster_p(pd.Series(["d1", "d1"]), pd.Series([0.1, 0.1])) is None   # one day: no test
