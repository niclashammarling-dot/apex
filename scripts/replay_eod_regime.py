"""
scripts/replay_eod_regime.py — Repair a contaminated stretch of sector_posterior_history.

Background (2026-09-16): before the as_of= fix in backend/scheduler.py, the startup
catch-up stamped the RESTART date instead of the missed date, and INSERT OR IGNORE
on sector_posterior_history then discarded the genuine 16:15 write for that date.
Two rows were confirmed wrong from the logs (2026-09-03, 2026-09-08), and the
posterior is sequential, so every row after the first bad one inherits it.

What this does, for a window [from, to]:
  1. Snapshot: the DB and regime_result_cache.json are copied to --workdir; unless
     --apply is given, ALL writes go to the copy (dry-run against a copy is the default).
  2. Reset state to the last clean session (the newest history date strictly before
     --from): sector_posteriors from that date's history rows, regime_result_cache.json
     (the hysteresis / leader state) rebuilt from that date's regime_signal_trace.jsonl rows.
  3. Delete history rows with date >= --from.
  4. Replay every NYSE session in [from, to] in order via run_eod_regime(as_of=d).
  5. Print old-vs-new per (date, sector), with the max and mean absolute posterior move —
     the "how much did the contamination matter" number.

The trace JSONL is redirected to <workdir>/regime_signal_trace.replay.jsonl in every
mode: the production trace keeps the contaminated rows as evidence and is append-only.

Usage:
  .venv/bin/python scripts/replay_eod_regime.py --from 2026-09-02 --to 2026-09-16              # dry run on a copy
  .venv/bin/python scripts/replay_eod_regime.py --from 2026-09-02 --to 2026-09-16 --apply      # write to data/apex.db
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from loguru import logger  # noqa: E402


def _rebuild_result_cache(trace_path: Path, clean_date: str, out_path: Path) -> dict:
    """Reconstruct regime_result_cache.json for clean_date from the signal trace rows of that date."""
    rows = [json.loads(l) for l in trace_path.read_text().splitlines() if l.strip()]
    day  = [r for r in rows if r.get("date") == clean_date]
    if not day:
        raise SystemExit(f"no trace rows for {clean_date} in {trace_path} — cannot rebuild hysteresis state")
    # The trace is append-only; if the day was written twice, take the last write per sector.
    by_sector: dict[str, dict] = {}
    for r in day:
        by_sector[r["sector"]] = r
    entries = sorted(by_sector.values(), key=lambda r: r["rank"])
    payload = {
        "date":       clean_date,
        "leader":     entries[0]["sector"],
        "qualifiers": [r["sector"] for r in entries if r["allocation"] > 0],
        "allocation": {r["sector"]: r["allocation"] for r in entries},
        "leaderboard": [
            {
                "sector":          r["sector"],
                "rank":            r["rank"],
                "aggregate_score": r["aggregate_score"],
                "posterior":       r["posterior"],
                "adjusted_score":  r["adjusted_score"],
                "allocation":      r["allocation"],
                "signal_trace":    {},
            }
            for r in entries
        ],
    }
    out_path.write_text(json.dumps(payload, indent=2))
    return payload


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", required=True)
    ap.add_argument("--to",   dest="date_to",   required=True)
    ap.add_argument("--apply", action="store_true", help="write to the production DB/cache instead of the copy")
    ap.add_argument("--workdir", default=str(REPO / "data" / "backups" / "replay"))
    args = ap.parse_args()

    d_from, d_to = date.fromisoformat(args.date_from), date.fromisoformat(args.date_to)
    workdir = Path(args.workdir); workdir.mkdir(parents=True, exist_ok=True)
    stamp   = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    import backend.db as db_module
    import backend.regime.regime_bayes as rb_module
    import backend.scheduler as sched

    # Hold the lock for the whole run: the live server's startup catch-up must not
    # see the deleted range and start filling it from its own (stale) in-memory prior.
    sched.EOD_REPLAY_LOCK.parent.mkdir(parents=True, exist_ok=True)
    sched.EOD_REPLAY_LOCK.touch()

    prod_db, prod_cache = db_module.DB_PATH, rb_module.RESULT_CACHE_PATH
    prod_trace          = rb_module.SIGNAL_TRACE_PATH

    # 1. snapshot — always, apply or not
    db_copy    = workdir / f"apex-{stamp}.db"
    cache_copy = workdir / f"regime_result_cache-{stamp}.json"
    shutil.copy2(prod_db, db_copy)
    shutil.copy2(prod_cache, cache_copy)
    logger.info(f"snapshot: {db_copy} / {cache_copy}")

    if args.apply:
        target_db, target_cache = prod_db, prod_cache
    else:
        target_db, target_cache = db_copy, cache_copy
        db_module.DB_PATH = target_db
        rb_module.RESULT_CACHE_PATH = target_cache
    rb_module.SIGNAL_TRACE_PATH = workdir / "regime_signal_trace.replay.jsonl"
    db_module.init_db()   # runs the written_at migration on whichever DB is the target

    conn = sqlite3.connect(target_db)
    conn.row_factory = sqlite3.Row

    # old series, for the diff
    old = {
        (r["date"], r["sector"]): r["posterior"]
        for r in conn.execute(
            "SELECT date, sector, posterior FROM sector_posterior_history WHERE date >= ? AND date <= ?",
            (args.date_from, args.date_to),
        )
    }

    # 2. reset to last clean session
    clean = conn.execute(
        "SELECT MAX(date) FROM sector_posterior_history WHERE date < ?", (args.date_from,)
    ).fetchone()[0]
    if not clean:
        raise SystemExit(f"no history row before {args.date_from} — nothing to reset to")
    clean_rows = conn.execute(
        "SELECT sector, posterior FROM sector_posterior_history WHERE date = ?", (clean,)
    ).fetchall()
    logger.info(f"resetting state to {clean} ({len(clean_rows)} sectors)")
    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute("DELETE FROM sector_posteriors")
    conn.executemany(
        "INSERT INTO sector_posteriors (sector, posterior, updated_at) VALUES (?, ?, ?)",
        [(r["sector"], r["posterior"], now_iso) for r in clean_rows],
    )
    cache = _rebuild_result_cache(prod_trace, clean, target_cache)
    logger.info(f"hysteresis state rebuilt from trace: leader={cache['leader']} qualifiers={cache['qualifiers']}")

    # 3. delete the window
    n_del = conn.execute(
        "DELETE FROM sector_posterior_history WHERE date >= ? AND date <= ?", (args.date_from, args.date_to)
    ).rowcount
    conn.commit()
    conn.close()
    logger.info(f"deleted {n_del} history rows in [{args.date_from}, {args.date_to}]")

    # 4. replay — the singleton must be created AFTER the reset so it loads the clean state
    sched._regime_bayes = None
    sessions = sched._nyse_sessions_between(d_from - timedelta(days=1), d_to)
    for d in sessions:
        sched.run_eod_regime(as_of=d)

    # 5. diff
    conn = sqlite3.connect(target_db)
    new = {
        (r[0], r[1]): r[2]
        for r in conn.execute(
            "SELECT date, sector, posterior FROM sector_posterior_history WHERE date >= ? AND date <= ?",
            (args.date_from, args.date_to),
        )
    }
    prov = conn.execute(
        "SELECT COUNT(*) FROM sector_posterior_history WHERE date >= ? AND written_at IS NULL", (args.date_from,)
    ).fetchone()[0]
    conn.close()

    diff_path = workdir / f"replay-diff-{stamp}.csv"
    moves = []
    with open(diff_path, "w") as f:
        f.write("date,sector,old,new,delta\n")
        for key in sorted(set(old) | set(new)):
            o, n = old.get(key), new.get(key)
            delta = (n - o) if (o is not None and n is not None) else None
            if delta is not None:
                moves.append(abs(delta))
            f.write(f"{key[0]},{key[1]},{'' if o is None else o},{'' if n is None else n},{'' if delta is None else round(delta, 4)}\n")
    dates_old, dates_new = {k[0] for k in old}, {k[0] for k in new}
    print(f"\nreplayed sessions : {len(sessions)}  ({sessions[0]}..{sessions[-1]})")
    print(f"dates before/after: {len(dates_old)} / {len(dates_new)}   filled: {sorted(dates_new - dates_old)}   lost: {sorted(dates_old - dates_new)}")
    if moves:
        print(f"|Δposterior| over {len(moves)} shared cells: max {max(moves):.4f}  mean {sum(moves)/len(moves):.4f}")
    print(f"rows without written_at after replay: {prov}")
    print(f"diff: {diff_path}")
    print(f"mode: {'APPLIED to ' + str(prod_db) if args.apply else 'DRY RUN on ' + str(target_db)}")
    if args.apply:
        print("NOTE: the running backend holds the pre-replay state in memory — restart it (or touch a backend file under --reload)")


def _release_lock():
    try:
        import backend.scheduler as sched
        sched.EOD_REPLAY_LOCK.unlink(missing_ok=True)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    finally:
        _release_lock()
