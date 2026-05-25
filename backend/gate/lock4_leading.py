"""
gate/lock4_leading.py — Lock 4: Leading Signals

Four sub-checks that ask "is informed/smart money positioning into this
ticker ahead of a move?":

  1. relative_strength    — ticker 5-day return > its sector ETF 5-day return
  2. put_call_ratio       — near-term options skewing toward calls (P/C < threshold)
  3. unusual_calls        — call volume >= 2x put volume (unusual accumulation)
  4. volume_accumulation  — up/down volume ratio > 1.2 over 20 days (equity-market
                            accumulation; independent of options and price momentum)

Passes when at least min_pass (default 2) of the 4 checks pass.

Public API:
    from gate.lock4_leading import evaluate
    result = evaluate(ticker, sector, min_pass=2)
"""
from __future__ import annotations

import yfinance as yf
from loguru import logger

from backend.gate.types import LockResult

LOCK_ID  = 4
MIN_PASS = 2

# PCR threshold — set to 0.85 based on 17-day APEX universe sample (mean 0.827).
# Generic options-literature prior (0.7) passes Utilities/Financials names that are
# excluded from APEX on fundamentals and blocks Technology/Industrials names that are
# the primary targets. 0.85 reflects the actual APEX universe distribution.
# Interim: replace with per-ticker P25 percentile once lock4_pcr_history has 4-8 weeks
# of daily observations (collection started 2026-05-14).
PCR_THRESHOLD = 0.85


def _build_sector_etf() -> dict[str, str]:
    try:
        from backend.config import EXCLUDED_SECTORS
        from backend.ticker_config import get_sectors
        return {s: cfg["etf"] for s, cfg in get_sectors().items() if s not in EXCLUDED_SECTORS}
    except Exception:
        logger.warning("lock4_leading: ticker_config load failed — using hardcoded fallback ETF map")
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
            "Semiconductors":  "SOXX",
            "Defense":         "ITA",
            "Homebuilders":    "ITB",
            "Transportation":  "IYT",
        }


SECTOR_ETF = _build_sector_etf()

# Sub-check weights for score computation
WEIGHTS = {
    "relative_strength":   0.25,
    "put_call_ratio":      0.25,
    "unusual_calls":       0.25,
    "volume_accumulation": 0.25,
}

# Up/down volume ratio threshold — 1.2 grounded in 90-day APEX universe sample
# (65 tickers, 2026-05-14). Distribution bifurcates cleanly: active APEX names
# (NVDA 1.89, AMD 2.21, AAPL 2.19, AMZN 2.24, QCOM 3.30) above threshold;
# excluded sectors (Utilities, beaten-down REITs) below. Median 0.88, P75 1.35.
# Snapshot taken during a recovery period — revisit after 3-6 months of live data.
VOL_ACCUM_THRESHOLD = 1.2
VOL_ACCUM_WINDOW    = 20  # trading days


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
        ("volume_accumulation", _check_volume_accumulation, (ticker,)),
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
    passed = pc < PCR_THRESHOLD

    return {
        "pass":      passed,
        "pc_ratio":  round(pc, 2),
        "call_oi":   call_oi,
        "put_oi":    put_oi,
        "threshold": PCR_THRESHOLD,
        "reason":    f"P/C {pc:.2f} vs threshold {PCR_THRESHOLD} ({'bullish' if passed else 'neutral/bearish'})",
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


def _check_volume_accumulation(ticker: str) -> dict:
    """Up/down volume ratio over 20 days > 1.2 (accumulation dominates distribution)."""
    # Need ~30 calendar days to guarantee 20 trading days after weekends/holidays
    raw = yf.download(ticker, period="30d", progress=False, auto_adjust=True)
    if raw.empty:
        return {"pass": False, "reason": "price/volume data unavailable"}

    close = raw["Close"].squeeze()
    vol   = raw["Volume"].squeeze()
    idx   = close.index.intersection(vol.index)
    close = close[idx].dropna()
    vol   = vol[idx].dropna()

    if len(close) < VOL_ACCUM_WINDOW:
        return {"pass": False, "reason": f"only {len(close)} trading days available (need {VOL_ACCUM_WINDOW})"}

    close = close.tail(VOL_ACCUM_WINDOW)
    vol   = vol.tail(VOL_ACCUM_WINDOW)
    ret   = close.diff()

    up_vol   = float(vol[ret > 0].sum())
    down_vol = float(vol[ret < 0].sum())

    if down_vol == 0:
        ratio = float("inf")
    else:
        ratio = up_vol / down_vol

    passed = ratio > VOL_ACCUM_THRESHOLD

    return {
        "pass":      passed,
        "ratio":     round(ratio, 3),
        "up_vol":    int(up_vol),
        "down_vol":  int(down_vol),
        "window":    VOL_ACCUM_WINDOW,
        "threshold": VOL_ACCUM_THRESHOLD,
        "reason":    f"up/down vol ratio {ratio:.2f} over {VOL_ACCUM_WINDOW}d ({'accumulation' if passed else 'distribution/neutral'})",
    }
