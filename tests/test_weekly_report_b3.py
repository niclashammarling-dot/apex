"""
The weekly report's b3 section (2026-11-14, Saturday order item 8): the position cap's decision rule (R009) read
from the slot fields the live gate stores from 2026-11-09, in both parts of the mail.
"""
import pytest

import backend.weekly_report as wr
from backend.db import get_db, init_db

init_db()
UNTIL = "2034-03-13T00:00:00+00:00"


@pytest.fixture(autouse=True)
def _clean():
    yield
    conn = get_db()
    try:
        conn.execute("DELETE FROM live_trades WHERE alpaca_order_id LIKE 'b3-%'")
        conn.execute("DELETE FROM live_gate_history WHERE alpaca_order_id LIKE 'b3-%'")
        conn.execute("DELETE FROM gate_cycles WHERE job = 'run_live_gate' AND started_at >= '2034-'")
        conn.commit()
    finally:
        conn.close()


def _entry(i: int, open_count: int, ret: float, ts: str = "2034-03-07T15:00:00+00:00"):
    conn = get_db()
    try:
        oid = f"b3-{i}"
        conn.execute("INSERT INTO live_trades (timestamp, ticker, sector, alpaca_order_id, entry_price, qty, notional, "
                     "outcome, pnl, exit_price) VALUES (?, ?, 'Tech', ?, 100, 10, 1000, ?, ?, ?)",
                     (ts, f"B{i}", oid, "WIN" if ret > 0 else "LOSS", ret * 1000, 100 * (1 + ret)))
        conn.execute("INSERT INTO live_gate_history (timestamp, ticker, sector, gate_decision, alpaca_order_id, "
                     "l2_risk_open_count) VALUES (?, ?, 'Tech', 'TRADE_EXECUTED', ?, ?)", (ts, f"B{i}", oid, open_count))
        conn.commit()
    finally:
        conn.close()


def _cycles(n: int, at_cap: int):
    conn = get_db()
    try:
        for k in range(n):
            conn.execute("INSERT INTO gate_cycles (job, trigger, started_at, held_at_start, l2_risk_max_positions_count) "
                         "VALUES ('run_live_gate', 'test', ?, ?, 9)", (f"2034-03-07T15:{k:02d}:00+00:00", 9 if k < at_cap else 5))
        conn.commit()
    finally:
        conn.close()


def test_below_thirty_in_slots_8_plus_the_rule_keeps_nine():
    for i in range(5):
        _entry(i, 3, 0.02)
    for i in range(5, 8):
        _entry(i, 8, 0.01)                                          # slot 9
    b = wr._b3_slots(UNTIL)
    assert (b["n_1_7"], b["n_8_plus"]) == (5, 3) and b["verdict"].startswith("keep 9: slots 8+ hold 3")


def test_a_worse_top_slot_at_thirty_meets_the_lowering_condition():
    for i in range(40):
        _entry(i, 2, 0.03 + (i % 5) * 0.001)
    for i in range(40, 72):
        _entry(i, 8, -0.02 - (i % 5) * 0.001)
    assert wr._b3_slots(UNTIL)["verdict"].startswith("lower 9 -> 8")


def test_the_cap_share_reads_recorded_live_cycles():
    _cycles(20, 3)
    b = wr._b3_slots(UNTIL)
    assert (b["cycles"], b["cycles_at_cap"]) == (20, 3) and abs(b["share_at_cap"] - 0.15) < 1e-12


def test_both_parts_of_the_mail_carry_the_b3_line(monkeypatch):
    import backend.config as config_module
    monkeypatch.setattr(config_module, "OPENAI_API_KEY", "")
    monkeypatch.setattr(wr, "_week_start_iso", lambda: "2034-03-06T00:00:00+00:00")
    monkeypatch.setattr(wr, "_fetch_prices", lambda tickers: {})
    _, html, plain = wr.build_report(recal_changes=None)
    assert "Position Cap (b3)" in html and "Rule (R009)" in html
    assert "POSITION CAP (b3)" in plain and "Rule (R009)" in plain
