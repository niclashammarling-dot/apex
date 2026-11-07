"""
The gate stores what it decided on (vault scope raw/notes/2026-11/2026-11-06-apex-scopes-gate-decision-inputs-per-row-and-
config-change-log.md, Job 1; built 2026-11-07). Each serial-phase row carries the open count and cap the slot logic
used, the overflow threshold it applied, its place in the serial order and the pre-rotation flag; the cycle row carries
the held count at the serial phase's start and the cap. The b3 read on 11-06 had to rebuild these from fills and logs
in four passes, with a ±1 limit for the pre-rotation order nobody stored.
"""
from contextlib import ExitStack

import pytest

from backend.gate.cycle import GateCycle


def _positions(n):
    return [{"ticker": f"H{i}", "qty": 1.0, "side": "long", "current_price": 10.0, "market_value": 10.0,
             "sector": "Utilities"} for i in range(n)]


def test_live_rows_store_open_count_rank_and_pre_rotation_with_pre_rotation_last():
    from tests.test_gate_runners import _live_patches, _signal
    a = {**_signal(ticker="AAA", sector="Technology", score=0.70, sid=1), "pre_rotation": True}
    b = _signal(ticker="BBB", sector="Healthcare", score=0.68, sid=2)
    c = _signal(ticker="CCC", sector="Energy", score=0.66, sid=3)
    cycle = GateCycle("run_live_gate", "test", "2026-11-07T15:00:00+00:00")
    with ExitStack() as st:
        mocks = [st.enter_context(p) for p in _live_patches(
            candidates=[a, b, c], positions=_positions(2),
            cfg_overrides={"l2_risk_max_positions_count": 3, "l2_risk_sector_cap_max": 1.0})]
        from backend.gate import gate_runner_live
        gate_runner_live.run(cycle=cycle)
    rows = {call.args[0]["ticker"]: call.args[0] for call in mocks[9].call_args_list}
    assert [rows[t]["exec_rank"] for t in ("BBB", "CCC", "AAA")] == [1, 2, 3]      # pre-rotation last despite its score
    assert [rows[t]["pre_rotation"] for t in ("BBB", "CCC", "AAA")] == [0, 0, 1]
    assert [rows[t]["l2_risk_open_count"] for t in ("BBB", "CCC", "AAA")] == [2, 3, 4]
    assert all(rows[t]["l2_risk_max_positions_count"] == 3 for t in rows)
    assert rows["BBB"]["l2_risk_overflow_threshold"] is None                         # a free slot: no threshold applied
    assert rows["CCC"]["l2_risk_overflow_threshold"] is not None
    assert rows["AAA"]["l2_risk_overflow_threshold"] > rows["CCC"]["l2_risk_overflow_threshold"]   # level 2 > level 1
    assert (cycle.held_at_start, cycle.l2_risk_max_positions_count) == (2, 3)


def test_the_columns_exist_and_take_the_values():
    import sqlite3

    import backend.db as db
    conn = sqlite3.connect(db.DB_PATH)
    for t in ("live_gate_history", "demo_gate_history"):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({t})")}
        assert {"l2_risk_open_count", "l2_risk_max_positions_count", "l2_risk_overflow_threshold", "exec_rank",
                "pre_rotation"} <= cols
    assert {"held_at_start", "l2_risk_max_positions_count"} <= {r[1] for r in conn.execute("PRAGMA table_info(gate_cycles)")}
    conn.close()
    cid = db.insert_gate_cycle("run_live_gate", "test", "2026-11-07T15:00:00+00:00")
    db.finish_gate_cycle(cid, "ok", None, 0, held_at_start=6, max_positions=9)
    conn = sqlite3.connect(db.DB_PATH)
    assert conn.execute("SELECT held_at_start, l2_risk_max_positions_count FROM gate_cycles WHERE id = ?",
                        (cid,)).fetchone() == (6, 9)
    conn.execute("DELETE FROM gate_cycles WHERE id = ?", (cid,))
    conn.commit()
    conn.close()


def test_slot_evidence_below_and_at_the_cap():
    from backend.gate.gate_runner import _slot_evidence
    below = _slot_evidence({"pre_rotation": False}, 1, 2, 3, 0.6, 0.05)
    at = _slot_evidence({"pre_rotation": True}, 2, 3, 3, 0.6, 0.05)
    assert below["l2_risk_overflow_threshold"] is None and below["pre_rotation"] == 0
    assert at["l2_risk_overflow_threshold"] == pytest.approx(0.63) and at["pre_rotation"] == 1


def test_demo_rows_store_the_same_evidence():
    # Scope item 3: the demo runner has the same serial loop and keeps one shape with live.
    from tests.test_gate_runners import _demo_patches, _signal
    a = {**_signal(ticker="AAA", sector="Technology", score=0.70, sid=1), "pre_rotation": True}
    b = _signal(ticker="BBB", sector="Healthcare", score=0.68, sid=2)
    cycle = GateCycle("run_gate", "test", "2026-11-07T15:00:00+00:00")
    with ExitStack() as st:
        mocks = [st.enter_context(p) for p in _demo_patches(candidates=[a, b], open_tickers={"H1"},
                                                            cfg_overrides={"l2_risk_max_positions_count": 2})]
        from backend.gate import gate_runner
        gate_runner.run(cycle=cycle)
    rows = {call.args[0]["ticker"]: call.args[0] for call in mocks[8].call_args_list}
    assert (rows["BBB"]["exec_rank"], rows["AAA"]["exec_rank"]) == (1, 2)
    assert (rows["BBB"]["pre_rotation"], rows["AAA"]["pre_rotation"]) == (0, 1)
    assert rows["BBB"]["l2_risk_open_count"] == 1 and rows["BBB"]["l2_risk_max_positions_count"] == 2
    assert cycle.held_at_start == 1


def test_gate_capacity_endpoint_says_not_recorded_then_shows_the_last_cycle():
    import backend.db as db
    from backend.routers.signals_router import get_gate_capacity
    conn = db.get_db()
    try:
        conn.execute("DELETE FROM gate_cycles WHERE job = 'run_live_gate'")
        conn.commit()
    finally:
        conn.close()
    assert get_gate_capacity()["books"]["live"]["last_cycle"] is None          # not recorded, never zero
    from datetime import datetime, timezone
    started = datetime.now(timezone.utc).isoformat()
    cid = db.insert_gate_cycle("run_live_gate", "test", started)
    db.finish_gate_cycle(cid, "ok", None, 0, held_at_start=9, max_positions=9)
    conn = db.get_db()
    try:
        conn.execute("INSERT INTO live_gate_history (timestamp, ticker, sector, signal_score, gate_decision, "
                     "cycle_started_at, exec_rank, pre_rotation, l2_risk_open_count, l2_risk_overflow_threshold) "
                     "VALUES (?, 'ZZA', 'Energy', 0.7, 'TRADE_EXECUTED', ?, 1, 0, 9, 0.63)", (started, started))
        conn.commit()
        live = get_gate_capacity()["books"]["live"]
        assert live["last_cycle"]["held_at_start"] == 9 and live["cycles_at_cap"] == 1
        assert live["last_order"][0]["ticker"] == "ZZA" and live["last_order"][0]["l2_risk_overflow_threshold"] == 0.63
    finally:
        conn.execute("DELETE FROM live_gate_history WHERE ticker = 'ZZA'")
        conn.execute("DELETE FROM gate_cycles WHERE id = ?", (cid,))
        conn.commit()
        conn.close()
