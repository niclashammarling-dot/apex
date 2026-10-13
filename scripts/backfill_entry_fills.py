"""
Backfill live_trades.entry_price with the entry order's fill (2026-10-13).

Traced 2026-10-12: every since-reset row held signal["price"] (the quote), not the
fill. The tracker captures the fill going forward; this sets it for history from the
broker's ledger: /account/activities/FILL buy rows grouped by order_id, quantity-
weighted average price. A row is written only when the fills on its order sum to the
row's qty; anything else is listed and left alone. record_entry_fill() writes once
(entry_filled_at NULL only) and recomputes pnl/WIN-LOSS on closed rows.

    venv/bin/python scripts/backfill_entry_fills.py --db <copy.db>            # dry run
    venv/bin/python scripts/backfill_entry_fills.py --db <copy.db> --apply    # on a copy
    venv/bin/python scripts/backfill_entry_fills.py --db data/apex.db --apply [--ids 1,2]

Read-only against the broker. --apply on data/apex.db refuses while anything listens
on :8000. Writes a JSON report of every row (before/after) next to the DB's backups.
"""
import argparse
import json
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--db", required=True, help="SQLite file to read/write (production: data/apex.db)")
    ap.add_argument("--apply", action="store_true", help="write; without it, print the plan only")
    ap.add_argument("--ids", help="comma-separated live_trades ids (default: every uncaptured since-reset row)")
    args = ap.parse_args(argv)

    db = Path(args.db).resolve()
    if not db.exists():
        print(f"no such DB: {db}")
        return 2
    if args.apply and db == (REPO / "data" / "apex.db").resolve():
        with socket.socket() as s:
            s.settimeout(1)
            if s.connect_ex(("127.0.0.1", 8000)) == 0:
                print("refusing: something is listening on :8000 (a serving backend) — stop it first")
                return 1

    import backend.db as dbm
    dbm.DB_PATH = db
    dbm.init_db()   # adds signal_price / entry_filled_at if this copy predates them
    from backend.brokers.alpaca import get_activities

    since = json.loads((REPO / "data" / "live_config.json").read_text())["live_account_since"]
    ids = {int(x) for x in args.ids.split(",")} if args.ids else None

    conn = dbm.get_db()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, ticker, timestamp, alpaca_order_id, entry_price, signal_price, qty, exit_price, "
            "pnl, outcome, exit_confidence FROM live_trades "
            "WHERE entry_filled_at IS NULL AND alpaca_order_id IS NOT NULL AND timestamp >= ? "
            "ORDER BY timestamp", (since,)).fetchall()]
    finally:
        conn.close()
    if ids is not None:
        rows = [r for r in rows if r["id"] in ids]

    fills = get_activities("FILL", after=f"{since}T00:00:00Z")
    by_order: dict[str, dict] = {}
    for f in fills:
        if f["side"] != "buy" or not f["order_id"] or not f["qty"]:
            continue
        g = by_order.setdefault(f["order_id"], {"qty": 0.0, "notional": 0.0, "last": ""})
        g["qty"] += f["qty"]
        g["notional"] += f["qty"] * f["price"]
        g["last"] = max(g["last"], f["filled_at"] or "")
    print(f"{len(fills)} FILL activities since {since}; {len(by_order)} buy orders; "
          f"{len(rows)} uncaptured rows", flush=True)

    plan, skipped = [], []
    for r in rows:
        g = by_order.get(r["alpaca_order_id"])
        if g is None:
            skipped.append({**r, "reason": "no buy fill on this order id"})
            continue
        if abs(g["qty"] - r["qty"]) > 1e-6:
            skipped.append({**r, "reason": f"fills sum to {g['qty']:g}, row qty {r['qty']:g}"})
            continue
        fill = round(g["notional"] / g["qty"], 6)
        new_pnl = round((r["exit_price"] - fill) * r["qty"], 2) if r["exit_price"] is not None else None
        plan.append({**r, "fill": fill, "filled_at": g["last"], "slip": fill / r["entry_price"] - 1,
                     "new_pnl": new_pnl})

    closed = [p for p in plan if p["new_pnl"] is not None and p["outcome"] in ("WIN", "LOSS")
              and p["exit_confidence"] != "unverified"]
    old_sum = round(sum(p["pnl"] for p in closed), 2)
    new_sum = round(sum(p["new_pnl"] for p in closed), 2)
    for p in plan:
        flip = ""
        if p["new_pnl"] is not None and p["outcome"] in ("WIN", "LOSS") and (p["new_pnl"] > 0) != (p["outcome"] == "WIN"):
            flip = f"  {p['outcome']} -> {'WIN' if p['new_pnl'] > 0 else 'LOSS'}"
        print(f"  id={p['id']:<4} {p['ticker']:<6} {p['timestamp'][:10]}  quote {p['entry_price']:>9.2f}  "
              f"fill {p['fill']:>10.4f}  slip {p['slip']:+.2%}"
              + (f"  pnl {p['pnl']:+.2f} -> {p['new_pnl']:+.2f}" if p["new_pnl"] is not None else "  OPEN")
              + flip)
    for s in skipped:
        print(f"  SKIP id={s['id']} {s['ticker']} {s['timestamp'][:10]}: {s['reason']}")
    print(f"plan: {len(plan)} rows, {len(skipped)} skipped; closed realized (verified) "
          f"{old_sum:+.2f} -> {new_sum:+.2f} (entry correction {new_sum - old_sum:+.2f})", flush=True)

    report = db.parent / f"entry_fill_backfill_{datetime.now(timezone.utc):%Y-%m-%dT%H%M%S}.json"
    if db.parent.name == "data":
        report = db.parent / "backups" / report.name
    written = 0
    if args.apply:
        for p in plan:
            written += dbm.record_entry_fill(p["id"], p["fill"], p["filled_at"])
        print(f"applied: {written} rows written", flush=True)
    report.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(), "db": str(db), "applied": args.apply,
        "written": written, "since": since, "realized_before": old_sum, "realized_after": new_sum,
        "plan": plan, "skipped": skipped}, indent=1, default=str))
    print(f"report: {report}")
    return 0 if not args.apply or written == len(plan) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
