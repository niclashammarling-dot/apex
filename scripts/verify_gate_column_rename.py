"""Verify the lock labels phase 3 column rename on a database: before (snapshot) against after (migrated).

Vault: raw/notes/2026-10/2026-10-31-apex-lock-labels-phase3-plan.md, part a step 3 ("on a snapshot of production:
dry run on a copy, then one transaction of RENAME COLUMN"). The rename itself is backend.db._rename_gate_columns,
run by init_db; this script runs init_db on a copy and proves, table by table:
  - no old name is left and every new name is present (in the tables that had the old one);
  - every table keeps its row count;
  - every renamed column holds, row for row by id, exactly what the old column held (NULLs included);
  - every column that was not renamed is unchanged row for row (the same comparison over the shared names);
  - a second init_db renames nothing.
Known-present control: the snapshot itself still has the old names (otherwise the comparison is vacuous).

Usage: .venv/bin/python -u scripts/verify_gate_column_rename.py SNAPSHOT.db WORKDIR OUT.json
  SNAPSHOT.db is read only; the copy is made in WORKDIR and migrated there.
"""
import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import backend.db as db  # noqa: E402

WHY = "raw/notes/2026-10/2026-10-31-apex-lock-labels-phase3-plan.md"


def _cols(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def main(snapshot: Path, workdir: Path, out: Path) -> int:
    workdir.mkdir(parents=True, exist_ok=True)
    copy = workdir / "migrated.db"
    shutil.copy(snapshot, copy)
    misses = []

    snap = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
    before = {t: _cols(snap, t) for t in db.GATE_COLUMN_TABLES}
    if not any(old in before[t] for t in before for old in db.GATE_COLUMN_RENAMES):
        misses.append("control: the snapshot has none of the old names")
    tables = [r[0] for r in snap.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    counts_before = {t: snap.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}
    snap.close()

    db.DB_PATH = copy
    db.init_db()
    conn = sqlite3.connect(copy)
    renamed = {}
    for t in db.GATE_COLUMN_TABLES:
        after = _cols(conn, t)
        left = [c for c in db.GATE_COLUMN_RENAMES if c in after]
        if left:
            misses.append(f"{t}: old names left {left}")
        pairs = {o: n for o, n in db.GATE_COLUMN_RENAMES.items() if o in before[t]}
        missing = [n for n in pairs.values() if n not in after]
        if missing:
            misses.append(f"{t}: new names missing {missing}")
        renamed[t] = pairs
    counts_after = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}
    changed = {t: (counts_before[t], counts_after.get(t)) for t in tables if counts_before[t] != counts_after.get(t)}
    if changed:
        misses.append(f"row counts changed: {changed}")

    conn.execute("ATTACH DATABASE ? AS snap", (f"file:{snapshot}?mode=ro",))
    value_diffs = {}
    for t, pairs in renamed.items():
        shared = [c for c in before[t] if c not in db.GATE_COLUMN_RENAMES and c != "id"]
        cond = [f"s.{o} IS NOT m.{n}" for o, n in pairs.items()] + [f"s.{c} IS NOT m.{c}" for c in shared]
        if not cond:
            continue
        n_bad = conn.execute(f"SELECT count(*) FROM snap.{t} s JOIN main.{t} m ON s.id = m.id "
                             f"WHERE {' OR '.join(cond)}").fetchone()[0]
        n_join = conn.execute(f"SELECT count(*) FROM snap.{t} s JOIN main.{t} m ON s.id = m.id").fetchone()[0]
        value_diffs[t] = {"rows_compared": n_join, "rows_differing": n_bad,
                          "renamed": len(pairs), "unchanged_columns": len(shared)}
        if n_bad or n_join != counts_before[t]:
            misses.append(f"{t}: {n_bad} rows differ, {n_join} of {counts_before[t]} joined by id")
    conn.close()

    second = db._rename_gate_columns(sqlite3.connect(copy))
    if second:
        misses.append(f"second run renamed {second}")

    commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    result = {
        "producer": {"repo": "apex", "commit": commit, "script": "scripts/verify_gate_column_rename.py",
                     "run": f".venv/bin/python -u scripts/verify_gate_column_rename.py {snapshot} {workdir} {out}",
                     "why": WHY, "run_at": datetime.now(timezone.utc).isoformat(),
                     "db": f"{snapshot} (read only); copy migrated in {workdir}"},
        "renamed": renamed, "row_counts": counts_before, "values": value_diffs, "controls": {"missed": misses},
    }
    out.write_text(json.dumps(result, indent=1))
    print(json.dumps({"values": value_diffs, "controls": result["controls"]}, indent=1))
    return 2 if misses else 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])))
