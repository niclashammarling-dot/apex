"""
Gate Runner — orchestrates Lock 1 → 2 → 3 for all signal candidates.

Flow per ticker:
  Lock 1 (quant)     → always evaluated from DB signal
  Lock 2 (Grok)      → only if Lock 1 passes
  Lock 3 (OpenAI)    → only if Lock 2 passes
  Trade execution    → wallet.py

Candidates are evaluated concurrently (ThreadPoolExecutor) to avoid stacking
Lock 2 + Lock 3 API latency across multiple tickers.
All gate results are logged to DB regardless of outcome.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from loguru import logger

from backend.gate import lock1_quant, lock_macro, lock2_sentiment, lock3_claude
from backend.db import (
    get_lock1_candidates, update_signal_gate, get_wallet_context,
    get_open_tickers, get_recently_failed_tickers,
)
from backend import wallet

# Max parallel workers — bounded to avoid hammering rate limits
_MAX_WORKERS = 4


def run() -> list[dict]:
    """
    Evaluate the gate for all current Lock-1 candidates concurrently.
    Reads thresholds from demo_config.json at call time so UI changes take effect immediately.
    Returns list of full gate result dicts.
    """
    from backend.demo_config import get_demo_config
    cfg = get_demo_config()

    candidates = get_lock1_candidates(threshold=cfg["lock1_threshold"])

    if not candidates:
        logger.info("Gate runner: no Lock 1 candidates this cycle")
        return []

    # Skip tickers already held or in cooloff
    open_tickers   = get_open_tickers()
    failed_tickers = get_recently_failed_tickers(cfg.get("gate_cooloff_hours", 4))
    skip = open_tickers | failed_tickers
    if skip:
        before = len(candidates)
        candidates = [c for c in candidates if c["ticker"] not in skip]
        logger.info(f"Gate runner: skipped {before - len(candidates)} ticker(s) "
                    f"(open={open_tickers & {c['ticker'] for c in candidates[:before]}}, cooloff={failed_tickers})")

    if not candidates:
        logger.info("Gate runner: all candidates skipped (open positions / cooloff)")
        return []

    logger.info(f"Gate runner: {len(candidates)} candidate(s) — {[c['ticker'] for c in candidates]}")

    wallet_ctx = get_wallet_context()
    results    = []

    # Evaluate all candidates in parallel, then execute trades sequentially
    # (trade execution must be serial to prevent race conditions on position limits)
    evaluated: list[tuple[dict, dict]] = []  # (signal, result)

    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(candidates))) as pool:
        future_to_signal = {
            pool.submit(_evaluate, signal, wallet_ctx, cfg): signal
            for signal in candidates
        }
        for future in as_completed(future_to_signal):
            signal = future_to_signal[future]
            try:
                result = future.result()
            except Exception as e:
                logger.error(f"Gate runner [{signal['ticker']}]: evaluation raised — {e}")
                continue
            evaluated.append((signal, result))

    for signal, result in evaluated:
        ticker = signal["ticker"]
        if result["outcome"] == "TRADE_QUEUED":
            trade = wallet.execute_trade(result, signal["price"])
            result["outcome"] = "TRADE_EXECUTED" if trade else "TRADE_REJECTED"
            if result["outcome"] == "TRADE_REJECTED":
                result["gate_decision"] = "TRADE_REJECTED"

        results.append(result)
        update_signal_gate(signal["id"], result)
        _log_summary(ticker, result)

    return results


def _evaluate(signal: dict, wallet_ctx: dict, cfg: dict) -> dict:
    ticker = signal["ticker"]

    l1 = lock1_quant.evaluate(signal, threshold=cfg["lock1_threshold"])

    if not l1["passed"]:
        return _gate_result(signal, l1, None, None, "FILTERED_L1")

    lm = lock_macro.evaluate(ticker, cfg)
    if not lm["passed"]:
        return _gate_result(signal, l1, None, None, "FILTERED_MACRO")

    l2 = lock2_sentiment.evaluate(ticker, sentiment_min=cfg["lock2_sentiment_min"])
    if not l2["passed"]:
        return _gate_result(signal, l1, l2, None, "FILTERED_L2")

    context = _build_claude_context(signal, l2, wallet_ctx)
    l3 = lock3_claude.evaluate(context, confidence_min=cfg["lock3_confidence_min"])

    outcome = "TRADE_QUEUED" if l3["passed"] else "FILTERED_L3"
    return _gate_result(signal, l1, l2, l3, outcome)


def _build_claude_context(signal: dict, l2: dict, wallet_ctx: dict) -> dict:
    return {
        # identity
        "ticker":            signal["ticker"],
        "sector":            signal["sector"],
        # L2 sentiment context (all that Lock 3 should see from upstream)
        "sentiment_score":   l2["score"],
        "sentiment_volume":  l2["volume"],
        "sentiment_themes":  l2["key_themes"],
        "sentiment_summary": l2["summary"],
        # portfolio context (Lock 3's unique domain)
        "wallet_balance":    wallet_ctx["balance"],
        "open_positions":    wallet_ctx["open_positions"],
        "sector_exposure":   wallet_ctx["sector_exposure"],
    }


def _gate_result(signal: dict, l1: dict, l2: dict | None, l3: dict | None, outcome: str) -> dict:
    return {
        "ticker":    signal["ticker"],
        "sector":    signal["sector"],
        "signal_id": signal["id"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "outcome":   outcome,
        "lock1":     l1,
        "lock2":     l2,
        "lock3":     l3,
        # Flattened for DB update
        "lock1_pass": int(l1["passed"]),
        "lock2_pass": int(l2["passed"]) if l2 else 0,
        "lock3_pass": int(l3["passed"]) if l3 else 0,
        "gate_decision": l3["decision"] if l3 else ("L2_FAIL" if l2 else "L1_FAIL"),
        "claude_confidence": l3["confidence"] if l3 else None,
        "claude_reasoning":  l3["reasoning"]  if l3 else None,
        "sentiment_score":   l2["score"]      if l2 else None,
    }


def _log_summary(ticker: str, result: dict) -> None:
    l1 = result["lock1"]
    l2 = result.get("lock2")
    l3 = result.get("lock3")
    outcome = result["outcome"]

    parts = [f"L1={'✓' if l1['passed'] else '✗'}({l1['score']:.3f})"]
    if l2:
        parts.append(f"L2={'✓' if l2['passed'] else '✗'}({l2.get('score', '?')})")
    if l3:
        parts.append(f"L3={'✓' if l3['passed'] else '✗'}({l3.get('decision')} {l3.get('confidence', 0):.2f})")

    logger.info(f"Gate [{ticker}]: {' → '.join(parts)} → {outcome}")
