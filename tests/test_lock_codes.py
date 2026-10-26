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
