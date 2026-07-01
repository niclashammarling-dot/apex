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

import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from contextlib import contextmanager

import yfinance as yf
from loguru import logger

from backend.gate.types import LockResult

# Shared semaphore across all outer gate-runner threads.
# Caps concurrent yfinance requests to stay below Yahoo rate-limit threshold.
# Current worst case: 5 outer threads × 3 inner fetches = 15 theoretical max.
# Set to 4: curl-cffi (used by yfinance ≥0.2.31) is a shared BoringSSL singleton;
# running >4 TLS handshakes simultaneously in WSL2 produces intermittent
# "curl:(35) TLS connect error" failures. Tune upward only on bare-metal Linux.
_YF_SEMAPHORE = threading.Semaphore(4)

# Hard ceiling on a single yfinance call. yf.download() defaults to timeout=10
# internally, but Ticker.options/.option_chain() take no timeout parameter and
# curl_cffi's shared BoringSSL singleton has been observed to hang outright
# under concurrent load rather than raise (2026-06-23: one ticker's Lock 4
# evaluation never returned, which blocked the entire gate cycle's
# ThreadPoolExecutor.shutdown(wait=True) forever — no further cycles ran).
# This bounds each sub-check's future so one hung call degrades to a logged
# failure instead of freezing the whole gate cycle. Acquiring the semaphore is
# itself time-bounded too: a thread that's still genuinely stuck inside a call
# leaks its permit permanently (Python has no way to cancel a running thread),
# but bounding acquire() means everyone else degrades to "fewer slots
# available" instead of every future caller blocking forever once permits run out.
_YF_CALL_TIMEOUT_S      = 15
_YF_SEMAPHORE_TIMEOUT_S = 20

LOCK_ID  = 4
MIN_PASS = 2


class _YfSemaphoreBusy(Exception):
    """Raised when the yfinance semaphore can't be acquired within the timeout —
    likely because a prior call leaked a permit by hanging forever."""


@contextmanager
def _yf_slot():
    """Bounded-wait semaphore acquire. Raises _YfSemaphoreBusy instead of
    blocking forever if every permit is held (possibly leaked by a hung call)."""
    if not _YF_SEMAPHORE.acquire(timeout=_YF_SEMAPHORE_TIMEOUT_S):
        raise _YfSemaphoreBusy(
            f"could not acquire yfinance semaphore within {_YF_SEMAPHORE_TIMEOUT_S}s "
            "— all permits busy or leaked by a hung call"
        )
    try:
        yield
    finally:
        _YF_SEMAPHORE.release()

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

    Gate logic — group constraint (not flat threshold):
      PRICE group  (relative_strength, volume_accumulation):
          any data error  → price group FAIL (yfinance errors are correlated;
                            a partial error state taints the group)
          no errors, ≥1 pass → price group PASS
      OPTIONS group (put_call_ratio, unusual_calls):
          ≥1 pass → options group PASS
          (options errors surface at the shared fetch level, not per-check)
      LD passes only when BOTH groups pass.

    The min_pass parameter is kept for backward compat with chain.py's cfg lookup
    but is no longer used in the pass logic.

    Args:
        ticker:   Stock ticker symbol
        sector:   Apex sector name
        min_pass: Unused — retained for caller compat only

    Returns:
        LockResult with lock_id=4
    """
    # Run the three independent I/O fetches in parallel.
    # put_call_ratio and unusual_calls are pure computation on options_chains — no I/O.
    # _YF_SEMAPHORE (module-level) caps total concurrent yfinance requests across all
    # outer gate-runner threads; inner pool size of 3 matches the fetch count.
    #
    # Deliberately NOT a `with ThreadPoolExecutor(...) as inner_pool:` block —
    # its __exit__ calls shutdown(wait=True) unconditionally, which would block
    # here forever on a genuinely hung future even with .result(timeout=...)
    # below. shutdown(wait=False) lets this function return; the hung thread
    # itself still leaks (Python has no way to kill a running thread), but it
    # no longer takes the whole gate cycle down with it.
    inner_pool  = ThreadPoolExecutor(max_workers=3)
    fut_options = inner_pool.submit(_fetch_options_chains, ticker)
    fut_rs      = inner_pool.submit(_check_relative_strength, ticker, sector)
    fut_va      = inner_pool.submit(_check_volume_accumulation, ticker)

    options_fetch_err: str | None = None
    try:
        options_chains = fut_options.result(timeout=_YF_CALL_TIMEOUT_S)
    except FutureTimeoutError:
        logger.error(f"Lock 4 [{ticker}] options fetch: timed out after {_YF_CALL_TIMEOUT_S}s — abandoning")
        options_chains    = []
        options_fetch_err = "timed out"
    except Exception as e:
        logger.warning(f"Lock 4 [{ticker}] options fetch: {e}")
        options_chains = []
        msg = str(e)
        if "curl" in msg.lower() or "tls" in msg.lower():
            options_fetch_err = "curl TLS error"
        elif "429" in msg or "rate" in msg.lower():
            options_fetch_err = "Yahoo 429 rate limit"
        else:
            options_fetch_err = f"fetch error: {msg[:60]}"

    try:
        rs_result = fut_rs.result(timeout=_YF_CALL_TIMEOUT_S)
    except FutureTimeoutError:
        logger.error(f"Lock 4 [{ticker}] relative_strength: timed out after {_YF_CALL_TIMEOUT_S}s — abandoning")
        rs_result = {"pass": False, "error": True, "reason": f"timed out after {_YF_CALL_TIMEOUT_S}s"}
    except Exception as e:
        logger.warning(f"Lock 4 [{ticker}] relative_strength: {e}")
        rs_result = {"pass": False, "error": True, "reason": f"error: {e}"}

    try:
        va_result = fut_va.result(timeout=_YF_CALL_TIMEOUT_S)
    except FutureTimeoutError:
        logger.error(f"Lock 4 [{ticker}] volume_accumulation: timed out after {_YF_CALL_TIMEOUT_S}s — abandoning")
        va_result = {"pass": False, "error": True, "reason": f"timed out after {_YF_CALL_TIMEOUT_S}s"}
    except Exception as e:
        logger.warning(f"Lock 4 [{ticker}] volume_accumulation: {e}")
        va_result = {"pass": False, "error": True, "reason": f"error: {e}"}

    inner_pool.shutdown(wait=False)

    checks = {
        "relative_strength":   rs_result,
        "put_call_ratio":      _check_put_call_ratio(options_chains, options_fetch_err),
        "unusual_calls":       _check_unusual_call_volume(options_chains, options_fetch_err),
        "volume_accumulation": va_result,
    }

    # Group evaluation
    price_checks   = [checks["relative_strength"], checks["volume_accumulation"]]
    options_checks = [checks["put_call_ratio"],     checks["unusual_calls"]]

    price_errors      = sum(1 for c in price_checks   if c and c.get("error"))
    price_passes      = sum(1 for c in price_checks   if c and c.get("pass"))
    options_passes    = sum(1 for c in options_checks if c and c.get("pass"))

    price_group_pass   = price_errors == 0 and price_passes >= 1
    options_group_pass = options_passes >= 1
    passed             = price_group_pass and options_group_pass

    pass_count    = sum(1 for v in checks.values() if v and v.get("pass"))
    failed_checks = [k for k, v in checks.items() if not (v and v.get("pass"))]
    score         = round(sum(WEIGHTS[k] for k, v in checks.items() if v and v.get("pass")), 4)

    data = {
        "checks":              checks,
        "pass_count":          pass_count,
        "min_pass":            min_pass,
        "failed_checks":       failed_checks,
        "price_group_pass":    price_group_pass,
        "options_group_pass":  options_group_pass,
        "price_errors":        price_errors,
        # Kept for DB compat (lock_leading_checks serialised as JSON)
        "sub_checks":          checks,
    }

    if not passed:
        if not price_group_pass and price_errors > 0:
            reason = (
                f"price group failed ({price_errors} error(s)) — "
                f"RS: {checks['relative_strength']['reason'][:60]}, "
                f"VA: {checks['volume_accumulation']['reason'][:60]}"
            )
        elif not price_group_pass:
            reason = "price group failed — neither RS nor VA passed"
        else:
            reason = "options group failed — neither PCR nor unusual calls passed"
        logger.debug(f"Lock 4 [{ticker}] FAIL — {reason}")
        return LockResult.fail(lock_id=LOCK_ID, reason=reason, data=data)

    passed_names = [k for k, v in checks.items() if v and v.get("pass")]
    reason = (
        f"price+options groups passed — "
        f"{', '.join(passed_names)}"
    )
    logger.debug(f"Lock 4 [{ticker}] PASS — {reason}")
    return LockResult.pass_(lock_id=LOCK_ID, score=score, reason=reason, data=data)


# ── Sub-checks ────────────────────────────────────────────────────────────────

def _fetch_options_chains(ticker: str) -> list[tuple]:
    """
    Fetch near-term options chains once per ticker per Lock 4 evaluation.
    Returns [(calls_df, puts_df), ...] for the first 2 expiries.
    Both put_call_ratio and unusual_calls consume this shared list.

    Retries once on curl TLS errors and on silently-empty t.options — both are
    intermittent under concurrent load via the shared curl_cffi BoringSSL singleton.
    """
    last_exc: Exception | None = None
    for attempt in range(2):
        if attempt > 0:
            time.sleep(0.5)
        try:
            with _yf_slot():
                t        = yf.Ticker(ticker)
                expiries = t.options
                if not expiries:
                    if attempt == 0:
                        logger.warning(f"Lock 4 [{ticker}] options empty on attempt 1, retrying")
                        continue
                    return []
                chains = []
                for exp in expiries[:2]:
                    try:
                        chain = t.option_chain(exp)
                        # Under concurrent session reuse, .calls/.puts can be None
                        # instead of an empty DataFrame — guard before appending.
                        if chain.calls is not None and chain.puts is not None:
                            chains.append((chain.calls, chain.puts))
                    except Exception as e:
                        logger.warning(f"Lock 4 [{ticker}] chain fetch {exp}: {e}")
                return chains
        except Exception as e:
            last_exc = e
            if attempt == 0:
                logger.warning(f"Lock 4 [{ticker}] options attempt 1 failed ({e}), retrying")
    raise last_exc  # type: ignore[misc]


def _check_relative_strength(ticker: str, sector: str) -> dict:
    """Ticker 5-day return exceeds its sector ETF return."""
    etf = SECTOR_ETF.get(sector)
    if not etf:
        return {"pass": False, "reason": f"no ETF mapped for sector '{sector}'"}

    data = None
    for attempt in range(2):
        if attempt > 0:
            time.sleep(0.5)
        try:
            with _yf_slot():
                raw = yf.download([ticker, etf], period="8d", progress=False,
                                   auto_adjust=True)
        except yf.exceptions.YFRateLimitError:
            return {"pass": False, "error": True, "reason": "Yahoo 429 rate limit"}
        except Exception as e:
            return {"pass": False, "error": True, "reason": f"download error: {e}"}

        try:
            data = raw["Close"]
        except KeyError:
            return {"pass": False, "error": True, "reason": f"no Close column — got {list(raw.columns[:4])}"}

        if data.empty:
            return {"pass": False, "error": True, "reason": "empty download (429 or delisted)"}
        missing = [t for t in [ticker, etf] if t not in data.columns]
        if not missing:
            break
        if attempt == 0:
            logger.warning(f"Lock 4 [{ticker}] RS partial download {missing}, retrying")
            continue
        return {"pass": False, "error": True, "reason": f"missing from download: {missing}"}

    try:
        data = data[[ticker, etf]].dropna()
    except (KeyError, ValueError) as e:
        return {"pass": False, "error": True, "reason": f"column select failed: {e}"}
    if len(data) < 5:
        return {"pass": False, "error": True, "reason": "insufficient price history for 5d RS"}

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


def _check_put_call_ratio(chains: list[tuple], fetch_err: str | None = None) -> dict:
    """Near-term put/call open-interest ratio < PCR_THRESHOLD (calls dominant)."""
    if not chains:
        reason = f"no options data ({fetch_err})" if fetch_err else "no options data (no listed expiries)"
        return {"pass": False, "reason": reason}

    call_oi = put_oi = 0
    for calls, puts in chains:
        call_oi += int(calls["openInterest"].fillna(0).sum())
        put_oi  += int(puts["openInterest"].fillna(0).sum())

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


def _check_unusual_call_volume(chains: list[tuple], fetch_err: str | None = None) -> dict:
    """Call volume >= 2x put volume across near-term expiries."""
    if not chains:
        reason = f"no options data ({fetch_err})" if fetch_err else "no options data (no listed expiries)"
        return {"pass": False, "reason": reason}

    call_vol = put_vol = 0
    for calls, puts in chains:
        call_vol += int(calls["volume"].fillna(0).sum())
        put_vol  += int(puts["volume"].fillna(0).sum())

    if call_vol + put_vol == 0:
        return {"pass": False, "reason": "no volume today"}

    ratio  = call_vol / put_vol if put_vol > 0 else float("inf")
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
    close = vol = None
    for attempt in range(2):
        if attempt > 0:
            time.sleep(0.5)
        try:
            with _yf_slot():
                raw = yf.download(ticker, period="30d", progress=False, auto_adjust=True)
        except yf.exceptions.YFRateLimitError:
            return {"pass": False, "error": True, "reason": "Yahoo 429 rate limit"}
        except Exception as e:
            return {"pass": False, "error": True, "reason": f"download error: {e}"}

        if raw.empty:
            return {"pass": False, "error": True, "reason": "empty download (429 or delisted)"}

        # Normalize MultiIndex columns to flat — concurrent RS download of [ticker, ETF]
        # can contaminate a single-ticker session, returning MultiIndex columns instead
        # of flat ones. Droplevel to the ticker name; then access by flat string key.
        if isinstance(raw.columns, __import__("pandas").MultiIndex):
            try:
                raw = raw.xs(ticker, axis=1, level=1)
            except KeyError:
                got = [str(c) for c in raw.columns[:4]]
                if attempt == 0:
                    logger.warning(f"Lock 4 [{ticker}] VA unexpected columns {got}, retrying")
                    continue
                return {"pass": False, "error": True, "reason": f"unexpected column format: {got}"}

        try:
            close = raw["Close"]
            vol   = raw["Volume"]
            break
        except KeyError:
            got = [str(c) for c in raw.columns[:4]]
            if attempt == 0:
                logger.warning(f"Lock 4 [{ticker}] VA unexpected columns {got}, retrying")
                continue
            return {"pass": False, "error": True, "reason": f"unexpected column format: {got}"}
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
