"""
Lock labels phase 2: re-code the gate history (backend/gate/history_migrate.py), dry run first.

    python scripts/migrate_gate_codes.py --dry-run [--db data/apex.db] [--out DIR]
        Copies the database with SQLite's backup API into DIR, migrates the copy, and checks it:
          1. counts per (table, old code, stop_code, basis) against the scope note's table (probed 10-26),
             on the rows that existed then; every row since must be a class the table already has;
          2. every skip row re-coded to its L1 rule (eighth round; a cooloff skip to gate or exit cooloff,
             2026-10-29), one for one against the original's codes;
          3. every column except the codes and the pass flags byte-identical to the original (scope step 3,
             first identity check); a pass flag either kept or set NULL, and never NULL where it was 1 on a
             row that ran a lock (ruling D; skips wrote lock1_pass 1 without running L3);
          4. reverse() on the migrated copy gives the original codes and flags back on every row;
          5. cooloff: the rows the new query selects (stop_code in lock_codes.COOLOFF_STOPS) are the row ids the
             old one selected on the original (gate_decision in FILTERED_L2, FILTERED_L3; ruling 6), per table;
          6. T7 (rejected_candidates_2026_10.gate_rows) reads the same (ticker, timestamp, code) from the migrated
             copy as from the original, so its archived result reproduces.
        Writes DIR/dry_run_<stamp>.json. The original is opened read-only.
    python scripts/migrate_gate_codes.py --db PATH
        Migrates PATH in place. Refuses data/apex.db unless --production is also given (scope step 5:
        a weekend with nothing on :8000, snapshot first).

Exit 1 when any check fails: the scope's rule is that an unexplained mismatch stops the run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.gate.history_classify import SKIPS, TABLES   # noqa: E402
from backend.gate import lock_codes                       # noqa: E402
from backend.gate.history_migrate import migrate, reverse  # noqa: E402
from backend.gate.lock_codes import sql_in                 # noqa: E402
from backend.backtest.rejected_candidates_2026_10 import gate_rows   # noqa: E402

# The cooloff set db.py selected before phase 2 (ruling 6, 2026-10-24), the original's side of check 5.
OLD_COOLOFF = ("FILTERED_L2", "FILTERED_L3")

PRODUCTION = (ROOT / "data" / "apex.db").resolve()

# The scope note's table (vault raw/notes/2026-10/2026-10-26-apex-lock-labels-phase2-scope.md § What each
# stored code holds), live / demo_gate_history / signals, read 2026-10-26 16:45–17:30 Stockholm. Skips are not
# lock history and were not counted there. Keys: (old code, stop_code, basis kind).
SCOPE_READ_UTC = ("2026-10-26T15:45:00", "2026-10-26T16:30:00")   # 16:45–17:30 CET
SCOPE_COUNTS: dict[tuple[str, str, str], tuple[int, int, int]] = {
    ("FILTERED_ELIGIBILITY", "FILTERED_L1_ELIGIBILITY_REGIME", "field"): (10214, 9964, 9552),
    ("FILTERED_ELIGIBILITY", "FILTERED_L1_ELIGIBILITY_VIX", "field"): (16, 8, 8),
    ("FILTERED_ELIGIBILITY", "FILTERED_L1_ELIGIBILITY_MACRO_EVENT", "field"): (1271, 989, 988),
    ("FILTERED_L1", "FILTERED_L1_ELIGIBILITY_MACRO_PRE_EVENT", "field"): (72, 50, 50),
    ("FILTERED_L1", "FILTERED_L1_ELIGIBILITY_MACRO_PRE_EVENT", "inferred"): (27, 27, 27),
    ("FILTERED_L1", "FILTERED_L3_SCORE_THRESHOLD", "field"): (81, 104, 90),
    ("FILTERED_L1", "REMOVE_TEST_ROW", "field"): (0, 1, 0),
    ("FILTERED_L2", "FILTERED_L4_SENTIMENT_GROK", "field"): (130, 175, 175),
    ("FILTERED_L2", "FILTERED_L4_SENTIMENT_UNAVAILABLE", "field"): (0, 3, 1),
    ("FILTERED_L2", "FILTERED_L4_SENTIMENT_UNAVAILABLE_INFERRED", "inferred"): (3, 3, 3),
    ("FILTERED_LEADING", "FILTERED_L5_POSITIONING_LEADING", "field"): (1838, 1621, 1616),
    ("FILTERED_LEADING", "FILTERED_L2_RISK_ETF", "field"): (17, 0, 0),
    ("FILTERED_ETF_PENALTY", "FILTERED_L2_RISK_ETF", "field"): (24, 0, 0),
    ("FILTERED_L3", "FILTERED_L6_REVIEW_CLAUDE", "field"): (101, 57, 57),
    ("FILTERED_L3", "FILTERED_L6_REVIEW_UNAVAILABLE", "field"): (4, 3, 3),
    ("FILTERED_L3", "FILTERED_L6_REVIEW_UNAVAILABLE", "inferred"): (2, 0, 0),
    ("FILTERED_L3", "FILTERED_L6_REVIEW_UNAVAILABLE_INFERRED", "inferred"): (13, 21, 21),
    ("FILTERED_L3", "FILTERED_L0_PORTFOLIO_DRAWDOWN_GUARD", "field"): (0, 21, 21),
    ("FILTERED_MACRO", "FILTERED_L1_ELIGIBILITY_MACRO_EVENT", "field"): (27, 27, 27),
    ("FILTERED_OVERFLOW_QUANT", "FILTERED_L2_RISK_OVERFLOW", "field"): (29, 32, 32),
    ("TRADE_REJECTED", "FILTERED_UNKNOWN", "unattributable"): (13, 3, 3),
    ("TRADE_EXECUTED", "TRADE_EXECUTED", "field"): (97, 92, 91),
    ("TRADE_FAILED", "FILTERED_L1_ELIGIBILITY_NOTIONAL_TOO_SMALL", "field"): (11, 0, 0),
}
# Classes the scope's table counted as one, split by a later ruling: the class → the scope row it is part of.
# L5's data errors (ninth round, 2026-10-28) were read as positioning stops on 10-26.
SCOPE_FOLD = {("FILTERED_LEADING", "FILTERED_L5_POSITIONING_UNAVAILABLE", "field"):
              ("FILTERED_LEADING", "FILTERED_L5_POSITIONING_LEADING", "field")}
# Skips were not counted in the scope's table (not lock history then); check 2 counts them one for one.
NOT_COUNTED = tuple(SKIPS)
FLAGS = tuple(lock_codes.FLAG_COLUMNS.values())


def _row_hashes(conn: sqlite3.Connection, table: str, skip: tuple[str, ...]) -> dict[int, str]:
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})") if r[1] not in skip]
    return {r[0]: hashlib.sha256(json.dumps(r[1:], default=str).encode()).hexdigest()
            for r in conn.execute(f"SELECT id, {', '.join(cols)} FROM {table} ORDER BY id")}


def _flags(conn: sqlite3.Connection, table: str) -> dict[int, tuple[tuple[str, ...], tuple]]:
    cols = tuple(c for c in FLAGS if c in [r[1] for r in conn.execute(f"PRAGMA table_info({table})")])
    return {r[0]: (cols, tuple(r[1:])) for r in conn.execute(f"SELECT id, {', '.join(cols)} FROM {table}")}


def _codes(conn: sqlite3.Connection, table: str) -> dict[int, str | None]:
    return dict(conn.execute(f"SELECT id, gate_decision FROM {table}"))


def _class_counts(conn: sqlite3.Connection, before: str | None) -> dict:
    """Counts per (table, old code, stop_code, basis kind) from the record, optionally only rows older than `before`."""
    out: dict = defaultdict(Counter)
    for table in TABLES:
        ts = dict(conn.execute(f"SELECT id, timestamp FROM {table}"))
        for row_id, old, stop, basis, deleted in conn.execute(
                "SELECT row_id, old_code, stop_code, basis, deleted_row FROM gate_code_migration WHERE tbl = ?", (table,)):
            t = ts.get(row_id) or (json.loads(deleted)["timestamp"] if deleted else None)
            if before is None or (t or "") < before:
                key = (old, stop, basis.split(":")[0])
                out[table][SCOPE_FOLD.get(key, key)] += 1
    return out


def dry_run(src: Path, out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    copy = out_dir / f"apex_dry_run_{stamp}.db"
    with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as orig, sqlite3.connect(copy) as dst:
        orig.backup(dst)
    findings: list[str] = []
    report: dict = {"source": str(src), "copy": str(copy), "ran_at_utc": stamp, "scope_read_utc": SCOPE_READ_UTC}

    conn = sqlite3.connect(copy)
    unchanged = ("gate_decision", "stop_code", "rule_states", *FLAGS)
    before = {t: (_row_hashes(conn, t, unchanged), _codes(conn, t), _flags(conn, t)) for t in TABLES}
    old_cooloff = {t: {r[0] for r in conn.execute(f"SELECT id FROM {t} WHERE gate_decision IN ({sql_in(OLD_COOLOFF)})")}
                   for t in TABLES}
    t7_before = sorted(gate_rows(conn, "2000-01-01", "2100-01-01"))
    migrate(conn)
    conn.row_factory = None
    # 5. Cooloff selects the same rows (ruling 6), and 6. T7 reads the same codes, on the migrated copy.
    for t in TABLES:
        new = {r[0] for r in conn.execute(f"SELECT id FROM {t} WHERE stop_code IN ({sql_in(lock_codes.COOLOFF_STOPS)})")}
        report.setdefault("cooloff_ids", {})[t] = {"old": len(old_cooloff[t]), "new": len(new),
                                                   "only_old": sorted(old_cooloff[t] - new)[:20],
                                                   "only_new": sorted(new - old_cooloff[t])[:20]}
        if new != old_cooloff[t]:
            findings.append(f"{t}: cooloff selects {len(new)} rows, the old query {len(old_cooloff[t])}")
    t7_after = sorted(gate_rows(conn, "2000-01-01", "2100-01-01"))
    report["t7_rows"] = {"before": len(t7_before), "after": len(t7_after), "identical": t7_before == t7_after}
    if t7_before != t7_after:
        findings.append(f"T7 gate rows differ after the migration ({len(t7_before)} before, {len(t7_after)} after)")

    # 1. Counts against the scope table: exactly, on rows older than the read's start; between the read's
    #    start and end a live session was writing, so a class may hold up to its later count there.
    lo, hi = (_class_counts(conn, SCOPE_READ_UTC[0]), _class_counts(conn, SCOPE_READ_UTC[1]))
    total = _class_counts(conn, None)
    report["counts"] = {}
    for i, table in enumerate(TABLES):
        rows = []
        for key in sorted(set(total[table]) | {k for k, v in SCOPE_COUNTS.items() if v[i]}):
            if key[0] in NOT_COUNTED:
                continue
            want = SCOPE_COUNTS.get(key, (None, None, None))[i]
            got_lo, got_hi, got = lo[table][key], hi[table][key], total[table][key]
            ok = want is not None and got_lo <= want <= got_hi
            rows.append({"old": key[0], "stop_code": key[1], "basis": key[2], "scope": want,
                         "before_read": got_lo, "by_read_end": got_hi, "now": got, "ok": ok})
            if not ok:
                findings.append(f"{table} {key}: scope {want}, rows before the read {got_lo}, by its end {got_hi}")
        report["counts"][table] = rows

    # 2. Skips: each SKIPPED_* row is now its L1 rule, and nothing else is.
    for t in TABLES:
        _, c0, _ = before[t]
        got = Counter(r[0] for r in conn.execute(f"SELECT stop_code FROM {t} WHERE stop_code IS NOT NULL"))
        for old, rule in SKIPS.items():
            want = sum(1 for c in c0.values() if c == old)
            # SKIPPED_COOLOFF splits into gate and exit cooloff (Niclas 2026-10-29); together they are one for one.
            rules = ("cooloff", "exit_cooloff") if old == "SKIPPED_COOLOFF" else (rule,)
            entry = {"original": want}
            n = 0
            for r in rules:
                stop = lock_codes.stop_code(1, r)
                extra = conn.execute(f"SELECT COUNT(*) FROM gate_code_migration WHERE tbl = ? AND stop_code = ? "
                                     f"AND old_code != ?", (t, stop, old)).fetchone()[0]
                entry[stop] = got[stop] - extra
                n += got[stop] - extra
            report.setdefault("skips", {}).setdefault(t, {})[old] = entry
            if n != want:
                findings.append(f"{t} {old}: {want} in the original, {n} re-coded to {', '.join(rules)}")

    # 3. Identity: every column except the codes and flags unchanged, same row ids (less the deleted test row);
    #    a flag kept or NULL, never NULL where it was 1 on a row that ran a lock (ruling D).
    for t in TABLES:
        h0, c0, f0 = before[t]
        f1 = _flags(conn, t)
        nulled, wrong = Counter(), []
        for i, new in f1.items():
            for col, a, b in zip(f0[i][0], f0[i][1], new[1]):
                if b is None and a is not None:
                    nulled[col] += 1
                    if a == 1 and c0[i] not in SKIPS:
                        wrong.append((i, col))
                elif a != b:
                    wrong.append((i, col))
        report.setdefault("flags", {})[t] = {"set_null": dict(nulled), "violations": wrong[:20], "n_violations": len(wrong)}
        if wrong:
            findings.append(f"{t}: {len(wrong)} pass flags changed other than to NULL, or NULLed where 1 (first {wrong[:5]})")
        h1 = _row_hashes(conn, t, unchanged)
        gone = set(h0) - set(h1)
        changed = [i for i in h1 if h0.get(i) != h1[i]]
        report.setdefault("identity", {})[t] = {"rows_before": len(h0), "rows_after": len(h1),
                                               "deleted": sorted(gone), "changed_non_code_rows": len(changed)}
        if changed or set(h1) - set(h0) or gone - ({1276} if t == "demo_gate_history" else set()):
            findings.append(f"{t}: identity broken (changed {len(changed)}, deleted {sorted(gone)})")

    # 4. Recovery: reverse() gives back the original code and flags on every row, and the deleted row.
    reverse(conn)
    for t in TABLES:
        h0, c0, f0 = before[t]
        if _codes(conn, t) != c0 or _flags(conn, t) != f0 or _row_hashes(conn, t, unchanged) != h0:
            findings.append(f"{t}: reverse() did not restore the original")
    report["reverse_restores_original"] = not any("reverse()" in f for f in findings)
    conn.close()

    report["findings"] = findings
    path = out_dir / f"dry_run_{stamp}.json"
    path.write_text(json.dumps(report, indent=1, default=str))
    print(f"dry run: {path}")
    for t, rows in report["counts"].items():
        print(f"== {t}")
        for r in rows:
            print(f"  {'ok ' if r['ok'] else 'BAD'} {r['old']:<24} {r['stop_code']:<44} {r['basis']:<15} "
                  f"scope {r['scope']!s:>6}  before-read {r['before_read']:>6}  read-end {r['by_read_end']:>6}  now {r['now']:>6}")
    for t, v in report["skips"].items():
        print(f"skips {t}: {v}")
    for t, v in report["flags"].items():
        print(f"flags {t}: set NULL {v['set_null']}, violations {v['n_violations']}")
    for t, v in report["identity"].items():
        print(f"identity {t}: {v}")
    print(f"reverse restores original: {report['reverse_restores_original']}")
    print("findings:", *(findings or ["none"]), sep="\n  ")
    return 1 if findings else 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(PRODUCTION))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "data" / "backups"))
    ap.add_argument("--production", action="store_true")
    a = ap.parse_args(argv)
    db = Path(a.db).resolve()
    if a.dry_run:
        return dry_run(db, Path(a.out))
    if db == PRODUCTION and not a.production:
        print(f"refusing to migrate {db} in place without --production (scope step 5)", file=sys.stderr)
        return 2
    with sqlite3.connect(db) as conn:
        counts = migrate(conn)
    print(json.dumps({"|".join(k): v for k, v in sorted(counts.items())}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
