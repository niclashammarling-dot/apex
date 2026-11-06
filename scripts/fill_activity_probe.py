"""Read-only probe: every FILL activity for one ticker since a time, with partial-fill detail.

Vault scope: raw/notes/2026-11/2026-11-02-apex-saturday-scopes-11-07.md § 8.1 (OXY 11-04: was the stop leg
filled in several partials from 14:31 UTC, depth at the open, or once around 14:32-14:35, a delayed trigger?).
The order object carries only filled_at and the average; the activities feed carries each slice
(backend/brokers/alpaca.py get_activities, the same read the tracker uses). Optionally lists the
minute bars over the same window so each slice can be placed against the tape.

GET only; writes only OUT.json. Usage:
  .venv/bin/python -u scripts/fill_activity_probe.py OUT.json TICKER AFTER_ISO [BARS_FROM_ISO BARS_TO_ISO]
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Vault note this run answers (producer block, vault DATA-PROVENANCE); APEX_PROBE_WHY overrides it per run.
WHY = "raw/notes/2026-11/2026-11-02-apex-saturday-scopes-11-07.md"
sys.path.insert(0, str(ROOT))           # backend.* imports when run as scripts/fill_activity_probe.py


def main(out: str, ticker: str, after: str, bars_from: str | None = None, bars_to: str | None = None) -> int:
    from backend.brokers.alpaca import get_activities

    fills = sorted((f for f in get_activities("FILL", after=after) if f["ticker"] == ticker),
                   key=lambda f: f["filled_at"])
    by_order: dict[str, list] = {}
    for f in fills:
        by_order.setdefault(f["order_id"], []).append(f)
    orders = {oid: {"side": fs[0]["side"], "slices": len(fs), "qty": sum(f["qty"] for f in fs),
                    "vwap": round(sum(f["qty"] * f["price"] for f in fs) / sum(f["qty"] for f in fs), 6),
                    "first": fs[0]["filled_at"], "last": fs[-1]["filled_at"]} for oid, fs in by_order.items()}
    bars = []
    if bars_from and bars_to:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        from backend.config import ALPACA_API_KEY, ALPACA_SECRET_KEY
        client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
        req = StockBarsRequest(symbol_or_symbols=ticker, timeframe=TimeFrame.Minute,
                               start=datetime.fromisoformat(bars_from), end=datetime.fromisoformat(bars_to))
        bars = [{"t": b.timestamp.isoformat(), "o": b.open, "h": b.high, "l": b.low, "c": b.close, "v": b.volume}
                for b in client.get_stock_bars(req).data.get(ticker, [])]
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                            text=True).stdout.strip()
    run = " ".join([".venv/bin/python", "-u", "scripts/fill_activity_probe.py", *sys.argv[1:]])
    result = {"producer": {"repo": "apex", "commit": commit, "script": "scripts/fill_activity_probe.py", "run": run,
                           "why": os.environ.get("APEX_PROBE_WHY", WHY),
                           "run_at": datetime.now(timezone.utc).isoformat(),
                           "args": [ticker, after, bars_from, bars_to],
                           "source": "Alpaca /account/activities/FILL (GET) + minute bars (GET)"},
              "fills": fills, "orders": orders, "bars": bars}
    Path(out).write_text(json.dumps(result, indent=1, default=str))
    print(json.dumps({"orders": orders, "fills": fills, "bars": bars}, indent=1, default=str))
    return 0 if fills else 2               # no fill for the ticker: the probe found nothing to read


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
