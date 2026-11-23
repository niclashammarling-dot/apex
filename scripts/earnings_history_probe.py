#!/usr/bin/env python3
"""
earnings_history_probe.py — does a historical earnings-date source cover the universe since 2015? (Q-DAT, GAP-8/9)

Why: the battery cell for the earnings rules (PRODUCTION_GAP rows 8 and 9) was blocked on "a historical
earnings-date source" (vault INDEX earnings entry, R081 a). Read-only: fetches yfinance get_earnings_dates with an
explicit limit for each ticker in ticker_config.get_sectors() and summarises coverage. Needs lxml, which the apex venv does not
have on purpose (installing it changes engine results before the cache key hashes the earnings sets), so run it from
a separate venv with yfinance 0.2.66 + lxml. Record: vault
raw/notes/2026-11/2026-11-23-apex-earnings-history-source-exists-and-the-engine-reads-only-from-2021.md

Usage: earnings_history_probe.py OUT.json [TICKER ...]   (no tickers: every ticker of ticker_config.get_sectors())
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date
from pathlib import Path

LIMIT = 60          # quarters requested; the property .earnings_dates uses the default and stops near 2021-01
SINCE = "2015-01-01"
GAP_DAYS = 150     # a Q4 report routinely lands 120-135 days after Q3 (COST, PEP); a missed quarter is about 180


def summarise(dates: list[str], today: str, since: str = SINCE) -> dict:
    """Coverage of one ticker's ISO report dates: count, span, reports in [since, today], gaps over GAP_DAYS."""
    d = sorted(dates)
    past = [x for x in d if x <= today]
    gaps = [(a, b) for a, b in zip(past, past[1:])
            if (date.fromisoformat(b) - date.fromisoformat(a)).days > GAP_DAYS]
    return {"n": len(d), "first": d[0], "last": d[-1], "n_past": len(past),
            "n_since": sum(x >= since for x in past), "gaps": gaps, "dates": d}


def universe() -> list[str]:
    """The engine's universe: ticker_config.get_sectors(), as precompute() reads it (excluded sectors included).
    Not config.SECTORS: the first run (2026-11-23) read that and missed 10 active tickers."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from backend.ticker_config import get_sectors
    return sorted({t for cfg in get_sectors().values() for t in cfg["tickers"]})


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    import yfinance as yf
    out_path, tickers = argv[0], argv[1:] or universe()
    today = date.today().isoformat()
    out: dict = {"limit": LIMIT, "since": SINCE, "run_on": today, "yfinance": yf.__version__, "tickers": {}}
    for t in tickers:
        try:
            df = yf.Ticker(t).get_earnings_dates(limit=LIMIT)
            out["tickers"][t] = (None if df is None or len(df) == 0
                                 else summarise([x.date().isoformat() for x in df.index], today))
        except Exception as e:  # recorded per ticker, never skipped
            out["tickers"][t] = f"ERR {type(e).__name__}: {e}"[:160]
        print(t, out["tickers"][t], flush=True)
        time.sleep(0.5)
    Path(out_path).write_text(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
