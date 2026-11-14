"""
gate/l2_risk.py — L2 RISK: the rules that weigh what a candidate adds to the book's risk.

Lock labels phase 3 (2026-10-31; ruling P3-D, Niclas 2026-10-29: "det ärliga namnet som kan härledas till en viss
plats/typ"): a module holds only its own lock's rules. Two of L2's rules lived in other modules and move here:
  - earnings and earnings_near, from l1_eligibility (a ticker's own event, not market or sector state);
  - etf, the ETF penalty re-test of the score, from chain.py.
The call order is unchanged: l1_eligibility calls check_earnings at the point it always ran the rule, and the
chain calls check_etf after L5. Moving them earlier is phase 4, behind a replay (L4's Grok cache and circuit
breaker make the sequence matter, 10-24 scope). overflow, l2_risk_max_positions_count and sector_cap still run in the runners'
execution phase until phase 4; lock_codes.RULES.source names each place.
"""
from __future__ import annotations

import time
from datetime import date, datetime
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
        earnings_date = _earnings_from_calendar(yf.Ticker(ticker).calendar)
        _earnings_cache[ticker] = {"date": earnings_date, "expires_at": now + _EARNINGS_TTL}
        return earnings_date
    except Exception as e:
        logger.debug(f"L2 Risk: earnings fetch [{ticker}] failed — {e}")
        _earnings_cache[ticker] = {"date": None, "expires_at": now + _EARNINGS_TTL}
        return None


def _earnings_from_calendar(cal) -> date | None:
    """The next earnings date from yf.Ticker(...).calendar: a dict ({"Earnings Date": [date, ...]}) in yfinance
    0.2.x (0.2.66 read 2026-11-14), a DataFrame before. Found 2026-10-18: the DataFrame-only code tested
    `cal.empty` on the dict, the AttributeError was debug-logged, and the rule never fired in recorded history.
    A two-date range gives its earlier date, the first day the report can fall on."""
    if cal is None:
        return None
    if isinstance(cal, dict):
        val = cal.get("Earnings Date")
        if isinstance(val, (list, tuple)):
            val = min((v for v in val if v is not None), default=None)
    else:
        if getattr(cal, "empty", True):
            return None
        col = "Earnings Date" if "Earnings Date" in cal.columns else cal.columns[0]
        val = cal[col].iloc[0]
    if isinstance(val, datetime) or (val is not None and not isinstance(val, date) and hasattr(val, "date")):
        return val.date()
    return val if isinstance(val, date) else None


def check_earnings(ticker: str, cfg: dict, today: date) -> tuple[dict | None, dict, str]:
    """Rules earnings and earnings_near. Returns (fail, near, state): fail is the data of an earnings stop
    (the caller builds the LockResult with its reason), near the soft-window data the score re-test reads,
    state the earnings rule's verdict (no_data when the date source returns none).

    Shadow mode (config.L2_RISK_EARNINGS_SHADOW, 2026-11-14): inside the near window (0..near_days, the blackout
    included) the data carries {"earnings_shadow": {days_to_earnings, earnings_date}} only: nothing blocks or
    penalises (no fail, no earnings_near_penalty key), the gate row records days_to_earnings from it, and L6's
    payload leaves the key out (l6_review._BOOKKEEPING), so Claude does not see a report date the active rule
    would not have shown it. The shadow read derives would-block (<= l2_risk_earnings_days) and would-penalise
    from days_to_earnings."""
    from backend import config
    _ea = cfg.get("l2_risk_earnings_days")
    earnings_blackout = config.L2_RISK_EARNINGS_DAYS if _ea is None else _ea
    _en = cfg.get("l2_risk_earnings_near_days")
    earnings_near_days = config.L2_RISK_EARNINGS_NEAR_DAYS if _en is None else _en
    _ep = cfg.get("l2_risk_earnings_near_penalty")
    earnings_near_penalty = config.L2_RISK_EARNINGS_NEAR_PENALTY if _ep is None else _ep

    earnings = _get_earnings_date(ticker)
    near: dict = {}
    if earnings:
        delta = (earnings - today).days  # positive = future (pre-event), negative = past
        if config.L2_RISK_EARNINGS_SHADOW:
            if 0 <= delta <= earnings_near_days:
                near = {"earnings_shadow": {"days_to_earnings": delta, "earnings_date": earnings.isoformat()}}
            return None, near, PASS
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
    l2_risk_etf_floor (default -1.0%) sets the noise floor; a rate of 0.20 would have penalised the LRCX
    SOXX=-1.3% entry (score 0.855→0.633 < 0.65)."""
    l2_risk_etf_penalty = cfg.get("l2_risk_etf_penalty", 0.0)
    if not l2_risk_etf_penalty > 0:
        return PASS, signal_score, None
    l2_risk_etf_floor = cfg.get("l2_risk_etf_floor", -1.0)
    rs_check = (l5_data or {}).get("checks", {}).get("relative_strength", {})
    etf_ret = rs_check.get("etf_return")
    if etf_ret is None:
        return NO_DATA, signal_score, None
    if etf_ret >= l2_risk_etf_floor:
        return PASS, signal_score, None
    original = signal_score
    signal_score = signal_score * (1.0 - l2_risk_etf_penalty)
    quant_threshold = threshold()
    logger.debug(
        f"[{ticker}] ETF negative penalty {l2_risk_etf_penalty:.0%} "
        f"(ETF {etf_ret:+.2f}% < floor {l2_risk_etf_floor:.1f}%): "
        f"signal_score {original:.4f} → {signal_score:.4f} "
        f"(sector threshold {quant_threshold:.3f})"
    )
    if signal_score < quant_threshold:
        return FAIL, signal_score, (
            f"ETF negative penalty: sector ETF {etf_ret:+.2f}% below floor "
            f"{l2_risk_etf_floor:.1f}% — penalised score {signal_score:.4f} "
            f"< sector threshold {quant_threshold:.3f}")
    return PASS, signal_score, None
