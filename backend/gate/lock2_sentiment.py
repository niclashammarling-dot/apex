"""
Lock 2 — Grok sentiment evaluation.

Fail-closed: if Grok is unreachable after retries, the lock fails.
Cache TTL: GROK_CACHE_TTL minutes per ticker (config.py).
Circuit breaker: after 3 consecutive API failures the circuit opens for
  5 minutes, causing all subsequent calls to fail-fast without hitting the
  network. Resets automatically once the cooldown expires.
"""
import json
import threading
import time
from datetime import datetime, timezone

import httpx
from loguru import logger

from backend.config import XAI_API_KEY, LOCK2_SENTIMENT_MIN, GROK_CACHE_TTL

GROK_URL   = "https://api.x.ai/v1/chat/completions"
GROK_MODEL = "grok-3"

RETRY_WAITS = [5, 15, 30]  # seconds between retry attempts

# ── In-memory cache ───────────────────────────────────────────────────────────
# ticker → {"expires_at": float (unix ts), "result": dict}
_cache: dict[str, dict] = {}

# ── Circuit breaker ───────────────────────────────────────────────────────────
_CB_THRESHOLD  = 3       # consecutive failures before opening
_CB_COOLDOWN   = 300     # seconds before half-open retry (5 min)
_cb_failures   = 0
_cb_open_until = 0.0     # unix timestamp; 0 means closed
_cb_lock       = threading.Lock()


def _cache_cleanup() -> None:
    """Remove expired entries to prevent indefinite memory growth."""
    now = datetime.now(timezone.utc).timestamp()
    expired = [k for k, v in _cache.items() if now >= v["expires_at"]]
    for k in expired:
        del _cache[k]


def _circuit_open() -> bool:
    with _cb_lock:
        return time.time() < _cb_open_until


def _record_success() -> None:
    global _cb_failures, _cb_open_until
    with _cb_lock:
        _cb_failures   = 0
        _cb_open_until = 0.0


def _record_failure() -> None:
    global _cb_failures, _cb_open_until
    with _cb_lock:
        _cb_failures += 1
        if _cb_failures >= _CB_THRESHOLD:
            _cb_open_until = time.time() + _CB_COOLDOWN
            logger.warning(
                f"Lock 2 circuit breaker OPEN — Grok API unreachable after "
                f"{_cb_failures} failures. Cooling down for {_CB_COOLDOWN}s."
            )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fetch_news(ticker: str) -> list[str]:
    """Fetch recent RSS headlines for context. Returns list of headline strings."""
    try:
        from backend.data.fetcher_rss import fetch_headlines
        items = fetch_headlines(ticker, max_items=5)
        return [item["headline"] for item in items if item["headline"]]
    except Exception:
        return []


def _prompt(ticker: str, headlines: list[str]) -> str:
    news_block = ""
    if headlines:
        lines = "\n".join(f"- {h}" for h in headlines)
        news_block = f"\n\nRecent news headlines:\n{lines}"

    return (
        f"Analyze current social sentiment on X/Twitter about ${ticker}."
        f"{news_block}\n\n"
        "Consider both the social signals and any news context above.\n"
        "Look for insider-adjacent signals, unusual volume spikes, "
        "trend setters, or anomalies that precede market moves.\n"
        "Return a JSON object with exactly these keys:\n"
        '{\n'
        '  "sentiment": "positive" | "neutral" | "negative",\n'
        '  "score": float between -1.0 and 1.0,\n'
        '  "volume": "high" | "medium" | "low",\n'
        '  "key_themes": [list of 3 short strings],\n'
        '  "summary": "one sentence"\n'
        '}\n'
        "Only return the JSON. No preamble."
    )


def _call_grok(ticker: str, headlines: list[str]) -> dict | None:
    """Makes the API call. Returns parsed dict or None on failure."""
    if not XAI_API_KEY:
        logger.warning("Lock 2: XAI_API_KEY not set — failing closed")
        return None

    if _circuit_open():
        logger.warning(f"Lock 2 [{ticker}]: circuit breaker open — failing fast")
        return None

    payload = {
        "model": GROK_MODEL,
        "messages": [{"role": "user", "content": _prompt(ticker, headlines)}],
        "response_format": {"type": "json_object"},
        "temperature": 0.1,
    }
    headers = {
        "Authorization": f"Bearer {XAI_API_KEY}",
        "Content-Type": "application/json",
    }

    for attempt, wait in enumerate(RETRY_WAITS, start=1):
        try:
            resp = httpx.post(GROK_URL, headers=headers, json=payload, timeout=20)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            result  = json.loads(content)
            _record_success()
            return result

        except httpx.HTTPStatusError as e:
            logger.warning(f"Lock 2 [{ticker}] attempt {attempt}: HTTP {e.response.status_code}")
        except httpx.RequestError as e:
            logger.warning(f"Lock 2 [{ticker}] attempt {attempt}: connection error — {e}")
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Lock 2 [{ticker}] attempt {attempt}: bad response — {e}")

        if attempt < len(RETRY_WAITS):
            logger.debug(f"Lock 2 [{ticker}]: retrying in {wait}s…")
            time.sleep(wait)

    _record_failure()
    logger.error(f"Lock 2 [{ticker}]: all retries exhausted — failing closed")
    return None


# ── Public API ────────────────────────────────────────────────────────────────

def evaluate(ticker: str, sentiment_min: float | None = None) -> dict:
    """
    Evaluate Grok sentiment for a ticker.
    Pass condition: score > sentiment_min (defaults to LOCK2_SENTIMENT_MIN) AND volume != "low"
    """
    effective_min = sentiment_min if sentiment_min is not None else LOCK2_SENTIMENT_MIN
    now = datetime.now(timezone.utc).timestamp()

    # Periodically clean stale cache entries (cheap, no extra dep)
    _cache_cleanup()

    # Check cache
    cached = _cache.get(ticker)
    if cached and now < cached["expires_at"]:
        logger.debug(f"Lock 2 [{ticker}]: cache hit")
        grok = cached["result"]
    else:
        headlines = _fetch_news(ticker)
        logger.debug(f"Lock 2 [{ticker}]: {len(headlines)} RSS headlines fetched")
        grok = _call_grok(ticker, headlines)
        if grok:
            _cache[ticker] = {
                "expires_at": now + GROK_CACHE_TTL * 60,
                "result":     grok,
            }

    if grok is None:
        return {
            "lock":       2,
            "passed":     False,
            "score":      None,
            "volume":     None,
            "key_themes": [],
            "summary":    None,
            "reason":     "grok_unavailable",
        }

    score  = float(grok.get("score", 0.0))
    volume = grok.get("volume", "low")
    passed = score > effective_min and volume != "low"

    reason = "pass" if passed else (
        f"score {score:.2f} <= {effective_min}" if score <= effective_min
        else "volume too low"
    )

    return {
        "lock":       2,
        "passed":     passed,
        "score":      score,
        "sentiment":  grok.get("sentiment"),
        "volume":     volume,
        "key_themes": grok.get("key_themes", []),
        "summary":    grok.get("summary"),
        "reason":     reason,
    }
