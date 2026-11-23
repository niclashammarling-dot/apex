#!/usr/bin/env python3
"""
m5_universe_probe.py — M5 (Q-DAT): can the names that left the S&P 500 since 2016 be priced? (read-only probe)

For every symbol that was an S&P 500 member at some date from START and is not a member on the source's last row,
read its membership span from the point-in-time membership file (GitHub fja05680/sp500, MIT, symbols as of each
date) and ask Alpaca's data API (daily bars, SIP, adjustment all; GET only, the account APEX already uses) for bars
inside that span. Coverage = bars / NYSE sessions in the span (sessions from SPY's own bars). Reports per symbol and
the distribution. A symbol reused by another company is not detectable here: the build's company-keyed map and its
boundary-continuity control are where that is caught (scope note § EDGAR probe).
Record: vault raw/notes/2026-11/2026-11-23-apex-m5-point-in-time-universe-scope.md

Usage: m5_universe_probe.py MEMBERSHIP.csv OUT.json
"""
from __future__ import annotations

import csv
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

START = "2016-01-04"


def spans(rows: list[tuple[str, set[str]]], start: str = START) -> dict[str, tuple[str, str]]:
    """{symbol: (first, last)} membership dates from `start` for symbols absent from the last row.
    rows: (date, members) sorted by date; a member's span runs to the last row that lists it."""
    rows = sorted(rows)
    last = rows[-1][1]
    out: dict[str, list[str]] = {}
    for d, members in rows:
        if d < start:
            continue
        for s in members:
            if s in last:
                continue
            if s not in out:
                out[s] = [d, d]
            else:
                out[s][1] = d
    return {s: (a, b) for s, (a, b) in out.items()}


def coverage(bar_dates: list[str], sessions: list[str], span: tuple[str, str]) -> float | None:
    """Share of the span's sessions that have a bar; None when the span holds no session."""
    want = [d for d in sessions if span[0] <= d <= span[1]]
    if not want:
        return None
    have = set(bar_dates)
    return round(sum(d in have for d in want) / len(want), 4)


def _bars(sym: str, start: str, end: str, headers: dict) -> list[str]:
    q = urllib.parse.urlencode({"timeframe": "1Day", "start": start, "end": end, "limit": 10000,
                                "adjustment": "all", "feed": "sip"})
    out, token = [], None
    while True:
        url = f"https://data.alpaca.markets/v2/stocks/{urllib.parse.quote(sym)}/bars?{q}" + (f"&page_token={token}" if token else "")
        d = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30))
        out += [b["t"][:10] for b in (d.get("bars") or [])]
        token = d.get("next_page_token")
        if not token:
            return out


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    from dotenv import dotenv_values
    env = dotenv_values(Path(__file__).resolve().parent.parent / ".env")
    headers = {"APCA-API-KEY-ID": env["ALPACA_API_KEY"], "APCA-API-SECRET-KEY": env["ALPACA_SECRET_KEY"]}
    rows = [(r["date"], set(r["tickers"].split(","))) for r in csv.DictReader(open(argv[0]))]
    sp = spans(rows)
    sessions = _bars("SPY", START, sorted(rows)[-1][0], headers)
    res = {}
    for i, (s, span) in enumerate(sorted(sp.items())):
        try:
            res[s] = {"span": span, "coverage": coverage(_bars(s, span[0], span[1], headers), sessions, span)}
        except Exception as e:  # recorded per symbol, never skipped
            res[s] = {"span": span, "coverage": None, "error": f"{type(e).__name__}: {getattr(e, 'code', '')} {e}"[:120]}
        if i % 50 == 0:
            print(i, s, res[s], flush=True)
        time.sleep(0.35)
    Path(argv[1]).write_text(json.dumps({"start": START, "membership_last_row": sorted(rows)[-1][0],
                                         "sessions": len(sessions), "symbols": res}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
