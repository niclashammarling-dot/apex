"""
gate/chain.py — Shared gate chain evaluation logic.

Consolidates the _evaluate() function previously duplicated between
gate_runner.py (demo) and gate_runner_live.py (live).

Both runners import evaluate_chain() from here. They differ only in:
    - context builder (wallet source, sector exposure source)
    - execution layer (wallet.execute_trade vs Alpaca bracket order)
    - DB writes (demo_gate_history vs live_gate_history)

The chain runs in order (each lock's number and name in backend/gate/lock_codes.py LOCKS; a module's LockResult
carries its chain lock as lock_id, and lock_results is keyed by it):
    L1 ELIGIBILITY (and L2 RISK's earnings rules through l2_risk.check_earnings)
    → earnings and macro pre-event penalties on the score
    → L3 SCORE → L4 SENTIMENT → L5 POSITIONING
    → ETF penalty re-test of the score (L2 RISK, rule etf; l2_risk.check_etf)
    → drawdown guard (L0 PORTFOLIO, rule drawdown_guard; here until phase 4 moves it)
    → L6 REVIEW (Claude)
Both runners pass defer_l6_review=True: the chain returns l6_review_pending after the guard, and the runner's
serial execution phase checks overflow (L2 RISK) before it runs L6 REVIEW for a candidate with a slot.

Each lock receives typed arguments. If any lock fails, the chain
stops immediately — downstream locks are not evaluated.

ChainResult.rule_states names every rule that ran, by lock_codes.rule_key, with its verdict (lock labels
phase 2, Niclas 2026-10-29: "Kedjan anger ägare och regel för varje stopp, men även för varje pass").
The stop's owner and rule are lock_codes.stop_of(rule_states), the same function every reader uses.

Usage:
    from gate.chain import evaluate_chain, ChainResult
    result = evaluate_chain(ticker, sector, signal_score, context, cfg,
                            on_watchlist=False)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from loguru import logger

from backend.gate.l1_eligibility import evaluate as l1_eligibility_evaluate
from backend.gate.l3_score import evaluate as l3_score_evaluate, _sector_threshold
from backend.gate.l4_sentiment import evaluate as l4_sentiment_evaluate
from backend.gate.l5_positioning import evaluate as l5_positioning_evaluate
from backend.gate.l6_review import evaluate as l6_review_evaluate
from backend.gate import l2_risk, lock_codes
from backend.gate.lock_codes import FAIL, PASS
from backend.gate.types import LockResult

# Minimum posterior gap for Bayes divergence to be considered signal vs. noise.
# Calibrated 2026-07-07 against 25 days of labeled cases: 12/17 disagree days have
# margin > 0.10; 4 are < 0.05 (noise).  Recalibrate if market regime changes significantly.
BAYES_MARGIN_SIGNAL_THRESHOLD = 0.10


@dataclass
class ChainResult:
    """
    Full result of a gate chain evaluation for one ticker.

    Attributes:
        ticker        — ticker symbol
        sector        — apex sector
        approved      — True if every lock passed
        lock_results  — {chain lock: LockResult} for every evaluated lock (the guard's under 0)
        final_score   — score of the last evaluated lock
        summary       — human-readable chain outcome
        rule_states   — {rule_key: PASS | FAIL | NO_DATA} for every rule that ran (lock_codes); the stop
                        and the stored codes come from it (stop, lock_codes.codes_for)
    """
    ticker:       str
    sector:       str
    approved:     bool
    lock_results: dict[int, LockResult] = field(default_factory=dict)
    final_score:  float = 0.0
    summary:      str   = ""
    l6_review_pending:   bool  = False   # L1-L5 passed; L6 REVIEW deferred to the serial execution phase
    exit_reason:  str | None = None  # custom reason for non-standard exits (shown in L6 REVIEW's slot)
    rule_states:  dict[str, str] = field(default_factory=dict)

    @property
    def stop(self) -> tuple[int, str] | None:
        """(owner, rule) of the stop, None when no rule failed (passed, pending L6, or an unknown stop)."""
        return lock_codes.stop_of(self.rule_states)

    def to_dict(self) -> dict:
        return {
            "ticker":       self.ticker,
            "sector":       self.sector,
            "approved":     self.approved,
            "stop":         list(self.stop) if self.stop else None,
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
    defer_l6_review:   bool        = False,
    sector_thresholds: dict | None = None,
) -> ChainResult:
    """
    Run the chain's locks in sequence for one ticker.

    Stops at first failure. If all locks pass, approved=True.

    Args:
        ticker:       Stock ticker symbol
        sector:       Apex sector name
        signal_score: Pre-computed score from signals/aggregator.py (0–1)
        context:      Rich context dict for L6 REVIEW (Claude)
        cfg:          Config dict (demo_config or live_config) — supplies
                      thresholds for all locks
        on_watchlist: If True, L3 SCORE applies 15% threshold discount

    Returns:
        ChainResult
    """
    lock_results: dict[int, LockResult] = {}
    states: dict[str, str] = {}

    # ── L1 ELIGIBILITY ────────────────────────────────────────────────────────
    l1 = l1_eligibility_evaluate(ticker, sector, cfg)
    lock_results[1] = l1
    for ident, verdict in l1.data.get("rule_states", {}).items():
        if not lock_codes.put(states, ident, verdict):   # outside the register: the stop is unknown, not a raise
            logger.error(f"[{ticker}] L1 ELIGIBILITY identifier {ident!r} is not in lock_codes.RULES — stop unknown")
    if not l1.passed:
        return _chain_fail(ticker, sector, lock_results, l1, states=states)
    unpenalised_score = signal_score

    # Near-term earnings penalty: reduce signal_score before L3 SCORE sees it
    earnings_penalty = l1.data.get("earnings_near_penalty", 0.0)
    if earnings_penalty:
        original = signal_score
        signal_score = signal_score * (1.0 - earnings_penalty)
        logger.debug(
            f"[{ticker}] Earnings near-term penalty {earnings_penalty:.0%}: "
            f"signal_score {original:.4f} → {signal_score:.4f}"
        )

    # Macro pre-event penalty (CPI/NFP day-before): reduce signal_score before L3 SCORE sees it
    macro_penalty = l1.data.get("l1_eligibility_macro_pre_event_penalty", 0.0)
    if macro_penalty:
        original = signal_score
        signal_score = signal_score * (1.0 - macro_penalty)
        logger.debug(
            f"[{ticker}] Macro pre-event penalty {macro_penalty:.0%} "
            f"({l1.data.get('macro_pre_event_event', '')}): "
            f"signal_score {original:.4f} → {signal_score:.4f}"
        )

    # ── L3 SCORE ──────────────────────────────────────────────────────────────
    l3 = l3_score_evaluate(ticker, sector, signal_score, on_watchlist=on_watchlist,
                           sector_thresholds=sector_thresholds, fallback=cfg.get("l3_score_threshold_min"))
    lock_results[3] = l3
    states.update(_score_states(l1, l3, unpenalised_score))
    if not l3.passed:
        return _chain_fail(ticker, sector, lock_results, l3, states=states)

    # ── L4 SENTIMENT ──────────────────────────────────────────────────────────
    l4 = l4_sentiment_evaluate(ticker, sentiment_min=cfg.get("l4_sentiment_grok_min"))
    lock_results[4] = l4
    states.update(verdict_states(4, "grok", l4))
    if not l4.passed:
        return _chain_fail(ticker, sector, lock_results, l4, states=states)

    # ── L5 POSITIONING ────────────────────────────────────────────────────────
    l5 = l5_positioning_evaluate(ticker, sector, pcr_mode=cfg.get("l5_positioning_leading_mode", "pooled_scalar"))
    lock_results[5] = l5
    states.update(verdict_states(5, "leading", l5))
    if not l5.passed:
        return _chain_fail(ticker, sector, lock_results, l5, states=states)

    # L2 RISK's ETF re-test (l2_risk.check_etf), after L5 POSITIONING as it always ran.
    etf_key = lock_codes.rule_key(2, "etf")
    states[etf_key], signal_score, etf_fail = l2_risk.check_etf(
        ticker, signal_score, l5.data, cfg, lambda: _sector_threshold(sector, sector_thresholds,
                                                               cfg.get("l3_score_threshold_min")))
    if etf_fail:
        return _chain_fail(ticker, sector, lock_results, l5, reason=etf_fail, states=states)

    # ── Mechanical risk guards (before L6 REVIEW) ────────────────────────────
    # These enforce hard limits that L6 REVIEW is instructed to check but can misstate
    # in its JSON decision field (reasoning ≠ decision is a real failure mode).
    _rl = context.get("risk_limits", {})
    if _rl:
        _start_bal   = _rl.get("starting_balance", 0)
        _wallet_bal  = context.get("wallet_balance", _start_bal)
        _max_dd      = _rl.get("max_drawdown_pct", 1.0)
        _drawdown    = (_start_bal - _wallet_bal) / _start_bal if _start_bal > 0 else 0.0
        states[lock_codes.rule_key(0, "drawdown_guard")] = FAIL if _drawdown > _max_dd else PASS
        if _drawdown > _max_dd:
            _dd_result = LockResult.fail(
                lock_id=0,
                reason=f"drawdown {_drawdown:.1%} > max {_max_dd:.1%} (mechanical pre-L5 guard)",
            )
            lock_results[0] = _dd_result
            return _chain_fail(ticker, sector, lock_results, _dd_result, states=states)

    # Deferred L6 REVIEW — the runners stop here so the serial executor runs L6 REVIEW
    # only for candidates that actually have a slot, avoiding wasted Claude API calls.
    if defer_l6_review:
        return ChainResult(
            ticker=ticker, sector=sector,
            approved=False,
            l6_review_pending=True,
            lock_results=lock_results,
            final_score=l5.score,
            summary=f"{ticker} L1-L5 passed — L6 REVIEW deferred to the execution phase",
            rule_states=states,
        )

    # ── L6 REVIEW ─────────────────────────────────────────────────────────────
    l6 = l6_review_evaluate(
        ticker,
        sector,
        lock_results,
        context,
        confidence_min=cfg.get("l6_review_claude_min"),
    )
    lock_results[6] = l6
    states.update(verdict_states(6, "claude", l6))
    if not l6.passed:
        return _chain_fail(ticker, sector, lock_results, l6, states=states)

    # ── All locks passed ──────────────────────────────────────────────────────
    summary = (
        f"{ticker} APPROVED — "
        f"L1={l1.score:.2f} L3={l3.score:.2f} L4={l4.score:.2f} "
        f"L5={l5.score:.2f} L6={l6.score:.2f}"
    )
    logger.info(f"Chain APPROVED: {summary}")

    return ChainResult(
        ticker=ticker,
        sector=sector,
        approved=True,
        lock_results=lock_results,
        final_score=l6.score,
        summary=summary,
        rule_states=states,
    )


def verdict_states(owner: int, verdict_rule: str, result: LockResult) -> dict[str, str]:
    """A judging lock's rule state (L4 grok, L5 leading, L6 claude): its verdict, or `unavailable` when the lock
    says a source or data error decided it (the lock's data["unavailable"], never its reason text). The runners'
    deferred L6 uses it too (gate_runner._run_l6_review_for_result)."""
    if result.passed:
        return {lock_codes.rule_key(owner, verdict_rule): PASS}
    rule = "unavailable" if result.data.get("unavailable") else verdict_rule
    return {lock_codes.rule_key(owner, rule): FAIL}


def _score_states(l1: LockResult, l3: LockResult, unpenalised_score: float) -> dict[str, str]:
    """L3's threshold test and the two pre-event penalties that bite at it: L1's macro_pre_event and L2's
    earnings_near. The threshold fails only when the unpenalised score fails it too; otherwise each penalty that
    applied fails, and stop_of gives the stop to the first in chain order (ruling 2026-10-28). A failed test
    without its threshold names no rule, so the row's stop is unknown and alerts, rather than guessed."""
    threshold = l3.data.get("effective_threshold")
    if not l3.passed and threshold is None:
        return {}
    own_fail  = (not l3.passed) and unpenalised_score < threshold
    penalty_fail = (not l3.passed) and not own_fail
    out = {lock_codes.rule_key(3, "threshold"): FAIL if own_fail else PASS}
    for owner, rule, key in ((1, "macro_pre_event", "l1_eligibility_macro_pre_event_penalty"),
                             (2, "earnings_near", "earnings_near_penalty")):
        out[lock_codes.rule_key(owner, rule)] = FAIL if (penalty_fail and l1.data.get(key)) else PASS
    return out


# ── Private helpers ───────────────────────────────────────────────────────────

def _chain_fail(
    ticker:       str,
    sector:       str,
    lock_results: dict[int, LockResult],
    failed:       LockResult,
    reason:       str | None = None,
    states:       dict[str, str] | None = None,
) -> ChainResult:
    msg     = reason if reason is not None else failed.reason
    stop    = lock_codes.stop_of(states or {})
    summary = f"{ticker} REJECTED at {lock_codes.lock_label(stop[0]) if stop else 'an unknown stop'} — {msg[:120]}"
    logger.debug(f"Chain REJECTED: {summary}")

    return ChainResult(
        ticker=ticker,
        sector=sector,
        approved=False,
        lock_results=lock_results,
        final_score=failed.score,
        summary=summary,
        exit_reason=msg,
        rule_states=dict(states or {}),
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
    Build the context dict passed to evaluate_chain() and ultimately L6 REVIEW (Claude).

    Injectable differences between demo and live:
        starting_balance  — cfg["starting_balance"] (demo) vs wallet_ctx["starting_balance"] (live)
        gate_history_fn   — get_ticker_gate_fails (demo) vs get_live_ticker_gate_fails (live)
        mode_label        — None (demo) vs "LIVE — real money" (live)
    """
    # The cap execution enforces for this ticker's sector today (dynamic, from the
    # runner), falling back to the flat base as execution does (2026-10-17).
    _caps       = wallet_ctx.get("dynamic_caps") or {}
    _sector     = signal.get("sector", "")
    ctx: dict[str, Any] = {
        "wallet_balance":  wallet_ctx["balance"],
        "open_positions":  wallet_ctx["open_positions"],
        "sector_exposure": wallet_ctx["sector_exposure"],
        # Claude's context keeps its own names (the prompt reads risk_limits.max_positions …); a rename there
        # is a prompt change for CHECK 90, not a key rename.
        "risk_limits": {
            "starting_balance":    starting_balance,
            "max_positions":       cfg["l2_risk_max_positions_count"],
            "sector_cap":          _caps.get(_sector, cfg["l2_risk_sector_cap_max"]),
            "sector_cap_source":   "dynamic" if _sector in _caps else "flat_fallback",
            "max_sector_exposure": cfg["l2_risk_sector_cap_max"],
            "max_position_size":   cfg["max_position_size"],
            "daily_loss_cap":      cfg["l0_portfolio_loss_cap_max"],
            "max_drawdown_pct":    cfg.get("l0_portfolio_drawdown_guard_max", 0.20),
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
        # L6 REVIEW one number to check rather than a boolean that fires most days.
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
