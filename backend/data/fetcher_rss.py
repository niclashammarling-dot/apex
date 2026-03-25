"""
RSS news fetcher — primary fallback when NewsAPI quota is exhausted.
Uses Yahoo Finance's public RSS feed per ticker.
"""
import feedparser
from datetime import datetime, timezone
from loguru import logger

from backend.config import RSS_URL_TEMPLATE


def fetch_headlines(ticker: str, max_items: int = 5) -> list[dict]:
    """
    Fetch recent headlines for a ticker via Yahoo Finance RSS.
    Returns list of dicts: {ticker, headline, source, published_at, url}
    """
    url = RSS_URL_TEMPLATE.format(ticker=ticker)
    try:
        feed = feedparser.parse(url)
        if feed.bozo and not feed.entries:
            logger.warning(f"RSS parse issue for {ticker}: {feed.bozo_exception}")
            return []

        results = []
        for entry in feed.entries[:max_items]:
            published_at = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                try:
                    published_at = datetime(
                        *entry.published_parsed[:6], tzinfo=timezone.utc
                    ).isoformat()
                except Exception:
                    pass

            results.append({
                "ticker":       ticker,
                "headline":     entry.get("title", ""),
                "source":       feed.feed.get("title", "Yahoo Finance"),
                "published_at": published_at,
                "url":          entry.get("link", ""),
            })

        logger.debug(f"RSS {ticker}: {len(results)} headlines")
        return results

    except Exception as e:
        logger.error(f"RSS fetch failed for {ticker}: {e}")
        return []


def fetch_headlines_multi(tickers: list[str]) -> list[dict]:
    """Fetch headlines for a list of tickers."""
    all_headlines = []
    for ticker in tickers:
        all_headlines.extend(fetch_headlines(ticker))
    return all_headlines
