"""
earnings_source.py — the engines' historical earnings dates, with holes marked (Q-DAT, PRODUCTION_GAP rows 8-9).

Why (2026-11-28, APEX Saturday order item 1): both engines read yfinance's `.earnings_dates` property, which
needs lxml (not installed until this commit) and, with lxml, returns yfinance's default 25 rows: AAPL, HAL and PLD
reach back only to 2021-01. Installing lxml alone would have left the filter silently empty for 2015-2020 (the
stacked shape of a silent zero). Measured 2026-11-23 by `scripts/earnings_history_probe.py` (apex fa51cc3, archive in the vault
raw/notes/2026-11/data/2026-11-23-apex-earnings-history-probe.json): `get_earnings_dates(limit=60)` covers the 83 active tickers since
2015 (median 48 reports), with holes FDX 2026-03 and MRVL 2015-05..2016-07; BLK (excluded sector) is empty
2009-10..2022-07.

Rules (Niclas 2026-11-23, the three conditions of the earnings build):
- a hole is marked, never read as "no report" (condition 2): a day before a ticker's first returned report, inside
  a gap longer than GAP_DAYS, after its last returned report, or for a ticker the source returned nothing for, is
  unknown. The engines do not filter on an unknown day; they count it and return the count, and the cell's
  producer excludes those ticker-days from every class (pre-registration § Holes).
- the battery unit key hashes the fetched set (condition 1): `EarningsSet.digest()`, appended to the key only when
  the unit sets `earnings_filter_days`, so every stored key holds.
- a source that returns nothing for every ticker raises (PRODUCTION_GAP row 8: "row 9's test must fail on an empty
  set"); before this module the engines swallowed that at debug and filtered nothing.
"""
from __future__ import annotations

import bisect
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

from loguru import logger

EARNINGS_LIMIT = 60   # rows per ticker; 60 quarters reach about 2011, before every window start (11-23 probe)
GAP_DAYS = 150        # the probe's gap rule: a Q4 report lands 120-135 days after Q3 (COST, PEP); a missed quarter ~180


def _yf_fetch(ticker: str) -> list[date]:
    import yfinance as yf
    df = yf.Ticker(ticker).get_earnings_dates(limit=EARNINGS_LIMIT)
    if df is None or df.empty:
        return []
    return [(ts.date() if hasattr(ts, "date") else ts) for ts in df.index]


@dataclass(frozen=True)
class EarningsSet:
    dates: dict[str, tuple[date, ...]]          # every date the source returned, sorted, deduplicated
    failed: dict[str, str] = field(default_factory=dict)   # ticker -> why nothing came back (all of its days unknown)

    def near(self, ticker: str, day: date, days: int) -> bool:
        """A report in [day, day + days]: the engines' existing filter, unchanged on covered days."""
        ds = self.dates.get(ticker, ())
        i = bisect.bisect_left(ds, day)
        return i < len(ds) and ds[i] <= day + timedelta(days=days)

    def in_hole(self, ticker: str, day: date) -> bool:
        """True when the source cannot say whether `ticker` reported around `day`."""
        ds = self.dates.get(ticker, ())
        if not ds:
            return True                          # failed, empty, or never fetched: unknown, not "no report"
        if day < ds[0] or day > ds[-1]:
            return True
        i = bisect.bisect_left(ds, day)
        if ds[i] == day:
            return False
        return (ds[i] - ds[i - 1]).days > GAP_DAYS

    def holes(self, ticker: str, start: date, end: date) -> list[tuple[date, date]]:
        """The unknown spans of `ticker` clipped to [start, end], as inclusive (from, to) pairs."""
        ds = self.dates.get(ticker, ())
        if not ds:
            return [(start, end)]
        spans = [(date.min, ds[0] - timedelta(days=1))]
        spans += [(a + timedelta(days=1), b - timedelta(days=1)) for a, b in zip(ds, ds[1:]) if (b - a).days > GAP_DAYS]
        spans.append((ds[-1] + timedelta(days=1), date.max))
        return [(max(a, start), min(b, end)) for a, b in spans if a <= end and b >= start and max(a, start) <= min(b, end)]

    def digest(self) -> str:
        raw = json.dumps({"limit": EARNINGS_LIMIT, "gap": GAP_DAYS,
                          "dates": {t: [d.isoformat() for d in ds] for t, ds in sorted(self.dates.items())},
                          "failed": sorted(self.failed)}, sort_keys=True)
        return hashlib.sha1(raw.encode()).hexdigest()[:12]


_MEMO: dict[tuple[str, ...], EarningsSet] = {}


def load(tickers: list[str], fetch: Callable[[str], list[date]] | None = None) -> EarningsSet:
    """Every report date the source returns for each ticker (EARNINGS_LIMIT rows), memoised per process so the
    unit key and the run read one set. Raises when no ticker returns anything (missing lxml, source down)."""
    key = tuple(sorted(set(tickers)))
    if fetch is None and key in _MEMO:
        return _MEMO[key]
    fetch = fetch or _yf_fetch
    dates: dict[str, tuple[date, ...]] = {}
    failed: dict[str, str] = {}
    for t in key:
        try:
            got = sorted(set(fetch(t)))
        except Exception as e:                   # one ticker's failure is a marked hole, not an empty set
            failed[t] = f"{type(e).__name__}: {e}"[:200]
            continue
        if got:
            dates[t] = tuple(got)
        else:
            failed[t] = "no rows"
    if key and not dates:
        first = next(iter(failed.values()), "")
        raise RuntimeError(f"earnings source returned nothing for all {len(key)} tickers ({first}); "
                           "the filter would be a silent no-op")
    if failed:
        logger.warning(f"Earnings source: {len(failed)} of {len(key)} tickers returned nothing, every day of "
                       f"theirs is a marked hole: {', '.join(sorted(failed))}")
    es = EarningsSet(dates, failed)
    if fetch is _yf_fetch:
        _MEMO[key] = es
    return es


def coverage(es: EarningsSet, tickers: list[str], start: date, end: date, hole_days: set[tuple[str, date]]) -> dict:
    """The run's coverage record: what was unknown inside the window, and how often the filter met it."""
    holes = {t: [[a.isoformat(), b.isoformat()] for a, b in es.holes(t, start, end)] for t in sorted(set(tickers))}
    return {"limit": EARNINGS_LIMIT, "gap_days": GAP_DAYS, "digest": es.digest(),
            "failed": sorted(es.failed), "holes": {t: h for t, h in holes.items() if h},
            "hole_ticker_days": len(hole_days)}
