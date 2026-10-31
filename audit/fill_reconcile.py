"""
Fill reconcile (2026-10-31): the live book's recorded entries and exits against the broker's fills, and the fees.

CHECK 89 read the DB only, so a capture could be right per row while the book drifted (bundle TODO 2026-10-13).
Found 2026-10-31: TER row 70 and MSFT row 47 were booked at one slice of a two-slice sell order, $4.84 above
the broker's fills, and nothing compared the two ledgers. M4's drawdown halt reads realised equity from these
rows (backend/gate/portfolio_l0.py), so a drifting book moves the stop.

Writer: publish_state calls write() before the checks, on the host (broker credentials), and writes
data/fill_reconcile.json. Reader: CHECK 89 (audit/checks_data.py), which warns when the file is missing or
stale, when a closed row has no matching broker order, or when the gap exceeds the fees plus $1.

Matching: an entry is its alpaca_order_id's buy fills; an exit is the one sell order of the ticker with a fill
within EXIT_MATCH_S of exited_at (scripts/backfill_entry_fills.plan_exit_corrections). Gaps are recorded minus
broker, in dollars (positive = the book shows more profit than the broker paid).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "fill_reconcile.json"


def reconcile(rows: list[dict], fills: list[dict], fees: list[dict]) -> dict:
    """Pure core: rows are closed live_trades (id, ticker, qty, entry_price, alpaca_order_id, exit_price,
    exited_at, pnl, outcome); fills are get_activities('FILL') rows; fees are get_fee_activities rows."""
    from scripts.backfill_entry_fills import plan_exit_corrections

    buys: dict[str, list[dict]] = {}
    for f in fills:
        if f.get("side") == "buy" and f.get("order_id") and f.get("qty"):
            buys.setdefault(f["order_id"], []).append(f)
    entry_gap, entry_unmatched, entry_rows = 0.0, [], []
    for r in rows:
        g = buys.get(r.get("alpaca_order_id") or "")
        q = sum(f["qty"] for f in g) if g else 0.0
        if not g or abs(q - r["qty"]) > 1e-6:
            entry_unmatched.append(r["id"])
            continue
        vwap = sum(f["qty"] * f["price"] for f in g) / q
        d = round((vwap - r["entry_price"]) * r["qty"], 2)     # a higher fill than recorded = less profit
        entry_gap += d
        if abs(d) >= 0.01:
            entry_rows.append({"id": r["id"], "ticker": r["ticker"], "gap": d})

    plan, skipped = plan_exit_corrections(rows, fills)
    exit_rows = [{"id": p["id"], "ticker": p["ticker"], "gap": round((p["exit_price"] - p["fill"]) * p["qty"], 2)}
                 for p in plan]
    exit_gap = round(sum(x["gap"] for x in exit_rows), 2)
    return {"rows": len(rows),
            "entry_gap": round(entry_gap, 2), "entry_rows": entry_rows, "entry_unmatched": entry_unmatched,
            "exit_gap": exit_gap, "exit_rows": exit_rows, "exit_unmatched": [s["id"] for s in skipped],
            "fees_total": round(sum(f["net_amount"] for f in fees), 2), "fee_days": len(fees)}


def write(db: Path | None = None, out: Path = OUT) -> dict:
    from backend.brokers.alpaca import get_activities, get_fee_activities
    db = db or REPO / "data" / "apex.db"
    since = json.loads((REPO / "data" / "live_config.json").read_text())["live_account_since"]
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, ticker, qty, entry_price, alpaca_order_id, exit_price, exited_at, pnl, outcome "
            "FROM live_trades WHERE outcome IN ('WIN', 'LOSS') AND exit_price IS NOT NULL "
            "AND exited_at IS NOT NULL AND timestamp >= ? ORDER BY timestamp", (since,))]
    finally:
        conn.close()
    after = f"{since}T00:00:00Z"
    result = {"generated_at": datetime.now(timezone.utc).isoformat(), "since": since,
              **reconcile(rows, get_activities("FILL", after=after), get_fee_activities(after))}
    out.write_text(json.dumps(result, indent=1))
    return result


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(REPO))
    r = write()
    print(json.dumps({k: v for k, v in r.items() if not k.endswith("_rows")}, indent=1))
