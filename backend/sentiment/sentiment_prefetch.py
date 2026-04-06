"""
sentiment_prefetch.py — Morning pre-fetch of Reddit and RSS sentiment data.

Runs once at market open via scheduler. Queries the watchlist table for all
active tickers, fetches Reddit posts and expanded RSS content for each,
and writes to the sentiment_cache table in apex.db.

Lock 2 reads from this cache during the trading day, combining cached content
with fresh X posts (via Grok native) before scoring sentiment 0-1.

If cache is missing for a ticker (new watchlist addition mid-day), Lock 2
falls back gracefully to X-only scoring.

Pipeline:
  1. Query watchlist table for active tickers
  2. For each ticker:
     a. Fetch Reddit posts via rdt-cli (subprocess)
     b. Fetch RSS feeds via Jina Reader (requests)
  3. Write to sentiment_cache table
  4. Log summary
"""
from __future__ import annotations

import subprocess
import time
from datetime import date, datetime, timezone
from typing import Optional

import requests
from loguru import logger

from backend.db import get_db


# ── RSS feed registry ─────────────────────────────────────────────────────────
# Standard financial RSS feeds for sentiment expansion.
# {ticker} is interpolated where the feed supports ticker-specific content.
# Feeds without {ticker} provide general financial news as broader context.

RSS_FEEDS: list[dict] = [
    # Ticker-specific feeds
    {
        "name":   "Seeking Alpha",
        "url":    "https://seekingalpha.com/api/sa/combined/{ticker}.xml",
        "ticker": True,
    },
    {
        "name":   "Benzinga",
        "url":    "https://www.benzinga.com/stock/{ticker}/feed",
        "ticker": True,
    },
    {
        "name":   "MarketWatch",
        "url":    "https://feeds.marketwatch.com/marketwatch/marketpulse/",
        "ticker": False,
    },
    {
        "name":   "Reuters Business",
        "url":    "https://feeds.reuters.com/reuters/businessNews",
        "ticker": False,
    },
    {
        "name":   "Reuters Markets",
        "url":    "https://feeds.reuters.com/reuters/FinanceNews",
        "ticker": False,
    },
    {
        "name":   "Investopedia",
        "url":    "https://www.investopedia.com/feedbuilder/feed/getfeed/?feedName=rss_articles",
        "ticker": False,
    },
    {
        "name":   "CNBC Markets",
        "url":    "https://www.cnbc.com/id/20910258/device/rss/rss.html",
        "ticker": False,
    },
    {
        "name":   "Motley Fool",
        "url":    "https://www.fool.com/feeds/index.aspx?id=foolwatch&symbol={ticker}",
        "ticker": True,
    },
    {
        "name":   "Barrons",
        "url":    "https://www.barrons.com/real-time/feed/rss/market-news",
        "ticker": False,
    },
]

# Jina Reader base URL — converts any URL to clean readable text
JINA_BASE = "https://r.jina.ai/"

# Request settings
REQUEST_TIMEOUT  = 10
REQUEST_DELAY    = 0.2    # seconds between requests — polite crawling
MAX_RSS_CHARS    = 3000   # max chars per RSS feed before truncation
MAX_REDDIT_CHARS = 4000   # max chars from Reddit per ticker
MAX_TOTAL_CHARS  = 15000  # max total raw content passed to Grok per ticker

# Reddit search settings
REDDIT_RESULTS = 10     # number of Reddit posts to fetch per ticker
REDDIT_TIME    = "week" # time filter: hour, day, week, month, year, all


# ── Database helpers ──────────────────────────────────────────────────────────

def _ensure_cache_table() -> None:
    """Create sentiment_cache table if it doesn't exist."""
    conn = get_db()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sentiment_cache (
                ticker       TEXT PRIMARY KEY,
                fetched_at   TEXT NOT NULL,
                reddit_raw   TEXT DEFAULT '',
                rss_raw      TEXT DEFAULT '',
                feed_count   INTEGER DEFAULT 0,
                reddit_posts INTEGER DEFAULT 0
            )
        """)
        conn.commit()
    finally:
        conn.close()


def _get_watchlist_tickers() -> list[str]:
    """Return all tickers currently on the watchlist."""
    try:
        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT ticker FROM watchlist ORDER BY ticker"
            ).fetchall()
            return [row["ticker"] for row in rows]
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"SentimentPrefetch: could not query watchlist: {e}")
        return []


def _write_cache(
    ticker: str,
    reddit_raw: str,
    rss_raw: str,
    feed_count: int,
    reddit_posts: int,
) -> None:
    now  = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO sentiment_cache (ticker, fetched_at, reddit_raw, rss_raw, feed_count, reddit_posts)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                fetched_at   = excluded.fetched_at,
                reddit_raw   = excluded.reddit_raw,
                rss_raw      = excluded.rss_raw,
                feed_count   = excluded.feed_count,
                reddit_posts = excluded.reddit_posts
        """, (ticker, now, reddit_raw, rss_raw, feed_count, reddit_posts))
        conn.commit()
    finally:
        conn.close()


# ── Reddit fetching ───────────────────────────────────────────────────────────

def _parse_reddit_yaml(raw: str) -> tuple[str, int]:
    """
    Parse rdt-cli YAML output into clean post summaries.
    Extracts: subreddit, title, body (selftext), score, upvote_ratio, num_comments.
    Returns (formatted_text, post_count).
    """
    try:
        import yaml
        doc      = yaml.safe_load(raw)
        children = doc["data"]["data"]["children"]
    except Exception:
        return "", 0

    lines      = []
    post_count = 0

    for child in children:
        d = child.get("data", {})
        title    = (d.get("title") or "").strip()
        body     = (d.get("selftext") or "").strip()
        sub      = d.get("subreddit", "")
        score    = d.get("score", 0)
        ratio    = d.get("upvote_ratio", 0)
        comments = d.get("num_comments", 0)

        if not title:
            continue

        # Trim long bodies
        if len(body) > 500:
            body = body[:500] + "…"

        block = f"[r/{sub} | ↑{score} ({int(ratio*100)}%) | {comments} comments]\n{title}"
        if body:
            block += f"\n{body}"

        lines.append(block)
        post_count += 1

    return "\n\n".join(lines), post_count


def _fetch_reddit(ticker: str) -> tuple[str, int]:
    """
    Fetch Reddit posts for a ticker using rdt-cli.
    Parses the YAML output into clean post summaries.
    Returns (formatted_text, post_count).
    Falls back to empty string if rdt-cli is not installed.
    """
    try:
        result = subprocess.run(
            ["rdt", "search", ticker, "--limit", str(REDDIT_RESULTS), "--time", REDDIT_TIME],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            logger.debug(f"SentimentPrefetch: rdt-cli error for {ticker}: {result.stderr[:200]}")
            return "", 0

        raw = result.stdout.strip()
        if not raw:
            return "", 0

        text, post_count = _parse_reddit_yaml(raw)

        if len(text) > MAX_REDDIT_CHARS:
            text = text[:MAX_REDDIT_CHARS] + "… [truncated]"

        return text, post_count

    except FileNotFoundError:
        logger.warning("SentimentPrefetch: rdt-cli not found — Reddit fetch skipped")
        return "", 0
    except subprocess.TimeoutExpired:
        logger.debug(f"SentimentPrefetch: rdt-cli timeout for {ticker}")
        return "", 0
    except Exception as e:
        logger.debug(f"SentimentPrefetch: Reddit fetch failed for {ticker}: {e}")
        return "", 0


# ── RSS fetching ──────────────────────────────────────────────────────────────

def _fetch_rss_feed(feed: dict, ticker: str) -> str:
    """
    Fetch a single RSS feed via Jina Reader.
    Jina converts the RSS/HTML to clean readable text.
    Returns raw text or empty string on failure.
    """
    url      = feed["url"].format(ticker=ticker) if feed["ticker"] else feed["url"]
    jina_url = JINA_BASE + url

    try:
        resp = requests.get(
            jina_url,
            headers={"Accept": "text/plain"},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            return ""

        text = resp.text.strip()
        if not text:
            return ""

        if len(text) > MAX_RSS_CHARS:
            text = text[:MAX_RSS_CHARS] + "... [truncated]"

        return f"[{feed['name']}]\n{text}"

    except Exception as e:
        logger.debug(f"SentimentPrefetch: RSS fetch failed ({feed['name']}, {ticker}): {e}")
        return ""


def _fetch_all_rss(ticker: str) -> tuple[str, int]:
    """
    Fetch all RSS feeds for a ticker.
    Returns (combined_raw_text, successful_feed_count).
    """
    parts = []
    count = 0

    for feed in RSS_FEEDS:
        text = _fetch_rss_feed(feed, ticker)
        if text:
            parts.append(text)
            count += 1
        time.sleep(REQUEST_DELAY)

    combined = "\n\n".join(parts)

    if len(combined) > MAX_RSS_CHARS * len(RSS_FEEDS):
        combined = combined[:MAX_RSS_CHARS * len(RSS_FEEDS)]

    return combined, count


# ── Public API ────────────────────────────────────────────────────────────────

def run() -> dict:
    """
    Run the full pre-fetch pipeline.
    Called by the scheduler at market open (Mon–Fri 9:30 AM ET).
    Returns summary dict with counts.
    """
    logger.info("SentimentPrefetch: starting morning pre-fetch")

    _ensure_cache_table()

    tickers = _get_watchlist_tickers()
    if not tickers:
        logger.warning("SentimentPrefetch: watchlist is empty — nothing to pre-fetch")
        return {"tickers": 0, "success": 0, "failed": 0}

    logger.info(f"SentimentPrefetch: fetching {len(tickers)} watchlist ticker(s)")

    success = failed = 0

    for i, ticker in enumerate(tickers, 1):
        try:
            logger.debug(f"SentimentPrefetch: [{i}/{len(tickers)}] {ticker}")

            reddit_raw, reddit_posts = _fetch_reddit(ticker)
            rss_raw, feed_count      = _fetch_all_rss(ticker)

            # Trim RSS first if over total limit (Reddit is higher signal)
            if len(reddit_raw) + len(rss_raw) > MAX_TOTAL_CHARS:
                rss_raw = rss_raw[:MAX_TOTAL_CHARS - len(reddit_raw)]

            _write_cache(ticker, reddit_raw, rss_raw, feed_count, reddit_posts)
            success += 1

            logger.debug(
                f"SentimentPrefetch: {ticker} — "
                f"Reddit={reddit_posts} posts, RSS={feed_count} feeds"
            )

        except Exception as e:
            logger.warning(f"SentimentPrefetch: failed for {ticker}: {e}")
            failed += 1

    summary = {
        "tickers": len(tickers),
        "success": success,
        "failed":  failed,
        "date":    date.today().isoformat(),
    }
    logger.info(
        f"SentimentPrefetch: complete — "
        f"{success}/{len(tickers)} tickers cached, {failed} failed"
    )
    return summary


def get_combined_content(ticker: str) -> str:
    """
    Return cached Reddit + RSS content for a ticker as a single string,
    ready to include in the Grok prompt.
    Returns empty string if no cache exists — Lock 2 falls back gracefully.
    """
    try:
        conn = get_db()
        try:
            row = conn.execute("""
                SELECT reddit_raw, rss_raw, feed_count, reddit_posts
                FROM sentiment_cache
                WHERE ticker = ?
            """, (ticker,)).fetchone()
        finally:
            conn.close()
    except Exception as e:
        logger.debug(f"SentimentPrefetch: cache read failed for {ticker}: {e}")
        return ""

    if not row:
        logger.debug(f"SentimentPrefetch: no cache for {ticker} — falling back to live-only")
        return ""

    parts = []
    if row["reddit_raw"]:
        parts.append(f"=== Reddit ({row['reddit_posts']} posts) ===\n{row['reddit_raw']}")
    if row["rss_raw"]:
        parts.append(f"=== RSS Feeds ({row['feed_count']} sources) ===\n{row['rss_raw']}")

    return "\n\n".join(parts)
