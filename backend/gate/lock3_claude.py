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
You receive quantitative signals and social sentiment that have already passed initial thresholds.
Your job is to make the final BUY, HOLD, or SELL decision based on the full picture —
including risk exposure, sector concentration, price context, and qualitative judgment.

Rules:
- Never exceed 40% exposure in one sector
- Never open more than 6 simultaneous positions
- Consider the stock's position relative to its 60-day range — avoid buying near 60d highs
- When uncertain, output HOLD

Return ONLY valid JSON with exactly these keys:
{
  "decision": "BUY" | "HOLD" | "SELL",
  "confidence": 0.0 to 1.0,
  "position_size_pct": 0.0 to 0.25,
  "reasoning": "one or two sentences"
}"""


def evaluate(context: dict) -> dict:
    """
    Send the full signal context to OpenAI and get a BUY/HOLD/SELL decision.
    Returns a gate result dict.
    """
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

    passed = decision == "BUY" and confidence >= LOCK3_CONFIDENCE_MIN

    reason = "pass" if passed else (
        f"decision={decision}" if decision != "BUY"
        else f"confidence {confidence:.2f} < {LOCK3_CONFIDENCE_MIN}"
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
