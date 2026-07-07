"""
gate/chain.py — Shared gate chain evaluation logic.

Consolidates the _evaluate() function previously duplicated between
gate_runner.py (demo) and gate_runner_live.py (live).

Both runners import evaluate_chain() from here. They differ only in:
    - context builder (wallet source, sector exposure source)
    - execution layer (wallet.execute_trade vs Alpaca bracket order)
    - DB writes (demo_gate_history vs live_gate_history)

The chain runs in order:
    Lock 1 (Eligibility) → Lock 2 (Quant) → Lock 3 (Sentiment)
    → Lock 4 (Leading) → Lock 5 (Claude) → execute

Each lock receives typed arguments. If any lock fails, the chain
stops immediately — downstream locks are not evaluated.

Usage:
    from gate.chain import evaluate_chain, ChainResult
    result = evaluate_chain(ticker, sector, signal_score, context, cfg,
                            on_watchlist=False)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from loguru import logger

# Minimum posterior gap for Bayes divergence to be considered signal vs. noise.
# Calibrated 2026-07-07 against 25 days of labeled cases: 12/17 disagree days have
# margin > 0.10; 4 are < 0.05 (noise).  Recalibrate if market regime changes significantly.
BAYES_MARGIN_SIGNAL_THRESHOLD = 0.10

from backend.gate.lock1_eligibility import evaluate as lock1_evaluate
from backend.gate.lock2_quant import evaluate as lock2_evaluate, _sector_threshold
from backend.gate.lock3_sentiment import evaluate as lock3_evaluate
from backend.gate.lock4_leading import evaluate as lock4_evaluate
from backend.gate.lock5_claude import evaluate as lock5_evaluate
from backend.gate.types import LockResult


@dataclass
class ChainResult:
    """
    Full result of a gate chain evaluation for one ticker.

    Attributes:
        ticker        — ticker symbol
        sector        — apex sector
        approved      — True if all 5 locks passed
        exit_lock     — lock_id where chain stopped (None if all passed)
        lock_results  — {lock_id: LockResult} for all evaluated locks
        final_score   — score of the last evaluated lock
        summary       — human-readable chain outcome
    """
    ticker:       str
    sector:       str
    approved:     bool
    exit_lock:    int | None
    lock_results: dict[int, LockResult] = field(default_factory=dict)
    final_score:  float = 0.0
    summary:      str   = ""
    l5_pending:   bool  = False  # L1-L4 passed; L5 deferred to serial execution phase

    def to_dict(self) -> dict:
        return {
            "ticker":       self.ticker,
            "sector":       self.sector,
            "approved":     self.approved,
            "exit_lock":    self.exit_lock,
            "final_score":  round(self.final_score, 4),
            "summary":      self.summary,
            "lock_results": {k: v.to_dict() for k, v in self.lock_results.items()},
        }


def evaluate_chain(
    ticker:            str,
    sector:            str,
    signal_score:      float,
    context:           dict[str, Any],
    cfg:               dict,
    on_watchlist:      bool        = False,
    stop_after_lock:   int | None  = None,
    sector_thresholds: dict | None = None,
) -> ChainResult:
    """
    Run all five locks in sequence for one ticker.

    Stops at first failure. If all locks pass, approved=True.

    Args:
        ticker:       Stock ticker symbol
        sector:       Apex sector name
        signal_score: Pre-computed score from signals/aggregator.py (0–1)
        context:      Rich context dict for Lock 5 (Claude)
        cfg:          Config dict (demo_config or live_config) — supplies
                      thresholds for all locks
        on_watchlist: If True, Lock 2 applies 15% threshold discount

    Returns:
        ChainResult
    """
    lock_results: dict[int, LockResult] = {}

    # ── Lock 1: Eligibility ───────────────────────────────────────────────────
    l1 = lock1_evaluate(ticker, sector, cfg)
    lock_results[1] = l1
    if not l1.passed:
        return _chain_fail(ticker, sector, lock_results, exit_lock=1)

    # Near-term earnings penalty: reduce signal_score before L2 sees it
    earnings_penalty = l1.data.get("earnings_near_penalty", 0.0)
    if earnings_penalty:
        original = signal_score
        signal_score = signal_score * (1.0 - earnings_penalty)
        logger.debug(
            f"[{ticker}] Earnings near-term penalty {earnings_penalty:.0%}: "
            f"signal_score {original:.4f} → {signal_score:.4f}"
        )

    # Macro pre-event penalty (CPI/NFP day-before): reduce signal_score before L2 sees it
    macro_penalty = l1.data.get("macro_pre_event_penalty", 0.0)
    if macro_penalty:
        original = signal_score
        signal_score = signal_score * (1.0 - macro_penalty)
        logger.debug(
            f"[{ticker}] Macro pre-event penalty {macro_penalty:.0%} "
            f"({l1.data.get('macro_pre_event_event', '')}): "
            f"signal_score {original:.4f} → {signal_score:.4f}"
        )

    # ── Lock 2: Quant ─────────────────────────────────────────────────────────
    l2 = lock2_evaluate(ticker, sector, signal_score, on_watchlist=on_watchlist,
                        sector_thresholds=sector_thresholds)
    lock_results[2] = l2
    if not l2.passed:
        return _chain_fail(ticker, sector, lock_results, exit_lock=2)

    # ── Lock 3: Sentiment ─────────────────────────────────────────────────────
    l3 = lock3_evaluate(ticker, sentiment_min=cfg.get("lock2_sentiment_min"))
    lock_results[3] = l3
    if not l3.passed:
        return _chain_fail(ticker, sector, lock_results, exit_lock=3)

    # ── Lock 4: Leading ───────────────────────────────────────────────────────
    l4 = lock4_evaluate(ticker, sector, min_pass=cfg.get("lock_leading_min_pass", 2))
    lock_results[4] = l4
    if not l4.passed:
        return _chain_fail(ticker, sector, lock_results, exit_lock=4)

    # ETF negative penalty: if the sector ETF 5d return fell below the floor,
    # penalise signal_score and re-check against the quant threshold.
    # Motivation: relative strength in a declining sector increases sector-level
    # drawdown risk even when the ticker outperforms its ETF.
    # Tune: etf_negative_floor (default -1.0%) sets the noise floor; a rate of
    # 0.20 would have penalised the LRCX SOXX=-1.3% entry (score 0.855→0.633 < 0.65).
    etf_negative_penalty = cfg.get("etf_negative_penalty", 0.0)
    if etf_negative_penalty > 0:
        etf_negative_floor = cfg.get("etf_negative_floor", -1.0)
        rs_check = (l4.data or {}).get("checks", {}).get("relative_strength", {})
        etf_ret  = rs_check.get("etf_return")
        if etf_ret is not None and etf_ret < etf_negative_floor:
            original        = signal_score
            signal_score    = signal_score * (1.0 - etf_negative_penalty)
            quant_threshold = _sector_threshold(sector, sector_thresholds)
            logger.debug(
                f"[{ticker}] ETF negative penalty {etf_negative_penalty:.0%} "
                f"(ETF {etf_ret:+.2f}% < floor {etf_negative_floor:.1f}%): "
                f"signal_score {original:.4f} → {signal_score:.4f} "
                f"(sector threshold {quant_threshold:.3f})"
            )
            if signal_score < quant_threshold:
                return _chain_fail(
                    ticker, sector, lock_results, exit_lock=4,
                    reason=(
                        f"ETF negative penalty: sector ETF {etf_ret:+.2f}% below floor "
                        f"{etf_negative_floor:.1f}% — penalised score {signal_score:.4f} "
                        f"< sector threshold {quant_threshold:.3f}"
                    ),
                )

    # ── Mechanical risk guards (pre-L5) ──────────────────────────────────────
    # These enforce hard limits that L5 is instructed to check but can misstate
    # in its JSON decision field (reasoning ≠ decision is a real failure mode).
    _rl = context.get("risk_limits", {})
    if _rl:
        _start_bal   = _rl.get("starting_balance", 0)
        _wallet_bal  = context.get("wallet_balance", _start_bal)
        _max_dd      = _rl.get("max_drawdown_pct", 1.0)
        _drawdown    = (_start_bal - _wallet_bal) / _start_bal if _start_bal > 0 else 0.0
        if _drawdown > _max_dd:
            _dd_result = LockResult.fail(
                lock_id=5,
                reason=f"drawdown {_drawdown:.1%} > max {_max_dd:.1%} (mechanical pre-L5 guard)",
            )
            lock_results[5] = _dd_result
            return _chain_fail(ticker, sector, lock_results, exit_lock=5)

    # Deferred L5 mode — live gate stops here so the serial executor runs L5
    # only for candidates that actually have a slot, avoiding wasted Claude API calls.
    if stop_after_lock == 4:
        return ChainResult(
            ticker=ticker, sector=sector,
            approved=False, exit_lock=None,
            l5_pending=True,
            lock_results=lock_results,
            final_score=l4.score,
            summary=f"{ticker} L1-L4 passed — L5 deferred to execution phase",
        )

    # ── Lock 5: Claude ────────────────────────────────────────────────────────
    l5 = lock5_evaluate(
        ticker,
        sector,
        lock_results,
        context,
        confidence_min=cfg.get("lock3_confidence_min"),
    )
    lock_results[5] = l5
    if not l5.passed:
        return _chain_fail(ticker, sector, lock_results, exit_lock=5)

    # ── All locks passed ──────────────────────────────────────────────────────
    summary = (
        f"{ticker} APPROVED — "
        f"L1={l1.score:.2f} L2={l2.score:.2f} L3={l3.score:.2f} "
        f"L4={l4.score:.2f} L5={l5.score:.2f}"
    )
    logger.info(f"Chain APPROVED: {summary}")

    return ChainResult(
        ticker=ticker,
        sector=sector,
        approved=True,
        exit_lock=None,
        lock_results=lock_results,
        final_score=l5.score,
        summary=summary,
    )


# ── Private helpers ───────────────────────────────────────────────────────────

def _chain_fail(
    ticker:       str,
    sector:       str,
    lock_results: dict[int, LockResult],
    exit_lock:    int,
    reason:       str | None = None,
) -> ChainResult:
    failed  = lock_results[exit_lock]
    msg     = reason if reason is not None else failed.reason
    summary = f"{ticker} REJECTED at Lock {exit_lock} — {msg[:120]}"
    logger.debug(f"Chain REJECTED: {summary}")

    return ChainResult(
        ticker=ticker,
        sector=sector,
        approved=False,
        exit_lock=exit_lock,
        lock_results=lock_results,
        final_score=failed.score,
        summary=summary,
    )


def build_base_context(
    signal:              dict,
    wallet_ctx:          dict,
    cfg:                 dict,
    starting_balance:    float,
    gate_history_fn:     Callable,
    sector_regime:       dict | None = None,
    rotation_scores:     dict | None = None,
    regime_bayes_result: Any        = None,
    mode_label:          str | None  = None,
) -> dict:
    """
    Build the context dict passed to evaluate_chain() and ultimately Lock 5 (Claude).

    Injectable differences between demo and live:
        starting_balance  — cfg["starting_balance"] (demo) vs wallet_ctx["starting_balance"] (live)
        gate_history_fn   — get_ticker_gate_fails (demo) vs get_live_ticker_gate_fails (live)
        mode_label        — None (demo) vs "LIVE — real money" (live)
    """
    ctx: dict[str, Any] = {
        "wallet_balance":  wallet_ctx["balance"],
        "open_positions":  wallet_ctx["open_positions"],
        "sector_exposure": wallet_ctx["sector_exposure"],
        "risk_limits": {
            "starting_balance":    starting_balance,
            "max_positions":       cfg["max_positions"],
            "max_sector_exposure": cfg["max_sector_exposure"],
            "max_position_size":   cfg["max_position_size"],
            "daily_loss_cap":      cfg["daily_loss_cap"],
            "max_drawdown_pct":    cfg.get("max_drawdown_pct", 0.20),
        },
    }
    if mode_label is not None:
        ctx["mode"] = mode_label

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
            next_prob = next(
                (item["probability"] for item in forecast.get("likely_next", [])
                 if item["sector"] == signal["sector"]),
                None,
            )
            confirmed = forecast.get("confirmed_transition")
            # rotation_leader intentionally omitted — equals market_leader (same source).
            # Including it makes two fields look like independent corroboration of one number.
            # The forward-looking fields and leader_breadth are the unique contributions here.
            ctx.update({
                "rotation_predecessor":        forecast.get("predecessor"),
                "sector_next_probability":     next_prob,
                "rotation_confirmed":          confirmed is not None,
                "rotation_transition_prob":    confirmed["probability"] if confirmed else None,
                "rotation_regime_conditioned": forecast.get("regime_conditioned"),
                "rotation_regime_sample_size": forecast.get("regime_sample_size"),
                "leader_breadth":              forecast.get("leader_breadth"),
            })
    except Exception as e:
        logger.debug(f"[{signal['ticker']}]: rotation forecast unavailable — {e}")

    if rotation_scores is not None:
        ctx["ticker_rotation_score"] = rotation_scores.get(signal["ticker"])

    if regime_bayes_result is not None:
        import math as _math
        sector = signal.get("sector", "")
        alloc  = regime_bayes_result.allocation.get(sector, 0.0)
        entry  = next((e for e in regime_bayes_result.leaderboard if e.sector == sector), None)
        ctx["regime_bayes_allocation"]     = alloc
        ctx["regime_bayes_posterior"]      = entry.posterior      if entry else None
        ctx["regime_bayes_adjusted_score"] = entry.adjusted_score if entry else None
        ctx["regime_bayes_rank"]           = entry.rank           if entry else None
        ctx["regime_bayes_qualified"]      = alloc > 0

        # bayes_margin: posterior(conviction_leader) − posterior(market_leader).
        # 0 when both agree on the leader.  > BAYES_MARGIN_SIGNAL_THRESHOLD means the conviction
        # layer has meaningfully higher belief in a different sector than the raw-score snapshot.
        # bayes_conviction_leader is set only when margin exceeds the signal threshold — giving
        # L5 one number to check rather than a boolean that fires most days.
        market_leader = ctx.get("market_leader")
        bayes_leader  = regime_bayes_result.leader
        bayes_posts   = {e.sector: e.posterior for e in regime_bayes_result.leaderboard}
        if market_leader and bayes_leader and market_leader != bayes_leader:
            ml_post = bayes_posts.get(market_leader, 0.0)
            bl_post = bayes_posts.get(bayes_leader, 0.0)
            margin  = round(bl_post - ml_post, 4)
        else:
            margin = 0.0
        ctx["bayes_margin"] = margin
        ctx["bayes_conviction_leader"] = (
            bayes_leader if margin >= BAYES_MARGIN_SIGNAL_THRESHOLD else None
        )

    try:
        fails = gate_history_fn(signal["ticker"], limit=5)
        if fails:
            ctx["ticker_gate_history"] = fails
    except Exception as e:
        logger.debug(f"[{signal['ticker']}]: gate fail history unavailable — {e}")

    return ctx
