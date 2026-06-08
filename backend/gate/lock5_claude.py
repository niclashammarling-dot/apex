"""
gate/lock5_claude.py — Lock 5: Claude Synthesis

The final and most contextual gate in the apex lock chain.
By the time a ticker reaches here it has passed:
    Lock 1 — sector is actively allocated, macro conditions clear
    Lock 2 — quant signal above sector threshold
    Lock 3 — sentiment is not strongly negative
    Lock 4 — leading signals confirm direction

Claude's role is synthesis — does everything together make sense
given the current portfolio state?

Model: claude-sonnet-4-6 only. No fallback to any other provider.

Rationale: L5 is a synthesis judge. A judge that hallucinates context produces
confident wrong output that is invisible to everything downstream — strictly worse
than no judge. When Anthropic is unavailable the gate fails closed; a missed trade
has a bounded cost, an invalid entry approved by a hallucinating fallback does not.
(June 2026 post-mortem: GPT-4o invoked as fallback fabricated "repeated L4 pass"
reasoning when L4 had passed exactly once, on the executing run.)

Do NOT add a provider fallback here. Do NOT change the model to Opus.

Public API:
    from gate.lock5_claude import evaluate
    result = evaluate(ticker, sector, lock_results, context, confidence_min=None)
"""
from __future__ import annotations

import json
from typing import Any

import anthropic
from loguru import logger

from backend.config import ANTHROPIC_API_KEY, LOCK3_CONFIDENCE_MIN
from backend.gate.types import LockResult

LOCK_ID = 5

_SONNET_MODEL = "claude-sonnet-4-6"

_anthropic_client: anthropic.Anthropic | None = None


def _get_anthropic() -> anthropic.Anthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY or None)
    return _anthropic_client


SYSTEM_PROMPT = """You are the final decision layer in an automated stock trading system.
By the time a ticker reaches you it has already passed four independent filters:
Lock 1 (regime eligibility + macro), Lock 2 (quantitative signal), Lock 3 (sentiment),
and Lock 4 (leading indicators). Do NOT re-evaluate those — trust that they have done their job.

Your sole responsibilities are:
1. Portfolio risk — compare open_positions against risk_limits.max_positions. Compute drawdown as (risk_limits.starting_balance - wallet_balance) / risk_limits.starting_balance. Reject if positions are at the limit or drawdown exceeds risk_limits.max_drawdown_pct.
2. Sector concentration — compare this ticker's sector value in sector_exposure against risk_limits.max_sector_exposure. Reject if at or near the limit.
3. Sentiment synthesis — given the sentiment score and themes from Lock 3, is there any macro or qualitative reason NOT to enter this specific ticker right now?
4. Gate history — if ticker_gate_history is provided, treat it as a pattern signal. Repeated L3 fails indicate persistent sentiment weakness. Repeated L4 fails indicate smart money is not positioning. A recent TRADE_EXECUTED means the ticker was entered recently and may still be in play.
5. When genuinely uncertain across all factors, output HOLD.

For `position_size_pct`: if your decision is BUY, set this to risk_limits.max_position_size unless a specific portfolio risk factor (high drawdown, near position limit, sector near capacity) warrants reduction. Do NOT factor regime_bayes_allocation, regime_bayes_posterior, regime_bayes_adjusted_score, regime_bayes_leader, regime_bayes_qualified, regime_bayes_rank, or any Bayesian field into your size — the system applies a separate mechanical Bayesian multiplier to your output after this gate. Your sizing is pure portfolio risk management; allocation weighting is handled downstream and is not your concern.

Return ONLY valid JSON with exactly these keys:
{
  "decision": "BUY" | "HOLD" | "SELL",
  "confidence": 0.0 to 1.0,
  "position_size_pct": 0.0 to risk_limits.max_position_size,
  "reasoning": "one or two sentences"
}"""


def evaluate(
    ticker:         str,
    sector:         str,
    lock_results:   dict[int, LockResult],
    context:        dict[str, Any],
    confidence_min: float | None = None,
) -> LockResult:
    """
    Evaluate final trade approval via Claude synthesis.

    Args:
        ticker:         Stock ticker symbol
        sector:         Apex sector name
        lock_results:   Results from all prior locks (1-4), keyed by lock_id
        context:        Rich context dict from the runner (regime, portfolio,
                        rotation, risk limits, gate history)
        confidence_min: Override minimum confidence (defaults to LOCK3_CONFIDENCE_MIN)

    Returns:
        LockResult with lock_id=5. data contains decision, confidence,
        position_size_pct, reasoning, and model used.
    """
    effective_min = confidence_min if confidence_min is not None else LOCK3_CONFIDENCE_MIN
    payload       = _build_context_payload(ticker, sector, lock_results, context)

    if not ANTHROPIC_API_KEY:
        logger.error(f"Lock 5 [{ticker}]: ANTHROPIC_API_KEY not set — failing closed")
        return LockResult.fail(
            lock_id=LOCK_ID,
            reason="anthropic_unavailable",
            data={"decision": "HOLD", "confidence": 0.0, "position_size_pct": 0.0,
                  "reasoning": None, "model": None},
        )

    try:
        raw = _call_anthropic(payload, _SONNET_MODEL)
        return _parse_to_result(raw, effective_min, ticker, _SONNET_MODEL)
    except Exception as e:
        logger.error(f"Lock 5 [{ticker}]: Sonnet failed ({e}) — failing closed")

    return LockResult.fail(
        lock_id=LOCK_ID,
        reason="anthropic_unavailable",
        data={"decision": "HOLD", "confidence": 0.0, "position_size_pct": 0.0,
              "reasoning": None, "model": None},
    )


# ── Context payload ───────────────────────────────────────────────────────────

def _build_context_payload(
    ticker:       str,
    sector:       str,
    lock_results: dict[int, LockResult],
    context:      dict[str, Any],
) -> dict:
    """
    Build the JSON context payload sent to Claude.
    Merges lock results summary into the existing runner context dict.
    """
    lock_summary = {}
    lock_names   = {1: "Eligibility", 2: "Quant", 3: "Sentiment", 4: "Leading"}
    for lid, name in lock_names.items():
        r = lock_results.get(lid)
        if r:
            lock_summary[f"lock{lid}_{name.lower()}"] = {
                "passed": r.passed,
                "score":  round(r.score, 4),
                "reason": r.reason,
                "data":   r.data,
            }

    payload = {
        "ticker":        ticker,
        "sector":        sector,
        "lock_results":  lock_summary,
        **context,
    }
    return payload


# ── Provider calls ────────────────────────────────────────────────────────────

def _call_anthropic(payload: dict, model: str) -> str:
    client   = _get_anthropic()
    response = client.messages.create(
        model      = model,
        max_tokens = 500,
        system     = SYSTEM_PROMPT,
        messages   = [{"role": "user", "content": json.dumps(payload, indent=2, default=str)}],
    )
    return response.content[0].text.strip()


# ── Parse & result ────────────────────────────────────────────────────────────

def _parse_to_result(raw: str, effective_min: float, ticker: str, model: str) -> LockResult:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(f"Lock 5 [{ticker}] ({model}): JSON parse failed — treating as HOLD\nRaw: {raw[:200]}")
        raise

    decision      = parsed.get("decision", "HOLD").upper()
    confidence    = float(parsed.get("confidence", 0.0))
    position_size = float(parsed.get("position_size_pct", 0.0))
    reasoning     = parsed.get("reasoning", "")

    passed = decision == "BUY" and confidence >= effective_min

    reason = "pass" if passed else (
        f"decision={decision}" if decision != "BUY"
        else f"confidence {confidence:.2f} < {effective_min}"
    )

    logger.info(
        f"Lock 5 [{ticker}] ({model}): {decision} "
        f"confidence={confidence:.2f} passed={passed} — {reasoning}"
    )

    data = {
        "decision":          decision,
        "confidence":        confidence,
        "position_size_pct": position_size,
        "reasoning":         reasoning,
        "model":             model,
    }

    if not passed:
        return LockResult.fail(lock_id=LOCK_ID, reason=reason, data=data)

    return LockResult.pass_(lock_id=LOCK_ID, score=confidence, reason=reason, data=data)
