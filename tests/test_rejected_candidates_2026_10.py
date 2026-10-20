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
    # FILTERED_L3 is Lock 5 Claude in weekly_report._FUNNEL_AFTER_LEADING, FILTERED_L2 Lock 3 sentiment.
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
