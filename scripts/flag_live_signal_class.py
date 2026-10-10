#!/usr/bin/env python3
"""
Flag every live entry with the class of signal it was decided on (Finding 1,
ruled 2026-10-07: "flag all 25 rows with their class"; pre-07-07 rows too, so
any all-history stat can exclude them).

Classes (scripts/finding1_snapshot_test.classify): in_session,
same_day_pre_open, earlier_day, no_signal. The signal is the newest signals
row for the ticker written at or before the entry. The 10-08 prune removed
un-gated history, so classification reads a DB that still holds it (--source,
default the pre-prune backup copy); entries newer than the source are
classified from --target itself.

Dry run by default: prints the classes and what would change. --apply writes
live_trades.signal_class in --target (only rows whose value differs).
Usage:
  venv/bin/python scripts/flag_live_signal_class.py --source <pre-prune copy> --target <db> [--apply]
"""
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from finding1_snapshot_test import classify


def main(argv: list[str]) -> int:
    if "--source" not in argv or "--target" not in argv:
        print(__doc__)
        return 2
    source = argv[argv.index("--source") + 1]
    target = argv[argv.index("--target") + 1]
    apply = "--apply" in argv

    src = sqlite3.connect(f"file:{source}?immutable=1", uri=True)
    src.row_factory = sqlite3.Row
    rows = {r["id"]: r for r in classify(src, "0000")}
    src_max = src.execute("SELECT MAX(timestamp) FROM live_trades").fetchone()[0]
    src.close()

    tgt_ro = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    tgt_ro.row_factory = sqlite3.Row
    newer = [r for r in classify(tgt_ro, "0000") if r["timestamp"] > src_max]
    current = {r["id"]: (r["ticker"], r["timestamp"], r["signal_class"]) for r in tgt_ro.execute(
        "SELECT id, ticker, timestamp, signal_class FROM live_trades").fetchall()}
    tgt_ro.close()
    for r in newer:
        rows[r["id"]] = r

    mismatched = [i for i in rows if i in current and current[i][0] != rows[i]["ticker"]]
    if mismatched:
        print(f"refusing: ids {mismatched[:10]} name different tickers in source and target")
        return 1
    missing = sorted(set(current) - set(rows))
    changes = [(rows[i]["class"], i) for i in rows if i in current and current[i][2] != rows[i]["class"]]
    print(f"classified {len(rows)} of {len(current)} live_trades rows "
          f"({len(newer)} from the target, newer than the source's {src_max[:19]})")
    print("  " + ", ".join(f"{k} {v}" for k, v in sorted(Counter(r['class'] for r in rows.values()).items())))
    if missing:
        print(f"  unclassified ids: {missing}")
    print(f"  {len(changes)} row(s) would change")
    if not apply:
        print("dry run — pass --apply to write")
        return 0
    conn = sqlite3.connect(target, timeout=30)
    with conn:
        conn.executemany("UPDATE live_trades SET signal_class = ? WHERE id = ?", changes)
    conn.close()
    print(f"wrote {len(changes)} row(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
