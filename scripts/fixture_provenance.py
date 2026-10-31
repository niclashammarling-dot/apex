"""
Fixture provenance check (2026-10-31): every test row copied from production is compared field by field,
read-only, against the production row it names.

Found 2026-10-29: the TMO fixture in test_history_classify.py copied production row 13309's identity with
invented pass flags (1, None, 0, None against 0, 0, 0, 0) and passed on two days. The code was right; the
test guarded a shape that never occurs. Niclas: "Även om koden var rätt, så kan fel flaggor orsaka andra
fel" — a fixture with production's identity and invented state can also pass a wrong reader. Ruled the same
day: production-derived fixture rows carry their id and are compared before the migration.

A test module opts in by exposing PRODUCTION_ROWS = [(table, row), ...]; each row carries its production
"id". Rules per field:
  - equal values match; floats match within 1e-9;
  - a fixture string that is a prefix of production's matches as "cut" (long texts are cut on purpose);
  - a fixture JSON object matches when every key it names matches production's parsed object (recursively);
  - anything else, a missing column or a missing row is a MISMATCH.
A row whose id is in IGNORED_IDS for its table is skipped with its reason (a synthetic row with an id).

CLI (read-only): python -m scripts.fixture_provenance [--db PATH]
Exit 0 when every row matches, 1 on any MISMATCH, 2 when no PRODUCTION_ROWS module was found (a check that
finds nothing to compare has not run).
"""
from __future__ import annotations

import importlib
import json
import re
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO / "data" / "apex.db"
TESTS = REPO / "tests"


def _modules() -> list[str]:
    return sorted(f"tests.{p.stem}" for p in TESTS.glob("test_*.py")
                  if re.search(r"^PRODUCTION_ROWS\s*=", p.read_text(encoding="utf-8"), re.M))


def _json(v):
    if isinstance(v, str) and v[:1] in "{[":
        try:
            return json.loads(v)
        except ValueError:
            return None
    return None


def compare_value(fix, prod) -> str | None:
    """None when the fixture value matches production's, else the mismatch text. 'cut' counts as a match."""
    if fix == prod:
        return None
    if isinstance(fix, float) and isinstance(prod, (int, float)) and abs(fix - prod) < 1e-9:
        return None
    if isinstance(fix, str) and isinstance(prod, str):
        jf, jp = _json(fix), _json(prod)
        if isinstance(jf, dict) and isinstance(jp, dict):
            return _compare_obj(jf, jp)
        if fix and prod.startswith(fix):
            return None
    return f"fixture {fix!r} != production {prod!r}"


def _compare_obj(fix: dict, prod: dict) -> str | None:
    for k, v in fix.items():
        if k not in prod:
            return f"key {k!r} not in production"
        if isinstance(v, dict) and isinstance(prod[k], dict):
            m = _compare_obj(v, prod[k])
        else:
            m = compare_value(v, prod[k])
        if m:
            return f"{k}: {m}"
    return None


def check_rows(conn: sqlite3.Connection, rows: list[tuple[str, dict]]) -> list[str]:
    """One line per row: 'OK table id' or 'MISMATCH table id field: why'."""
    conn.row_factory = sqlite3.Row
    out = []
    for table, row in rows:
        rid = row.get("id")
        r = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (rid,)).fetchone()
        if r is None:
            out.append(f"MISMATCH {table} {rid}: no such row in production")
            continue
        prod = dict(r)
        bad = []
        for field, v in row.items():
            if field not in prod:
                bad.append(f"{field}: no such column")
                continue
            m = compare_value(v, prod[field])
            if m:
                bad.append(f"{field}: {m}")
        out.append(f"MISMATCH {table} {rid} " + "; ".join(bad) if bad else f"OK {table} {rid}")
    return out


def main(argv: list[str]) -> int:
    db = Path(argv[argv.index("--db") + 1]) if "--db" in argv else DEFAULT_DB
    mods = _modules()
    if not mods:
        print("FIXTURE-PROVENANCE: no test module exposes PRODUCTION_ROWS — nothing compared (BROKEN)")
        return 2
    sys.path.insert(0, str(REPO))
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    total, bad = 0, 0
    for name in mods:
        mod = importlib.import_module(name)
        ignored = getattr(mod, "IGNORED_IDS", {})
        rows = []
        for table, row in mod.PRODUCTION_ROWS:
            why = ignored.get((table, row.get("id")))
            if why:
                print(f"SKIP {name} {table} {row.get('id')}: {why}")
            else:
                rows.append((table, row))
        for line in check_rows(conn, rows):
            total += 1
            bad += line.startswith("MISMATCH")
            print(f"{name} {line}")
    print(f"FIXTURE-PROVENANCE: {total} rows, {bad} mismatch(es), db {db}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
