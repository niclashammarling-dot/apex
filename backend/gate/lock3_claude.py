"""
Lock 3 — Claude final decision, GPT-4o fallback.

Primary:  claude-sonnet-4-6  (Anthropic)
Fallback: gpt-4o             (OpenAI) — used if Claude is unavailable or errors

Pass condition: decision == "BUY" AND confidence >= LOCK3_CONFIDENCE_MIN
JSON parse failure → treated as HOLD (fail closed, logged).
"""
import json

import anthropic
from loguru import logger

from backend.config import ANTHROPIC_API_KEY, OPENAI_API_KEY, LOCK3_CONFIDENCE_MIN

_CLAUDE_MODEL = "claude-sonnet-4-6"
_GPT_MODEL    = "gpt-4o"

_anthropic_client: anthropic.Anthropic | None = None
_openai_client = None  # openai.OpenAI, typed loosely to avoid import at module load


def _get_anthropic() -> anthropic.Anthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY or None)
    return _anthropic_client


def _get_openai():
    global _openai_client
    if _openai_client is None:
        from openai import OpenAI
        _openai_client = OpenAI(api_key=OPENAI_API_KEY)
    return _openai_client


SYSTEM_PROMPT = """You are the final decision layer in an automated stock trading system.
By the time a ticker reaches you it has already passed two independent filters:
Lock 1 (quantitative momentum and volume) and Lock 2 (social sentiment and news).
Do NOT re-evaluate those — trust that they have done their job.

Your sole responsibilities are:
1. Portfolio risk — compare open_positions against risk_limits.max_positions. Compute drawdown as (risk_limits.starting_balance - wallet_balance) / risk_limits.starting_balance. Reject if positions are at the limit or drawdown exceeds risk_limits.max_drawdown_pct.
2. Sector concentration — compare this ticker's sector value in sector_exposure against risk_limits.max_sector_exposure. Reject if at or near the limit.
3. Sentiment synthesis — given the social themes and sentiment summary from Lock 2, is there any macro or qualitative reason NOT to enter this specific ticker right now?
4. Gate history — if ticker_gate_history is provided, treat it as a pattern signal. Repeated L2 fails indicate persistent sentiment weakness. Repeated leading fails indicate smart money is not positioning. A recent TRADE_EXECUTED means the ticker was entered recently and may still be in play. Weight this history alongside current signals — do not ignore it.
5. When genuinely uncertain across all factors, output HOLD.

Return ONLY valid JSON with exactly these keys:
{
  "decision": "BUY" | "HOLD" | "SELL",
  "confidence": 0.0 to 1.0,
  "position_size_pct": 0.0 to risk_limits.max_position_size,
  "reasoning": "one or two sentences"
}"""


def evaluate(context: dict, confidence_min: float | None = None) -> dict:
    """
    Get a BUY/HOLD/SELL decision from Claude, falling back to GPT-4o if Claude fails.
    Fails closed only if both providers fail.
    """
    effective_min = confidence_min if confidence_min is not None else LOCK3_CONFIDENCE_MIN
    ticker = context.get("ticker", "?")

    # Primary: Claude
    if ANTHROPIC_API_KEY:
        try:
            raw = _call_claude(context)
            return _parse(raw, effective_min, ticker, model=_CLAUDE_MODEL)
        except Exception as e:
            logger.warning(f"Lock 3 [{ticker}]: Claude failed ({e}) — trying GPT-4o fallback")
    else:
        logger.warning(f"Lock 3 [{ticker}]: ANTHROPIC_API_KEY not set — trying GPT-4o fallback")

    # Fallback: GPT-4o
    if OPENAI_API_KEY:
        try:
            raw = _call_openai(context)
            return _parse(raw, effective_min, ticker, model=_GPT_MODEL)
        except Exception as e:
            logger.error(f"Lock 3 [{ticker}]: GPT-4o fallback also failed ({e}) — failing closed")
    else:
        logger.error(f"Lock 3 [{ticker}]: OPENAI_API_KEY not set, no fallback available — failing closed")

    return _fail("both_providers_failed")


# ── Provider calls ────────────────────────────────────────────────────────────

def _call_claude(context: dict) -> str:
    client = _get_anthropic()
    response = client.messages.create(
        model=_CLAUDE_MODEL,
        max_tokens=500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(context, indent=2)}],
    )
    return response.content[0].text.strip()


def _call_openai(context: dict) -> str:
    client = _get_openai()
    response = client.chat.completions.create(
        model=_GPT_MODEL,
        response_format={"type": "json_object"},
        max_tokens=500,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": json.dumps(context, indent=2)},
        ],
    )
    return response.choices[0].message.content.strip()


# ── Parse & result helpers ────────────────────────────────────────────────────

def _parse(raw: str, effective_min: float, ticker: str, model: str) -> dict:
    """Parse a raw JSON string from either provider into a gate result dict."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(f"Lock 3 [{ticker}] ({model}): JSON parse failed — treating as HOLD\nRaw: {raw[:200]}")
        raise  # let evaluate() handle — if Claude, will trigger GPT-4o fallback; if GPT-4o, fails closed

    decision   = parsed.get("decision", "HOLD").upper()
    confidence = float(parsed.get("confidence", 0.0))
    position   = float(parsed.get("position_size_pct", 0.0))
    reasoning  = parsed.get("reasoning", "")

    passed = decision == "BUY" and confidence >= effective_min

    reason = "pass" if passed else (
        f"decision={decision}" if decision != "BUY"
        else f"confidence {confidence:.2f} < {effective_min}"
    )

    logger.info(
        f"Lock 3 [{ticker}] ({model}): {decision} "
        f"confidence={confidence:.2f} passed={passed} — {reasoning}"
    )

    return {
        "lock": 3,
        "passed": passed,
        "decision": decision,
        "confidence": confidence,
        "position_size_pct": position,
        "reasoning": reasoning,
        "reason": reason,
        "model": model,
    }


def _fail(reason: str) -> dict:
    return {
        "lock": 3,
        "passed": False,
        "decision": "HOLD",
        "confidence": 0.0,
        "position_size_pct": 0.0,
        "reasoning": None,
        "reason": reason,
        "model": None,
    }
