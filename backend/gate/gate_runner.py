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

import json
from backend.gate import lock1_quant, lock_macro, lock2_sentiment, lock_leading, lock3_claude
from backend.db import (
    get_lock1_candidates, update_signal_gate, get_wallet_context,
    get_open_tickers, get_recently_failed_tickers,
)
from backend import wallet

# Max parallel workers — bounded to avoid hammering rate limits
_MAX_WORKERS = 4

# Pre-rotation promotion: tickers in "watching" sectors are eligible for gate
# evaluation at this fraction of their normal L1 threshold, catching setups that
# are building momentum before they clear the standard signal filter.
PRE_ROTATION_FLOOR = 0.85


def run() -> list[dict]:
    """
    Evaluate the gate for all current Lock-1 candidates concurrently.
    Reads thresholds from demo_config.json at call time so UI changes take effect immediately.
    Returns list of full gate result dicts.
    """
    from backend.demo_config import get_demo_config
    from backend.db import get_ticker_thresholds
    cfg = get_demo_config()

    sector_thresholds = get_ticker_thresholds()
    candidates = get_lock1_candidates(threshold=cfg["lock1_threshold"],
                                      sector_thresholds=sector_thresholds)

    if not candidates:
        logger.info("Gate runner: no Lock 1 candidates this cycle")
        return []

    # Skip tickers already held or in cooloff — mark them in DB so funnel is complete
    open_tickers   = get_open_tickers()
    failed_tickers = get_recently_failed_tickers(cfg.get("gate_cooloff_hours", 4))
    skipped = [c for c in candidates if c["ticker"] in (open_tickers | failed_tickers)]
    candidates = [c for c in candidates if c["ticker"] not in (open_tickers | failed_tickers)]

    for c in skipped:
        decision = "SKIPPED_OPEN" if c["ticker"] in open_tickers else "SKIPPED_COOLOFF"
        update_signal_gate(c["id"], {
            "lock1_pass": 1, "lock2_pass": 0, "lock3_pass": 0,
            "gate_decision": decision, "lock3_reasoning": None,
        })

    if skipped:
        logger.info(f"Gate runner: skipped {len(skipped)} ticker(s) "
                    f"(open={len(open_tickers & {c['ticker'] for c in skipped})}, "
                    f"cooloff={len(failed_tickers & {c['ticker'] for c in skipped})})")

    if not candidates:
        logger.info("Gate runner: all candidates skipped (open positions / cooloff)")
        return []

    logger.info(f"Gate runner: {len(candidates)} candidate(s) — {[c['ticker'] for c in candidates]}")

    wallet_ctx = get_wallet_context()
    results    = []

    # Compute dynamic sector caps + regime + rotation scores once per gate cycle
    from backend.sector_caps import compute_dynamic_caps
    from backend.sector_regime import compute_sector_regime
    from backend.sector_transitions import compute_ticker_rotation_scores, get_rotation_forecast
    from backend.config import MAX_SECTOR_EXPOSURE
    dynamic_caps    = compute_dynamic_caps(cfg.get("max_sector_exposure", MAX_SECTOR_EXPOSURE))
    sector_regime   = compute_sector_regime()
    rotation_scores = compute_ticker_rotation_scores()

    # Pre-rotation promotion — pull tickers from "watching" sectors at a discounted
    # L1 threshold so early-stage setups enter the pipeline before they clear the
    # standard score filter.
    forecast         = get_rotation_forecast()
    watching_sectors = {w["sector"] for w in forecast.get("watching", [])}
    if watching_sectors:
        pr_sector_thresholds = {
            s: (sector_thresholds or {}).get(s, cfg["lock1_threshold"]) * PRE_ROTATION_FLOOR
            for s in watching_sectors
        }
        pr_candidates = get_lock1_candidates(
            threshold=cfg["lock1_threshold"] * PRE_ROTATION_FLOOR,
            sector_thresholds=pr_sector_thresholds,
        )
        existing = {c["ticker"] for c in candidates}
        for c in pr_candidates:
            if (c.get("sector") in watching_sectors
                    and c["ticker"] not in existing
                    and c["ticker"] not in open_tickers
                    and c["ticker"] not in failed_tickers):
                c = dict(c)
                c["pre_rotation"] = True
                candidates.append(c)
                existing.add(c["ticker"])
        pr_count = sum(1 for c in candidates if c.get("pre_rotation"))
        if pr_count:
            logger.info(f"Gate runner: +{pr_count} pre-rotation candidate(s) from {sorted(watching_sectors)}")

    # Evaluate all candidates in parallel, then execute trades sequentially
    # (trade execution must be serial to prevent race conditions on position limits)
    evaluated: list[tuple[dict, dict]] = []  # (signal, result)

    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(candidates))) as pool:
        future_to_signal = {
            pool.submit(_evaluate, signal, wallet_ctx, cfg, sector_regime, sector_thresholds, rotation_scores): signal
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
            trade = wallet.execute_trade(result, signal["price"], dynamic_caps=dynamic_caps)
            result["outcome"] = "TRADE_EXECUTED" if trade else "TRADE_REJECTED"
            if result["outcome"] == "TRADE_REJECTED":
                result["gate_decision"] = "TRADE_REJECTED"

        results.append(result)
        update_signal_gate(signal["id"], result)
        _log_summary(ticker, result)

    return results


def _evaluate(signal: dict, wallet_ctx: dict, cfg: dict,
              sector_regime: dict | None = None,
              sector_thresholds: dict | None = None,
              rotation_scores: dict | None = None) -> dict:
    ticker = signal["ticker"]

    l1_threshold = (sector_thresholds or {}).get(signal.get("sector", ""), cfg["lock1_threshold"])
    if signal.get("pre_rotation"):
        l1_threshold = l1_threshold * PRE_ROTATION_FLOOR
    l1 = lock1_quant.evaluate(signal, threshold=l1_threshold)

    if not l1["passed"]:
        return _gate_result(signal, l1, None, None, None, "FILTERED_L1")

    lm = lock_macro.evaluate(ticker, cfg)
    if not lm["passed"]:
        return _gate_result(signal, l1, None, None, None, "FILTERED_MACRO")

    l2 = lock2_sentiment.evaluate(ticker, sentiment_min=cfg["lock2_sentiment_min"])
    if not l2["passed"]:
        return _gate_result(signal, l1, l2, None, None, "FILTERED_L2")

    l_leading = lock_leading.evaluate(ticker, signal.get("sector", ""),
                                      min_pass=cfg.get("lock_leading_min_pass", 2))
    if not l_leading["passed"]:
        return _gate_result(signal, l1, l2, l_leading, None, "FILTERED_LEADING")

    context = _build_claude_context(signal, l2, l_leading, wallet_ctx, cfg, sector_regime, rotation_scores)
    l3 = lock3_claude.evaluate(context, confidence_min=cfg["lock3_confidence_min"])

    outcome = "TRADE_QUEUED" if l3["passed"] else "FILTERED_L3"
    return _gate_result(signal, l1, l2, l_leading, l3, outcome)


def _build_claude_context(signal: dict, l2: dict, l_leading: dict,
                          wallet_ctx: dict, cfg: dict,
                          sector_regime: dict | None = None,
                          rotation_scores: dict | None = None) -> dict:
    from backend.config import STARTING_BALANCE
    ctx = {
        # identity
        "ticker":            signal["ticker"],
        "sector":            signal["sector"],
        # L2 sentiment context
        "sentiment_score":   l2["score"],
        "sentiment_volume":  l2["volume"],
        "sentiment_themes":  l2["key_themes"],
        "sentiment_summary": l2["summary"],
        # leading indicators context
        "leading_pass_count":     l_leading["pass_count"],
        "leading_checks":         {k: v["pass"] for k, v in l_leading["checks"].items()},
        # portfolio state
        "wallet_balance":    wallet_ctx["balance"],
        "open_positions":    wallet_ctx["open_positions"],
        "sector_exposure":   wallet_ctx["sector_exposure"],
        # configured risk limits — model uses these for its checks
        "risk_limits": {
            "starting_balance":    STARTING_BALANCE,
            "max_positions":       cfg["max_positions"],
            "max_sector_exposure": cfg["max_sector_exposure"],
            "max_position_size":   cfg["max_position_size"],
            "daily_loss_cap":      cfg["daily_loss_cap"],
        },
    }

    # Sector regime context — gives Lock 3 macro cycle awareness
    if sector_regime and sector_regime.get("available"):
        sector_detail = sector_regime.get("sectors", {}).get(signal["sector"], {})
        ctx.update({
            "market_regime":       sector_regime["regime"],               # risk_on/off/neutral
            "regime_confidence":   sector_regime.get("regime_confidence"), # 0–1 conviction score
            "sector_signal":       sector_detail.get("signal"),            # breakout/trending/extended/weak
            "sector_streak_days":  sector_detail.get("streak_days"),       # duration of current trend
            "market_leader":       sector_regime.get("leader"),            # currently strongest sector
            "sector_velocity":     sector_detail.get("velocity"),          # accelerating/decelerating/flat
            "sector_velocity_5d":  sector_detail.get("velocity_5d"),       # raw 5d score delta
            "sector_accel":        sector_detail.get("accel"),             # change in 5d velocity
        })

    # Rotation forecast — gives Lock 3 transition probability awareness
    try:
        from backend.sector_transitions import get_rotation_forecast
        forecast = get_rotation_forecast()
        if forecast.get("available"):
            # Is this ticker's sector the predicted next rotation target?
            next_sectors    = {item["sector"] for item in forecast.get("likely_next", [])}
            next_prob       = next((item["probability"] for item in forecast.get("likely_next", [])
                                    if item["sector"] == signal["sector"]), None)
            confirmed       = forecast.get("confirmed_transition")
            ctx.update({
                "rotation_leader":           forecast["leader"],
                "rotation_predecessor":      forecast.get("predecessor"),
                "sector_next_probability":   next_prob,                        # None if sector not predicted next
                "rotation_confirmed":        confirmed is not None,
                "rotation_transition_prob":  confirmed["probability"] if confirmed else None,
                "rotation_regime_conditioned": forecast.get("regime_conditioned"),  # True = regime-specific matrix
                "rotation_regime_sample_size": forecast.get("regime_sample_size"),  # transitions in this regime
            })
    except Exception:
        pass

    # Rotation score — pre-computed once per gate cycle, passed in from run()
    if rotation_scores is not None:
        ctx["ticker_rotation_score"] = rotation_scores.get(signal["ticker"])

    return ctx


def _gate_result(signal: dict, l1: dict, l2: dict | None,
                 l_leading: dict | None, l3: dict | None, outcome: str) -> dict:
    return {
        "ticker":       signal["ticker"],
        "sector":       signal["sector"],
        "signal_id":    signal["id"],
        "pre_rotation": signal.get("pre_rotation", False),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "outcome":   outcome,
        "lock1":     l1,
        "lock2":     l2,
        "lock_leading": l_leading,
        "lock3":     l3,
        # Flattened for DB update
        "lock1_pass":          int(l1["passed"]),
        "lock2_pass":          int(l2["passed"])       if l2        else 0,
        "lock_leading_pass":   int(l_leading["passed"]) if l_leading else 0,
        "lock_leading_checks": json.dumps(l_leading["checks"]) if l_leading else None,
        "lock3_pass":          int(l3["passed"])       if l3        else 0,
        "gate_decision":       outcome if outcome != "TRADE_QUEUED" else "TRADE_EXECUTED",
        "claude_confidence":   l3["confidence"] if l3 else None,
        "claude_reasoning":    l3["reasoning"]  if l3 else None,
        "sentiment_score":     l2["score"]      if l2 else None,
    }


def _log_summary(ticker: str, result: dict) -> None:
    l1        = result["lock1"]
    l2        = result.get("lock2")
    l_leading = result.get("lock_leading")
    l3        = result.get("lock3")
    outcome   = result["outcome"]

    parts = [f"L1={'✓' if l1['passed'] else '✗'}({l1['score']:.3f})"]
    if l2:
        parts.append(f"L2={'✓' if l2['passed'] else '✗'}({l2.get('score', '?')})")
    if l_leading:
        parts.append(f"LD={'✓' if l_leading['passed'] else '✗'}({l_leading['pass_count']}/{l_leading['min_pass']})")
    if l3:
        parts.append(f"L3={'✓' if l3['passed'] else '✗'}({l3.get('decision')} {l3.get('confidence', 0):.2f})")

    logger.info(f"Gate [{ticker}]: {' → '.join(parts)} → {outcome}")
