"""
Every field the two trade-log cards read is a column their endpoint returns (vault scope
raw/notes/2026-11/2026-11-02-apex-saturday-scopes-11-07.md § 10, Niclas 2026-11-05: "Jag saknar exit date på
varje. Jag hade önskat att alla sålda är sorterade efter exit date").

The tooltip's Exit read `t.timestamp_exit` from the 2026-05-12 redesign (`ace44a5`) on; no backend code writes that
name, the column is `exited_at`, so every closed trade showed "—" for six months and "—" is also the legitimate
missing value. The live card's P&L fallback read `t.pnl_pct` the same way. Both endpoints return `SELECT *`
(`/api/live/trades` → db.get_all_live_trades, `/api/demo/trades` → db.get_all_trades), so a field outside the
table's columns is a reader with no writer. There is no JS test runner in frontend/package.json, so the test reads
the source, as tests/test_legacy_code_readers.py does.
"""
from __future__ import annotations

import inspect
import re
import sqlite3
from pathlib import Path

import pytest

import backend.db as db

ROOT = Path(__file__).resolve().parents[1]
CARDS = {  # card → (the db function its endpoint returns, the table that function selects from)
    "frontend/src/components/LiveTradeLog.jsx": (db.get_all_live_trades, "live_trades"),
    "frontend/src/components/DemoTradeLog.jsx": (db.get_all_trades, "trades"),
}
# `t` is the row in TradeRow; `a`/`b` are rows in the closed-row comparator.
_FIELD = re.compile(r"\b[tab]\.([a-z_][a-z0-9_]*)")


def _columns(table: str) -> set[str]:
    conn = sqlite3.connect(db.DB_PATH)  # conftest's temp DB, schema from init_db()
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


@pytest.mark.parametrize("card", sorted(CARDS))
def test_every_field_read_is_a_column_of_the_endpoint_table(card):
    fn, table = CARDS[card]
    assert f"SELECT * FROM {table}" in inspect.getsource(fn), f"{fn.__name__} no longer returns every {table} column"
    read = set(_FIELD.findall((ROOT / card).read_text()))
    assert read, f"no row field found in {card}: the pattern no longer matches the component"
    missing = read - _columns(table)
    assert not missing, f"{card} reads {sorted(missing)}, not columns of {table}: a reader with no writer"


@pytest.mark.parametrize("card", sorted(CARDS))
def test_closed_rows_sort_by_exit_then_entry(card):
    src = (ROOT / card).read_text()
    assert re.search(r"const byExitDesc = \(a, b\) =>\s*\(b\.exited_at \?\? \"\"\)\.localeCompare\(a\.exited_at",
                     src), f"{card}: the closed-row comparator does not compare exited_at first"
    assert re.search(r"\(b\.timestamp \?\? \"\"\)\.localeCompare\(a\.timestamp", src), \
        f"{card}: no entry-time tie-break"
    assert re.search(r"closedTrades\s*=.*?\.sort\(byExitDesc\)", src, re.S), f"{card}: closed rows are not sorted by it"


@pytest.mark.parametrize("card", sorted(CARDS))
def test_closed_row_shows_entry_and_exit_date(card):
    src = (ROOT / card).read_text()
    assert "{fmtDate(t.timestamp)}{!isOpen && t.exited_at && ` → ${fmtDate(t.exited_at)}`}" in src
