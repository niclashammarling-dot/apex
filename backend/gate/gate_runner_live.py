"""
Live Gate Runner — same 5-lock chain as gate_runner.py but:
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

from backend.config import LIVE_ENABLED
from backend.ticker_config import get_sectors
from backend.db import (
    get_live_ticker_gate_fails,
    get_lock1_candidates,
    get_open_live_tickers,
    get_recently_exited_live_tickers,
    get_recently_failed_live_tickers,
    insert_live_gate_result,
    insert_live_trade,
)
from backend.gate.chain import build_base_context, evaluate_chain
from backend.gate.gate_runner import (
    PRE_ROTATION_FLOOR,
    _chain_to_gate_result,
    _compute_bayesian_multipliers,
    _persist_multiplier_stats,
    _run_l5_for_result,
)
from backend.sector_caps import compute_dynamic_caps

_MAX_WORKERS = 5
_loss_cap_alerted: set[str] = set()  # dates (YYYY-MM-DD) for which the alert was already sent


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
    from backend.db import get_ticker_thresholds
    from backend.live_config import get_live_config
    cfg = get_live_config()
    sector_thresholds = get_ticker_thresholds()

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
    day_loss = abs(min(acct["day_pnl"], 0))
    if day_loss >= cfg["daily_loss_cap"]:
        from datetime import date
        today = date.today().isoformat()
        logger.warning(f"Live gate: daily loss cap hit — day loss ${day_loss:.2f} >= cap ${cfg['daily_loss_cap']:.2f}")
        if today not in _loss_cap_alerted:
            _loss_cap_alerted.add(today)
            from backend.alerts import alert_daily_loss_cap
            alert_daily_loss_cap(day_loss, cfg["daily_loss_cap"])
        return []

    candidates = get_lock1_candidates(threshold=cfg["lock1_threshold"],
                                      sector_thresholds=sector_thresholds)
    if not candidates:
        logger.info("Live gate runner: no Lock 1 candidates this cycle")
        _persist_multiplier_stats({}, None, [], runner="live")
        return []

    # Skip tickers already held, in gate cooloff, or in exit cooloff
    open_tickers   = get_open_live_tickers()
    failed_tickers = get_recently_failed_live_tickers(cfg.get("gate_cooloff_hours", 4))
    exited_tickers = get_recently_exited_live_tickers(
        sl_hours=cfg.get("exit_cooloff_hours", 24),
        tp_hours=cfg.get("tp_cooloff_hours", 168),
    )
    blocked = open_tickers | failed_tickers | exited_tickers
    skipped = [c for c in candidates if c["ticker"] in blocked]
    candidates = [c for c in candidates if c["ticker"] not in blocked]

    ts = datetime.now(timezone.utc).isoformat()
    for c in skipped:
        decision = "SKIPPED_OPEN" if c["ticker"] in open_tickers else "SKIPPED_COOLOFF"
        insert_live_gate_result({
            "timestamp":             ts,
            "ticker":                c["ticker"],
            "sector":                c.get("sector", ""),
            "signal_score":          c["signal_score"],
            "lock1_pass":            1,
            "lock2_pass":            0,
            "lock_leading_pass":     0,
            "lock_leading_checks":   None,
            "lock3_pass":            0,
            "gate_decision":         decision,
            "lock3_reasoning":       None,
            "alpaca_order_id":       None,
            "l2_summary":            None,
            "lock3_sentiment_score": None,
            "lock3_conviction":      None,
            "macro_reason":          None,
            "ticker_signal":         None,
            "earnings_near":         None,
            "days_to_earnings":      None,
        })

    if skipped:
        skipped_set = {c["ticker"] for c in skipped}
        logger.info(f"Live gate runner: skipped {len(skipped)} ticker(s) "
                    f"(open={len(open_tickers & skipped_set)}, "
                    f"cooloff={len(failed_tickers & skipped_set)}, "
                    f"exit_cooloff={len(exited_tickers & skipped_set)})")

    if not candidates:
        logger.info("Live gate runner: all candidates skipped (open positions / cooloff)")
        _persist_multiplier_stats({}, None, [], runner="live")
        return []

    from backend.config import EXCLUDED_SECTORS
    candidates = [c for c in candidates if c.get("sector", "") not in EXCLUDED_SECTORS]
    if not candidates:
        logger.info("Live gate runner: all candidates in excluded sectors")
        _persist_multiplier_stats({}, None, [], runner="live")
        return []

    logger.info(f"Live gate runner: {len(candidates)} candidate(s) — {[c['ticker'] for c in candidates]}")

    _ticker_sector = {t: s for s, data in get_sectors().items() for t in data["tickers"]}
    _positions = broker.get_positions()
    # Exposure ratios: all numerators and denominators must use equity scale.
    # Do not substitute buying_power here — on margin accounts it is 2-4x equity
    # and will inflate projected exposure past every sector cap.
    _starting_balance = cfg["starting_balance"]
    _sector_exposure: dict[str, float] = {}
    for _p in _positions:
        _sec = _ticker_sector.get(_p["ticker"], "")
        if _sec:
            _sector_exposure[_sec] = round(
                _sector_exposure.get(_sec, 0.0) + _p["cost_basis"] / _starting_balance, 3
            )

    wallet_ctx = {
        "balance":          acct["equity"],
        "open_positions":   len(_positions),
        "sector_exposure":  _sector_exposure,
        "starting_balance": _starting_balance,
    }

    # Compute sector regime + rotation scores + Bayesian regime once per gate cycle
    from backend.scheduler import _get_regime_bayes
    from backend.sector_regime import compute_sector_regime, compute_ticker_signals
    from backend.sector_transitions import (
        compute_ticker_rotation_scores,
        get_rotation_forecast,
    )
    sector_regime       = compute_sector_regime()
    rotation_scores     = compute_ticker_rotation_scores()
    ticker_signals      = compute_ticker_signals()
    regime_bayes_result = _get_regime_bayes().last_result()

    sector_exposure_live: dict[str, float] = dict(_sector_exposure)
    dynamic_caps_live    = compute_dynamic_caps(cfg.get("max_sector_exposure", 0.30),
                                                regime_result=regime_bayes_result)

    # Pre-rotation promotion — same logic as demo gate
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
            logger.info(f"Live gate: +{pr_count} pre-rotation candidate(s) from {sorted(watching_sectors)}")

    evaluated: list[tuple[dict, dict]] = []

    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(candidates))) as pool:
        future_to_signal = {
            pool.submit(_evaluate, signal, wallet_ctx, cfg, sector_regime, sector_thresholds,
                        rotation_scores, regime_bayes_result, ticker_signals): signal
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

    evaluated.sort(key=lambda x: (0 if x[0].get("pre_rotation") else 1, x[0].get("signal_score", 0)), reverse=True)
    max_positions        = cfg["max_positions"]
    overflow_increment   = cfg.get("overflow_quant_increment", 0.05)
    open_count           = len(open_positions)  # snapshot; incremented on each executed trade
    bayesian_multipliers = _compute_bayesian_multipliers(evaluated, regime_bayes_result)

    for signal, result in evaluated:
        ticker   = signal["ticker"]
        order_id = None

        # L5 deferred path — run Claude only when a slot is confirmed available.
        if result["outcome"] == "TRADE_QUEUED_PENDING_L5":
            sector         = signal.get("sector", "")
            base_threshold = (sector_thresholds or {}).get(sector, cfg["lock1_threshold"])
            if ticker in open_tickers:
                logger.info(f"Live trade skipped [{ticker}]: position already open (pre-L5)")
                result["outcome"] = "TRADE_REJECTED"
                result.pop("_l5_lock_results", None)
                result.pop("_l5_context", None)
            elif open_count >= max_positions:
                overflow_level     = open_count - max_positions + 1
                overflow_threshold = round(base_threshold * (1 + overflow_level * overflow_increment), 4)
                if signal["signal_score"] < overflow_threshold:
                    logger.warning(
                        f"Live gate [{ticker}]: overflow slot {overflow_level} rejected pre-L5 — "
                        f"score {signal['signal_score']:.4f} below {overflow_threshold:.4f}"
                    )
                    result["outcome"] = "FILTERED_OVERFLOW_QUANT"
                    result.pop("_l5_lock_results", None)
                    result.pop("_l5_context", None)
                else:
                    _run_l5_for_result(signal, result, cfg)
            else:
                _run_l5_for_result(signal, result, cfg)

        # Apply Bayesian sector sizing before notional is computed.
        if result["outcome"] == "TRADE_QUEUED" and result.get("lock3"):
            _scale = bayesian_multipliers.get(ticker, 1.0)
            if _scale != 1.0:
                _raw  = result["lock3"]["position_size_pct"]
                result["lock3"]["position_size_pct"] = min(
                    round(_raw * _scale, 4),
                    cfg.get("max_position_size", 0.10),
                )
                logger.info(
                    f"Live gate [{ticker}]: Bayesian size scale={_scale:.3f} "
                    f"({_raw:.4f} → {result['lock3']['position_size_pct']:.4f})"
                )

        if result["outcome"] == "TRADE_QUEUED":
            if ticker in open_tickers:
                logger.info(f"Live trade skipped [{ticker}]: position already open")
                result["outcome"] = "TRADE_REJECTED"
            elif open_count >= max_positions:
                # Overflow slot — check escalating quant threshold
                sector             = signal.get("sector", "")
                base_threshold     = (sector_thresholds or {}).get(sector, cfg["lock1_threshold"])
                overflow_level     = open_count - max_positions + 1
                overflow_threshold = round(base_threshold * (1 + overflow_level * overflow_increment), 4)
                if signal["signal_score"] < overflow_threshold:
                    logger.warning(
                        f"Live gate [{ticker}]: overflow slot {overflow_level} rejected — "
                        f"score {signal['signal_score']:.4f} below {overflow_threshold:.4f} "
                        f"(base {base_threshold:.4f} ×{1 + overflow_level * overflow_increment:.2f})"
                    )
                    result["outcome"] = "FILTERED_OVERFLOW_QUANT"
                else:
                    position_pct = result["lock3"]["position_size_pct"] if result.get("lock3") else cfg["max_position_size"]
                    notional     = acct["equity"] * min(position_pct, cfg["max_position_size"])
                    if notional < 10:
                        logger.warning(f"Live trade rejected [{ticker}]: notional too small (${notional:.2f})")
                        result["outcome"] = "TRADE_REJECTED"
                    else:
                        sector    = signal.get("sector", "")
                        sec_cap   = dynamic_caps_live.get(sector, cfg.get("max_sector_exposure", 0.30))
                        projected = round(sector_exposure_live.get(sector, 0.0) + notional / _starting_balance, 3)
                        if projected > sec_cap:
                            logger.warning(
                                f"Live trade rejected [{ticker}]: {sector} sector cap — "
                                f"projected {projected:.0%} > cap {sec_cap:.0%}"
                            )
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
                                open_count += 1
                                sector_exposure_live[sector] = projected
                                _record_live_trade(signal, notional, order_id, cfg)
                                _fire_trade_alert(ticker, signal, notional, order_id, cfg)
                                logger.info(
                                    f"Live gate [{ticker}]: overflow slot {overflow_level} executed — "
                                    f"score {signal['signal_score']:.4f} >= {overflow_threshold:.4f}"
                                )
                            except Exception as e:
                                logger.error(f"Live trade failed [{ticker}]: {e}")
                                result["outcome"] = "TRADE_FAILED"
            else:
                position_pct = result["lock3"]["position_size_pct"] if result.get("lock3") else cfg["max_position_size"]
                notional     = acct["equity"] * min(position_pct, cfg["max_position_size"])
                if notional < 10:
                    logger.warning(f"Live trade rejected [{ticker}]: notional too small (${notional:.2f})")
                    result["outcome"] = "TRADE_REJECTED"
                else:
                    sector    = signal.get("sector", "")
                    sec_cap   = dynamic_caps_live.get(sector, cfg.get("max_sector_exposure", 0.30))
                    projected = round(sector_exposure_live.get(sector, 0.0) + notional / _starting_balance, 3)
                    if projected > sec_cap:
                        logger.warning(
                            f"Live trade rejected [{ticker}]: {sector} sector cap — "
                            f"projected {projected:.0%} > cap {sec_cap:.0%}"
                        )
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
                            open_count += 1
                            sector_exposure_live[sector] = projected
                            _record_live_trade(signal, notional, order_id, cfg)
                            _fire_trade_alert(ticker, signal, notional, order_id, cfg)
                        except Exception as e:
                            logger.error(f"Live trade failed [{ticker}]: {e}")
                            result["outcome"] = "TRADE_FAILED"

        insert_live_gate_result({
            "timestamp":              result["timestamp"],
            "ticker":                 ticker,
            "sector":                 signal["sector"],
            "signal_score":           signal["signal_score"],
            "lock1_pass":             result["lock1_pass"],
            "lock2_pass":             result["lock2_pass"],
            "lock_leading_pass":      result.get("lock_leading_pass", 0),
            "lock_leading_checks":    result.get("lock_leading_checks"),
            "lock3_pass":             result["lock3_pass"],
            "gate_decision":          result["outcome"],  # use final outcome — gate_decision is set early and not updated on TRADE_FAILED/REJECTED
            "lock3_reasoning":        result.get("claude_reasoning"),
            "alpaca_order_id":        order_id,
            "l2_summary":             result.get("l2_summary"),
            "lock3_sentiment_score":  result.get("lock3_sentiment_score"),
            "lock3_conviction":       result.get("lock3_conviction"),
            "macro_reason":           result.get("macro_reason"),
            "ticker_signal":          result.get("ticker_signal"),
            "earnings_near":          result.get("earnings_near"),
            "days_to_earnings":       result.get("days_to_earnings"),
        })

        _log_summary(ticker, result)
        results.append(result)

    _persist_multiplier_stats(bayesian_multipliers, regime_bayes_result, evaluated, runner="live")
    return results


def _evaluate(signal: dict, wallet_ctx: dict, cfg: dict,
              sector_regime: dict | None = None,
              sector_thresholds: dict | None = None,
              rotation_scores: dict | None = None,
              regime_bayes_result=None,
              ticker_signals: dict | None = None) -> dict:
    ticker       = signal["ticker"]
    sector       = signal.get("sector", "")
    signal_score = signal["signal_score"]
    on_watchlist = signal.get("pre_rotation", False)

    signal = dict(signal)
    signal["ticker_signal"] = (ticker_signals or {}).get(ticker, {}).get("signal")

    context = build_base_context(
        signal, wallet_ctx, cfg,
        starting_balance=wallet_ctx["starting_balance"],
        gate_history_fn=get_live_ticker_gate_fails,
        sector_regime=sector_regime,
        rotation_scores=rotation_scores,
        regime_bayes_result=regime_bayes_result,
        mode_label="LIVE — real money",
    )
    chain   = evaluate_chain(ticker, sector, signal_score, context, cfg,
                             on_watchlist=on_watchlist, stop_after_lock=4,
                             sector_thresholds=sector_thresholds)
    result  = _chain_to_gate_result(signal, chain)
    # Stash L5 inputs so the serial executor can run L5 only when a slot is available.
    if chain.l5_pending:
        result["_l5_lock_results"] = chain.lock_results
        result["_l5_context"]      = context
    return result



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


def _fire_trade_alert(ticker: str, signal: dict, notional: float, order_id: str, cfg: dict) -> None:
    try:
        from backend.alerts import alert_trade_executed
        price = signal["price"]
        alert_trade_executed(
            ticker    = ticker,
            sector    = signal["sector"],
            notional  = notional,
            price     = price,
            tp        = round(price * (1 + cfg["take_profit_pct"]), 2),
            sl        = round(price * (1 - cfg["stop_loss_pct"]), 2),
            order_id  = order_id,
        )
    except Exception as e:
        logger.warning(f"Alert dispatch failed [{ticker}]: {e}")


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

    logger.info(f"Live gate [{ticker}]: {' → '.join(parts)} → {outcome}")
