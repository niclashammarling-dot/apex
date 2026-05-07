"""
gate/lock1_eligibility.py — Lock 1: Eligibility

The first and coarsest gate in the apex lock chain. Combines two checks:

  A. Regime eligibility — is this ticker's sector currently qualifying
     for portfolio allocation? Requires sector adjusted score
     (aggregate × posterior) >= ALLOCATION_THRESHOLD (0.5).

  B. Macro filter — are current conditions safe for new entries?
       1. VIX above threshold — market-wide fear, block all tickers
       2. Within N days of a scheduled macro event (FOMC, CPI, NFP)
       3. Ticker earnings within N days

If the regime singleton hasn't run yet this session, the lock fails
safely with a clear reason rather than crashing downstream locks.

Deliberately minimal — no scoring logic, no signal computation.
Downstream locks (Quant, Sentiment, Leading, Claude) handle quality.

Public API:
    from gate.lock1_eligibility import evaluate
    result = evaluate(ticker, sector, cfg)
"""
from __future__ import annotations

import threading
import time
from datetime import date, timedelta

from loguru import logger

from backend.config import EXCLUDED_SECTORS
from backend.gate.types import LockResult
from backend.regime.regime_bayes import ALLOCATION_THRESHOLD, LEADERBOARD_SIZE

LOCK_ID = 1

# ── VIX cache ─────────────────────────────────────────────────────────────────
_vix_cache: dict = {}
_vix_lock  = threading.Lock()
_VIX_TTL   = 30 * 60   # 30 minutes

# ── Economic calendar ─────────────────────────────────────────────────────────
_FOMC_DATES = {
    date(2025, 1, 29), date(2025, 3, 19), date(2025, 5, 7),  date(2025, 6, 18),
    date(2025, 7, 30), date(2025, 9, 17), date(2025, 10, 29), date(2025, 12, 10),
    date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29), date(2026, 6, 17),
    date(2026, 7, 29), date(2026, 9, 16), date(2026, 10, 28), date(2026, 12, 9),
}

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
            days_ahead = (4 - d.weekday()) % 7
            dates.add(d + timedelta(days=days_ahead))
    return dates


_NFP_DATES   = _nfp_dates()
_MACRO_DATES = _FOMC_DATES | _CPI_DATES | _NFP_DATES

# ── Earnings cache ────────────────────────────────────────────────────────────
_earnings_cache: dict[str, dict] = {}
_EARNINGS_TTL = 6 * 3600


def evaluate(ticker: str, sector: str, cfg: dict) -> LockResult:
    """
    Evaluate eligibility for a ticker.

    Checks (in order):
      1. Regime: sector must be in leaderboard above allocation floor
      2. VIX: must be below threshold
      3. Macro events: must not be within blackout window of FOMC/CPI/NFP
      4. Earnings: ticker must not have earnings within blackout window

    Args:
        ticker: Stock ticker symbol
        sector: Apex sector name
        cfg:    Config dict (demo_config or live_config) — supplies
                vix_threshold, macro_event_blackout_days,
                macro_earnings_blackout_days

    Returns:
        LockResult with lock_id=1
    """
    # ── 0. Structural exclusion — before any market-state check ──────────────
    if sector in EXCLUDED_SECTORS:
        return LockResult.fail(
            lock_id=LOCK_ID,
            reason=f"Sector '{sector}' excluded from entry: {EXCLUDED_SECTORS[sector]}",
            data={
                "regime_available": True,
                "fail_type":        "excluded_sector",
                "sector":           sector,
                "exclusion_reason": EXCLUDED_SECTORS[sector],
            },
        )

    # ── A. Regime eligibility ─────────────────────────────────────────────────
    regime_result = _get_regime_result()

    if regime_result is None:
        return LockResult.fail(
            lock_id=LOCK_ID,
            reason="Regime module has not run this session — eligibility cannot be determined",
            data={"regime_available": False, "fail_type": "regime_unavailable"},
        )

    sector_entry = _find_sector(regime_result, sector)

    if sector_entry is None:
        leaders = [e.sector for e in regime_result.leaderboard]
        return LockResult.fail(
            lock_id=LOCK_ID,
            reason=(
                f"Sector '{sector}' below leaderboard cutoff — "
                f"only top {LEADERBOARD_SIZE} sectors by adjusted score qualify "
                f"(leaders: {', '.join(leaders)})"
            ),
            data={
                "regime_available":  True,
                "fail_type":         "below_leaderboard_cutoff",
                "sector":            sector,
                "leaderboard_size":  LEADERBOARD_SIZE,
                "leaderboard":       leaders,
            },
        )

    adjusted_score = sector_entry.adjusted_score
    allocation     = sector_entry.allocation
    rank           = sector_entry.rank

    if adjusted_score < ALLOCATION_THRESHOLD:
        return LockResult.fail(
            lock_id=LOCK_ID,
            reason=(
                f"Sector '{sector}' adjusted score {adjusted_score:.3f} "
                f"< allocation floor {ALLOCATION_THRESHOLD:.2f}"
            ),
            data={
                "regime_available": True,
                "fail_type":        "regime_floor",
                "sector":           sector,
                "adjusted_score":   adjusted_score,
                "allocation":       allocation,
                "rank":             rank,
                "floor":            ALLOCATION_THRESHOLD,
            },
        )

    # ── B. Macro filter ───────────────────────────────────────────────────────
    _vt = cfg.get("vix_threshold");          vix_threshold     = 25.0 if _vt is None else _vt
    _eb = cfg.get("macro_event_blackout_days");    event_blackout    = 1    if _eb is None else _eb
    _ea = cfg.get("macro_earnings_blackout_days"); earnings_blackout = 3    if _ea is None else _ea
    today             = date.today()

    vix = _get_vix()
    if vix is not None and vix >= vix_threshold:
        return LockResult.fail(
            lock_id=LOCK_ID,
            reason=f"VIX={vix:.1f} >= threshold {vix_threshold}",
            data={
                "regime_available": True,
                "adjusted_score":   adjusted_score,
                "fail_type":        "vix",
                "vix":              vix,
            },
        )

    blocked, event_desc = _days_to_nearest_event(today, event_blackout)
    if blocked:
        return LockResult.fail(
            lock_id=LOCK_ID,
            reason=f"Macro event blackout: {event_desc}",
            data={
                "regime_available": True,
                "adjusted_score":   adjusted_score,
                "fail_type":        "macro_event",
                "event":            event_desc,
                "vix":              vix,
            },
        )

    earnings = _get_earnings_date(ticker)
    if earnings:
        delta = abs((earnings - today).days)
        if delta <= earnings_blackout:
            return LockResult.fail(
                lock_id=LOCK_ID,
                reason=f"Earnings on {earnings.isoformat()} (Δ{delta}d)",
                data={
                    "regime_available": True,
                    "adjusted_score":   adjusted_score,
                    "fail_type":        "earnings",
                    "earnings_date":    earnings.isoformat(),
                    "days_to_earnings": delta,
                    "vix":              vix,
                },
            )

    # ── Pass ──────────────────────────────────────────────────────────────────
    logger.debug(
        f"Lock 1 [{ticker}] PASS — sector={sector} "
        f"adj={adjusted_score:.3f} alloc={allocation:.1%} rank={rank}"
        + (f" VIX={vix:.1f}" if vix else "")
    )

    return LockResult.pass_(
        lock_id=LOCK_ID,
        score=1.0,
        reason=(
            f"Sector '{sector}' qualifies — "
            f"adjusted score {adjusted_score:.3f} "
            f"(rank #{rank}, allocation {allocation:.1%})"
        ),
        data={
            "regime_available": True,
            "sector":           sector,
            "adjusted_score":   adjusted_score,
            "allocation":       allocation,
            "rank":             rank,
            "floor":            ALLOCATION_THRESHOLD,
            "regime_leader":    regime_result.leader,
            "regime_date":      regime_result.date,
            "vix":              vix,
        },
    )


# ── Regime helpers ────────────────────────────────────────────────────────────

def _get_regime_result():
    """Retrieve the current RegimeResult from the scheduler singleton."""
    try:
        from backend.scheduler import _get_regime_bayes
        rb = _get_regime_bayes()
        if rb is None:
            return None
        return rb.last_result()
    except Exception as e:
        logger.warning(f"Lock 1: could not access regime singleton: {e}")
        return None


def _find_sector(regime_result, sector: str):
    """Find a sector entry in the regime leaderboard by name."""
    for entry in regime_result.leaderboard:
        if entry.sector == sector:
            return entry
    return None


# ── VIX helpers ───────────────────────────────────────────────────────────────

def _get_vix() -> float | None:
    with _vix_lock:
        now = time.time()
        if _vix_cache.get("expires_at", 0) > now:
            return _vix_cache["value"]

    try:
        import yfinance as yf
        data  = yf.download("^VIX", period="1d", interval="1m", progress=False, auto_adjust=True)
        value = float(data["Close"].values.flatten()[-1]) if not data.empty else None
        with _vix_lock:
            _vix_cache["value"]      = value
            _vix_cache["expires_at"] = time.time() + _VIX_TTL
        logger.debug(f"Lock 1: VIX={value:.2f}" if value else "Lock 1: VIX fetch empty")
        return value
    except Exception as e:
        logger.warning(f"Lock 1: VIX fetch failed — {e}")
        return None


# ── Macro event helpers ───────────────────────────────────────────────────────

def _days_to_nearest_event(today: date, blackout: int) -> tuple[bool, str | None]:
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


# ── Earnings helpers ──────────────────────────────────────────────────────────

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
        logger.debug(f"Lock 1: earnings fetch [{ticker}] failed — {e}")
        _earnings_cache[ticker] = {"date": None, "expires_at": now + _EARNINGS_TTL}
        return None
