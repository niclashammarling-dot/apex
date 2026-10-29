"""
Lock labels phase 2: the migration (backend/gate/history_migrate.py) on a fixture database in tmp_path.

The dry run on a copy of production (scripts/migrate_gate_codes.py --dry-run) is the counts' positive control;
these tests pin the mechanics: every coded row re-coded with its stop_code and recorded, the pass flags after
the last lock that ran set to NULL (ruling D), the test row deleted with its full row kept, reverse() restoring
the original exactly, a second run refused, and an unclassifiable
row rolling the whole transaction back.
"""
import sqlite3

import pytest

from backend.gate.history_classify import UnclassifiedRow
from backend.gate.history_migrate import RECORD, migrate, reverse

COLS = ("id INTEGER PRIMARY KEY, timestamp TEXT, ticker TEXT, signal_score REAL, gate_decision TEXT, "
        "lock1_pass INTEGER, lock2_pass INTEGER, lock3_pass INTEGER, lock_leading_pass INTEGER, "
        "lock3_reasoning TEXT, l2_summary TEXT, macro_reason TEXT, outcome_reason TEXT")
ROWS = {
    "live_gate_history": [
        # Flags as production holds them on every FILTERED_L1 row (180/182/167, read 2026-10-29): 0 and 0.
        (1, "2026-09-03T19:11:02+00:00", "TMO", 0.6049, "FILTERED_L1", 0, 0, 0, 0,
         "Signal score 0.5747 below threshold 0.5892", None, None, None),
        (2, "2026-07-16T17:44:52+00:00", "JNJ", 0.5177, "FILTERED_ETF_PENALTY", 1, 1, 0, 1, None, None, None, None),
        (3, "2026-10-12T19:34:13+00:00", "LLY", 0.6495, "TRADE_FAILED", 1, 1, 1, 1, None, None, None,
         "broker_error: Computed qty=0"),
        (4, "2026-06-03T17:48:00+00:00", "CVX", 0.61, "SKIPPED_COOLOFF", 1, 0, 0, 0, None, None, None, None),
    ],
    "demo_gate_history": [
        (1276, "2026-01-01T00:00:00", "_TEST", 0.5, "FILTERED_L1", 0, 0, 0, None, None, None, None, None),
        (7439, "2026-07-17T13:50:12+00:00", "CVX", 0.7684, "FILTERED_L3", 1, 1, 0, 1,
         "drawdown 25.1% > max 20.0% (mechanical pre-L5 guard)", None, None, None),
    ],
    "signals": [
        (10, "2026-10-01T15:00:00+00:00", "AAPL", 0.61, None, None, None, None, None, None, None, None, None),
        (11, "2026-06-04T13:48:08+00:00", "NOW", 0.712, "FILTERED_ELIGIBILITY", 0, 0, 0, None, None, None,
         "Macro event blackout: NFP on 2026-06-05 (in 1d)", None),
    ],
}


@pytest.fixture
def db(tmp_path):
    conn = sqlite3.connect(tmp_path / "gate.db")
    for t, rows in ROWS.items():
        conn.execute(f"CREATE TABLE {t} ({COLS})")
        conn.executemany(f"INSERT INTO {t} VALUES ({', '.join('?' * 13)})", rows)
    conn.commit()
    yield conn
    conn.close()


def _dump(conn):
    conn.row_factory = None
    return {t: conn.execute(f"SELECT id, timestamp, ticker, signal_score, gate_decision, lock1_pass, lock2_pass, "
                            f"lock3_pass, lock_leading_pass, lock3_reasoning, l2_summary, macro_reason, "
                            f"outcome_reason FROM {t} ORDER BY id").fetchall() for t in ROWS}


def test_migrate_recodes_records_and_reverse_restores(db):
    original = _dump(db)
    counts = migrate(db, "2026-10-28T12:00:00+00:00")
    db.row_factory = None
    got = {t: dict(db.execute(f"SELECT id, stop_code FROM {t}")) for t in ROWS}
    assert got == {
        "live_gate_history": {1: "FILTERED_L1_ELIGIBILITY_MACRO_PRE_EVENT", 2: "FILTERED_L2_RISK_ETF",
                              3: "FILTERED_L1_ELIGIBILITY_NOTIONAL_TOO_SMALL", 4: "FILTERED_L1_ELIGIBILITY_COOLOFF"},
        "demo_gate_history": {7439: "FILTERED_L0_PORTFOLIO_DRAWDOWN_GUARD"},          # 1276 deleted
        "signals": {10: None, 11: "FILTERED_L1_ELIGIBILITY_MACRO_EVENT"},             # ungated stays NULL
    }
    assert dict(db.execute("SELECT id, gate_decision FROM live_gate_history")) == {
        1: "FILTERED_L1_ELIGIBILITY", 2: "FILTERED_L2_RISK", 3: "FILTERED_L1_ELIGIBILITY", 4: "FILTERED_L1_ELIGIBILITY"}
    # Ruling D: (lock1_pass, lock2_pass, lock3_pass, lock_leading_pass), NULL after the last lock that ran.
    flags = lambda t: {r[0]: r[1:] for r in db.execute(
        f"SELECT id, lock1_pass, lock2_pass, lock3_pass, lock_leading_pass FROM {t}")}
    assert flags("live_gate_history") == {
        1: (0, None, None, None),          # the pre-event penalty bit at L3's re-test: the test as run failed
        2: (1, 1, None, 1),                # the ETF re-test ran after L5, before L6
        3: (1, 1, 1, 1),                   # Claude approved, the order could not buy
        4: (None, None, None, None),       # a skip ran no lock (it wrote lock1_pass 1)
    }
    assert flags("demo_gate_history") == {7439: (1, 1, None, 1)}     # the guard ran before L6
    assert flags("signals")[11] == (None, None, None, None)           # eligibility: no flagged lock ran
    assert sum(counts.values()) == 7 == db.execute(f"SELECT COUNT(*) FROM {RECORD}").fetchone()[0]
    rec = db.execute(f"SELECT old_code, basis, old_pass_flags, deleted_row FROM {RECORD} "
                     f"WHERE tbl = 'demo_gate_history' AND row_id = 1276").fetchone()
    assert rec[0] == "FILTERED_L1" and '"ticker": "_TEST"' in rec[3]
    assert '"lock1_pass": 1' in db.execute(f"SELECT old_pass_flags FROM {RECORD} WHERE row_id = 7439").fetchone()[0]

    with pytest.raises(RuntimeError):
        migrate(db)                                              # a migrated database is refused
    assert reverse(db) == 7
    assert _dump(db) == original
    assert db.execute(f"SELECT COUNT(*) FROM {RECORD}").fetchone()[0] == 0


def test_an_unclassifiable_row_rolls_everything_back(db):
    db.execute("INSERT INTO signals (id, timestamp, gate_decision) VALUES (12, '2026-10-01', 'FILTERED_SOMETHING_NEW')")
    db.commit()
    original = _dump(db)
    with pytest.raises(UnclassifiedRow):
        migrate(db)
    assert _dump(db) == original
    assert db.execute(f"SELECT COUNT(*) FROM {RECORD}").fetchone()[0] == 0


def test_migrate_writes_the_rule_states_the_row_proves_and_reverse_clears_them(db):
    import json
    migrate(db, migrated_at="2026-10-31T10:00:00+00:00")
    db.row_factory = None
    got = {(t, i): json.loads(s) for t in ROWS
           for i, s in db.execute(f"SELECT id, rule_states FROM {t} WHERE rule_states IS NOT NULL")}
    # TMO: a macro pre-event bite after the eligibility module went live. The penalty failed the score test (its
    # flag is 0), so only the stop and the module's regime and macro checks are known; earnings had no data.
    assert got[("live_gate_history", 1)] == {"L1_ELIGIBILITY_MACRO_PRE_EVENT": "fail", "L1_ELIGIBILITY_REGIME": "pass",
                                             "L1_ELIGIBILITY_MACRO_EVENT": "pass", "L2_RISK_EARNINGS": "no_data"}
    # LLY: Claude approved (lock3_pass 1), the order could not buy one share.
    assert got[("live_gate_history", 3)]["L1_ELIGIBILITY_NOTIONAL_TOO_SMALL"] == "fail"
    assert got[("live_gate_history", 3)]["L6_REVIEW_CLAUDE"] == "pass"
    # A skip ran no chain lock: only its rule.
    assert got[("live_gate_history", 4)] == {"L1_ELIGIBILITY_COOLOFF": "fail"}
    # The guard (L0): L5 passed, L6 never ran (its 0 is NULL after ruling D, so no Claude verdict is claimed).
    assert got[("demo_gate_history", 7439)]["L0_PORTFOLIO_DRAWDOWN_GUARD"] == "fail"
    assert "L6_REVIEW_CLAUDE" not in got[("demo_gate_history", 7439)]
    assert ("signals", 10) not in got                                   # ungated rows stay NULL
    reverse(db)
    assert db.execute("SELECT COUNT(*) FROM live_gate_history WHERE rule_states IS NOT NULL").fetchone()[0] == 0
