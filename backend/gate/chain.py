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
from typing import Any

from loguru import logger

from backend.gate.lock1_eligibility import evaluate as lock1_evaluate
from backend.gate.lock2_quant import evaluate as lock2_evaluate
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
) -> ChainResult:
    failed  = lock_results[exit_lock]
    summary = f"{ticker} REJECTED at Lock {exit_lock} — {failed.reason[:120]}"
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
