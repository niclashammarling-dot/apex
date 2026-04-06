"""
Lock 1.5 — Macro event filter.

Blocks entries on high-risk macro days before wasting Grok/Claude API calls.

Three checks:
1. VIX above threshold — market-wide fear, block all tickers
2. Within N days of a scheduled macro event (FOMC, CPI, NFP) — block all tickers
3. Ticker earnings within N days — block that specific ticker only

All thresholds are read from config at call time (demo_config / live_config).
VIX is cached for 30 minutes to avoid hammering yfinance on every gate run.
"""
import threading
from datetime import date, datetime, timedelta
from loguru import logger

# ── VIX cache ─────────────────────────────────────────────────────────────────
_vix_cache: dict = {}          # {"value": float, "expires_at": float}
_vix_lock  = threading.Lock()
_VIX_TTL   = 30 * 60          # 30 minutes in seconds


def _get_vix() -> float | None:
    import time
    import yfinance as yf

    with _vix_lock:
        now = time.time()
        if _vix_cache.get("expires_at", 0) > now:
            return _vix_cache["value"]

    try:
        data  = yf.download("^VIX", period="1d", interval="1m", progress=False, auto_adjust=True)
        value = float(data["Close"].iloc[-1]) if not data.empty else None
        with _vix_lock:
            _vix_cache["value"]      = value
            _vix_cache["expires_at"] = time.time() + _VIX_TTL
        logger.debug(f"Macro: VIX fetched = {value:.2f}" if value else "Macro: VIX fetch returned empty")
        return value
    except Exception as e:
        logger.warning(f"Macro: VIX fetch failed — {e}")
        return None


# ── Economic calendar ──────────────────────────────────────────────────────────
# FOMC decision dates (second day of 2-day meeting) — Fed publishes these in advance
_FOMC_DATES = {
    date(2025, 1, 29), date(2025, 3, 19), date(2025, 5, 7),  date(2025, 6, 18),
    date(2025, 7, 30), date(2025, 9, 17), date(2025, 10, 29), date(2025, 12, 10),
    date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29), date(2026, 6, 17),
    date(2026, 7, 29), date(2026, 9, 16), date(2026, 10, 28), date(2026, 12, 9),
}

# CPI release dates (BLS publishes schedule in advance)
_CPI_DATES = {
    date(2025, 1, 15), date(2025, 2, 12), date(2025, 3, 12), date(2025, 4, 10),
    date(2025, 5, 13), date(2025, 6, 11), date(2025, 7, 15), date(2025, 8, 12),
    date(2025, 9, 10), date(2025, 10, 15), date(2025, 11, 13), date(2025, 12, 10),
    date(2026, 1, 14), date(2026, 2, 11), date(2026, 3, 11), date(2026, 4, 8),
    date(2026, 5, 13), date(2026, 6, 10), date(2026, 7, 15), date(2026, 8, 12),
    date(2026, 9, 9),  date(2026, 10, 14), date(2026, 11, 12), date(2026, 12, 9),
}


def _nfp_dates(start_year: int = 2025, end_year: int = 2026) -> set[date]:
    """Non-Farm Payrolls — first Friday of each month."""
    dates = set()
    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            d = date(year, month, 1)
            # Advance to first Friday (weekday 4)
            days_ahead = (4 - d.weekday()) % 7
            dates.add(d + timedelta(days=days_ahead))
    return dates


_NFP_DATES   = _nfp_dates()
_MACRO_DATES = _FOMC_DATES | _CPI_DATES | _NFP_DATES


def _days_to_nearest_event(today: date, blackout: int) -> tuple[bool, str | None]:
    """Return (is_blocked, event_name) if today is within blackout days of any event."""
    for d in _MACRO_DATES:
        delta = abs((d - today).days)
        if delta <= blackout:
            if d in _FOMC_DATES:
                name = "FOMC"
            elif d in _CPI_DATES:
                name = "CPI"
            else:
                name = "NFP"
            return True, f"{name} on {d.isoformat()} (Δ{delta}d)"
    return False, None


# ── Earnings ───────────────────────────────────────────────────────────────────
_earnings_cache: dict[str, dict] = {}   # ticker → {"date": date|None, "expires_at": float}
_EARNINGS_TTL = 6 * 3600                # 6 hours


def _get_earnings_date(ticker: str) -> date | None:
    import time
    import yfinance as yf

    now = time.time()
    cached = _earnings_cache.get(ticker)
    if cached and cached["expires_at"] > now:
        return cached["date"]

    try:
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
        logger.debug(f"Macro: earnings fetch [{ticker}] failed — {e}")
        _earnings_cache[ticker] = {"date": None, "expires_at": now + _EARNINGS_TTL}
        return None


# ── Public API ─────────────────────────────────────────────────────────────────

def evaluate(ticker: str, cfg: dict) -> dict:
    """
    Run all macro checks for a ticker.
    cfg keys used: vix_threshold, macro_event_blackout_days, macro_earnings_blackout_days
    Returns {"passed": bool, "reason": str, "vix": float|None}
    """
    _vix = cfg.get("vix_threshold")
    vix_threshold      = _vix if _vix is not None else 25.0
    _eb = cfg.get("macro_event_blackout_days")
    event_blackout     = _eb if _eb is not None else 1
    _earn = cfg.get("macro_earnings_blackout_days")
    earnings_blackout  = _earn if _earn is not None else 3
    today              = date.today()

    # 1. VIX check
    vix = _get_vix()
    if vix is not None and vix >= vix_threshold:
        return _fail(f"VIX={vix:.1f} >= threshold {vix_threshold}", vix)

    # 2. Macro event proximity
    blocked, event_desc = _days_to_nearest_event(today, event_blackout)
    if blocked:
        return _fail(f"macro event: {event_desc}", vix)

    # 3. Earnings proximity
    earnings = _get_earnings_date(ticker)
    if earnings:
        delta = abs((earnings - today).days)
        if delta <= earnings_blackout:
            return _fail(f"earnings on {earnings.isoformat()} (Δ{delta}d)", vix)

    return {"passed": True, "reason": "pass", "vix": vix}


def _fail(reason: str, vix: float | None) -> dict:
    logger.info(f"Macro filter BLOCKED — {reason}")
    return {"passed": False, "reason": reason, "vix": vix}
