"""
gate/l2_risk.py — L2 RISK: the rules that weigh what a candidate adds to the book's risk.

Lock labels phase 3 (2026-10-31; ruling P3-D, Niclas 2026-10-29: "det ärliga namnet som kan härledas till en viss
plats/typ"): a module holds only its own lock's rules. Two of L2's rules lived in other modules and move here:
  - earnings and earnings_near, from l1_eligibility (a ticker's own event, not market or sector state);
  - etf, the ETF penalty re-test of the score, from chain.py.
The call order is unchanged: l1_eligibility calls check_earnings at the point it always ran the rule, and the
chain calls check_etf after L5. Moving them earlier is phase 4, behind a replay (L4's Grok cache and circuit
breaker make the sequence matter, 10-24 scope). overflow, max_positions and sector_cap still run in the runners'
execution phase until phase 4; lock_codes.RULES.source names each place.
"""
from __future__ import annotations

import time
from datetime import date
from typing import Callable

from loguru import logger

from backend.gate.lock_codes import FAIL, NO_DATA, PASS

# ── Earnings cache ────────────────────────────────────────────────────────────
_earnings_cache: dict[str, dict] = {}
_EARNINGS_TTL = 6 * 3600


def _get_earnings_date(ticker: str) -> date | None:
    now = time.time()
    cached = _earnings_cache.get(ticker)
    if cached and cached["expires_at"] > now:
        return cached["date"]

    try:
        import yfinance as yf
        cal = yf.Ticker(ticker).calendar
        earnings_date = None
        if cal is not None and not cal.empty:
            col = "Earnings Date" if "Earnings Date" in cal.columns else cal.columns[0]
            val = cal[col].iloc[0]
            if hasattr(val, "date"):
                earnings_date = val.date()
            elif isinstance(val, date):
                earnings_date = val
        _earnings_cache[ticker] = {"date": earnings_date, "expires_at": now + _EARNINGS_TTL}
        return earnings_date
    except Exception as e:
        logger.debug(f"L2 Risk: earnings fetch [{ticker}] failed — {e}")
        _earnings_cache[ticker] = {"date": None, "expires_at": now + _EARNINGS_TTL}
        return None


def check_earnings(ticker: str, cfg: dict, today: date) -> tuple[dict | None, dict, str]:
    """Rules earnings and earnings_near. Returns (fail, near, state): fail is the data of an earnings stop
    (the caller builds the LockResult with its reason), near the soft-window data the score re-test reads,
    state the earnings rule's verdict (no_data when the date source returns none)."""
    _ea = cfg.get("macro_earnings_blackout_days")
    earnings_blackout = 3 if _ea is None else _ea
    _en = cfg.get("macro_earnings_near_days")
    earnings_near_days = 14 if _en is None else _en
    _ep = cfg.get("macro_earnings_near_penalty")
    earnings_near_penalty = 0.15 if _ep is None else _ep

    earnings = _get_earnings_date(ticker)
    near: dict = {}
    if earnings:
        delta = (earnings - today).days  # positive = future (pre-event), negative = past
        if 0 <= delta <= earnings_blackout:
            return ({"reason": f"Earnings on {earnings.isoformat()} (Δ{delta}d)", "fail_type": "earnings",
                     "earnings_date": earnings.isoformat(), "days_to_earnings": delta}, {}, FAIL)
        if earnings_blackout < delta <= earnings_near_days:
            near = {
                "earnings_near":         True,
                "earnings_date":         earnings.isoformat(),
                "days_to_earnings":      delta,
                "earnings_near_penalty": earnings_near_penalty,
            }
    return None, near, PASS if earnings else NO_DATA


def check_etf(ticker: str, signal_score: float, l5_data: dict | None, cfg: dict,
              threshold: Callable[[], float]) -> tuple[str, float, str | None]:
    """Rule etf: when the sector ETF's 5-day return is below the floor, the score is penalised and re-tested
    against the sector threshold. Returns (state, score, fail_reason). Motivation: relative strength in a
    declining sector increases sector-level drawdown risk even when the ticker outperforms its ETF. Tune:
    etf_negative_floor (default -1.0%) sets the noise floor; a rate of 0.20 would have penalised the LRCX
    SOXX=-1.3% entry (score 0.855→0.633 < 0.65)."""
    etf_negative_penalty = cfg.get("etf_negative_penalty", 0.0)
    if not etf_negative_penalty > 0:
        return PASS, signal_score, None
    etf_negative_floor = cfg.get("etf_negative_floor", -1.0)
    rs_check = (l5_data or {}).get("checks", {}).get("relative_strength", {})
    etf_ret = rs_check.get("etf_return")
    if etf_ret is None:
        return NO_DATA, signal_score, None
    if etf_ret >= etf_negative_floor:
        return PASS, signal_score, None
    original = signal_score
    signal_score = signal_score * (1.0 - etf_negative_penalty)
    quant_threshold = threshold()
    logger.debug(
        f"[{ticker}] ETF negative penalty {etf_negative_penalty:.0%} "
        f"(ETF {etf_ret:+.2f}% < floor {etf_negative_floor:.1f}%): "
        f"signal_score {original:.4f} → {signal_score:.4f} "
        f"(sector threshold {quant_threshold:.3f})"
    )
    if signal_score < quant_threshold:
        return FAIL, signal_score, (
            f"ETF negative penalty: sector ETF {etf_ret:+.2f}% below floor "
            f"{etf_negative_floor:.1f}% — penalised score {signal_score:.4f} "
            f"< sector threshold {quant_threshold:.3f}")
    return PASS, signal_score, None
