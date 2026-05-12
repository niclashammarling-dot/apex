"""
Gate Runner (demo) — orchestrates the 5-lock chain for all signal candidates.

Delegates lock evaluation to evaluate_chain() in gate/chain.py.
Locks: Eligibility → Quant → Sentiment → Leading → Claude.
Trade execution via wallet.py. All results logged to DB.
"""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path

from loguru import logger

from backend import wallet
from backend.db import (
    get_lock1_candidates,
    get_open_tickers,
    get_recently_failed_tickers,
    get_wallet_context,
    insert_demo_gate_result,
    update_signal_gate,
)
from backend.gate.chain import ChainResult, evaluate_chain

# Max parallel workers — bounded to avoid hammering rate limits
_MAX_WORKERS = 5

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
    from backend.db import get_ticker_thresholds
    from backend.demo_config import get_demo_config
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

    ts = datetime.now(timezone.utc).isoformat()
    for c in skipped:
        decision = "SKIPPED_OPEN" if c["ticker"] in open_tickers else "SKIPPED_COOLOFF"
        update_signal_gate(c["id"], {
            "lock1_pass": 1, "lock2_pass": 0, "lock3_pass": 0,
            "gate_decision": decision, "lock3_reasoning": None,
        })
        insert_demo_gate_result({
            "timestamp": ts, "ticker": c["ticker"], "sector": c.get("sector", ""),
            "signal_score": c["signal_score"],
            "lock1_pass": 1, "lock2_pass": 0, "lock_leading_pass": 0,
            "lock_leading_checks": None, "lock3_pass": 0,
            "gate_decision": decision, "lock3_reasoning": None,
            "l2_summary": None, "macro_reason": None,
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
    from backend.config import MAX_SECTOR_EXPOSURE
    from backend.scheduler import _get_regime_bayes
    from backend.sector_caps import compute_dynamic_caps
    from backend.sector_regime import compute_sector_regime
    from backend.sector_transitions import (
        compute_ticker_rotation_scores,
        get_rotation_forecast,
    )
    regime_bayes_result = _get_regime_bayes().last_result()
    dynamic_caps    = compute_dynamic_caps(cfg.get("max_sector_exposure", MAX_SECTOR_EXPOSURE),
                                           regime_result=regime_bayes_result)
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
            pool.submit(_evaluate, signal, wallet_ctx, cfg, sector_regime, sector_thresholds,
                        rotation_scores, regime_bayes_result): signal
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

    evaluated.sort(key=lambda x: (0 if x[0].get("pre_rotation") else 1, x[0].get("signal_score", 0)), reverse=True)
    max_positions        = cfg["max_positions"]
    overflow_increment   = cfg.get("overflow_quant_increment", 0.05)
    open_count           = len(open_tickers)  # snapshot; incremented on each executed trade
    bayesian_multipliers = _compute_bayesian_multipliers(evaluated, regime_bayes_result)

    for signal, result in evaluated:
        ticker = signal["ticker"]

        # Apply Bayesian sector sizing — scale Lock 5 recommendation by relative
        # signal strength within the sector's portfolio allocation.
        if result["outcome"] == "TRADE_QUEUED" and result.get("lock3"):
            _scale = bayesian_multipliers.get(ticker, 1.0)
            if _scale != 1.0:
                _raw  = result["lock3"]["position_size_pct"]
                result["lock3"]["position_size_pct"] = min(
                    round(_raw * _scale, 4),
                    cfg.get("max_position_size", 0.10),
                )
                logger.info(
                    f"Gate [{ticker}]: Bayesian size scale={_scale:.3f} "
                    f"({_raw:.4f} → {result['lock3']['position_size_pct']:.4f})"
                )

        if result["outcome"] == "TRADE_QUEUED":
            if open_count >= max_positions:
                # Overflow slot — check escalating quant threshold
                sector           = signal.get("sector", "")
                base_threshold   = (sector_thresholds or {}).get(sector, cfg["lock1_threshold"])
                overflow_level   = open_count - max_positions + 1
                overflow_threshold = round(base_threshold * (1 + overflow_level * overflow_increment), 4)
                if signal["signal_score"] < overflow_threshold:
                    result["outcome"]       = "TRADE_REJECTED"
                    result["gate_decision"] = "FILTERED_OVERFLOW_QUANT"
                    logger.info(
                        f"Gate runner [{ticker}]: overflow slot {overflow_level} rejected — "
                        f"score {signal['signal_score']:.4f} below {overflow_threshold:.4f} "
                        f"(base {base_threshold:.4f} ×{1 + overflow_level * overflow_increment:.2f})"
                    )
                else:
                    trade = wallet.execute_trade(result, signal["price"], dynamic_caps=dynamic_caps,
                                                 overflow=True)
                    result["outcome"] = "TRADE_EXECUTED" if trade else "TRADE_REJECTED"
                    if result["outcome"] == "TRADE_REJECTED":
                        result["gate_decision"] = "TRADE_REJECTED"
                    else:
                        open_count += 1
                        logger.info(
                            f"Gate runner [{ticker}]: overflow slot {overflow_level} executed — "
                            f"score {signal['signal_score']:.4f} >= {overflow_threshold:.4f}"
                        )
            else:
                trade = wallet.execute_trade(result, signal["price"], dynamic_caps=dynamic_caps)
                result["outcome"] = "TRADE_EXECUTED" if trade else "TRADE_REJECTED"
                if result["outcome"] == "TRADE_REJECTED":
                    result["gate_decision"] = "TRADE_REJECTED"
                else:
                    open_count += 1

        results.append(result)
        update_signal_gate(signal["id"], result)
        insert_demo_gate_result({
            "timestamp":           result["timestamp"],
            "ticker":              ticker,
            "sector":              signal.get("sector", ""),
            "signal_score":        signal["signal_score"],
            "lock1_pass":          result["lock1_pass"],
            "lock2_pass":          result["lock2_pass"],
            "lock_leading_pass":   result.get("lock_leading_pass", 0),
            "lock_leading_checks": result.get("lock_leading_checks"),
            "lock3_pass":          result["lock3_pass"],
            "gate_decision":       result["gate_decision"],
            "lock3_reasoning":     result.get("claude_reasoning"),
            "l2_summary":          result.get("l2_summary"),
            "macro_reason":        result.get("macro_reason"),
        })
        _log_summary(ticker, result)

    _persist_multiplier_stats(bayesian_multipliers, regime_bayes_result, evaluated)
    return results


def _evaluate(signal: dict, wallet_ctx: dict, cfg: dict,
              sector_regime: dict | None = None,
              sector_thresholds: dict | None = None,
              rotation_scores: dict | None = None,
              regime_bayes_result=None) -> dict:
    ticker       = signal["ticker"]
    sector       = signal.get("sector", "")
    signal_score = signal["signal_score"]
    # Pre-rotation tickers use the watchlist 15% discount in Lock 2 — same as PRE_ROTATION_FLOOR
    on_watchlist = signal.get("pre_rotation", False)

    context = _build_base_context(signal, wallet_ctx, cfg, sector_regime,
                                  rotation_scores, regime_bayes_result)
    chain   = evaluate_chain(ticker, sector, signal_score, context, cfg,
                             on_watchlist=on_watchlist)
    return _chain_to_gate_result(signal, chain)


def _build_base_context(signal: dict, wallet_ctx: dict, cfg: dict,
                        sector_regime: dict | None = None,
                        rotation_scores: dict | None = None,
                        regime_bayes_result=None) -> dict:
    """
    Build the base context dict passed to evaluate_chain() and ultimately Lock 5 (Claude).
    Sentiment and leading data come from lock_results inside lock5_claude; not duplicated here.
    """
    ctx = {
        "wallet_balance":  wallet_ctx["balance"],
        "open_positions":  wallet_ctx["open_positions"],
        "sector_exposure": wallet_ctx["sector_exposure"],
        "risk_limits": {
            "starting_balance":    cfg["starting_balance"],
            "max_positions":       cfg["max_positions"],
            "max_sector_exposure": cfg["max_sector_exposure"],
            "max_position_size":   cfg["max_position_size"],
            "daily_loss_cap":      cfg["daily_loss_cap"],
            "max_drawdown_pct":    cfg.get("max_drawdown_pct", 0.20),
        },
    }

    if sector_regime and sector_regime.get("available"):
        sector_detail = sector_regime.get("sectors", {}).get(signal["sector"], {})
        ctx.update({
            "market_regime":      sector_regime["regime"],
            "regime_confidence":  sector_regime.get("regime_confidence"),
            "sector_signal":      sector_detail.get("signal"),
            "sector_streak_days": sector_detail.get("streak_days"),
            "market_leader":      sector_regime.get("leader"),
            "sector_velocity":    sector_detail.get("velocity"),
            "sector_velocity_5d": sector_detail.get("velocity_5d"),
            "sector_accel":       sector_detail.get("accel"),
        })

    try:
        from backend.sector_transitions import get_rotation_forecast
        forecast = get_rotation_forecast()
        if forecast.get("available"):
            next_prob = next((item["probability"] for item in forecast.get("likely_next", [])
                              if item["sector"] == signal["sector"]), None)
            confirmed = forecast.get("confirmed_transition")
            ctx.update({
                "rotation_leader":             forecast["leader"],
                "rotation_predecessor":        forecast.get("predecessor"),
                "sector_next_probability":     next_prob,
                "rotation_confirmed":          confirmed is not None,
                "rotation_transition_prob":    confirmed["probability"] if confirmed else None,
                "rotation_regime_conditioned": forecast.get("regime_conditioned"),
                "rotation_regime_sample_size": forecast.get("regime_sample_size"),
            })
    except Exception as e:
        logger.debug(f"Gate [{signal['ticker']}]: rotation forecast unavailable — {e}")

    if rotation_scores is not None:
        ctx["ticker_rotation_score"] = rotation_scores.get(signal["ticker"])

    if regime_bayes_result is not None:
        sector = signal.get("sector", "")
        alloc  = regime_bayes_result.allocation.get(sector, 0.0)
        entry  = next((e for e in regime_bayes_result.leaderboard if e.sector == sector), None)
        ctx["regime_bayes_allocation"]     = alloc
        ctx["regime_bayes_posterior"]      = entry.posterior      if entry else None
        ctx["regime_bayes_adjusted_score"] = entry.adjusted_score if entry else None
        ctx["regime_bayes_rank"]           = entry.rank           if entry else None
        ctx["regime_bayes_qualified"]      = alloc > 0
        ctx["regime_bayes_leader"]         = regime_bayes_result.leader

    try:
        from backend.db import get_ticker_gate_fails
        fails = get_ticker_gate_fails(signal["ticker"], limit=5)
        if fails:
            ctx["ticker_gate_history"] = fails
    except Exception as e:
        logger.debug(f"Gate [{signal['ticker']}]: gate fail history unavailable — {e}")

    return ctx


def _chain_to_gate_result(signal: dict, chain: ChainResult) -> dict:
    """Translate ChainResult into the flat result dict expected by run() and DB writers.

    DB column mapping (schema preserved, semantics shifted):
        lock1_pass = Lock 2 Quant result
        lock2_pass = Lock 3 Sentiment result
        lock3_pass = Lock 5 Claude result
    """
    lr = chain.lock_results
    l1 = lr.get(1)   # Eligibility
    l2 = lr.get(2)   # Quant      → DB lock1_pass
    l3 = lr.get(3)   # Sentiment  → DB lock2_pass
    l4 = lr.get(4)   # Leading
    l5 = lr.get(5)   # Claude     → DB lock3_pass

    _OUTCOMES = {
        1: "FILTERED_ELIGIBILITY",
        2: "FILTERED_L1",
        3: "FILTERED_L2",
        4: "FILTERED_LEADING",
        5: "FILTERED_L3",
    }
    outcome = "TRADE_QUEUED" if chain.approved else _OUTCOMES.get(chain.exit_lock, "FILTERED_UNKNOWN")

    # Lock 5 (Claude) — backward compat with result["lock3"]["position_size_pct"] in wallet.py
    l5_data = l5.data if l5 else {}
    lock3_compat = {
        "passed":            l5.passed if l5 else False,
        "decision":          l5_data.get("decision"),
        "confidence":        l5_data.get("confidence", 0.0),
        "position_size_pct": l5_data.get("position_size_pct", 0.0),
        "reasoning":         l5_data.get("reasoning"),
        "model":             l5_data.get("model"),
    }

    # Lock 4 (Leading) — flatten pass_count/min_pass for _log_summary()
    l4_data   = l4.data if l4 else {}
    # lock4_leading uses "checks"; "sub_checks" is a DB-compat alias (same dict)
    l4_checks = l4_data.get("checks") or l4_data.get("sub_checks", {})
    lock_leading_compat = None
    if l4:
        lock_leading_compat = {
            "passed":     l4.passed,
            "score":      l4.score,
            "reason":     l4.reason,
            "pass_count": l4_data.get("pass_count", 0),
            "min_pass":   l4_data.get("min_pass", 2),
            "checks":     l4_checks,
        }

    # Lock 3 (Sentiment) data — for DB sentiment_score and l2_summary columns
    l3_data = l3.data if l3 else {}

    # For _log_summary: show Quant (L2) if it ran, else Eligibility (L1)
    lock1_compat = l2.to_dict() if l2 else (l1.to_dict() if l1 else {"passed": False, "score": 0.0})

    return {
        "ticker":       signal["ticker"],
        "sector":       signal.get("sector", ""),
        "signal_id":    signal["id"],
        "pre_rotation": signal.get("pre_rotation", False),
        "timestamp":    datetime.now(timezone.utc).isoformat(),
        "outcome":      outcome,
        # Named lock dicts (used by _log_summary and wallet.execute_trade)
        "lock1":        lock1_compat,
        "lock2":        l3.to_dict() if l3 else None,
        "lock_leading": lock_leading_compat,
        "lock3":        lock3_compat,
        # Flat DB fields
        "lock1_pass":          int(l2.passed) if l2 else 0,
        "lock2_pass":          int(l3.passed) if l3 else 0,
        "lock_leading_pass":   int(l4.passed) if l4 else 0,
        "lock_leading_checks": json.dumps(l4_checks) if l4 else None,
        "lock3_pass":          int(l5.passed) if l5 else 0,
        "gate_decision":       outcome if outcome != "TRADE_QUEUED" else "TRADE_EXECUTED",
        "claude_confidence":   l5_data.get("confidence"),
        "claude_reasoning":    l5_data.get("reasoning"),
        "sentiment_score":     l3_data.get("score"),
        "l2_summary":          l3_data.get("summary"),
        "macro_reason":        l1.reason if (l1 and not l1.passed) else None,
    }


_MULTIPLIER_STATS_PATH = Path(__file__).parent.parent.parent / "data" / "bayesian_multiplier_stats.json"


def _persist_multiplier_stats(
    multipliers: dict[str, float],
    regime_bayes_result,
    evaluated: list[tuple[dict, dict]],
) -> None:
    """
    Append one cycle record to the daily Bayesian multiplier stats file.

    The file accumulates per-cycle entries within a trading day. The audit
    check reads it nightly and flags any cycle where regime was present,
    ≥3 tickers were queued, but every multiplier was exactly 1.0 — the
    silent failure mode where ticker_allocations() returned zeros.
    """
    today = date.today().isoformat()
    queued_count    = sum(1 for _, r in evaluated if r["outcome"] == "TRADE_QUEUED")
    regime_present  = regime_bayes_result is not None and bool(
        getattr(regime_bayes_result, "allocation", {})
    )
    vals      = list(multipliers.values())
    all_unity = bool(vals) and all(v == 1.0 for v in vals)
    variance  = 0.0
    if len(vals) >= 2:
        mean     = sum(vals) / len(vals)
        variance = round(sum((v - mean) ** 2 for v in vals) / len(vals), 6)

    cycle = {
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "regime_present":  regime_present,
        "queued_count":    queued_count,
        "multiplier_count": len(multipliers),
        "all_unity":       all_unity,
        "variance":        variance,
    }
    suspicious = regime_present and queued_count >= 3 and all_unity

    try:
        existing: dict = {}
        if _MULTIPLIER_STATS_PATH.exists():
            try:
                existing = json.loads(_MULTIPLIER_STATS_PATH.read_text())
            except Exception:
                existing = {}

        if existing.get("date") != today:
            existing = {"date": today, "cycles": [], "suspicious_cycles": 0}

        existing["cycles"].append(cycle)
        if suspicious:
            existing["suspicious_cycles"] = existing.get("suspicious_cycles", 0) + 1

        _MULTIPLIER_STATS_PATH.write_text(json.dumps(existing, indent=2))
    except Exception as e:
        logger.warning(f"Gate runner: failed to persist multiplier stats: {e}")


def _compute_bayesian_multipliers(
    evaluated: list[tuple[dict, dict]],
    regime_bayes_result,
) -> dict[str, float]:
    """
    Per-ticker size multipliers from Bayesian sector allocation.

    For each sector with queued trades, distribute the sector's portfolio
    allocation proportionally by signal score via RegimeBayes.ticker_allocations().
    The multiplier is ticker_bayesian_alloc / equal_weight_baseline — tickers
    with above-average signal strength get sized up, laggards get sized down.

    Returns {} when regime_bayes_result is unavailable (multipliers default to 1.0).
    Non-qualifying sectors (allocation=0) are excluded; their trades are handled
    by dynamic_caps and the regime floor elsewhere.
    """
    if not regime_bayes_result:
        return {}

    from collections import defaultdict
    from backend.regime.regime_bayes import RegimeBayes

    sector_queued: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for signal, result in evaluated:
        if result["outcome"] == "TRADE_QUEUED":
            sector_queued[signal.get("sector", "")].append(
                (signal["ticker"], signal["signal_score"])
            )

    multipliers: dict[str, float] = {}
    allocs = regime_bayes_result.allocation

    for sector, tickers in sector_queued.items():
        sector_alloc = allocs.get(sector, 0.0)
        if sector_alloc <= 0 or not tickers:
            continue
        baseline     = sector_alloc / len(tickers)   # equal-weight reference
        ticker_scores = {t: s for t, s in tickers}
        per_ticker    = RegimeBayes.ticker_allocations(sector_alloc, ticker_scores)
        for ticker, alloc in per_ticker.items():
            multipliers[ticker] = round(alloc / baseline, 4) if baseline > 0 else 1.0

    return multipliers


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
