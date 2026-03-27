"""
Lock 3 — OpenAI final decision.

Pass condition: decision == "BUY" AND confidence >= LOCK3_CONFIDENCE_MIN
JSON parse failure → treated as HOLD (fail closed, logged).
"""
import json

from openai import OpenAI
from loguru import logger

from backend.config import OPENAI_API_KEY, LOCK3_CONFIDENCE_MIN

MODEL = "gpt-4o"

# Module-level client — created once, reused across all calls
_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=OPENAI_API_KEY)
    return _client


SYSTEM_PROMPT = """You are the final decision layer in an automated stock trading system.
By the time a ticker reaches you it has already passed two independent filters:
Lock 1 (quantitative momentum and volume) and Lock 2 (social sentiment and news).
Do NOT re-evaluate those — trust that they have done their job.

Your sole responsibilities are:
1. Portfolio risk — is the current wallet balance and number of open positions healthy enough to take another trade?
2. Sector concentration — would this trade push exposure in one sector too high? (hard limit: 40% of portfolio in one sector)
3. Sentiment synthesis — given the social themes and sentiment summary from Lock 2, is there any macro or qualitative reason NOT to enter this specific ticker right now?
4. When genuinely uncertain across all three, output HOLD.

Return ONLY valid JSON with exactly these keys:
{
  "decision": "BUY" | "HOLD" | "SELL",
  "confidence": 0.0 to 1.0,
  "position_size_pct": 0.0 to 0.25,
  "reasoning": "one or two sentences"
}"""


def evaluate(context: dict, confidence_min: float | None = None) -> dict:
    """
    Send the full signal context to OpenAI and get a BUY/HOLD/SELL decision.
    Returns a gate result dict.
    Pass condition: decision == "BUY" AND confidence >= confidence_min (defaults to LOCK3_CONFIDENCE_MIN).
    """
    effective_min = confidence_min if confidence_min is not None else LOCK3_CONFIDENCE_MIN

    if not OPENAI_API_KEY:
        logger.warning("Lock 3: OPENAI_API_KEY not set — failing closed")
        return _fail("openai_key_missing")

    client = _get_client()

    try:
        response = client.chat.completions.create(
            model=MODEL,
            response_format={"type": "json_object"},
            max_tokens=500,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": json.dumps(context, indent=2)},
            ],
        )
        raw = response.choices[0].message.content.strip()

    except Exception as e:
        logger.error(f"Lock 3 [{context.get('ticker')}]: API call failed — {e}")
        return _fail(f"api_error: {e}")

    # Parse — treat any failure as HOLD
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(f"Lock 3 [{context.get('ticker')}]: JSON parse failed — treating as HOLD\nRaw: {raw[:200]}")
        return _hold(raw, reason="json_parse_failed")

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
        f"Lock 3 [{context.get('ticker')}]: {decision} "
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
    }


def _hold(raw: str, reason: str) -> dict:
    return {
        "lock": 3,
        "passed": False,
        "decision": "HOLD",
        "confidence": 0.0,
        "position_size_pct": 0.0,
        "reasoning": raw[:500] if raw else None,
        "reason": reason,
    }
