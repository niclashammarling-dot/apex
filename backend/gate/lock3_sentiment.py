"""
gate/lock3_sentiment.py — Lock 3: Sentiment

Renamed and upgraded from gate/lock2_sentiment.py.
Logic is identical — return type upgraded from plain dict to LockResult.

Grok synthesizes quantitative signals (analyst consensus, short interest,
recent analyst actions, news) fetched from yfinance, then supplements with
a live X/web search for breaking catalysts.

Fail-closed: if Grok is unreachable after retries, the lock fails.
Cache TTL: GROK_CACHE_TTL minutes per ticker (config.py).
Circuit breaker: after 3 consecutive API failures the circuit opens for
  5 minutes, causing all subsequent calls to fail-fast without hitting the
  network. Resets automatically once the cooldown expires.

Public API:
    from gate.lock3_sentiment import evaluate
    result = evaluate(ticker, sentiment_min=None)
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone

import httpx
from loguru import logger

from backend.config import GROK_CACHE_TTL, LOCK2_SENTIMENT_MIN, XAI_API_KEY
from backend.data.fetcher_sentiment import fetch_market_signals
from backend.gate.types import LockResult
from backend.sentiment.sentiment_prefetch import (
    get_combined_content as get_prefetch_content,
)

LOCK_ID    = 3
GROK_URL   = "https://api.x.ai/v1/chat/completions"
GROK_MODEL = "grok-3"

RETRY_WAITS = [5, 15, 30]

# ── In-memory cache ───────────────────────────────────────────────────────────
_cache: dict[str, dict] = {}

# ── Circuit breaker ───────────────────────────────────────────────────────────
_CB_THRESHOLD  = 3
_CB_COOLDOWN   = 300
_cb_failures   = 0
_cb_open_until = 0.0
_cb_lock       = threading.Lock()


def _cache_cleanup() -> None:
    now     = datetime.now(timezone.utc).timestamp()
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
                f"Lock 3 circuit breaker OPEN — Grok API unreachable after "
                f"{_cb_failures} failures. Cooling down for {_CB_COOLDOWN}s."
            )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fetch_rss(ticker: str) -> list[str]:
    try:
        from backend.data.fetcher_rss import fetch_headlines
        items = fetch_headlines(ticker, max_items=5)
        return [item["headline"] for item in items if item["headline"]]
    except Exception:
        return []


def _build_prompt(ticker: str, signals: dict, rss_headlines: list[str], prefetch: str = "") -> str:
    company = signals.get("company_name", ticker)
    blocks  = [f"Sentiment evaluation for {company} ({ticker})."]

    # ── Analyst signals ───────────────────────────────────────────────────────
    analyst_lines = []

    mean  = signals.get("analyst_mean")
    label = signals.get("analyst_label")
    count = signals.get("analyst_count", 0)
    if mean is not None:
        analyst_lines.append(
            f"Consensus: {mean:.1f}/5 ({label}) from {count} analyst(s)"
        )

    target  = signals.get("price_target_mean")
    current = signals.get("current_price")
    upside  = signals.get("upside_pct")
    if target and current and upside is not None:
        analyst_lines.append(
            f"Price target: ${target:.0f} mean vs ${current:.0f} current "
            f"({upside:+.1f}% implied upside)"
        )

    actions = signals.get("recent_analyst_actions", [])
    if actions:
        action_strs = []
        for a in actions[:5]:
            t_str = f" @ ${a['target']:.0f}" if a.get("target") else ""
            action_strs.append(
                f"  {a['date']} {a['firm']}: {a['from'] or '?'} → {a['to']}{t_str}"
            )
        analyst_lines.append("Recent grade changes (14d):\n" + "\n".join(action_strs))

    if analyst_lines:
        blocks.append("ANALYST SIGNALS:\n" + "\n".join(analyst_lines))

    # ── Short interest ────────────────────────────────────────────────────────
    short_lines = []
    short_pct   = signals.get("short_pct_float")
    short_ratio = signals.get("short_ratio")
    if short_pct is not None:
        pct  = short_pct * 100
        slbl = "high" if pct > 15 else "moderate" if pct > 7 else "low"
        short_lines.append(f"{pct:.1f}% of float short ({slbl})")
    if short_ratio is not None:
        short_lines.append(f"{short_ratio:.1f} days to cover at avg volume")

    if short_lines:
        blocks.append("SHORT INTEREST:\n" + "\n".join(short_lines))

    # ── News headlines ────────────────────────────────────────────────────────
    seen: dict[str, None] = {}
    for title in signals.get("news_titles", []) + rss_headlines:
        seen[title] = None
    all_headlines = list(seen.keys())[:10]

    if all_headlines:
        headline_block = "\n".join(f"- {h}" for h in all_headlines)
        blocks.append(f"RECENT NEWS:\n{headline_block}")

    # ── Pre-fetched Reddit / RSS content ─────────────────────────────────────
    if prefetch:
        blocks.append(f"PRE-FETCHED COMMUNITY & NEWS CONTENT:\n{prefetch}")

    # ── Grok instruction ──────────────────────────────────────────────────────
    blocks.append(
        "Also search X/Twitter and live web for any breaking news, unusual social "
        "activity, or material catalysts about this ticker not captured above.\n\n"
        "Synthesize all signals — analyst positioning, market structure, and social — "
        "into a sentiment assessment. "
        "Return a JSON object with exactly these keys:\n"
        "{\n"
        '  "sentiment": "positive" | "neutral" | "negative",\n'
        '  "score": float between -1.0 and 1.0,\n'
        '  "conviction": "high" | "medium" | "low",\n'
        '  "key_themes": [list of 3 short strings],\n'
        '  "summary": "one sentence"\n'
        "}\n"
        "conviction = how aligned and consistent are the signals across all sources. "
        "high = strong multi-source agreement. low = mixed, divergent, or very thin signal. "
        "Only return the JSON. No preamble."
    )

    return "\n\n".join(blocks)


def _call_grok(ticker: str, signals: dict, rss_headlines: list[str], prefetch: str = "") -> dict | None:
    if not XAI_API_KEY:
        logger.warning("Lock 3: XAI_API_KEY not set — failing closed")
        return None

    if _circuit_open():
        logger.warning(f"Lock 3 [{ticker}]: circuit breaker open — failing fast")
        return None

    payload = {
        "model":           GROK_MODEL,
        "messages":        [{"role": "user", "content": _build_prompt(ticker, signals, rss_headlines, prefetch)}],
        "response_format": {"type": "json_object"},
        "temperature":     0.1,
    }
    headers = {
        "Authorization": f"Bearer {XAI_API_KEY}",
        "Content-Type":  "application/json",
    }

    for attempt, wait in enumerate(RETRY_WAITS, start=1):
        try:
            resp    = httpx.post(GROK_URL, headers=headers, json=payload, timeout=20)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            result  = json.loads(content)
            _record_success()
            return result

        except httpx.HTTPStatusError as e:
            logger.warning(f"Lock 3 [{ticker}] attempt {attempt}: HTTP {e.response.status_code}")
        except httpx.RequestError as e:
            logger.warning(f"Lock 3 [{ticker}] attempt {attempt}: connection error — {e}")
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Lock 3 [{ticker}] attempt {attempt}: bad response — {e}")

        if attempt < len(RETRY_WAITS):
            logger.debug(f"Lock 3 [{ticker}]: retrying in {wait}s…")
            time.sleep(wait)

    _record_failure()
    logger.error(f"Lock 3 [{ticker}]: all retries exhausted — failing closed")
    return None


# ── Public API ────────────────────────────────────────────────────────────────

def evaluate(ticker: str, sentiment_min: float | None = None) -> LockResult:
    """
    Evaluate sentiment for a ticker.

    Collects quantitative signals (analyst, short interest, news) from yfinance,
    then asks Grok to synthesize them alongside a live X/web search.

    Pass condition: score > sentiment_min AND conviction != "low".

    Args:
        ticker:        Stock ticker symbol
        sentiment_min: Override minimum sentiment score (defaults to LOCK2_SENTIMENT_MIN)

    Returns:
        LockResult with lock_id=3
    """
    effective_min = sentiment_min if sentiment_min is not None else LOCK2_SENTIMENT_MIN
    now = datetime.now(timezone.utc).timestamp()

    _cache_cleanup()

    cached = _cache.get(ticker)
    if cached and now < cached["expires_at"]:
        logger.debug(f"Lock 3 [{ticker}]: cache hit")
        grok = cached["result"]
    else:
        signals       = fetch_market_signals(ticker)
        rss_headlines = _fetch_rss(ticker)
        prefetch      = get_prefetch_content(ticker)
        logger.debug(
            f"Lock 3 [{ticker}]: {len(signals.get('news_titles', []))} yf news, "
            f"{len(rss_headlines)} RSS headlines, "
            f"prefetch={'yes' if prefetch else 'no'}, "
            f"analyst={signals.get('analyst_label')}, "
            f"short={signals.get('short_pct_float')}"
        )
        grok = _call_grok(ticker, signals, rss_headlines, prefetch)
        if grok:
            _cache[ticker] = {
                "expires_at": now + GROK_CACHE_TTL * 60,
                "result":     grok,
            }

    if grok is None:
        return LockResult.fail(
            lock_id=LOCK_ID,
            reason="Grok API unavailable — failing closed",
            data={
                "score":      None,
                "conviction": None,
                "key_themes": [],
                "summary":    None,
            },
        )

    score      = float(grok.get("score", 0.0))
    conviction = grok.get("conviction", "low")
    passed     = score > effective_min and conviction != "low"

    reason = "pass" if passed else (
        f"score {score:.2f} <= {effective_min}" if score <= effective_min
        else "conviction too low"
    )

    data = {
        "score":      score,
        "sentiment":  grok.get("sentiment"),
        "conviction": conviction,
        "key_themes": grok.get("key_themes", []),
        "summary":    grok.get("summary"),
    }

    if not passed:
        logger.debug(f"Lock 3 [{ticker}] FAIL — {reason}")
        return LockResult.fail(lock_id=LOCK_ID, reason=reason, data=data)

    logger.debug(
        f"Lock 3 [{ticker}] PASS — score={score:.2f} conviction={conviction}"
    )
    return LockResult.pass_(
        lock_id=LOCK_ID,
        score=max(0.0, min(1.0, (score + 1.0) / 2.0)),  # normalise -1..1 → 0..1 for LockResult
        reason=reason,
        data=data,
    )
