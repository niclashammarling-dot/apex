"""
One prune_signals pass, by hand, with the backend down (2026-10-07).

prune_signals had not run since 2026-09-16 (its 02:00 ET slot sat outside the
market window), so the first pass deletes ~30k un-gated signals rows in one
write transaction (27 s on a DB copy). Run it in the morning, after the
snapshot, before the 14:20 CEST launch — not as the startup catch-up, where it
would hold the write lock while the launch's other writers start. It stamps
job_runs like the scheduled job, so the launch's _check_missed_prune sees a
recent prune and the catch-up afterwards only removes about a day's rows.

    venv/bin/python scripts/prune_signals_once.py --run

Refuses without --run (no other flags), and while anything listens on :8000.
"""
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main(argv: list[str]) -> int:
    if argv != ["--run"]:
        print(__doc__.strip())
        return 2
    with socket.socket() as s:
        s.settimeout(1)
        if s.connect_ex(("127.0.0.1", 8000)) == 0:
            print("refusing: something is listening on :8000 (a serving backend) — stop it first")
            return 1

    from backend.db import get_db, prune_signals, stamp_job_run

    def counts():
        conn = get_db()
        try:
            return conn.execute("SELECT COUNT(*), SUM(gate_decision IS NULL) FROM signals").fetchone()
        finally:
            conn.close()

    before = counts()
    print(f"signals before: {before[0]} rows, {before[1]} un-gated", flush=True)
    stamp_job_run("prune_signals", "start")
    try:
        deleted = prune_signals(keep_per_ticker=10)
    except Exception as e:
        stamp_job_run("prune_signals", "end", f"error: {e!r}"[:300])
        raise
    stamp_job_run("prune_signals", "end", "ok")
    after = counts()
    print(f"deleted {deleted}; signals after: {after[0]} rows, {after[1]} un-gated", flush=True)
    if before[0] - after[0] != deleted or before[0] - before[1] != after[0] - after[1]:
        print("MISMATCH: deleted count or gated rows changed — inspect before launching")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
