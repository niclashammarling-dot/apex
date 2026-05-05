"""
gate/lock4_leading.py — Lock 4: Leading Signals

Renamed and upgraded from gate/lock_leading.py.
Logic is identical — return type upgraded from plain dict to LockResult.
Data is downloaded internally (same as current lock_leading.py).

Four sub-checks that ask "is informed/smart money positioning into this
ticker ahead of a move?":

  1. relative_strength  — ticker 5-day return > its sector ETF 5-day return
  2. put_call_ratio     — near-term options skewing toward calls (P/C < 0.7)
  3. unusual_calls      — call volume >= 2x put volume (unusual accumulation)
  4. insider_cluster    — 2+ distinct insider Form 4 filings in last 14 days

Passes when at least min_pass (default 2) of the 4 checks pass.

Public API:
    from gate.lock4_leading import evaluate
    result = evaluate(ticker, sector, min_pass=2)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import requests
import yfinance as yf
from loguru import logger

from backend.gate.types import LockResult

LOCK_ID  = 4
MIN_PASS = 2


def _build_sector_etf() -> dict[str, str]:
    try:
        from backend.ticker_config import get_sectors
        return {s: cfg["etf"] for s, cfg in get_sectors().items()}
    except Exception:
        return {
            "Technology":      "XLK",
            "Energy":          "XLE",
            "ConsumerDisc":    "XLY",
            "ConsumerStaples": "XLP",
            "Healthcare":      "XLV",
            "Financials":      "XLF",
            "Industrials":     "XLI",
            "Materials":       "XLB",
            "Utilities":       "XLU",
            "RealEstate":      "XLRE",
            "Communication":   "XLC",
        }


SECTOR_ETF = _build_sector_etf()

_EDGAR_SEARCH = (
    "https://efts.sec.gov/LATEST/search-index"
    "?q=%22{ticker}%22&forms=4&dateRange=custom&startdt={start}&enddt={end}"
)
_EDGAR_HEADERS = {"User-Agent": "apex-signal-gate contact@example.com"}

# Sub-check weights for score computation
WEIGHTS = {
    "relative_strength": 0.25,
    "put_call_ratio":    0.25,
    "unusual_calls":     0.25,
    "insider_cluster":   0.25,
}


def evaluate(ticker: str, sector: str, min_pass: int = MIN_PASS) -> LockResult:
    """
    Evaluate leading signals for a ticker.

    Args:
        ticker:   Stock ticker symbol
        sector:   Apex sector name
        min_pass: Minimum sub-checks that must pass (default 2)

    Returns:
        LockResult with lock_id=4
    """
    checks = {}
    for name, fn, args in [
        ("relative_strength", _check_relative_strength, (ticker, sector)),
        ("put_call_ratio",    _check_put_call_ratio,    (ticker,)),
        ("unusual_calls",     _check_unusual_call_volume, (ticker,)),
        ("insider_cluster",   _check_insider_cluster,   (ticker,)),
    ]:
        try:
            checks[name] = fn(*args)
        except Exception as e:
            logger.warning(f"Lock 4 [{ticker}] {name}: {e}")
            checks[name] = {"pass": False, "reason": f"error: {e}"}

    pass_count    = sum(1 for v in checks.values() if v["pass"])
    failed_checks = [k for k, v in checks.items() if not v["pass"]]
    passed        = pass_count >= min_pass

    score = round(sum(WEIGHTS[k] for k, v in checks.items() if v["pass"]), 4)

    data = {
        "checks":        checks,
        "pass_count":    pass_count,
        "min_pass":      min_pass,
        "failed_checks": failed_checks,
        # Kept for DB compat (lock_leading_checks serialised as JSON)
        "sub_checks":    checks,
    }

    if not passed:
        reason = (
            f"Only {pass_count}/{len(checks)} leading checks passed "
            f"(need {min_pass}) — failed: {', '.join(failed_checks)}"
        )
        logger.debug(f"Lock 4 [{ticker}] FAIL — {reason}")
        return LockResult.fail(lock_id=LOCK_ID, reason=reason, data=data)

    passed_names = [k for k, v in checks.items() if v["pass"]]
    reason = (
        f"{pass_count}/{len(checks)} leading checks passed: "
        f"{', '.join(passed_names)}"
    )
    logger.debug(f"Lock 4 [{ticker}] PASS — {reason}")
    return LockResult.pass_(lock_id=LOCK_ID, score=score, reason=reason, data=data)


# ── Sub-checks ────────────────────────────────────────────────────────────────

def _check_relative_strength(ticker: str, sector: str) -> dict:
    """Ticker 5-day return exceeds its sector ETF return."""
    etf = SECTOR_ETF.get(sector)
    if not etf:
        return {"pass": False, "reason": f"no ETF mapped for sector '{sector}'"}

    data = yf.download([ticker, etf], period="8d", progress=False,
                       auto_adjust=True)["Close"]
    if data.empty:
        return {"pass": False, "reason": "price data unavailable"}

    data = data[[ticker, etf]].dropna()
    if len(data) < 2:
        return {"pass": False, "reason": "insufficient price history"}

    ticker_ret = float(data[ticker].iloc[-1] / data[ticker].iloc[0]) - 1
    etf_ret    = float(data[etf].iloc[-1]    / data[etf].iloc[0])    - 1
    passed     = ticker_ret > etf_ret

    return {
        "pass":          passed,
        "ticker_return": round(ticker_ret * 100, 2),
        "etf_return":    round(etf_ret    * 100, 2),
        "etf":           etf,
        "reason":        f"{ticker} {ticker_ret*100:+.1f}% vs {etf} {etf_ret*100:+.1f}% (5d)",
    }


def _check_put_call_ratio(ticker: str) -> dict:
    """Near-term put/call open-interest ratio < 0.7 (calls dominant)."""
    t        = yf.Ticker(ticker)
    expiries = t.options
    if not expiries:
        return {"pass": False, "reason": "no options data"}

    call_oi = put_oi = 0
    for exp in expiries[:2]:
        chain    = t.option_chain(exp)
        call_oi += int(chain.calls["openInterest"].fillna(0).sum())
        put_oi  += int(chain.puts["openInterest"].fillna(0).sum())

    if call_oi + put_oi == 0:
        return {"pass": False, "reason": "zero open interest"}

    pc     = put_oi / call_oi if call_oi > 0 else 99.0
    passed = pc < 0.7

    return {
        "pass":     passed,
        "pc_ratio": round(pc, 2),
        "call_oi":  call_oi,
        "put_oi":   put_oi,
        "reason":   f"P/C {pc:.2f} ({'bullish' if passed else 'neutral/bearish'})",
    }


def _check_unusual_call_volume(ticker: str) -> dict:
    """Call volume >= 2x put volume across near-term expiries."""
    t        = yf.Ticker(ticker)
    expiries = t.options
    if not expiries:
        return {"pass": False, "reason": "no options data"}

    call_vol = put_vol = 0
    for exp in expiries[:2]:
        chain     = t.option_chain(exp)
        call_vol += int(chain.calls["volume"].fillna(0).sum())
        put_vol  += int(chain.puts["volume"].fillna(0).sum())

    if call_vol + put_vol == 0:
        return {"pass": False, "reason": "no volume today"}

    ratio  = call_vol / put_vol if put_vol > 0 else float(call_vol)
    passed = ratio >= 2.0

    return {
        "pass":     passed,
        "cv_ratio": round(ratio, 2),
        "call_vol": call_vol,
        "put_vol":  put_vol,
        "reason":   f"call/put vol {ratio:.1f}x ({'unusual' if passed else 'normal'})",
    }


def _check_insider_cluster(ticker: str) -> dict:
    """2+ distinct insiders filed Form 4 for this ticker in the last 14 days."""
    end   = datetime.now(timezone.utc)
    start = end - timedelta(days=14)

    url = _EDGAR_SEARCH.format(
        ticker=ticker,
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
    )

    resp = requests.get(url, timeout=10, headers=_EDGAR_HEADERS)
    if resp.status_code != 200:
        return {"pass": False, "reason": f"EDGAR returned {resp.status_code}"}

    hits   = resp.json().get("hits", {}).get("hits", [])
    filers = {h.get("_source", {}).get("entity_name", "") for h in hits
              if h.get("_source", {}).get("entity_name")}
    count  = len(filers)
    passed = count >= 2

    return {
        "pass":   passed,
        "count":  count,
        "reason": f"{count} distinct insider Form 4 filer(s) in last 14d",
    }
