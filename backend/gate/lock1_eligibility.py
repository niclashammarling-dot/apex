"""
gate/lock1_eligibility.py — Lock 1: Eligibility

The first and coarsest gate in the apex lock chain. Combines two checks:

  A. Regime eligibility — is this ticker's sector currently qualifying
     for portfolio allocation? Requires sector adjusted score
     (aggregate × posterior) >= ALLOCATION_FLOOR (0.35).

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
from backend.regime.regime_bayes import ALLOCATION_FLOOR, LEADERBOARD_SIZE

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

# FOMC spans two meeting days; pre-event window is 2 to cover the full session.
# CPI and NFP are single-day releases; their window comes from macro_event_blackout_days (cfg).
_FOMC_PRE_EVENT_DAYS = 2

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
      4. Earnings: three-tier pre-event gate
           imminent  (0 ≤ Δ ≤ blackout_days)  → hard fail
           near-term (blackout < Δ ≤ near_days) → pass + score penalty in chain.py
           clear     (Δ > near_days)            → pass unchanged
         Post-earnings (Δ < 0) is never blocked.

    Args:
        ticker: Stock ticker symbol
        sector: Apex sector name
        cfg:    Config dict (demo_config or live_config) — supplies
                vix_threshold, macro_event_blackout_days,
                macro_earnings_blackout_days, macro_earnings_near_days,
                macro_earnings_near_penalty

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

    if adjusted_score < ALLOCATION_FLOOR:
        return LockResult.fail(
            lock_id=LOCK_ID,
            reason=(
                f"Sector '{sector}' adjusted score {adjusted_score:.3f} "
                f"< allocation floor {ALLOCATION_FLOOR:.2f}"
            ),
            data={
                "regime_available": True,
                "fail_type":        "regime_floor",
                "sector":           sector,
                "adjusted_score":   adjusted_score,
                "allocation":       allocation,
                "rank":             rank,
                "floor":            ALLOCATION_FLOOR,
            },
        )

    # ── B. Macro filter ───────────────────────────────────────────────────────
    _vt = cfg.get("vix_threshold")
    vix_threshold     = 25.0 if _vt is None else _vt
    _ep2 = cfg.get("macro_pre_event_penalty")
    macro_pre_event_penalty = 0.05 if _ep2 is None else _ep2
    _ea = cfg.get("macro_earnings_blackout_days")
    earnings_blackout = 3    if _ea is None else _ea
    _en = cfg.get("macro_earnings_near_days")
    earnings_near_days = 14  if _en is None else _en
    _ep = cfg.get("macro_earnings_near_penalty")
    earnings_near_penalty = 0.15 if _ep is None else _ep
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

    macro_status, event_desc = _macro_status(today, _FOMC_PRE_EVENT_DAYS)
    if macro_status == "hard_block":
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
    macro_penalty_data: dict = {}
    if macro_status == "pre_event":
        macro_penalty_data = {
            "macro_pre_event":         True,
            "macro_pre_event_event":   event_desc,
            "macro_pre_event_penalty": macro_pre_event_penalty,
        }

    earnings = _get_earnings_date(ticker)
    earnings_data: dict = {}
    if earnings:
        delta = (earnings - today).days  # positive = future (pre-event), negative = past
        if 0 <= delta <= earnings_blackout:
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
        elif earnings_blackout < delta <= earnings_near_days:
            earnings_data = {
                "earnings_near":         True,
                "earnings_date":         earnings.isoformat(),
                "days_to_earnings":      delta,
                "earnings_near_penalty": earnings_near_penalty,
            }

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
            "floor":            ALLOCATION_FLOOR,
            **earnings_data,
            **macro_penalty_data,
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

# VIX has never closed above ~90 (2008/2020 peaks). Anything above this is a
# yfinance data error, not a market reading — treat as unavailable (don't
# block) rather than substituting a default, so the bad read doesn't get
# masked. 2026-06-04: a single VIX=332.9 read blocked an entire session.
_VIX_SANITY_CEILING = 100.0


def _get_vix() -> float | None:
    with _vix_lock:
        now = time.time()
        if _vix_cache.get("expires_at", 0) > now:
            return _vix_cache["value"]

    try:
        import yfinance as yf
        data  = yf.download("^VIX", period="1d", interval="1m", progress=False, auto_adjust=True)
        value = float(data["Close"].values.flatten()[-1]) if not data.empty else None
        if value is not None and value > _VIX_SANITY_CEILING:
            logger.warning(
                f"Lock 1: VIX reading {value:.1f} exceeds sanity ceiling "
                f"{_VIX_SANITY_CEILING} — discarding as data error, treating as unavailable"
            )
            value = None
        with _vix_lock:
            _vix_cache["value"]      = value
            _vix_cache["expires_at"] = time.time() + _VIX_TTL
        logger.debug(f"Lock 1: VIX={value:.2f}" if value else "Lock 1: VIX fetch empty")
        return value
    except Exception as e:
        logger.warning(f"Lock 1: VIX fetch failed — {e}")
        return None


# ── Macro event helpers ───────────────────────────────────────────────────────

def _macro_status(today: date, fomc_blackout: int) -> tuple[str, str | None]:
    """Return ("hard_block", desc), ("pre_event", desc), or ("clear", None).

    FOMC: hard block for the full pre-event window (fomc_blackout days).
    CPI/NFP: hard block on the event day; pre_event on the day before.
    Post-event entries are never blocked — the print is known.
    """
    for d in _MACRO_DATES:
        days_ahead = (d - today).days
        if days_ahead < 0:
            continue
        if d in _FOMC_DATES:
            if days_ahead <= fomc_blackout:
                return "hard_block", f"FOMC on {d.isoformat()} (in {days_ahead}d)"
        else:
            name = "CPI" if d in _CPI_DATES else "NFP"
            if days_ahead == 0:
                return "hard_block", f"{name} on {d.isoformat()} (today)"
            if days_ahead == 1:
                return "pre_event", f"{name} on {d.isoformat()} (tomorrow)"
    return "clear", None


def _check_calendar_expiry() -> None:
    """Warn when the macro calendar is within 60 days of expiring."""
    today       = date.today()
    future      = [d for d in _MACRO_DATES if d >= today]
    if not future:
        logger.error("Lock 1: macro event calendar is exhausted — update _FOMC_DATES/_CPI_DATES")
        return
    last_date   = max(future)
    days_left   = (last_date - today).days
    if days_left < 60:
        logger.warning(
            f"Lock 1: macro calendar expires in {days_left}d (last entry {last_date.isoformat()}) "
            "— update _FOMC_DATES/_CPI_DATES/_nfp_dates before it runs dry"
        )

_check_calendar_expiry()

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
