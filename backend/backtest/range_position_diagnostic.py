"""
Range-position-at-entry performance diagnostic.

Computes range_position = (entry_price - low_52w) / (high_52w - low_52w) for
every backtest trade, then buckets by range position and reports win rate and
profit factor per bucket.

If win rate / PF degrades monotonically above some threshold, that threshold is
the RANGE_EXHAUSTION_CEILING candidate for Lock 1 check 6.  If the distribution
is flat, the filter adds no value and should not be shipped.

The range cache is built from a separate yfinance download with a 400-calendar-
day lookback so the rolling-252 window is fully seeded from the first trading day
of the diagnostic window.  Trades where the window is not yet full (None) are
excluded from bucketing and reported separately.

Finding (2026-05-13, 2022–2026 backtest): range exhaustion is not a failure
mode in this system.  Near-52w-high entries (0.90–1.00) posted PF 1.80 and
56% win rate — on par with or better than lower-range entries.  The 0.85–0.90
bucket was the strongest of all (PF 11.71, 90% WR, 10 trades).  The signal and
regime filters already select for sustained strength, which clusters near annual
highs.  Adding a ceiling would filter out the system's best entries.  Do not
re-run this diagnostic without a specific new hypothesis to test.

Usage:
    cd /home/promenix/apex
    python -m backend.backtest.range_position_diagnostic
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import TypedDict

import pandas as pd
import yfinance as yf
from loguru import logger

from backend.config import (
    DAILY_LOSS_CAP,
    EXCLUDED_SECTORS,
    LOCK1_THRESHOLD,
    MAX_POSITION_SIZE,
    MAX_POSITIONS,
    MAX_SECTOR_EXPOSURE,
    SECTORS,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    TIME_STOP_DAYS,
    WIN_RATE_MIN_TRADES,
)
from backend.backtest.engine_fast import (
    precompute,
    _check_exits_fast,
    _get_candidates_from_cache,
    _sector_exposure,
    _trading_days_count,
)

# ── Diagnostic window ─────────────────────────────────────────────────────────
#
# Precompute starts at PRECOMPUTE_START so that the rolling-252 range window has
# a full year of data by DIAG_START.  Only trades from DIAG_START onward are
# included in bucketing.
PRECOMPUTE_START = "2021-01-01"
DIAG_START       = "2022-01-01"
DIAG_END         = "2026-05-01"

# ── Range-position buckets ─────────────────────────────────────────────────────
BUCKETS = [
    (0.00, 0.70, "0.00–0.70  (lower range)"),
    (0.70, 0.80, "0.70–0.80"),
    (0.80, 0.85, "0.80–0.85"),
    (0.85, 0.90, "0.85–0.90"),
    (0.90, 1.01, "0.90–1.00  (near 52w high)"),
]

# Ceiling candidates to simulate — reported after the bucket table
THRESHOLD_CANDIDATES = [0.80, 0.85, 0.90]


# ── Range cache ───────────────────────────────────────────────────────────────

def _build_range_cache(
    ticker_sector: dict[str, str],
    trading_days: list[date],
    diag_start: date,
    diag_end: date,
) -> dict[tuple[str, str], tuple[float, float] | None]:
    """
    {(ticker, date_str): (high_52w, low_52w)} for every ticker × trading day.

    Uses a 400-calendar-day lookback so rolling(252) is fully seeded before
    DIAG_START.  Returns None for any entry where the window is partial or the
    fetch failed — callers must handle None.
    """
    tickers     = list(ticker_sector.keys())
    range_start = diag_start - timedelta(days=400)

    logger.info(
        f"Range cache: downloading {len(tickers)} tickers "
        f"{range_start} → {diag_end} …"
    )
    raw = yf.download(
        tickers,
        start=range_start.isoformat(),
        end=diag_end.isoformat(),
        auto_adjust=True,
        progress=False,
        threads=True,
    )
    if raw.empty:
        logger.warning("Range cache: download returned empty DataFrame")
        return {}

    # yfinance download returns (field, ticker) MultiIndex — normalise single-ticker edge case
    if not isinstance(raw.columns, pd.MultiIndex):
        raw.columns = pd.MultiIndex.from_product([raw.columns, [tickers[0]]])

    cache: dict[tuple[str, str], tuple[float, float] | None] = {}

    # Tickers are on level 1; fields on level 0
    available = set(raw.columns.get_level_values(1))

    for ticker in tickers:
        if ticker not in available:
            for d in trading_days:
                cache[(ticker, d.isoformat())] = None
            continue
        try:
            high_s   = raw[("High", ticker)].dropna()
            low_s    = raw[("Low",  ticker)].dropna()
            high_252 = high_s.rolling(252).max()
            low_252  = low_s.rolling(252).min()
        except Exception as e:
            logger.debug(f"Range cache: {ticker} rolling failed — {e}")
            for d in trading_days:
                cache[(ticker, d.isoformat())] = None
            continue

        for d in trading_days:
            date_str = d.isoformat()
            ts       = pd.Timestamp(d)
            try:
                h_slice = high_252[high_252.index <= ts]
                l_slice = low_252[low_252.index <= ts]
                if h_slice.empty or l_slice.empty:
                    cache[(ticker, date_str)] = None
                    continue
                h = h_slice.iloc[-1]
                l = l_slice.iloc[-1]
                if pd.isna(h) or pd.isna(l):
                    cache[(ticker, date_str)] = None
                else:
                    cache[(ticker, date_str)] = (float(h), float(l))
            except Exception:
                cache[(ticker, date_str)] = None

    valid = sum(1 for v in cache.values() if v is not None)
    logger.info(f"Range cache: {valid}/{len(cache)} valid entries")
    return cache


# ── Simulation ────────────────────────────────────────────────────────────────

class TradeWithRange(TypedDict):
    ticker:         str
    sector:         str
    entry_date:     str
    outcome:        str
    pnl_pct:        float | None
    exit_reason:    str | None
    entry_range_pos: float | None


def _range_pos(
    entry_price: float,
    hl: tuple[float, float] | None,
) -> float | None:
    if hl is None:
        return None
    high_52w, low_52w = hl
    spread = high_52w - low_52w
    if spread <= 0:
        return None
    return round((entry_price - low_52w) / spread, 4)


def _simulate(
    trading_days: list[date],
    price_cache:  dict,
    spy_cache:    dict,
    signal_cache: dict,
    ticker_sector: dict[str, str],
    sector_etf:   dict[str, str],
    etf_cache:    dict,
    range_cache:  dict,
    diag_start:   date,
) -> list[TradeWithRange]:
    tp    = TAKE_PROFIT_PCT
    sl    = STOP_LOSS_PCT
    tdays = TIME_STOP_DAYS
    l1    = LOCK1_THRESHOLD
    SL_COOLDOWN = 5

    balance       = 10_000.0
    open_trades:   list[dict] = []
    closed_trades: list[TradeWithRange] = []
    daily_losses:  dict[str, float] = {}
    sl_cooldown:   dict[str, date]  = {}

    for today in trading_days:
        today_str = today.isoformat()

        closed_today = _check_exits_fast(
            open_trades, price_cache, today, today_str, tp, sl, tdays
        )
        for ct in closed_today:
            trade  = ct["_trade"]
            record = ct["record"]
            open_trades.remove(trade)
            balance += trade["amount"] + (record["pnl"] or 0)
            if record["outcome"] in ("LOSS", "EXPIRED") and (record["pnl"] or 0) < 0:
                daily_losses[today_str] = (
                    daily_losses.get(today_str, 0) + abs(record["pnl"] or 0)
                )
            if record["exit_reason"] in ("SL", "TSL"):
                sl_cooldown[trade["ticker"]] = today + timedelta(days=SL_COOLDOWN)
            # Only include in bucketing if entry was within the diagnostic window
            if today >= diag_start:
                closed_trades.append(TradeWithRange(
                    ticker          = record["ticker"],
                    sector          = record["sector"],
                    entry_date      = record["entry_date"],
                    outcome         = record["outcome"],
                    pnl_pct         = record["pnl_pct"],
                    exit_reason     = record["exit_reason"],
                    entry_range_pos = trade["entry_range_pos"],
                ))

        spy_reg       = spy_cache.get(today_str, {}).get("regime", 0.0)
        wins_so_far   = sum(1 for t in closed_trades if t["outcome"] == "WIN")
        closed_so_far = len(closed_trades)
        rolling_wr    = (wins_so_far / closed_so_far) if closed_so_far >= WIN_RATE_MIN_TRADES else None

        candidates = _get_candidates_from_cache(
            signal_cache, ticker_sector, sector_etf, etf_cache,
            today_str, l1, spy_reg, rolling_wr,
        )

        open_tickers     = {t["ticker"] for t in open_trades}
        sector_exp       = _sector_exposure(open_trades, balance)
        daily_loss_today = daily_losses.get(today_str, 0.0)

        for sig in sorted(candidates, key=lambda s: s["signal_score"], reverse=True):
            if len(open_trades) >= MAX_POSITIONS:
                break
            if sig["ticker"] in open_tickers:
                continue
            if sl_cooldown.get(sig["ticker"], date.min) > today:
                continue
            if daily_loss_today >= DAILY_LOSS_CAP:
                break

            sector    = sig["sector"]
            alloc_pct = min(sig["kelly_size"] if sig["kelly_size"] > 0 else 0.10, MAX_POSITION_SIZE)
            amount    = balance * alloc_pct
            projected = sector_exp.get(sector, 0.0) + (amount / balance)
            if projected > MAX_SECTOR_EXPOSURE:
                continue

            entry_price = price_cache.get((sig["ticker"], today_str))
            if entry_price is None or entry_price <= 0 or amount < 50:
                continue

            hl            = range_cache.get((sig["ticker"], today_str))
            entry_rp      = _range_pos(entry_price, hl)

            balance -= amount
            open_trades.append({
                "ticker":          sig["ticker"],
                "sector":          sector,
                "entry_date":      today_str,
                "entry_price":     entry_price,
                "peak_price":      entry_price,
                "shares":          amount / entry_price,
                "amount":          amount,
                "signal_score":    sig["signal_score"],
                "entry_range_pos": entry_rp,
                "tp":              tp,
                "sl":              sl,
            })
            open_tickers.add(sig["ticker"])
            sector_exp[sector] = sector_exp.get(sector, 0.0) + (amount / balance)

    # Mark open positions at last day
    last_str = trading_days[-1].isoformat()
    for trade in open_trades:
        price = price_cache.get((trade["ticker"], last_str))
        if price is None:
            continue
        pnl_pct = (price - trade["entry_price"]) / trade["entry_price"]
        if date.fromisoformat(trade["entry_date"]) >= diag_start:
            closed_trades.append(TradeWithRange(
                ticker          = trade["ticker"],
                sector          = trade["sector"],
                entry_date      = trade["entry_date"],
                outcome         = "WIN" if pnl_pct >= 0 else "LOSS",
                pnl_pct         = round(pnl_pct, 4),
                exit_reason     = "OPEN",
                entry_range_pos = trade["entry_range_pos"],
            ))

    return closed_trades


# ── Analysis helpers ──────────────────────────────────────────────────────────

def _bucket_label(rp: float | None) -> str:
    if rp is None:
        return "none"
    for lo, hi, label in BUCKETS:
        if lo <= rp < hi:
            return label
    return "none"


def _stats(trades: list) -> dict:
    n      = len(trades)
    wins   = [t for t in trades if t["outcome"] == "WIN"]
    losses = [t for t in trades if t["outcome"] in ("LOSS", "EXPIRED")]
    closed = wins + losses
    gw = sum(t["pnl_pct"] for t in wins   if t["pnl_pct"] and t["pnl_pct"] > 0)
    gl = abs(sum(t["pnl_pct"] for t in losses if t["pnl_pct"] and t["pnl_pct"] < 0))
    return {
        "n":        n,
        "win_rate": len(wins) / len(closed) if closed else None,
        "avg_pnl":  sum(t["pnl_pct"] or 0 for t in trades) / n if n else None,
        "pf":       gw / gl if gl > 0 else None,
    }


# ── Formatting ────────────────────────────────────────────────────────────────

W = 68

def _bar(char: str = "─") -> str:
    return char * W

def _pct(v: float | None, decimals: int = 1, signed: bool = False) -> str:
    if v is None:
        return "  —  "
    prefix = "+" if signed and v >= 0 else ""
    return f"{prefix}{v * 100:.{decimals}f}%"

def _f(v: float | None, fmt: str = ".2f") -> str:
    return f"{v:{fmt}}" if v is not None else "  —"

def _print_header(label: str) -> None:
    print()
    print(_bar("═"))
    print(f"  {label}")
    print(_bar("═"))


def _print_bucket_table(trades: list[TradeWithRange]) -> None:
    buckets: dict[str, list] = defaultdict(list)
    for t in trades:
        buckets[_bucket_label(t["entry_range_pos"])].append(t)

    n_none = len(buckets["none"])
    print()
    print(_bar())
    print(
        f"  {'Range position':<28}  {'N':>5}  "
        f"{'Win%':>6}  {'AvgPnL%':>8}  {'PF':>6}"
    )
    print(_bar())
    for _, _, label in BUCKETS:
        bucket = buckets[label]
        if not bucket:
            print(f"  {label:<28}  {'—':>5}")
            continue
        s = _stats(bucket)
        print(
            f"  {label:<28}  {s['n']:>5}  "
            f"{_pct(s['win_rate']):>6}  "
            f"{_pct(s['avg_pnl'], signed=True):>8}  "
            f"{_f(s['pf']):>6}"
        )
    if n_none:
        print(f"  {'(range unavailable)':<28}  {n_none:>5}  (excluded from bucketing)")
    print()


def _print_threshold_table(trades: list[TradeWithRange]) -> None:
    """
    For each ceiling candidate, show what blocking range_pos >= threshold would do:
    trades blocked, remaining win rate, remaining PF, delta vs baseline.
    """
    scoreable = [t for t in trades if t["entry_range_pos"] is not None]
    base      = _stats(scoreable)

    print()
    print(_bar())
    print(
        f"  {'Ceiling':<10}  {'Blocked':>7}  {'Remain':>7}  "
        f"{'WinRate':>8}  {'WinΔ':>7}  {'PF':>6}  {'PFΔ':>6}"
    )
    print(_bar())
    for threshold in THRESHOLD_CANDIDATES:
        filtered  = [t for t in scoreable if t["entry_range_pos"] < threshold]
        blocked_n = len(scoreable) - len(filtered)
        s         = _stats(filtered)
        wr_delta  = (
            (s["win_rate"] - base["win_rate"])
            if s["win_rate"] is not None and base["win_rate"] is not None
            else None
        )
        pf_delta  = (
            (s["pf"] - base["pf"])
            if s["pf"] is not None and base["pf"] is not None
            else None
        )
        print(
            f"  {f'>= {threshold:.2f}':<10}  "
            f"{blocked_n:>7}  "
            f"{len(filtered):>7}  "
            f"{_pct(s['win_rate']):>8}  "
            f"{_pct(wr_delta, signed=True):>7}  "
            f"{_f(s['pf']):>6}  "
            f"{_f(pf_delta, fmt='+.2f') if pf_delta is not None else '  —':>6}"
        )
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    logger.info(f"Range position diagnostic: {DIAG_START} → {DIAG_END}")
    logger.info(f"Building precompute caches from {PRECOMPUTE_START} …")
    pc = precompute(PRECOMPUTE_START, DIAG_END)

    raw_data      = pc["raw_data"]
    ticker_sector = pc["ticker_sector"]
    sector_etf    = pc["sector_etf"]
    trading_days  = pc["trading_days"]
    price_cache   = pc["price_cache"]
    spy_cache     = pc["spy_cache"]
    etf_cache     = pc["etf_cache"]
    signal_cache  = pc["signal_cache"]

    diag_start = date.fromisoformat(DIAG_START)
    diag_end   = date.fromisoformat(DIAG_END)

    logger.info("Building 52-week range cache …")
    range_cache = _build_range_cache(ticker_sector, trading_days, diag_start, diag_end)

    logger.info("Running simulation …")
    trades = _simulate(
        trading_days, price_cache, spy_cache, signal_cache,
        ticker_sector, sector_etf, etf_cache, range_cache,
        diag_start,
    )

    diag_trades = [t for t in trades if t["entry_date"] >= DIAG_START]
    scoreable   = [t for t in diag_trades if t["entry_range_pos"] is not None]

    _print_header(
        f"RANGE POSITION DIAGNOSTIC  {DIAG_START} → {DIAG_END}"
    )

    base = _stats(diag_trades)
    print()
    print(f"  Trades in window: {len(diag_trades)}")
    print(f"  With range data:  {len(scoreable)}")
    print(f"  Baseline win rate: {_pct(base['win_rate'])}  "
          f"PF: {_f(base['pf'])}  AvgPnL: {_pct(base['avg_pnl'], signed=True)}")

    _print_header("BUCKET BREAKDOWN")
    _print_bucket_table(diag_trades)

    _print_header("CEILING SIMULATION")
    print(
        "  Win rate and PF if range_pos >= threshold were blocked at L1."
    )
    _print_threshold_table(diag_trades)

    print(_bar("═"))
    print()


if __name__ == "__main__":
    main()
