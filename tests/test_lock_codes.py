"""Lock labels phase 1 (2026-10-26): one table of locks and gate_decision codes.

Phase 1 changes no stored string, so the codes are pinned to what the gate tables hold; the cooloff
set is pinned to ruling 6 ("behåll cool off nu"); the labels to the chain approved 2026-10-24.
"""
from datetime import datetime, timezone

from backend.gate import lock_codes
from backend.gate.chain import ChainResult


def test_codes_are_the_stored_ones_until_phase_2():
    assert lock_codes.OUTCOME_BY_EXIT_LOCK == {
        1: "FILTERED_ELIGIBILITY", 2: "FILTERED_L1", 3: "FILTERED_L2", 4: "FILTERED_LEADING", 5: "FILTERED_L3",
    }
    assert lock_codes.ETF_PENALTY == "FILTERED_ETF_PENALTY"
    assert lock_codes.OVERFLOW == "FILTERED_OVERFLOW_QUANT"
    assert lock_codes.outcome_for(None) == lock_codes.outcome_for(9) == "FILTERED_UNKNOWN"


def test_cooloff_set_is_exactly_sentiment_and_review():
    assert set(lock_codes.COOLOFF_CODES) == {"FILTERED_L2", "FILTERED_L3"}
    assert {lock_codes.BY_CODE[c].lock for c in lock_codes.COOLOFF_CODES} == {4, 6}


def test_labels_follow_the_approved_chain():
    assert [lock_codes.LOCKS[n][0] for n in range(7)] == [
        "PORTFOLIO", "ELIGIBILITY", "RISK", "SCORE", "SENTIMENT", "POSITIONING", "REVIEW"]
    for s in lock_codes.STAGES:
        if s.kind == "filter":
            assert s.label.startswith(f"L{s.lock} · "), s
            assert lock_codes.LOCKS[s.lock][0].lower() in s.label.lower(), s


def _result(**kw):
    return ChainResult(ticker="X", sector="Tech", approved=False, lock_results={}, final_score=0.0,
                       summary="", **kw)


def test_runner_records_the_table_code_for_each_exit_lock():
    from backend.gate.gate_runner import _chain_to_gate_result
    sig = {"id": 1, "ticker": "X", "sector": "Tech", "signal_score": 0.7}
    for n, code in lock_codes.OUTCOME_BY_EXIT_LOCK.items():
        assert _chain_to_gate_result(sig, _result(exit_lock=n))["outcome"] == code
    etf = _result(exit_lock=4, exit_label=lock_codes.ETF_PENALTY)
    assert _chain_to_gate_result(sig, etf)["outcome"] == "FILTERED_ETF_PENALTY"


def test_cooloff_queries_select_the_table_set():
    from backend import db
    now = datetime.now(timezone.utc).isoformat()
    codes = [s.code for s in lock_codes.STAGES]
    conn = db.get_db()
    try:
        for i, code in enumerate(codes):
            conn.execute("INSERT INTO live_gate_history (ticker, sector, gate_decision, timestamp) VALUES (?, 'Tech', ?, ?)",
                         (f"LC{i}", code, now))
            conn.execute("INSERT INTO signals (ticker, sector, gate_decision, timestamp) VALUES (?, 'Tech', ?, ?)",
                         (f"LC{i}", code, now))
        conn.commit()
    finally:
        conn.close()
    try:
        want = {f"LC{i}" for i, c in enumerate(codes) if c in lock_codes.COOLOFF_CODES}
        got_live = {t for t in db.get_recently_failed_live_tickers(1.0) if t.startswith("LC")}
        got_demo = {t for t in db.get_recently_failed_tickers(1.0) if t.startswith("LC")}
        assert got_live == got_demo == want and len(want) == 2
    finally:
        # The session DB is shared: a scoreless signals row broke get_lock1_candidates in a later file.
        _delete("DELETE FROM live_gate_history WHERE ticker LIKE 'LC%'", "DELETE FROM signals WHERE ticker LIKE 'LC%'")


def _delete(*sql):
    from backend import db
    conn = db.get_db()
    try:
        for s in sql:
            conn.execute(s)
        conn.commit()
    finally:
        conn.close()


def test_api_shape():
    api = lock_codes.as_api()
    assert [x["lock"] for x in api["locks"]] == list(range(7))
    assert [x["code"] for x in api["stages"]] == [s.code for s in lock_codes.STAGES]
    etf = next(x for x in api["stages"] if x["code"] == "FILTERED_ETF_PENALTY")
    assert etf["lock"] == 2 and etf["exit_lock"] is None and etf["config_keys"]


def test_endpoint_serves_the_table():
    from backend.routers.signals_router import gate_locks
    assert gate_locks() == lock_codes.as_api()


def test_terminal_columns_and_states_follow_evaluation_order():
    api = lock_codes.as_api()
    assert [c["head"] for c in api["columns"]] == ["L1", "L3", "L4", "L5", "L2", "L6"]
    states = {s["code"]: s["states"] for s in api["stages"]}
    assert states["FILTERED_L1"] == [True, False, None, None, None, None]
    assert states["FILTERED_ETF_PENALTY"] == [True, True, True, True, False, None]
    assert states["TRADE_EXECUTED"] == [True] * 6
    assert {s["code"]: s["badge"] for s in api["stages"]}["FILTERED_L2"] == "L4 FAIL"


def test_live_funnel_counts_the_etf_penalty():
    # Scope probe 2026-10-24: the dashboard funnel query counted no FILTERED_ETF_PENALTY (24 live rows).
    from backend import db
    conn = db.get_db()
    try:
        conn.execute("INSERT INTO live_gate_history (ticker, sector, gate_decision, timestamp) "
                     "VALUES ('ETFX', 'Tech', 'FILTERED_ETF_PENALTY', '2099-01-02T15:00:00+00:00')")
        conn.commit()
    finally:
        conn.close()
    try:
        stages = db.get_live_gate_funnel_counts(since="2099-01-01T00:00:00+00:00")["stages"]
    finally:
        _delete("DELETE FROM live_gate_history WHERE ticker = 'ETFX'")
    etf = next(s for s in stages if s["code"] == "FILTERED_ETF_PENALTY")
    assert etf["count"] == 1 and etf["label"] == "L2 · Risk (ETF)"
    assert [s["code"] for s in stages][:4] == ["SKIPPED_BLOCKLIST", "SKIPPED_OPEN", "SKIPPED_COOLOFF",
                                               "FILTERED_ELIGIBILITY"]


# ── Review before merge (2026-10-26, findings R1-R6, R8) ─────────────────────

def test_overflow_stops_before_review_in_order_and_states():
    # R1: both runners defer L6 (stop_after_lock=4) and check overflow first; no overflow row reached L6.
    order = [s.code for s in lock_codes.STAGES]
    assert order.index("FILTERED_OVERFLOW_QUANT") < order.index("FILTERED_L3")
    assert lock_codes.lock_states("FILTERED_OVERFLOW_QUANT") == [True, True, True, True, True, None]
    assert lock_codes.lock_states("FILTERED_L3") == [True, True, True, True, True, False]


def test_every_filter_fails_exactly_its_own_column():
    for i, col in enumerate(lock_codes.CHAIN):
        states = lock_codes.lock_states(col.code)
        assert states[i] is False and states.count(False) == 1
        assert all(v is True for v in states[:i]) and all(v is None for v in states[i + 1:])


def test_skip_legacy_and_unknown_codes_show_no_lock_as_passed():
    # R3: they rendered every column as passed.
    for code in ("SKIPPED_OPEN", "SKIPPED_BLOCKLIST", "FILTERED_MACRO", "FILTERED_SOMETHING_NEW", None):
        assert lock_codes.lock_states(code) == [None] * len(lock_codes.CHAIN)


def test_a_trade_row_passed_review_only_if_claude_approved():
    # R2: the pre-review "position already open" reject records TRADE_REJECTED with lock3_pass 0.
    assert lock_codes.row_states("TRADE_REJECTED", 1) == [True] * 6
    assert lock_codes.row_states("TRADE_REJECTED", 0) == [True] * 5 + [None]
    assert lock_codes.row_states("FILTERED_L3", 0) == lock_codes.lock_states("FILTERED_L3")


def test_reason_label_names_the_stage_that_wrote_the_reason():
    # R5: the Terminal labelled lock3_reasoning "L5:" for every row.
    assert lock_codes.reason_label("FILTERED_LEADING") == "L5 · Positioning"
    assert lock_codes.reason_label("FILTERED_ELIGIBILITY") == "L1 · Eligibility"
    assert lock_codes.reason_label("TRADE_EXECUTED") == "L6 · Review"
    assert lock_codes.reason_label("FILTERED_OVERFLOW_QUANT") is None
    rows = lock_codes.decorate_rows([{"gate_decision": "TRADE_REJECTED", "lock3_pass": 0}])
    assert rows[0]["lock_states"][-1] is None and rows[0]["reason_label"] == "L6 · Review"


def test_detail_labels_name_what_each_column_holds():
    # R5: l2_summary is the sentiment summary (gate_runner: l3_data summary), shown as "L3:" before.
    assert lock_codes.as_api()["detail_labels"] == {
        "macro_reason": "L1 · Eligibility", "l2_summary": "L4 · Sentiment", "lock_leading_checks": "L5 · Positioning"}


def test_lock_owned_config_keys_follow_the_approved_chain():
    # R4: L2 RISK owns ETF, earnings, macro and concentration (chain approved 2026-10-24); L1 market state.
    labels = lock_codes.config_labels()
    for k in ("macro_event_blackout_days", "macro_earnings_blackout_days", "macro_earnings_near_penalty",
              "macro_pre_event_penalty", "max_sector_exposure", "etf_negative_penalty"):
        assert labels[k].startswith("L2 · Risk"), k
    assert labels["vix_threshold"] == "L1 · Eligibility"
    assert labels["max_drawdown_pct"] == "L6 · Review"            # R8: the pre-review guard's threshold
    assert lock_codes.LOCKS[2][1] == "ETF, earnings, macro, concentration"
    assert "calendar" not in lock_codes.LOCKS[1][1]


def test_owned_keys_are_real_config_keys():
    from backend.live_config import _KEYS
    keys = set(lock_codes.config_labels())
    assert keys <= set(_KEYS), keys - set(_KEYS)


def test_gate_history_rows_carry_states_and_reason_label(monkeypatch):
    from backend import db
    from backend.routers import live_router
    from backend.routers.live_router import live_gate_history
    monkeypatch.setattr(live_router, "LIVE_ENABLED", False)   # no broker call (conftest refuses it)
    now = datetime.now(timezone.utc).isoformat()
    conn = db.get_db()
    try:
        conn.execute("INSERT INTO live_gate_history (ticker, sector, gate_decision, lock3_pass, timestamp) "
                     "VALUES ('RSX', 'Tech', 'FILTERED_OVERFLOW_QUANT', 0, ?)", (now,))
        conn.commit()
    finally:
        conn.close()
    try:
        row = next(r for r in live_gate_history()["rows"] if r["ticker"] == "RSX")
    finally:
        _delete("DELETE FROM live_gate_history WHERE ticker = 'RSX'")
    assert row["lock_states"] == [True, True, True, True, True, None] and row["reason_label"] is None


def test_review_history_shows_the_deciding_reason_and_its_stage():
    # R6: l2_summary (sentiment) came first, so a positioning or review rejection reached Claude as a
    # sentiment text; SKIPPED_BLOCKLIST passed the "non-skip" filter.
    from backend import db
    conn = db.get_db()
    try:
        for i, (code, l2s, reason) in enumerate((
            ("FILTERED_LEADING", "sentiment text", "options group failed"),
            ("FILTERED_L3", "sentiment text", "Portfolio is at the maximum position limit"),
            ("FILTERED_L2", "weak sentiment", None),
            ("SKIPPED_BLOCKLIST", None, None),
        )):
            conn.execute("INSERT INTO live_gate_history (ticker, sector, gate_decision, l2_summary, lock3_reasoning, "
                         "timestamp) VALUES ('RVX', 'Tech', ?, ?, ?, ?)", (code, l2s, reason, f"2099-01-0{i + 1}T15:00:00"))
        conn.commit()
    finally:
        conn.close()
    try:
        got = db.get_live_ticker_gate_fails("RVX", limit=10)
    finally:
        _delete("DELETE FROM live_gate_history WHERE ticker = 'RVX'")
    assert [(r["outcome"], r["stage"], r["detail"]) for r in got] == [
        ("FILTERED_L2", "L4 · Sentiment", "weak sentiment"),
        ("FILTERED_L3", "L6 · Review", "Portfolio is at the maximum position limit"),
        ("FILTERED_LEADING", "L5 · Positioning", "options group failed"),
    ]
