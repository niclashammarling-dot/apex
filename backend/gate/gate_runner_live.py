"""
Live Gate Runner — same 3-lock pipeline as gate_runner.py but:
  - Uses LIVE_* thresholds from config (stricter than demo)
  - On TRADE_QUEUED, places a real Alpaca bracket order
  - Stores gate results in live_gate_history (not the demo signals table)
  - Only runs when LIVE_ENABLED=true

Bracket orders set TP + SL at Alpaca order time, so no separate exit
checker is needed — Alpaca manages the position lifecycle natively.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from loguru import logger

from backend.gate import lock1_quant, lock2_sentiment, lock3_claude
from backend.db import get_lock1_candidates, insert_live_gate_result, get_live_gate_history, insert_live_trade
from backend.config import LIVE_ENABLED, LIVE_MAX_SECTOR_EXPOSURE

_MAX_WORKERS = 4


def run() -> list[dict]:
    """
    Evaluate the live gate for all LIVE_LOCK1 candidates.
    Does nothing (returns []) when LIVE_ENABLED is false.
    Reads thresholds from live_config.json at call time so Promote takes effect immediately.
    """
    if not LIVE_ENABLED:
        logger.debug("Live gate: LIVE_ENABLED=false — skipping")
        return []

    from backend.brokers import alpaca as broker
    from backend.live_config import get_live_config
    cfg = get_live_config()

    # Pre-flight: verify account is tradeable before wasting LLM calls
    try:
        acct = broker.get_account()
        if acct["trading_blocked"] or acct["account_blocked"]:
            logger.warning("Live gate: Alpaca account is blocked — skipping cycle")
            return []
    except Exception as e:
        logger.error(f"Live gate: could not reach Alpaca — {e}")
        return []

    # Daily loss cap check
    if _daily_loss_exceeded(acct["equity"], cfg["daily_loss_cap"]):
        logger.warning("Live gate: daily loss cap hit — skipping cycle")
        from backend.alerts import alert_daily_loss_cap
        day_loss = abs(min(acct["day_pnl"], 0))
        alert_daily_loss_cap(day_loss, cfg["daily_loss_cap"])
        return []

    candidates = get_lock1_candidates(threshold=cfg["lock1_threshold"])
    if not candidates:
        logger.info("Live gate runner: no Lock 1 candidates this cycle")
        return []

    logger.info(f"Live gate runner: {len(candidates)} candidate(s) — {[c['ticker'] for c in candidates]}")

    wallet_ctx = {
        "balance":         acct["equity"],
        "open_positions":  len(broker.get_positions()),
        "sector_exposure": {},  # not computed for live — Lock 3 prompt uses open_positions count
    }

    evaluated: list[tuple[dict, dict]] = []

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
                logger.error(f"Live gate [{signal['ticker']}]: evaluation raised — {e}")
                continue
            evaluated.append((signal, result))

    results = []
    # Execute trades serially to prevent race conditions on position limits
    open_positions = broker.get_positions()
    open_tickers   = {p["ticker"] for p in open_positions}

    for signal, result in evaluated:
        ticker     = signal["ticker"]
        order_id   = None

        if result["outcome"] == "TRADE_QUEUED":
            if len(open_positions) >= cfg["max_positions"]:
                logger.warning(f"Live trade rejected [{ticker}]: max positions ({cfg['max_positions']}) reached")
                result["outcome"] = "TRADE_REJECTED"
            elif ticker in open_tickers:
                logger.info(f"Live trade skipped [{ticker}]: position already open")
                result["outcome"] = "TRADE_REJECTED"
            else:
                position_pct = result["lock3"]["position_size_pct"] if result.get("lock3") else cfg["max_position_size"]
                notional     = acct["buying_power"] * min(position_pct, cfg["max_position_size"])
                if notional < 10:
                    logger.warning(f"Live trade rejected [{ticker}]: notional too small (${notional:.2f})")
                    result["outcome"] = "TRADE_REJECTED"
                else:
                    try:
                        order_id = broker.place_bracket_order(
                            ticker          = ticker,
                            notional        = notional,
                            current_price   = signal["price"],
                            take_profit_pct = cfg["take_profit_pct"],
                            stop_loss_pct   = cfg["stop_loss_pct"],
                        )
                        result["outcome"] = "TRADE_EXECUTED"
                        open_tickers.add(ticker)
                        _record_live_trade(signal, notional, order_id, cfg)
                        _fire_trade_alert(ticker, signal, notional, order_id)
                    except Exception as e:
                        logger.error(f"Live trade failed [{ticker}]: {e}")
                        result["outcome"] = "TRADE_FAILED"

        insert_live_gate_result({
            "timestamp":       result["timestamp"],
            "ticker":          ticker,
            "sector":          signal["sector"],
            "signal_score":    signal["signal_score"],
            "lock1_pass":      result["lock1_pass"],
            "lock2_pass":      result["lock2_pass"],
            "lock3_pass":      result["lock3_pass"],
            "gate_decision":   result["gate_decision"],
            "lock3_reasoning": result.get("claude_reasoning"),
            "alpaca_order_id": order_id,
        })

        _log_summary(ticker, result)
        results.append(result)

    return results


def _evaluate(signal: dict, wallet_ctx: dict, cfg: dict) -> dict:
    ticker = signal["ticker"]

    l1 = lock1_quant.evaluate(signal, threshold=cfg["lock1_threshold"])
    if not l1["passed"]:
        return _gate_result(signal, l1, None, None, "FILTERED_L1")

    l2 = lock2_sentiment.evaluate(ticker, sentiment_min=cfg["lock2_sentiment_min"])
    if not l2["passed"]:
        return _gate_result(signal, l1, l2, None, "FILTERED_L2")

    context = _build_context(signal, l2, wallet_ctx)
    l3 = lock3_claude.evaluate(context, confidence_min=cfg["lock3_confidence_min"])

    outcome = "TRADE_QUEUED" if l3["passed"] else "FILTERED_L3"
    return _gate_result(signal, l1, l2, l3, outcome)


def _build_context(signal: dict, l2: dict, wallet_ctx: dict) -> dict:
    price    = signal["price"]
    high_60d = signal.get("high_60d")
    low_60d  = signal.get("low_60d")
    return {
        "mode":             "LIVE — real money",
        "ticker":           signal["ticker"],
        "sector":           signal["sector"],
        "signal_score":     signal["signal_score"],
        "momentum_score":   signal["momentum_score"],
        "volume_ratio":     signal["volume_ratio"],
        "rsi":              signal["rsi"],
        "ev":               signal["ev"],
        "kelly_size":       signal["kelly_size"],
        "effective_sl":     signal.get("effective_sl"),
        "atr_pct":          signal.get("atr_pct"),
        "price":            price,
        "ma20":             signal.get("ma20"),
        "high_60d":         high_60d,
        "low_60d":          low_60d,
        "pct_from_60d_high": round((price - high_60d) / high_60d, 4) if high_60d else None,
        "pct_from_60d_low":  round((price - low_60d)  / low_60d,  4) if low_60d  else None,
        "sentiment_score":  l2["score"],
        "sentiment_volume": l2["volume"],
        "sentiment_themes": l2["key_themes"],
        "sentiment_summary": l2["summary"],
        "wallet_balance":   wallet_ctx["balance"],
        "open_positions":   wallet_ctx["open_positions"],
        "sector_exposure":  wallet_ctx["sector_exposure"],
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
        "lock1_pass": int(l1["passed"]),
        "lock2_pass": int(l2["passed"]) if l2 else 0,
        "lock3_pass": int(l3["passed"]) if l3 else 0,
        "gate_decision": l3["decision"] if l3 else ("L2_FAIL" if l2 else "L1_FAIL"),
        "claude_confidence": l3["confidence"] if l3 else None,
        "claude_reasoning":  l3["reasoning"]  if l3 else None,
        "sentiment_score":   l2["score"]      if l2 else None,
    }


def _daily_loss_exceeded(current_equity: float, cap: float) -> bool:
    """
    Rough daily loss check: compare Alpaca's day P&L against LIVE_DAILY_LOSS_CAP.
    Returns True if losses today already exceed the cap.
    """
    from backend.brokers import alpaca as broker
    try:
        acct     = broker.get_account()
        day_loss = abs(min(acct["day_pnl"], 0))
        if day_loss >= cap:
            logger.warning(f"Live gate: day loss ${day_loss:.2f} >= cap ${cap:.2f}")
            return True
    except Exception:
        pass
    return False


def _record_live_trade(signal: dict, notional: float, order_id: str, cfg: dict) -> None:
    try:
        from datetime import datetime, timezone
        price = signal["price"]
        qty   = round(notional / price, 6)
        insert_live_trade({
            "timestamp":       datetime.now(timezone.utc).isoformat(),
            "ticker":          signal["ticker"],
            "sector":          signal["sector"],
            "alpaca_order_id": order_id,
            "entry_price":     price,
            "qty":             qty,
            "notional":        round(notional, 2),
            "tp_price":        round(price * (1 + cfg["take_profit_pct"]), 2),
            "sl_price":        round(price * (1 - cfg["stop_loss_pct"]), 2),
        })
    except Exception as e:
        logger.warning(f"Failed to record live trade [{signal['ticker']}]: {e}")


def _fire_trade_alert(ticker: str, signal: dict, notional: float, order_id: str) -> None:
    try:
        from backend.alerts import alert_trade_executed
        price = signal["price"]
        alert_trade_executed(
            ticker    = ticker,
            sector    = signal["sector"],
            notional  = notional,
            price     = price,
            tp        = round(price * (1 + LIVE_TAKE_PROFIT_PCT), 2),
            sl        = round(price * (1 - LIVE_STOP_LOSS_PCT), 2),
            order_id  = order_id,
        )
    except Exception as e:
        logger.warning(f"Alert dispatch failed [{ticker}]: {e}")


def _log_summary(ticker: str, result: dict) -> None:
    l1      = result["lock1"]
    l2      = result.get("lock2")
    l3      = result.get("lock3")
    outcome = result["outcome"]

    parts = [f"L1={'✓' if l1['passed'] else '✗'}({l1['score']:.3f})"]
    if l2:
        parts.append(f"L2={'✓' if l2['passed'] else '✗'}({l2.get('score', '?')})")
    if l3:
        parts.append(f"L3={'✓' if l3['passed'] else '✗'}({l3.get('decision')} {l3.get('confidence', 0):.2f})")

    logger.info(f"Live gate [{ticker}]: {' → '.join(parts)} → {outcome}")
