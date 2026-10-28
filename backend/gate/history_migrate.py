"""
gate/history_migrate.py — apply the history classifier to the three gate tables, with a record (phase 2).

Scope: vault raw/notes/2026-10/2026-10-26-apex-lock-labels-phase2-scope.md § Method, steps 2 and 4. The
classifier (history_classify) decides each row's class; this module writes it:
  - gate_decision → the class's code, a new column `stop_code` → its stop_code (ruling B), on every row with
    a code (ungated `signals` rows stay NULL, P1);
  - the one test row that reached production (demo_gate_history 1276) is deleted, its full row kept in the
    record as JSON;
  - every touched row gets one row in `gate_code_migration`: old and new code, rule, basis (ruling F), the
    old pass flags (ruling D re-codes them later), and the time. That table is the recovery path
    (reverse()) and the bridge for archived producers that read old codes (T7).

One transaction per call. Pass flags (ruling D) and column renames are not written here: they change with
the readers that read them, in the build. Nothing here opens a connection: the caller passes one, and
scripts/migrate_gate_codes.py refuses the production database without --production.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone

from backend.gate.history_classify import TABLES, TEST_ROW_CODE, classify

RECORD = "gate_code_migration"
PASS_FLAGS = ("lock1_pass", "lock2_pass", "lock3_pass", "lock_leading_pass")

_RECORD_DDL = f"""
CREATE TABLE IF NOT EXISTS {RECORD} (
    tbl            TEXT    NOT NULL,
    row_id         INTEGER NOT NULL,
    old_code       TEXT    NOT NULL,
    new_code       TEXT    NOT NULL,
    stop_code      TEXT    NOT NULL,
    rule           TEXT,
    basis          TEXT    NOT NULL,
    old_pass_flags TEXT    NOT NULL,
    deleted_row    TEXT,
    migrated_at    TEXT    NOT NULL,
    PRIMARY KEY (tbl, row_id)
)"""


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def migrate(conn: sqlite3.Connection, migrated_at: str | None = None) -> Counter:
    """Re-code every coded row of the three tables. Returns counts per (table, old code, stop_code, basis kind).
    Raises (and rolls back) on the first row the classifier cannot class, or if a record already exists."""
    migrated_at = migrated_at or datetime.now(timezone.utc).isoformat()
    counts: Counter = Counter()
    conn.row_factory = sqlite3.Row
    with conn:
        if conn.execute(f"SELECT name FROM sqlite_master WHERE name = '{RECORD}'").fetchone():
            if conn.execute(f"SELECT COUNT(*) FROM {RECORD}").fetchone()[0]:
                raise RuntimeError(f"{RECORD} already holds rows: this database was migrated")
        conn.execute(_RECORD_DDL)
        for table in TABLES:
            if "stop_code" not in _columns(conn, table):
                conn.execute(f"ALTER TABLE {table} ADD COLUMN stop_code TEXT")
            flags = [f for f in PASS_FLAGS if f in _columns(conn, table)]
            rows = conn.execute(f"SELECT * FROM {table} WHERE gate_decision IS NOT NULL ORDER BY id").fetchall()
            for r in rows:
                row = dict(r)
                k = classify(table, row)
                deleted = None
                if k.code == TEST_ROW_CODE:
                    deleted = json.dumps(row, sort_keys=True, default=str)
                    conn.execute(f"DELETE FROM {table} WHERE id = ?", (row["id"],))
                else:
                    conn.execute(f"UPDATE {table} SET gate_decision = ?, stop_code = ? WHERE id = ?",
                                 (k.code, k.stop_code, row["id"]))
                conn.execute(
                    f"INSERT INTO {RECORD} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (table, row["id"], row["gate_decision"], k.code, k.stop_code, k.rule, k.basis,
                     json.dumps({f: row[f] for f in flags}, sort_keys=True), deleted, migrated_at))
                counts[(table, row["gate_decision"], k.stop_code, k.basis.split(":")[0])] += 1
    return counts


def reverse(conn: sqlite3.Connection) -> int:
    """Undo migrate() from the record: old codes back, stop_code NULL, the deleted row re-inserted, the
    record emptied. The added stop_code column stays (SQLite drops columns only by rebuild); it is NULL."""
    conn.row_factory = sqlite3.Row
    n = 0
    with conn:
        for rec in conn.execute(f"SELECT * FROM {RECORD}").fetchall():
            if rec["deleted_row"] is not None:
                row = json.loads(rec["deleted_row"])
                cols = [c for c in _columns(conn, rec["tbl"]) if c in row]
                conn.execute(f"INSERT INTO {rec['tbl']} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                             [row[c] for c in cols])
            else:
                conn.execute(f"UPDATE {rec['tbl']} SET gate_decision = ?, stop_code = NULL WHERE id = ?",
                             (rec["old_code"], rec["row_id"]))
            n += 1
        conn.execute(f"DELETE FROM {RECORD}")
    return n
