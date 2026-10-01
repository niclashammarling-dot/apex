"""
audit/gate_cycles.py (2026-10-01): one cycle per cycle_started_at, expected from the grid.

09-30 read demo 20/19 on /api/ops/window with 19 real cycles: one cycle's rows
spanned a minute and counted twice, and 390 // 20 = 19 is not the number of
grid slots a full day holds at live's phase (20).
"""
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from audit.gate_cycles import cycle_starts, expected_cycles

NY = ZoneInfo("America/New_York")
D = "2026-09-30"
OPEN, CLOSE = datetime(2026, 9, 30, 9, 30, tzinfo=NY), datetime(2026, 9, 30, 16, 0, tzinfo=NY)


def _db(rows):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE demo_gate_history (timestamp TEXT, cycle_started_at TEXT)")
    conn.executemany("INSERT INTO demo_gate_history VALUES (?, ?)", rows)
    return conn


def test_a_stamped_cycle_straddling_a_minute_is_one_cycle():
    cyc = f"{D}T15:01:20+00:00"
    conn = _db([(f"{D}T15:01:20+00:00", cyc), (f"{D}T15:03:04+00:00", cyc), (f"{D}T15:21:06+00:00", f"{D}T15:21:06+00:00")])
    assert cycle_starts(conn, "demo_gate_history", D) == {D: [f"{D}T15:01", f"{D}T15:21"]}


def test_unstamped_rows_fall_back_to_their_own_minute():
    conn = _db([(f"{D}T15:01:20+00:00", None), (f"{D}T15:03:04+00:00", None)])
    assert len(cycle_starts(conn, "demo_gate_history", D)[D]) == 2


def test_a_table_without_the_column_counts_row_minutes():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE demo_gate_history (timestamp TEXT)")
    conn.executemany("INSERT INTO demo_gate_history VALUES (?)", [(f"{D}T15:01:20+00:00",), (f"{D}T15:21:06+00:00",)])
    assert cycle_starts(conn, "demo_gate_history", D) == {D: [f"{D}T15:01", f"{D}T15:21"]}


def test_expected_follows_the_phase_live_20_demo_19():
    assert expected_cycles(OPEN, CLOSE, [f"{D}T13:33"], 20) == 20      # 09:33 … 15:53 ET
    assert expected_cycles(OPEN, CLOSE, [f"{D}T13:41"], 20) == 19      # 09:41 … 15:41 ET


def test_no_starts_falls_back_to_the_floor_estimate():
    assert expected_cycles(OPEN, CLOSE, [], 20) == 19


def test_early_close_counts_its_own_slots():
    early = datetime(2026, 11, 27, 13, 0, tzinfo=ZoneInfo("America/New_York"))
    start = datetime(2026, 11, 27, 9, 30, tzinfo=ZoneInfo("America/New_York"))
    assert expected_cycles(start, early, ["2026-11-27T14:33"], 20) == 11   # 09:33 … 12:53 ET (EST)


def test_ops_window_reports_both_books_on_cycle_stamps(tmp_path, monkeypatch):
    import backend.routers.signals_router as sr
    from backend.db import get_db
    monkeypatch.setattr(sr, "_LOG_DIR", tmp_path)
    before = sr.get_market_window(days=10)["sessions"][-1]
    d = before["date"]
    rows = []
    for t, base in [("live_gate_history", 33), ("demo_gate_history", 41)]:
        for k in range(3):
            hh, mm = divmod(13 * 60 + base + 20 * k, 60)
            cyc = f"{d}T{hh:02d}:{mm:02d}:30+00:00"
            # two rows per cycle, the second a minute later: the old key counted 6
            rows += [(t, cyc, cyc), (t, f"{d}T{hh:02d}:{mm + 1:02d}:05+00:00" if mm < 59 else cyc, cyc)]
    with get_db() as conn:
        for t, ts, cyc in rows:
            conn.execute(f"INSERT INTO {t} (timestamp, ticker, sector, cycle_started_at) VALUES (?, 'TST', 'Test', ?)",
                         (ts, cyc))
        conn.commit()
    try:
        s = next(x for x in sr.get_market_window(days=10)["sessions"] if x["date"] == d)
        # delta, not absolute: the temp DB is shared with the rest of the suite
        assert (s["cycles"] - before["cycles"], s["live_cycles"] - before["live_cycles"]) == (3, 3)
        if before["cycles"] == 0 and before["live_cycles"] == 0 and not s["early_close"]:
            assert (s["expected"], s["live_expected"]) == (19, 20)
    finally:
        with get_db() as conn:
            for t in ("live_gate_history", "demo_gate_history"):
                conn.execute(f"DELETE FROM {t} WHERE ticker = 'TST'")
            conn.commit()


def test_check80_coverage_reads_cycle_stamps_and_the_grid():
    from audit.checks_gate import _c80_session_coverage
    demo_day = [f"{D}T{(13 * 60 + 41 + 20 * k) // 60:02d}:{(13 * 60 + 41 + 20 * k) % 60:02d}" for k in range(19)]
    assert _c80_session_coverage({D: demo_day}) == [(D, 19, 19)]
