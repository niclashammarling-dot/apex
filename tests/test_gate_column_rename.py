"""
Lock labels phase 3 (2026-11-07): init_db renames the gate columns to the names lock_codes derives, in place,
before any column is added (vault raw/notes/2026-10/2026-10-31-apex-lock-labels-phase3-plan.md, Columns).

The hazard it closes: with the new names in the code, a start on a database still on the old names would have
_add_column_if_missing create the new names as empty columns beside the old ones, and the gate would write to one
while history sits in the other. The production rename runs the same function on a snapshot copy first.
"""
from __future__ import annotations

import sqlite3

import pytest

import backend.db as db

OLD_SCHEMA = """
CREATE TABLE live_gate_history (id INTEGER PRIMARY KEY, ticker TEXT, lock1_pass INTEGER, lock2_pass INTEGER,
  lock_leading_pass INTEGER, lock3_pass INTEGER, lock_leading_checks TEXT, l2_summary TEXT,
  lock3_sentiment_score REAL, lock3_conviction TEXT, macro_reason TEXT, lock3_reasoning TEXT);
CREATE TABLE demo_gate_history (id INTEGER PRIMARY KEY, ticker TEXT, lock3_pass INTEGER, macro_reason TEXT);
CREATE TABLE signals (id INTEGER PRIMARY KEY, ticker TEXT, lock_leading_pass INTEGER, lock3_reasoning TEXT);
"""


def _cols(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


@pytest.fixture
def old_db(tmp_path):
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.executescript(OLD_SCHEMA)
    conn.execute("INSERT INTO live_gate_history (ticker, lock3_pass, lock3_reasoning, macro_reason) "
                 "VALUES ('OXY', 1, 'approved', NULL)")
    conn.commit()
    yield conn
    conn.close()


def test_old_columns_are_renamed_in_place_with_their_rows(old_db):
    done = db._rename_gate_columns(old_db)
    assert len(done) == 10 + 2 + 2
    cols = _cols(old_db, "live_gate_history")
    assert not set(db.GATE_COLUMN_RENAMES) & set(cols)
    assert set(db.GATE_COLUMN_RENAMES.values()) <= set(cols)
    assert old_db.execute("SELECT ticker, l6_review_pass, decision_reason FROM live_gate_history").fetchone() == \
        ("OXY", 1, "approved")


def test_rename_is_idempotent(old_db):
    db._rename_gate_columns(old_db)
    assert db._rename_gate_columns(old_db) == []


def test_old_and_new_side_by_side_raises(old_db):
    old_db.execute("ALTER TABLE demo_gate_history ADD COLUMN l6_review_pass INTEGER")
    with pytest.raises(RuntimeError, match="both lock3_pass and l6_review_pass"):
        db._rename_gate_columns(old_db)


def test_init_db_on_an_old_database_adds_no_second_column(tmp_path, monkeypatch):
    """The production shape: today's schema with the gate columns under their old names (built by reversing the
    rename on a fresh schema), one row in it, then init_db as the next start runs it."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "prod_shape.db")   # a temp file, never production (conftest)
    db.init_db()
    conn = sqlite3.connect(tmp_path / "prod_shape.db")
    for table in db.GATE_COLUMN_TABLES:
        for old, new in db.GATE_COLUMN_RENAMES.items():
            if new in _cols(conn, table):
                conn.execute(f"ALTER TABLE {table} RENAME COLUMN {new} TO {old}")
    notnull = [r[1] for r in conn.execute("PRAGMA table_info(live_gate_history)") if r[3] and r[1] != "id"]
    fill = {c: "x" for c in notnull} | {"ticker": "OXY", "lock3_pass": 1, "lock3_reasoning": "approved"}
    conn.execute(f"INSERT INTO live_gate_history ({', '.join(fill)}) VALUES ({', '.join('?' * len(fill))})",
                 list(fill.values()))
    conn.commit()
    conn.close()
    db.init_db()
    conn = sqlite3.connect(tmp_path / "prod_shape.db")
    for table in db.GATE_COLUMN_TABLES:
        cols = _cols(conn, table)
        assert not set(db.GATE_COLUMN_RENAMES) & set(cols), f"{table} kept an old name"
        assert len(cols) == len(set(cols))
    assert conn.execute("SELECT ticker, l6_review_pass, decision_reason FROM live_gate_history").fetchone() == \
        ("OXY", 1, "approved")
    conn.close()


def test_fresh_database_has_only_new_names():
    conn = sqlite3.connect(db.DB_PATH)   # conftest's temp DB, built by init_db()
    for table in db.GATE_COLUMN_TABLES:
        assert not set(db.GATE_COLUMN_RENAMES) & set(_cols(conn, table))
    conn.close()
