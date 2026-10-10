#!/usr/bin/env python3
"""
Finding 1 snapshot test (adopted 2026-10-09): replay the enforced half of the
session bound — signal written before the entry session's open — on every
live entry since the 07-07 reset, against a DB that still holds the signals
those entries were decided on (data/backups/apex_2026-10-08_pre-prune.db; the
10-08 prune removed un-gated history).

Expected (2026-10-07 measurement): 25 of 55 refused (15 same-day pre-open,
10 earlier-day), 30 passed. The bar-date half cannot be replayed: old rows
carry no bar_date.

Read-only: opens the DB with immutable=1. Pass a copy, not the production DB.
Usage: venv/bin/python scripts/finding1_snapshot_test.py <path-to-db-copy> [--since 2026-07-07]
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.gate.freshness import signal_class


def classify(conn: sqlite3.Connection, since: str) -> list[dict]:
    out = []
    for t in conn.execute("SELECT id, ticker, timestamp FROM live_trades WHERE timestamp >= ? "
                          "ORDER BY timestamp", (since,)).fetchall():
        sig = conn.execute("SELECT timestamp FROM signals WHERE ticker = ? AND timestamp <= ? "
                           "ORDER BY timestamp DESC LIMIT 1", (t["ticker"], t["timestamp"])).fetchone()
        if sig is None:
            out.append({**dict(t), "signal_ts": None, "class": "no_signal", "refused": True})
            continue
        cls = signal_class(sig["timestamp"], t["timestamp"])
        out.append({**dict(t), "signal_ts": sig["timestamp"], "class": cls, "refused": cls != "in_session"})
    return out


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    path = Path(argv[0]).resolve()
    if path == (Path(__file__).resolve().parent.parent / "data" / "apex.db").resolve():
        print("refusing the production DB — pass a copy")
        return 2
    since = argv[argv.index("--since") + 1] if "--since" in argv else "2026-07-07"
    conn = sqlite3.connect(f"file:{path}?immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    rows = classify(conn, since)
    conn.close()
    from collections import Counter
    c = Counter(r["class"] for r in rows)
    refused = sum(r["refused"] for r in rows)
    print(f"live entries since {since}: {len(rows)}; refused {refused}, passed {len(rows) - refused}")
    for k in ("in_session", "same_day_pre_open", "earlier_day", "no_signal"):
        print(f"  {k}: {c.get(k, 0)}")
    for r in rows:
        if r["refused"]:
            print(f"  REFUSED id={r['id']} {r['ticker']:5} entry {r['timestamp'][:19]} "
                  f"signal {str(r['signal_ts'])[:19]} {r['class']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
