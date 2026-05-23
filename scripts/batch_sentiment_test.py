"""
Batch Grok sentiment test — full watchlist score distribution.

Calls _call_grok directly for every ticker in data/tickers.json that has a
current sentiment_cache entry. Skips tickers with no cache — run after the
morning pre-fetch completes (09:30 ET).

Usage:
    cd /home/promenix/apex
    python -m scripts.batch_sentiment_test

Output:
    Per-ticker rows grouped by sector: score, conviction, summary.
    Distribution summary: min/p25/median/p75/max, conviction counts,
    pass rates at thresholds 0.1 / 0.15 / 0.2 / 0.25 / 0.3.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import median, quantiles

from loguru import logger

# ── Suppress loguru noise from imported modules ───────────────────────────────
logger.remove()
logger.add(sys.stderr, level="WARNING")

from backend.data.fetcher_sentiment import fetch_market_signals
from backend.data.fetcher_rss import fetch_headlines
from backend.gate.lock3_sentiment import _call_grok, _build_prompt
from backend.db import get_db

TICKERS_JSON    = Path("data/tickers.json")
CALL_DELAY_S    = 1.0    # seconds between Grok calls — polite pacing
THRESHOLDS      = [0.10, 0.15, 0.20, 0.25, 0.30]
STALE_DAYS      = 2      # cache entries older than this are flagged stale


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _load_cache_index() -> dict[str, dict]:
    """Return {ticker: {fetched_at, reddit_raw, rss_raw, reddit_posts, feed_count}}."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT ticker, fetched_at, reddit_raw, rss_raw, reddit_posts, feed_count "
            "FROM sentiment_cache"
        ).fetchall()
    finally:
        conn.close()

    index = {}
    for r in rows:
        parts = []
        if r["reddit_raw"]:
            parts.append(f"=== Reddit ({r['reddit_posts']} posts) ===\n{r['reddit_raw']}")
        if r["rss_raw"]:
            parts.append(f"=== RSS Feeds ({r['feed_count']} sources) ===\n{r['rss_raw']}")
        index[r["ticker"]] = {
            "fetched_at":    r["fetched_at"],
            "combined":      "\n\n".join(parts),
            "reddit_posts":  r["reddit_posts"],
            "feed_count":    r["feed_count"],
        }
    return index


def _cache_age_days(fetched_at: str) -> float:
    try:
        dt  = datetime.fromisoformat(fetched_at)
        now = datetime.now(timezone.utc)
        return (now - dt).total_seconds() / 86400
    except Exception:
        return 999.0


# ── Formatting helpers ────────────────────────────────────────────────────────

CONV_COLOUR = {"high": "\033[92m", "medium": "\033[93m", "low": "\033[91m"}
SCORE_COLOUR = lambda s: "\033[92m" if s >= 0.3 else "\033[93m" if s >= 0.1 else "\033[91m"
RESET = "\033[0m"

def _fmt_score(score: float | None) -> str:
    if score is None:
        return "  N/A "
    c = SCORE_COLOUR(score)
    return f"{c}{score:+.2f}{RESET}"

def _fmt_conviction(conv: str | None) -> str:
    if conv is None:
        return "  ---  "
    c = CONV_COLOUR.get(conv.lower(), "")
    return f"{c}{conv:<6}{RESET}"

def _percentile(data: list[float], p: float) -> float:
    if not data:
        return 0.0
    if len(data) == 1:
        return data[0]
    qs = quantiles(data, n=100)
    idx = max(0, min(int(p) - 1, len(qs) - 1))
    return qs[idx]


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    tickers_data: dict = json.loads(TICKERS_JSON.read_text())
    cache_index         = _load_cache_index()
    today               = date.today().isoformat()

    results: list[dict] = []   # {sector, ticker, score, conviction, summary, skipped, skip_reason}
    skipped: list[tuple[str, str, str]] = []  # (sector, ticker, reason)

    total_tickers = sum(len(v["tickers"]) for v in tickers_data.values())
    cached_today  = sum(
        1 for e in cache_index.values()
        if e["fetched_at"][:10] == today
    )

    print()
    print("=" * 72)
    print(f"  APEX batch sentiment test — {today}")
    print(f"  Tickers in universe: {total_tickers}  |  Cache entries today: {cached_today}")
    print("=" * 72)

    for sector, sector_data in tickers_data.items():
        sector_tickers = sector_data["tickers"]
        sector_results = []

        print(f"\n  {sector} ({len(sector_tickers)} tickers)")
        print(f"  {'TICKER':<8}  {'SCORE':>6}  {'CONV':<8}  SUMMARY")
        print(f"  {'-'*8}  {'-'*6}  {'-'*8}  {'-'*40}")

        for ticker in sector_tickers:
            cache_entry = cache_index.get(ticker)

            if cache_entry is None:
                reason = "no cache entry — pre-fetch not run yet"
                skipped.append((sector, ticker, reason))
                print(f"  {ticker:<8}  {'SKIP':>6}  {'':8}  [{reason}]")
                continue

            age = _cache_age_days(cache_entry["fetched_at"])
            if age > STALE_DAYS:
                reason = f"cache stale ({age:.1f}d old, fetched {cache_entry['fetched_at'][:10]})"
                skipped.append((sector, ticker, reason))
                print(f"  {ticker:<8}  {'SKIP':>6}  {'':8}  [{reason}]")
                continue

            try:
                signals       = fetch_market_signals(ticker)
                rss_headlines = [
                    item["headline"]
                    for item in fetch_headlines(ticker, max_items=5)
                    if item["headline"]
                ]
                prefetch      = cache_entry["combined"]

                result = _call_grok(ticker, signals, rss_headlines, prefetch)

                if result is None:
                    skipped.append((sector, ticker, "Grok API call failed"))
                    print(f"  {ticker:<8}  {'FAIL':>6}  {'':8}  [Grok API call failed]")
                    continue

                score      = float(result.get("score", 0.0))
                conviction = (result.get("conviction") or "").lower()
                summary    = result.get("summary", "")[:72]

                row = {
                    "sector":     sector,
                    "ticker":     ticker,
                    "score":      score,
                    "conviction": conviction,
                    "summary":    summary,
                }
                results.append(row)
                sector_results.append(row)

                print(
                    f"  {ticker:<8}  {_fmt_score(score):>6}  {_fmt_conviction(conviction):<8}  {summary}"
                )

            except Exception as e:
                skipped.append((sector, ticker, f"error: {e}"))
                print(f"  {ticker:<8}  {'ERR':>6}  {'':8}  [{e}]")

            time.sleep(CALL_DELAY_S)

    # ── Distribution summary ──────────────────────────────────────────────────

    scores     = [r["score"] for r in results]
    convictions = [r["conviction"] for r in results]

    print()
    print("=" * 72)
    print(f"  DISTRIBUTION SUMMARY  ({len(results)} tickers scored, {len(skipped)} skipped)")
    print("=" * 72)

    if scores:
        p25  = _percentile(scores, 25)
        p75  = _percentile(scores, 75)
        med  = median(scores)
        print()
        print(f"  Score distribution:")
        print(f"    min    {min(scores):+.3f}")
        print(f"    p25    {p25:+.3f}")
        print(f"    median {med:+.3f}")
        print(f"    p75    {p75:+.3f}")
        print(f"    max    {max(scores):+.3f}")

        print()
        print(f"  Conviction breakdown ({len(convictions)} tickers):")
        for label in ("high", "medium", "low"):
            n   = convictions.count(label)
            pct = 100 * n / len(convictions) if convictions else 0
            bar = "█" * int(pct / 5)
            print(f"    {label:<8}  {n:>3}  ({pct:4.0f}%)  {bar}")

        print()
        print(f"  Pass rates by threshold (score > threshold AND conviction != low):")
        print(f"    {'Threshold':>10}  {'Pass':>5}  {'%':>6}  bar")
        for t in THRESHOLDS:
            passing = [
                r for r in results
                if r["score"] > t and r["conviction"] not in ("low", "")
            ]
            pct = 100 * len(passing) / len(results) if results else 0
            bar = "█" * int(pct / 5)
            marker = "  ← current live" if t == 0.20 else ("  ← current demo" if t == 0.10 else "")
            print(f"    {t:>10.2f}  {len(passing):>5}  {pct:5.0f}%  {bar}{marker}")

    if skipped:
        print()
        print(f"  Skipped ({len(skipped)}):")
        for sector, ticker, reason in skipped:
            print(f"    {ticker:<8}  {sector:<16}  {reason}")

    print()


if __name__ == "__main__":
    main()
