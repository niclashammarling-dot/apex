"""
Gate Runner (demo) — runs the gate for all signal candidates.

Before the chain: L1's skips (an open position, gate cooloff, exit cooloff) and affordability. Then the chain
(gate/chain.py: the five module locks, L1 ELIGIBILITY .. L5 POSITIONING, the ETF re-test and the guard), then
the serial execution phase: L2 overflow, L6 REVIEW (Claude) for a candidate with a slot, and wallet.py.
Every row records the rules that ran (rule_states) and is stored under the code lock_codes derives from them
(lock labels phase 2); an outcome no rule covers is FILTERED_UNKNOWN and is mailed once per cycle.
"""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path

from loguru import logger

from backend import wallet
from backend.db import (
    get_l3_score_candidates,
    get_open_tickers,
    get_recently_exited_tickers,
    get_recently_failed_tickers,
    get_ticker_gate_fails,
    get_wallet_context,
    insert_demo_gate_result,
    update_signal_gate,
)
from backend.gate.chain import ChainResult, build_base_context, evaluate_chain, verdict_states
from backend.gate.cycle import GateCycle, evaluated_reason, record_stale
from backend.gate import lock_codes

# Max parallel workers — bounded to avoid hammering rate limits
_MAX_WORKERS = 5

# Pre-rotation promotion: tickers in "watching" sectors are eligible for gate
# evaluation at this fraction of their normal L1 threshold, catching setups that
# are building momentum before they clear the standard signal filter.
PRE_ROTATION_FLOOR = 0.85

# The internal outcome of a row a rule stopped. What is stored is derived from its rule_states when the row is
# written (_finalize), so the control flow never names a stored code.
STOPPED = "STOPPED"


def _finalize(result: dict) -> dict:
    """The stored codes and pass flags of a row, from its rule_states (lock_codes.codes_for and flags; ruling D:
    a flag is NULL where its lock did not run). `outcome` becomes the stored gate_decision."""
    states = result.setdefault("rule_states", {})
    gate_decision, stop_code = lock_codes.codes_for(states, executed=result["outcome"] == "TRADE_EXECUTED")
    result.update(lock_codes.flags(states))
    result["gate_decision"] = result["outcome"] = gate_decision
    result["stop_code"] = stop_code
    return result


def _pre_chain_result(c: dict, states: dict[str, str], refusal: str | None = None) -> dict:
    """The result of a candidate stopped before the chain: a skip, or an affordability refusal (`refusal`)."""
    return {"ticker": c["ticker"], "sector": c.get("sector", ""), "signal_id": c.get("id"),
            "timestamp": datetime.now(timezone.utc).isoformat(), "outcome": STOPPED, "rule_states": states,
            "outcome_reason": refusal, "l5_positioning_checks": None, "claude_reasoning": None}


def _affordability(states: dict[str, str], notional: float, price: float | None, *,
                   min_notional: float, whole_shares: bool) -> str | None:
    """L1's checks that an order can be bought, recorded in states: a price, a notional of at least
    `min_notional`, and with `whole_shares` (live's bracket orders) at least one share. Returns the refusing rule's
    identifier, or None. Used before the chain on the largest possible notional, and by live at execution."""
    lock_codes.put(states, "price_missing", lock_codes.FAIL if not price else lock_codes.PASS)
    if not price:
        return "price_missing"
    too_small = notional < min_notional or (whole_shares and int(notional / price) < 1)
    lock_codes.put(states, "notional_too_small", lock_codes.FAIL if too_small else lock_codes.PASS)
    return "notional_too_small" if too_small else None


def _alert_unknown(book: str, rows: list[dict]) -> None:
    """One mail per cycle for the rows no registered rule covers (Niclas 2026-10-28: "om ett fel inte täcks av
    nuvarande regler och markörer så får vi ha en 'unknown', som direkt flaggas och skickas till min mail")."""
    if not rows:
        return
    from backend.alerts import alert_gate_unknown
    alert_gate_unknown(book, rows)


# The slot evidence each serial-phase row stores (2026-11-07, vault scope 2026-11-06 Job 1): the open count and cap
# the slot logic used, the overflow threshold it applied (NULL below the cap), the 1-based place in the serial order
# (regular candidates before pre-rotation ones, score descending within each) and the pre-rotation flag. Pre-chain
# rows never reach the serial phase and store NULL.
SLOT_FIELDS = ("l2_risk_open_count", "l2_risk_max_positions_count", "l2_risk_overflow_threshold", "exec_rank",
               "pre_rotation")


def _slot_evidence(signal: dict, exec_rank: int, open_count: int, cap: int, base_threshold: float,
                   increment: float) -> dict:
    over = open_count >= cap
    return {"l2_risk_open_count": open_count, "l2_risk_max_positions_count": cap,
            "l2_risk_overflow_threshold": round(base_threshold * (1 + (open_count - cap + 1) * increment), 4)
            if over else None,
            "exec_rank": exec_rank, "pre_rotation": 1 if signal.get("pre_rotation") else 0}


def _write_demo(signal: dict, result: dict, ts: str, unknown: list[dict]) -> None:
    """Finalize a demo row and write both copies (signals and demo_gate_history)."""
    _finalize(result)
    if result["gate_decision"] == lock_codes.UNKNOWN:
        unknown.append(result)
    update_signal_gate(signal["id"], result)
    insert_demo_gate_result({
        "cycle_started_at":       ts,
        "timestamp":              result["timestamp"],
        "ticker":                 signal["ticker"],
        "sector":                 signal.get("sector", ""),
        "signal_score":           signal["signal_score"],
        "l3_score_pass":             result["l3_score_pass"],
        "l4_sentiment_pass":             result["l4_sentiment_pass"],
        "l5_positioning_pass":      result["l5_positioning_pass"],
        "l5_positioning_checks":    result.get("l5_positioning_checks"),
        "l6_review_pass":             result["l6_review_pass"],
        "gate_decision":          result["gate_decision"],
        "stop_code":              result["stop_code"],
        "rule_states":            result["rule_states"],
        "decision_reason":        result.get("claude_reasoning"),
        "l4_sentiment_summary":             result.get("l4_sentiment_summary"),
        "l4_sentiment_score":  result.get("l4_sentiment_score"),
        "l4_sentiment_conviction":       result.get("l4_sentiment_conviction"),
        "l1_eligibility_reason":           result.get("l1_eligibility_reason"),
        "ticker_signal":          result.get("ticker_signal"),
        "earnings_near":          result.get("earnings_near"),
        "days_to_earnings":       result.get("days_to_earnings"),
        "overflow_slot":          result.get("overflow_slot", False),
        "outcome_reason":         result.get("outcome_reason"),   # the wallet's or the runner's rule identifier
        "signal_ts":              signal.get("timestamp"),
        "bar_date":               signal.get("bar_date"),
        **{k: (result.get("_slot") or {}).get(k) for k in SLOT_FIELDS},
    })


def run(cycle: GateCycle | None = None) -> list[dict]:
    """
    Evaluate the gate for all current Lock-1 candidates concurrently.
    Reads thresholds from demo_config.json at call time so UI changes take effect immediately.
    Returns list of full gate result dicts. cycle (backend/gate/cycle.py) carries
    the start every row of this cycle is stamped with, and takes the exit reason.
    """
    cycle = cycle or GateCycle.unrecorded()
    from backend.db import get_ticker_thresholds
    from backend.demo_config import get_demo_config
    cfg = get_demo_config()

    sector_thresholds = get_ticker_thresholds()
    stale: list[dict] = []
    candidates = get_l3_score_candidates(threshold=cfg["l3_score_threshold_min"],
                                      sector_thresholds=sector_thresholds,
                                      stale_out=stale)
    record_stale(cycle, stale, "Gate runner")
    n_stale_main = len(stale)

    if not candidates:
        logger.info("Gate runner: no L3 SCORE candidates this cycle")
        cycle.reason = "no_fresh_candidates" if stale else "no_candidates"
        _persist_multiplier_stats({}, None, [])
        return []

    # Skip tickers already held, in gate cooloff, or in exit cooloff
    open_tickers   = get_open_tickers()
    failed_tickers = get_recently_failed_tickers(cfg.get("l1_eligibility_cooloff_hours", 4))
    exited_tickers, won_tickers = get_recently_exited_tickers(
        sl_hours=cfg.get("l1_eligibility_exit_cooloff_hours", 24),
        tp_hours=cfg.get("l1_eligibility_exit_cooloff_win_hours", 168),
    )
    blocked = open_tickers | failed_tickers | exited_tickers | won_tickers
    pre_checks = {"position_open": open_tickers, "cooloff": failed_tickers, "exit_cooloff": exited_tickers,
                  "exit_cooloff_win": won_tickers}
    skipped = [c for c in candidates if c["ticker"] in blocked]
    candidates = [c for c in candidates if c["ticker"] not in blocked]

    ts = cycle.started_at
    unknown: list[dict] = []
    for c in skipped:
        _write_demo(c, _pre_chain_result(c, _pre_chain_states(c["ticker"], pre_checks)), ts, unknown)

    if skipped:
        skipped_set = {c["ticker"] for c in skipped}
        # held= counts candidates this cycle already holds; the book size is separate
        # (2026-09-30: "open=6" was read as the position count on 09-28 and 09-29).
        logger.info(f"Gate runner: skipped {len(skipped)} candidate(s) "
                    f"(held={len(open_tickers & skipped_set)}, "
                    f"cooloff={len(failed_tickers & skipped_set)}, "
                    f"exit_cooloff={len(exited_tickers & skipped_set)}, "
                    f"exit_cooloff_win={len(won_tickers & skipped_set)}) "
                    f"— book holds {len(open_tickers)}")

    if not candidates:
        logger.info("Gate runner: all candidates skipped (open positions / cooloff)")
        cycle.reason = "all_skipped"
        _persist_multiplier_stats({}, None, [])
        return []

    from backend.config import EXCLUDED_SECTORS
    candidates = [c for c in candidates if c.get("sector", "") not in EXCLUDED_SECTORS]
    if not candidates:
        logger.info("Gate runner: all candidates in excluded sectors")
        cycle.reason = "all_excluded"
        _persist_multiplier_stats({}, None, [])
        return []

    logger.info(f"Gate runner: {len(candidates)} candidate(s) — {[c['ticker'] for c in candidates]}")

    wallet_ctx = get_wallet_context()
    results    = []

    # Compute dynamic sector caps + regime + rotation scores once per gate cycle
    from backend.config import DEMO_L2_RISK_SECTOR_CAP_MAX
    from backend.scheduler import _get_regime_bayes
    from backend.sector_caps import compute_dynamic_caps
    from backend.sector_regime import compute_sector_regime, compute_ticker_signals
    from backend.sector_transitions import (
        compute_ticker_rotation_scores,
        get_rotation_forecast,
    )
    regime_bayes_result = _get_regime_bayes().last_result()
    dynamic_caps    = compute_dynamic_caps(cfg.get("l2_risk_sector_cap_max", DEMO_L2_RISK_SECTOR_CAP_MAX),
                                           regime_result=regime_bayes_result)
    wallet_ctx = {**wallet_ctx, "dynamic_caps": dynamic_caps}   # L6 REVIEW judges the enforced cap (2026-10-17)
    sector_regime   = compute_sector_regime()
    rotation_scores = compute_ticker_rotation_scores()
    ticker_signals  = compute_ticker_signals()

    # Pre-rotation promotion — pull tickers from "watching" sectors at a discounted
    # L1 threshold so early-stage setups enter the pipeline before they clear the
    # standard score filter.
    forecast         = get_rotation_forecast()
    watching_sectors = {w["sector"] for w in forecast.get("watching", [])}
    if watching_sectors:
        pr_sector_thresholds = {
            s: (sector_thresholds or {}).get(s, cfg["l3_score_threshold_min"]) * PRE_ROTATION_FLOOR
            for s in watching_sectors
        }
        pr_candidates = get_l3_score_candidates(
            threshold=cfg["l3_score_threshold_min"] * PRE_ROTATION_FLOOR,
            sector_thresholds=pr_sector_thresholds,
            stale_out=stale,
        )
        record_stale(cycle, stale, "Gate runner", start=n_stale_main)
        existing = {c["ticker"] for c in candidates}
        for c in pr_candidates:
            if (c.get("sector") in watching_sectors
                    and c["ticker"] not in existing
                    and c["ticker"] not in open_tickers
                    and c["ticker"] not in failed_tickers
                    and c["ticker"] not in exited_tickers):
                c = dict(c)
                c["pre_rotation"] = True
                candidates.append(c)
                existing.add(c["ticker"])
        pr_count = sum(1 for c in candidates if c.get("pre_rotation"))
        if pr_count:
            logger.info(f"Gate runner: +{pr_count} pre-rotation candidate(s) from {sorted(watching_sectors)}")

    # L1 affordability before the chain (Niclas 2026-10-28: "L1 måste kontrollera först att beloppet räcker …
    # Kan vi spara anrop så är det bra"): the largest notional any later size can reach is cash × max_position_size
    # (L6 REVIEW's size and the Bayesian scaling are capped by it), so a refusal on it moves no order that could fill.
    from backend.config import DEMO_MAX_POSITION_SIZE
    from backend.db import get_portfolio_summary
    max_notional = get_portfolio_summary()["cash"] * cfg.get("max_position_size", DEMO_MAX_POSITION_SIZE)
    pre_states: dict[str, dict] = {}
    affordable = []
    for c in candidates:
        states = _pre_chain_states(c["ticker"], pre_checks)
        refusal = _affordability(states, max_notional, c.get("price"), min_notional=wallet.MIN_ORDER,
                                 whole_shares=False)
        if refusal:
            logger.info(f"Gate runner [{c['ticker']}]: {refusal} before the chain "
                        f"(largest notional ${max_notional:.2f}, price {c.get('price')!r})")
            _write_demo(c, _pre_chain_result(c, states, refusal), ts, unknown)
        else:
            pre_states[c["ticker"]] = states
            affordable.append(c)
    candidates = affordable
    if not candidates:
        logger.info("Gate runner: no candidate affordable this cycle")
        cycle.reason = "all_skipped"
        _alert_unknown("demo", unknown)
        _persist_multiplier_stats({}, None, [])
        return []

    # Evaluate all candidates in parallel, then execute trades sequentially
    # (trade execution must be serial to prevent race conditions on position limits)
    evaluated: list[tuple[dict, dict]] = []  # (signal, result)

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
                logger.exception(f"Gate runner [{signal['ticker']}]: evaluation raised — {e}")
                continue
            result["rule_states"] = {**pre_states[signal["ticker"]], **result.get("rule_states", {})}
            evaluated.append((signal, result))
    cycle.reason = evaluated_reason(len(candidates), len(evaluated))

    evaluated.sort(key=lambda x: (0 if x[0].get("pre_rotation") else 1, x[0].get("signal_score", 0)), reverse=True)
    l2_risk_max_positions_count        = cfg["l2_risk_max_positions_count"]
    overflow_increment   = cfg.get("l2_risk_overflow_step", 0.05)
    open_count           = len(open_tickers)  # snapshot; incremented on each executed trade
    bayesian_multipliers = _compute_bayesian_multipliers(evaluated, regime_bayes_result)
    # Count queued outcomes before the serial loop mutates them to EXECUTED/REJECTED.
    pre_exec_queued = sum(
        1 for _, r in evaluated
        if r["outcome"] in ("TRADE_QUEUED", "TRADE_QUEUED_PENDING_L6")
    )

    cycle.held_at_start, cycle.l2_risk_max_positions_count = open_count, l2_risk_max_positions_count
    for exec_rank, (signal, result) in enumerate(evaluated, 1):
        result["_slot"] = _slot_evidence(
            signal, exec_rank, open_count, l2_risk_max_positions_count,
            (sector_thresholds or {}).get(signal.get("sector", ""), cfg["l3_score_threshold_min"]), overflow_increment)
        ticker = signal["ticker"]

        # L5 deferred path — run Claude only when a slot is confirmed available.
        if result["outcome"] == "TRADE_QUEUED_PENDING_L6":
            sector         = signal.get("sector", "")
            base_threshold = (sector_thresholds or {}).get(sector, cfg["l3_score_threshold_min"])
            states = result.setdefault("rule_states", {})
            lock_codes.put(states, "position_open", lock_codes.FAIL if ticker in open_tickers else lock_codes.PASS)
            if ticker in open_tickers:
                logger.info(f"Gate runner: trade skipped [{ticker}]: position already open (pre-L5)")
                result["outcome"] = STOPPED
                result["outcome_reason"] = "position_open"
                result.pop("_l5_lock_results", None)
                result.pop("_l5_context", None)
            elif open_count >= l2_risk_max_positions_count:
                overflow_level     = open_count - l2_risk_max_positions_count + 1
                overflow_threshold = round(base_threshold * (1 + overflow_level * overflow_increment), 4)
                _overflow_state(result, signal["signal_score"] < overflow_threshold)
                if signal["signal_score"] < overflow_threshold:
                    logger.warning(
                        f"Gate runner [{ticker}]: overflow slot {overflow_level} rejected pre-L5 — "
                        f"score {signal['signal_score']:.4f} below {overflow_threshold:.4f}"
                    )
                    result["outcome"] = STOPPED
                    result.pop("_l5_lock_results", None)
                    result.pop("_l5_context", None)
                else:
                    _run_l6_review_for_result(signal, result, cfg)
            else:
                _overflow_state(result, False)          # a free slot passes overflow
                _run_l6_review_for_result(signal, result, cfg)

        # Apply Bayesian sector sizing — scale L6 REVIEW recommendation by relative
        # signal strength within the sector's portfolio allocation.
        if result["outcome"] == "TRADE_QUEUED" and result.get("l6_review"):
            _scale = bayesian_multipliers.get(ticker, 1.0)
            if _scale != 1.0:
                _raw  = result["l6_review"]["position_size_pct"]
                result["l6_review"]["position_size_pct"] = min(
                    round(_raw * _scale, 4),
                    cfg.get("max_position_size", 0.10),
                )
                logger.info(
                    f"Gate [{ticker}]: Bayesian size scale={_scale:.3f} "
                    f"({_raw:.4f} → {result['l6_review']['position_size_pct']:.4f})"
                )

        if result["outcome"] == "TRADE_QUEUED":
            if open_count >= l2_risk_max_positions_count:
                # Overflow slot — check escalating quant threshold
                sector           = signal.get("sector", "")
                base_threshold   = (sector_thresholds or {}).get(sector, cfg["l3_score_threshold_min"])
                overflow_level   = open_count - l2_risk_max_positions_count + 1
                overflow_threshold = round(base_threshold * (1 + overflow_level * overflow_increment), 4)
                _overflow_state(result, signal["signal_score"] < overflow_threshold)
                if signal["signal_score"] < overflow_threshold:
                    result["outcome"] = STOPPED
                    logger.info(
                        f"Gate runner [{ticker}]: overflow slot {overflow_level} rejected — "
                        f"score {signal['signal_score']:.4f} below {overflow_threshold:.4f} "
                        f"(base {base_threshold:.4f} ×{1 + overflow_level * overflow_increment:.2f})"
                    )
                else:
                    trade = wallet.execute_trade(result, signal["price"], dynamic_caps=dynamic_caps,
                                                 overflow=True)
                    result["outcome"] = "TRADE_EXECUTED" if trade else STOPPED
                    if result["outcome"] == "TRADE_EXECUTED":
                        result["overflow_slot"] = True
                        open_count += 1
                        logger.info(
                            f"Gate runner [{ticker}]: overflow slot {overflow_level} executed — "
                            f"score {signal['signal_score']:.4f} >= {overflow_threshold:.4f}"
                        )
            else:
                _overflow_state(result, False)          # a free slot passes overflow
                trade = wallet.execute_trade(result, signal["price"], dynamic_caps=dynamic_caps)
                result["outcome"] = "TRADE_EXECUTED" if trade else STOPPED
                if result["outcome"] == "TRADE_EXECUTED":
                    open_count += 1

        _write_demo(signal, result, ts, unknown)
        results.append(result)
        _log_summary(ticker, result)

    _alert_unknown("demo", unknown)
    _persist_multiplier_stats(bayesian_multipliers, regime_bayes_result, evaluated,
                              pre_exec_queued=pre_exec_queued)
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
    # Pre-rotation tickers use the watchlist 15% discount in L3 SCORE — same as PRE_ROTATION_FLOOR
    on_watchlist = signal.get("pre_rotation", False)

    signal = dict(signal)
    signal["ticker_signal"] = (ticker_signals or {}).get(ticker, {}).get("signal")

    context = build_base_context(
        signal, wallet_ctx, cfg,
        starting_balance=wallet_ctx["starting_balance"],
        gate_history_fn=get_ticker_gate_fails,
        sector_regime=sector_regime,
        rotation_scores=rotation_scores,
        regime_bayes_result=regime_bayes_result,
    )
    chain   = evaluate_chain(ticker, sector, signal_score, context, cfg,
                             on_watchlist=on_watchlist, defer_l6_review=True,
                             sector_thresholds=sector_thresholds)
    result  = _chain_to_gate_result(signal, chain)
    # Stash L5 inputs so the serial executor can run L5 only when a slot is available.
    if chain.l6_review_pending:
        result["_l5_lock_results"] = chain.lock_results
        result["_l5_context"]      = context
    return result


def _pre_chain_states(ticker: str, checks: dict[str, set[str]]) -> dict[str, str]:
    """L1's checks before the chain, each with its verdict for this ticker: the live blocklist, an open position,
    gate cooloff, exit cooloff (Niclas 2026-10-29: separate markers). Only the checks the candidate met are passed
    in: pre-rotation candidates are not checked against the live blocklist (gate_runner_live; input to the
    watchlist and rotation work after phase 4), so blocklist is absent on their rows."""
    states: dict[str, str] = {}
    for ident, tickers in checks.items():
        lock_codes.put(states, ident, lock_codes.FAIL if ticker in tickers else lock_codes.PASS)
    return states


def _overflow_state(result: dict, refused: bool) -> None:
    """L2 overflow's verdict on a candidate beyond l2_risk_max_positions_count (both runners' execution phase). With a slot
    free the rule does not run: the row's L2 stays open until l2_risk_max_positions_count is reached (ruling E, 2026-10-26)."""
    lock_codes.put(result.setdefault("rule_states", {}), "overflow", lock_codes.FAIL if refused else lock_codes.PASS)


def _run_l6_review_for_result(signal: dict, result: dict, cfg: dict) -> None:
    """
    Run L6 REVIEW (Claude) in the serial execution phase using stashed inputs.
    Mutates result in-place: sets outcome, lock3, l6_review_pass, claude_reasoning.
    """
    from backend.gate.l6_review import evaluate as l6_review_evaluate
    ticker       = signal["ticker"]
    sector       = signal.get("sector", "")
    lock_results = result.pop("_l5_lock_results", {})
    context      = result.pop("_l5_context", {})

    l5      = l6_review_evaluate(ticker, sector, lock_results, context,
                             confidence_min=cfg.get("l6_review_claude_min"))
    l5_data = l5.data if l5 else {}

    result["l6_review"] = {
        "passed":            l5.passed,
        "decision":          l5_data.get("decision"),
        "confidence":        l5_data.get("confidence", 0.0),
        "position_size_pct": l5_data.get("position_size_pct", 0.0),
        "reasoning":         l5_data.get("reasoning"),
        "model":             l5_data.get("model"),
    }
    result["l6_review_pass"]       = int(l5.passed)
    result.setdefault("rule_states", {}).update(verdict_states(6, "claude", l5))
    result["claude_reasoning"] = l5_data.get("reasoning")
    result["outcome"]         = "TRADE_QUEUED" if l5.passed else STOPPED


def _chain_to_gate_result(signal: dict, chain: ChainResult) -> dict:
    """Translate ChainResult into the flat result dict expected by run() and DB writers.

    The stored codes and the pass flags (l3_score_pass = the score test, l4_sentiment_pass = L4, l5_positioning_pass = L5,
    l6_review_pass = L6) are not set here: _finalize derives them from rule_states when the row is written
    (lock_codes.codes_for, lock_codes.flags). Phase 3 part a renames the columns to the chain (l3_score_pass …;
    ruling 2026-10-28, eighth round).
    """
    lr = chain.lock_results           # keyed by chain lock (the drawdown guard's under 0, read through exit_reason)
    l1 = lr.get(1)   # L1 ELIGIBILITY
    l3 = lr.get(3)   # L3 SCORE      → DB l3_score_pass
    l4 = lr.get(4)   # L4 SENTIMENT  → DB l4_sentiment_pass
    l5 = lr.get(5)   # L5 POSITIONING
    l6 = lr.get(6)   # L6 REVIEW     → DB l6_review_pass

    outcome = (
        "TRADE_QUEUED"            if chain.approved    else
        "TRADE_QUEUED_PENDING_L6" if chain.l6_review_pending  else
        STOPPED
    )

    # L6 REVIEW (Claude) — backward compat with result["l6_review"]["position_size_pct"] in wallet.py
    l6_data = l6.data if l6 else {}
    _l6_reasoning = l6_data.get("reasoning") or chain.exit_reason or (l6.reason if l6 else None)
    l6_review_compat = {
        "passed":            l6.passed if l6 else False,
        "decision":          l6_data.get("decision"),
        "confidence":        l6_data.get("confidence", 0.0),
        "position_size_pct": l6_data.get("position_size_pct", 0.0),
        "reasoning":         _l6_reasoning,
        "model":             l6_data.get("model"),
    }

    # L5 POSITIONING — flatten pass_count and the two groups for _log_summary()
    l5_data   = l5.data if l5 else {}
    # l5_positioning uses "checks"; "sub_checks" is a DB-compat alias (same dict)
    l5_checks = l5_data.get("checks") or l5_data.get("sub_checks", {})
    l5_positioning_compat = None
    if l5:
        l5_positioning_compat = {
            "passed":     l5.passed,
            "score":      l5.score,
            "reason":     l5.reason,
            "pass_count": l5_data.get("pass_count", 0),
            "price_group_pass":   l5_data.get("price_group_pass"),
            "options_group_pass": l5_data.get("options_group_pass"),
            "checks":     l5_checks,
        }

    # L4 SENTIMENT data — for DB sentiment_score and l4_sentiment_summary columns
    l4_data = l4.data if l4 else {}

    # For _log_summary: show L3 SCORE if it ran, else L1 ELIGIBILITY
    l3_score_compat = l3.to_dict() if l3 else (l1.to_dict() if l1 else {"passed": False, "score": 0.0})

    return {
        "ticker":        signal["ticker"],
        "sector":        signal.get("sector", ""),
        "signal_id":     signal["id"],
        "pre_rotation":  signal.get("pre_rotation", False),
        "ticker_signal": signal.get("ticker_signal"),
        "timestamp":    datetime.now(timezone.utc).isoformat(),
        "outcome":      outcome,
        "rule_states":  dict(chain.rule_states),   # lock_codes.rule_key → verdict; L6 added by _run_l6_review_for_result
        # Named lock dicts (used by _log_summary and wallet.execute_trade)
        "l3_score":       l3_score_compat,
        "l4_sentiment":   l4.to_dict() if l4 else None,
        "l5_positioning": l5_positioning_compat,
        "l6_review":      l6_review_compat,
        # Flat DB fields (the codes and pass flags are derived from rule_states when the row is written, _finalize)
        "l5_positioning_checks": json.dumps(l5_checks) if l5 else None,
        "claude_confidence":   l6_data.get("confidence"),
        "claude_reasoning":    _l6_reasoning,
        "sentiment_score":        l4_data.get("score"),
        "l4_sentiment_summary":             l4_data.get("summary"),
        "l4_sentiment_score":  l4_data.get("score"),
        "l4_sentiment_conviction":       l4_data.get("conviction"),
        "l1_eligibility_reason":           l1.reason if (l1 and not l1.passed) else None,
        "earnings_near":          int(l1.data.get("earnings_near", False)) if l1 and l1.passed else None,
        "days_to_earnings":       (l1.data.get("days_to_earnings", (l1.data.get("earnings_shadow") or {}).get("days_to_earnings"))
                                   if l1 and l1.passed else None),   # shadow mode records it too (2026-11-14)
    }


_MULTIPLIER_STATS_PATH = Path(__file__).parent.parent.parent / "data" / "bayesian_multiplier_stats.json"


def _persist_multiplier_stats(
    multipliers: dict[str, float],
    regime_bayes_result,
    evaluated: list[tuple[dict, dict]],
    runner: str = "demo",
    pre_exec_queued: int | None = None,
) -> None:
    """
    Append one cycle record to the daily Bayesian multiplier stats file.

    The file accumulates per-cycle entries within a trading day, tagged by
    runner ("demo" or "live") so the two gate runners' cycles don't get
    conflated into one signal. The audit check reads it nightly and flags
    any cycle where regime was present, ≥3 tickers were queued, but every
    multiplier was exactly 1.0 — the silent failure mode where
    ticker_allocations() returned zeros.
    """
    today = date.today().isoformat()
    queued_count    = pre_exec_queued if pre_exec_queued is not None else sum(
        1 for _, r in evaluated if r["outcome"] in ("TRADE_QUEUED", "TRADE_QUEUED_PENDING_L6")
    )
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
        "runner":          runner,
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
            existing = {"date": today, "cycles": [], "suspicious_cycles": {"demo": 0, "live": 0}}
        if not isinstance(existing.get("suspicious_cycles"), dict):
            existing["suspicious_cycles"] = {"demo": 0, "live": 0}

        existing["cycles"].append(cycle)
        if suspicious:
            existing["suspicious_cycles"][runner] = existing["suspicious_cycles"].get(runner, 0) + 1

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
        if result["outcome"] in ("TRADE_QUEUED", "TRADE_QUEUED_PENDING_L6"):
            sector_queued[signal.get("sector", "")].append(
                (signal["ticker"], signal["signal_score"])
            )

    # One implementation, shared with the backtest engine (2026-10-17).
    return RegimeBayes.size_multipliers(regime_bayes_result.allocation, sector_queued)


def _mark(passed: bool | None) -> str:
    return "?" if passed is None else "✓" if passed else "✗"


def _log_summary(ticker: str, result: dict) -> None:
    l3        = result["l3_score"]
    l4        = result.get("l4_sentiment")
    l5        = result.get("l5_positioning")
    l6        = result.get("l6_review")
    outcome   = result["outcome"]

    parts = [f"L3={'✓' if l3['passed'] else '✗'}({l3['score']:.3f})"]
    if l4:
        parts.append(f"L4={'✓' if l4['passed'] else '✗'}({l4.get('score', '?')})")
    if l5:
        parts.append(f"L5={'✓' if l5['passed'] else '✗'}(price {_mark(l5.get('price_group_pass'))} "
                     f"options {_mark(l5.get('options_group_pass'))})")
    if l6:
        parts.append(f"L6={'✓' if l6['passed'] else '✗'}({l6.get('decision')} {l6.get('confidence', 0):.2f})")

    logger.info(f"Gate [{ticker}]: {' → '.join(parts)} → {outcome}")
