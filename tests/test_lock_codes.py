"""Lock labels: one table of locks, rules and stored codes (phase 1 2026-10-26, phase 2 2026-10-29).

From phase 2 every writer stores the code lock_codes derives from the row's rule_states (codes_for), the pass
flags from FLAG_RULES, and every surface (Terminal marks, funnel, Claude's gate history, reason labels) reads the
same table. The cooloff set is pinned to ruling 6 ("behåll cool off nu"), the labels to the chain approved
2026-10-24.
"""
import json
from pathlib import Path
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

import pytest

from backend.gate import lock_codes
from backend.gate.lock_codes import FAIL, NO_DATA, PASS
from backend.gate.lock_codes import rule_key as K


def test_writers_store_a_lock_code_executed_or_unknown_and_never_a_legacy_code():
    assert lock_codes.codes_for({K(4, "grok"): FAIL}, executed=False) == ("FILTERED_L4_SENTIMENT",
                                                                           "FILTERED_L4_SENTIMENT_GROK")
    assert lock_codes.codes_for({K(6, "claude"): PASS}, executed=True) == ("TRADE_EXECUTED", "TRADE_EXECUTED")
    assert lock_codes.codes_for({K(6, "claude"): PASS}, executed=False) == ("FILTERED_UNKNOWN", "FILTERED_UNKNOWN")
    every = {lock_codes.codes_for({lock_codes.rule_key(r.owner, r.name): FAIL}, False)[0] for r in lock_codes.RULES}
    assert every == {lock_codes.lock_code(n) for n in lock_codes.LOCKS}
    assert every.isdisjoint(lock_codes.LEGACY_CODES) and len(lock_codes.LEGACY_CODES) == 13


def test_module_numbers_map_to_the_chain_lock_they_run():
    # Module lock_id N runs chain lock MODULE_TO_CHAIN[N]; since phase 3 the module file carries the chain lock's
    # name (l<n>_<lock>.py), not N.
    assert lock_codes.MODULE_TO_CHAIN == {1: 1, 2: 3, 3: 4, 4: 5, 5: 6}
    for n, chain in lock_codes.MODULE_TO_CHAIN.items():
        name = lock_codes.LOCKS[chain][0].lower()
        assert f"l{chain}_{name}.py" in lock_codes.LOCKS[chain][2]
        assert (Path(__file__).parent.parent / f"backend/gate/l{chain}_{name}.py").exists()


def test_cooloff_set_is_every_sentiment_and_review_decision_and_the_guard():
    # Ruling 6: the set {FILTERED_L2, FILTERED_L3} before phase 2, which held L4, L6 and the guard; not a broker error.
    owners = {lock_codes.parse_stop_code(c) for c in lock_codes.COOLOFF_STOPS}
    assert owners == {(4, "grok"), (4, "unavailable"), (4, "unavailable_inferred"), (6, "claude"),
                      (6, "unavailable"), (6, "unavailable_inferred"), (0, "drawdown_guard")}


def test_labels_follow_the_approved_chain():
    assert [lock_codes.LOCKS[n][0] for n in range(7)] == [
        "PORTFOLIO", "ELIGIBILITY", "RISK", "SCORE", "SENTIMENT", "POSITIONING", "REVIEW"]
    assert [lock_codes.lock_label(n) for n in (0, 4)] == ["L0 · Portfolio", "L4 · Sentiment"]


@pytest.mark.parametrize("states, want", [
    ({}, {"lock1_pass": None, "lock2_pass": None, "lock_leading_pass": None, "lock3_pass": None}),
    # The score test as run: a penalty that pushed the score under fails it, as lock1_pass held before.
    ({K(3, "threshold"): PASS, K(1, "macro_pre_event"): FAIL, K(2, "earnings_near"): PASS},
     {"lock1_pass": 0, "lock2_pass": None, "lock_leading_pass": None, "lock3_pass": None}),
    # Claude approved and the broker refused: lock3_pass is Claude's decision.
    ({K(3, "threshold"): PASS, K(4, "grok"): PASS, K(5, "leading"): PASS, K(6, "claude"): PASS,
      K(6, "broker_error"): FAIL}, {"lock1_pass": 1, "lock2_pass": 1, "lock_leading_pass": 1, "lock3_pass": 1}),
    ({K(4, "unavailable"): FAIL}, {"lock1_pass": None, "lock2_pass": 0, "lock_leading_pass": None, "lock3_pass": None}),
])
def test_pass_flags_follow_flag_rules(states, want):
    assert lock_codes.flags(states) == want


def _delete(*sql):
    from backend import db
    conn = db.get_db()
    try:
        for s in sql:
            conn.execute(s)
        conn.commit()
    finally:
        conn.close()


def test_cooloff_queries_select_the_cooloff_stops():
    from backend import db
    now = datetime.now(timezone.utc).isoformat()
    codes = [lock_codes.stop_code(r.owner, r.name) for r in lock_codes.RULES] + ["TRADE_EXECUTED", "FILTERED_UNKNOWN"]
    conn = db.get_db()
    try:
        for i, code in enumerate(codes):
            for table in ("live_gate_history", "signals"):
                conn.execute(f"INSERT INTO {table} (ticker, sector, gate_decision, stop_code, timestamp) "
                             "VALUES (?, 'Tech', ?, ?, ?)", (f"LC{i}", code.rsplit("_", 1)[0], code, now))
        conn.commit()
    finally:
        conn.close()
    try:
        want = {f"LC{i}" for i, c in enumerate(codes) if c in lock_codes.COOLOFF_STOPS}
        got_live = {t for t in db.get_recently_failed_live_tickers(1.0) if t.startswith("LC")}
        got_demo = {t for t in db.get_recently_failed_tickers(1.0) if t.startswith("LC")}
        assert got_live == got_demo == want and len(want) == 7
    finally:
        # The session DB is shared: a scoreless signals row broke get_lock1_candidates in a later file.
        _delete("DELETE FROM live_gate_history WHERE ticker LIKE 'LC%'", "DELETE FROM signals WHERE ticker LIKE 'LC%'")


def test_api_shape():
    api = lock_codes.as_api()
    assert [x["lock"] for x in api["locks"]] == list(range(7))
    assert [c["head"] for c in api["columns"]] == ["L0", "L1", "L2", "L3", "L4", "L5", "L6"]
    assert set(api["marks"]) == {"pass", "fail", "n/a", "partial", "none"} and api["marks"]["n/a"] == "N/A"
    assert len(api["rules"]) == len(lock_codes.RULES)
    etf = next(r for r in api["rules"] if r["rule"] == "etf")
    assert etf["lock"] == 2 and etf["stop_code"] == "FILTERED_L2_RISK_ETF" and etf["config_keys"]


def test_both_gate_history_responses_serve_the_table(monkeypatch):
    # The funnel, Gate Activity, Settings and Promote read the table from these two responses; the standalone
    # /api/gate/locks had no caller and left 2026-10-31. Runs on the test DB (conftest).
    import backend.routers.live_router as lr
    from backend.routers.live_router import live_gate_history
    monkeypatch.setattr(lr, "LIVE_ENABLED", False)          # the blocked-reason probe reaches the broker
    from backend.routers.signals_router import gate_history
    assert gate_history()["locks"] == lock_codes.as_api()
    assert live_gate_history()["locks"] == lock_codes.as_api()


LIVE_PASSED = {K(o, r): PASS for o, r in (
    (0, "halt_m4_drawdown"), (0, "loss_cap"), (0, "halt_m4_expectancy"), (1, "blocklist"), (1, "position_open"),
    (1, "cooloff"), (1, "exit_cooloff"), (1, "notional_too_small"), (1, "price_missing"), (1, "regime"),
    (1, "vix"), (1, "macro_event"), (2, "earnings"), (3, "threshold"), (1, "macro_pre_event"),
    (2, "earnings_near"), (4, "grok"), (5, "leading"), (2, "etf"), (0, "drawdown_guard"), (2, "overflow"),
    (6, "claude"), (2, "sector_cap"))}


def test_an_executed_live_row_passes_every_lock():
    assert lock_codes.lock_marks(LIVE_PASSED, "live") == ["pass"] * 7


def test_a_stopped_row_has_one_fail_in_its_owners_column():
    # Ruling 2026-10-28: one ✗ per stopped row, in its owner's column.
    ran = {K(2, "earnings"), K(2, "earnings_near")}                   # L2's rules that run before L4
    states = {k: v for k, v in LIVE_PASSED.items() if parse(k)[0] in (0, 1, 3) or k in ran} | {K(4, "grok"): FAIL}
    states = {k: v for k, v in states.items() if k != K(0, "drawdown_guard")}
    marks = lock_codes.lock_marks(states, "live")
    assert marks.count("fail") == 1 and marks[4] == "fail"
    assert marks[3] == "pass" and marks[5] is None and marks[6] is None
    assert marks[0] == "partial"                      # M4 passed, the guard (after L5) did not run yet
    assert marks[2] == "partial"                      # earnings passed; etf, overflow, sector cap did not run


def test_no_data_shows_n_a_and_a_demo_row_needs_no_m4_or_blocklist():
    states = dict(LIVE_PASSED, **{K(2, "earnings"): NO_DATA})
    assert lock_codes.lock_marks(states, "live")[2] == "n/a"
    demo = {k: v for k, v in LIVE_PASSED.items()
            if k not in (K(0, "halt_m4_drawdown"), K(0, "halt_m4_expectancy"), K(1, "blocklist"))}
    assert lock_codes.lock_marks(demo, "demo") == ["pass"] * 7
    assert lock_codes.lock_marks(demo, "live")[0] == "partial"


def test_an_unknown_row_shows_no_fail():
    marks = lock_codes.lock_marks({K(3, "threshold"): PASS}, "live")
    assert "fail" not in marks and marks[3] == "pass"


def parse(key):
    return lock_codes.parse_rule_key(key)


def test_funnel_counts_each_stop_under_its_lock_and_rule_in_chain_order():
    from backend import db
    rows = [("FUNA", "TRADE_EXECUTED", "TRADE_EXECUTED", 1), ("FUNB", "TRADE_EXECUTED", "TRADE_EXECUTED", 0),
            ("FUNC", "FILTERED_L2_RISK", "FILTERED_L2_RISK_SECTOR_CAP", 0),
            ("FUND", "FILTERED_L2_RISK", "FILTERED_L2_RISK_OVERFLOW", 0),
            ("FUNE", "FILTERED_L1_ELIGIBILITY", "FILTERED_L1_ELIGIBILITY_POSITION_OPEN", 0),
            ("FUNF", "FILTERED_UNKNOWN", "FILTERED_UNKNOWN", 0)]
    conn = db.get_db()
    try:
        for table in ("demo_gate_history", "live_gate_history"):
            conn.executemany(f"INSERT INTO {table} (ticker, sector, gate_decision, stop_code, overflow_slot, timestamp) "
                             "VALUES (?, 'Tech', ?, ?, ?, '2099-01-02T15:00:00+00:00')", rows)
        conn.commit()
    finally:
        conn.close()
    since = "2099-01-01T00:00:00+00:00"
    try:
        demo, live = db.get_demo_gate_funnel_counts(since), db.get_live_gate_funnel_counts(since)
    finally:
        for table in ("demo_gate_history", "live_gate_history"):
            _delete(f"DELETE FROM {table} WHERE ticker LIKE 'FUN_'")
    assert demo == live                                   # R14: one function for both books
    assert demo["overflow_executed"] == 1 and demo["total_candidates"] == 6
    by_code = {s["code"]: s for s in demo["stages"]}
    assert [s["code"] for s in demo["stages"]][:7] == [lock_codes.lock_code(n) for n in range(7)]
    assert by_code["FILTERED_L2_RISK"]["count"] == 2
    assert {r["rule"]: r["count"] for r in by_code["FILTERED_L2_RISK"]["rules"]} == {"overflow": 1, "sector_cap": 1}
    assert by_code["TRADE_EXECUTED"]["count"] == 2 and by_code["FILTERED_UNKNOWN"]["count"] == 1
    assert sum(s["count"] for s in demo["stages"]) == demo["total_candidates"]


def test_an_unregistered_code_is_listed_not_dropped():
    stages = lock_codes.funnel_stages({"FILTERED_L9_NOWHERE_X": 3, "FILTERED_L3": 2})
    assert {s["code"]: s["count"] for s in stages if s["kind"] == "unregistered"} == {
        "FILTERED_L3": 2, "FILTERED_L9_NOWHERE_X": 3}


def test_reason_label_names_whose_reason_the_row_holds():
    # R5: the Terminal labelled lock3_reasoning "L5:" for every row.
    assert lock_codes.reason_label({"stop_code": "FILTERED_L5_POSITIONING_LEADING", "lock3_reasoning": "x"}) == \
        "L5 · Positioning"
    assert lock_codes.reason_label({"stop_code": "TRADE_EXECUTED", "lock3_pass": 1, "lock3_reasoning": "x"}) == \
        "L6 · Review"
    # Claude approved, then the sector cap refused: the reason is still Claude's.
    assert lock_codes.reason_label({"stop_code": "FILTERED_L2_RISK_SECTOR_CAP", "lock3_pass": 1,
                                    "lock3_reasoning": "x"}) == "L6 · Review"
    assert lock_codes.reason_label({"stop_code": "FILTERED_L2_RISK_OVERFLOW", "lock3_reasoning": None}) is None


def test_badges_and_skips():
    assert lock_codes.badge({"stop_code": "FILTERED_L1_ELIGIBILITY_EXIT_COOLOFF"}) == "L1 EXIT COOLOFF"
    assert lock_codes.badge({"stop_code": "FILTERED_UNKNOWN"}) == "UNKNOWN"
    skip = {"stop_code": "FILTERED_L1_ELIGIBILITY_COOLOFF", "lock1_pass": None, "lock2_pass": None,
            "lock_leading_pass": None, "lock3_pass": None}
    assert lock_codes.is_skip(skip) and lock_codes.badge_kind(skip) == "skip"
    # position_open at execution, after the chain: not a skip.
    assert not lock_codes.is_skip({**skip, "stop_code": "FILTERED_L1_ELIGIBILITY_POSITION_OPEN", "lock1_pass": 1})


def test_detail_labels_name_what_each_column_holds():
    # R5: l2_summary is the sentiment summary (gate_runner: l3_data summary), shown as "L3:" before.
    assert lock_codes.as_api()["detail_labels"] == {
        "macro_reason": "L1 · Eligibility", "l2_summary": "L4 · Sentiment", "lock_leading_checks": "L5 · Positioning"}


def test_lock_owned_config_keys_follow_the_approved_chain():
    # Chain approved 2026-10-24, amended 2026-10-26 (Niclas): macro to L1 ("flytta makro till l1"),
    # the drawdown guard to L0 ("dd är före allt annat"). L2 RISK keeps ETF, earnings, concentration.
    labels = lock_codes.config_labels()
    for k in ("macro_earnings_blackout_days", "macro_earnings_near_days", "macro_earnings_near_penalty",
              "max_sector_exposure", "etf_negative_penalty", "overflow_quant_increment"):
        assert labels[k].startswith("L2 · Risk"), k
    for k in ("vix_threshold", "macro_event_blackout_days", "macro_pre_event_penalty", "gate_cooloff_hours",
              "exit_cooloff_hours", "tp_cooloff_hours"):
        assert labels[k] == "L1 · Eligibility", k
    assert labels["max_drawdown_pct"] == labels["daily_loss_cap"] == "L0 · Portfolio"
    assert lock_codes.LOCKS[2][1] == "ETF, earnings, concentration, overflow"   # overflow: ruling E, 10-26
    assert "macro" in lock_codes.LOCKS[1][1] and "macro" not in lock_codes.LOCKS[2][1]


def test_every_macro_key_the_code_reads_is_owned_by_the_lock_its_rule_belongs_to():
    """The macro_* keys l1_eligibility reads: the calendar's (no earnings in the name) are L1's, the
    earnings ones L2's. A new macro key that is read but not owned would get no lock prefix."""
    import inspect
    import re

    from backend.gate import l1_eligibility
    read = set(re.findall(r'cfg\.get\("(macro_[a-z_]+)"\)', inspect.getsource(l1_eligibility)))
    assert read, "positive control: the reader must be found"
    labels = lock_codes.config_labels()
    for k in read:
        assert labels.get(k) == lock_codes.lock_label(2 if "earnings" in k else 1), k


def test_owned_keys_are_real_config_keys():
    from backend.live_config import _KEYS
    keys = set(lock_codes.config_labels())
    assert keys <= set(_KEYS), keys - set(_KEYS)


def test_gate_history_rows_carry_marks_badge_and_reason_label(monkeypatch):
    from backend import db
    from backend.routers import live_router
    from backend.routers.live_router import live_gate_history
    monkeypatch.setattr(live_router, "LIVE_ENABLED", False)   # no broker call (conftest refuses it)
    # The endpoint serves rows since today's 09:30 ET; a row stamped "now" is outside it before the open
    # (failed every morning in Stockholm, found 2026-10-29 10:08), so stamp it at the open.
    et = ZoneInfo("America/New_York")
    at_open = datetime.combine(datetime.now(et).date(), time(9, 30), tzinfo=et).isoformat()
    states = json.dumps({K(5, "leading"): PASS, K(2, "overflow"): FAIL})
    conn = db.get_db()
    try:
        conn.execute("INSERT INTO live_gate_history (ticker, sector, gate_decision, stop_code, rule_states, "
                     "lock_leading_pass, timestamp) VALUES ('RSX', 'Tech', 'FILTERED_L2_RISK', "
                     "'FILTERED_L2_RISK_OVERFLOW', ?, 1, ?)", (states, at_open))
        conn.commit()
    finally:
        conn.close()
    try:
        row = next(r for r in live_gate_history()["rows"] if r["ticker"] == "RSX")
    finally:
        _delete("DELETE FROM live_gate_history WHERE ticker = 'RSX'")
    assert row["lock_states"] == [None, None, "fail", None, None, "pass", None]
    assert row["badge"] == "L2 OVERFLOW" and row["badge_kind"] == "stop" and row["reason_label"] is None


def test_review_history_shows_the_owner_rule_and_reason_and_leaves_out_skips():
    # R6: l2_summary (sentiment) came first, so a positioning or review rejection reached Claude as a
    # sentiment text. From phase 2 the stage is the owner's lock (the guard is L0's); a skip carries no judgement.
    from backend import db
    conn = db.get_db()
    try:
        for i, (code, stop, l2s, reason, l1p) in enumerate((
            ("FILTERED_L5_POSITIONING", "FILTERED_L5_POSITIONING_LEADING", "sentiment text", "options group failed", 1),
            ("FILTERED_L6_REVIEW", "FILTERED_L6_REVIEW_CLAUDE", "sentiment text", "Portfolio is at the limit", 1),
            ("FILTERED_L4_SENTIMENT", "FILTERED_L4_SENTIMENT_GROK", "weak sentiment", None, 1),
            ("FILTERED_L0_PORTFOLIO", "FILTERED_L0_PORTFOLIO_DRAWDOWN_GUARD", None, "drawdown 25% > max 20%", 1),
            ("FILTERED_L1_ELIGIBILITY", "FILTERED_L1_ELIGIBILITY_BLOCKLIST", None, None, None),
        )):
            # Writers set every flag explicitly (NULL where the lock did not run); the schema's DEFAULT 0 is never used.
            conn.execute("INSERT INTO live_gate_history (ticker, sector, gate_decision, stop_code, l2_summary, "
                         "lock3_reasoning, lock1_pass, lock2_pass, lock_leading_pass, lock3_pass, timestamp) "
                         "VALUES ('RVX', 'Tech', ?, ?, ?, ?, ?, NULL, NULL, NULL, ?)",
                         (code, stop, l2s, reason, l1p, f"2099-01-0{i + 1}T15:00:00"))
        conn.commit()
    finally:
        conn.close()
    try:
        got = db.get_live_ticker_gate_fails("RVX", limit=10)
    finally:
        _delete("DELETE FROM live_gate_history WHERE ticker = 'RVX'")
    assert [(r["outcome"], r["stage"], r["detail"]) for r in got] == [
        ("FILTERED_L0_PORTFOLIO", "L0 · Portfolio", "drawdown 25% > max 20%"),
        ("FILTERED_L4_SENTIMENT", "L4 · Sentiment", "weak sentiment"),
        ("FILTERED_L6_REVIEW", "L6 · Review", "Portfolio is at the limit"),
        ("FILTERED_L5_POSITIONING", "L5 · Positioning", "options group failed"),
    ]
