"""
floor_reconstruction.py — Bayesian allocation floor time series reconstruction.

Replays RegimeBayes day-by-day over the full sector_snapshots history to produce
a per-sector time series of adjusted_score (aggregate × posterior).  Reports:
  - Fraction of trading days each sector spent above ALLOCATION_FLOOR
  - Longest qualification streak and longest drought
  - Qualification calendar (which months each sector was open/blocked)
  - Comparison with demo_gate_history trade counts where available

Signal sourcing:
  Signal 1 (ticker balance)     — ticker_history.signal_score day-over-day deltas
                                  (proxy for consecutive positive/negative price days)
  Signals 2/3/5 (ETF based)    — yfinance ETF prices, downloaded and cached locally
  Signal 4 (IPO share)         — uniform 1/n throughout (neutral; low impact)

Run from apex/ root:
  python -m backend.backtest.floor_reconstruction
  python -m backend.backtest.floor_reconstruction --start 2023-01-01
  python -m backend.backtest.floor_reconstruction --csv out.csv
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from backend.regime.regime_bayes import (
    ALLOCATION_FLOOR,
    POSTERIOR_DECAY,
    TICKER_RECOVERY_DAYS,
    RS_WINDOW_DAYS,
    _apply_signals,
    _lr_etf_streak,
    _lr_rs_divergence,
    _lr_ticker_balance,
    _compute_rank_lrs,
)
from backend.ticker_config import get_sectors

# ── Config ────────────────────────────────────────────────────────────────────

ETF_CACHE_PATH = Path(__file__).parent.parent.parent / "data" / "etf_price_cache.parquet"
DB_PATH        = Path(__file__).parent.parent.parent / "data" / "apex.db"

N_SECTORS    = 11
BASE_PRIOR   = 1.0 / N_SECTORS


# ── Data loaders ─────────────────────────────────────────────────────────────

def load_etf_prices(sectors_cfg: dict, force_refresh: bool = False) -> pd.DataFrame:
    """
    Download or load cached ETF close prices.
    Returns DataFrame indexed by date, columns = ETF symbols.
    """
    etfs = sorted({cfg["etf"] for cfg in sectors_cfg.values()})

    if ETF_CACHE_PATH.exists() and not force_refresh:
        df = pd.read_parquet(ETF_CACHE_PATH)
        # Refresh if cache is more than 1 trading day stale
        if not df.empty and pd.Timestamp(df.index[-1]).date() >= (date.today() - timedelta(days=3)):
            print(f"ETF cache loaded ({len(df)} days, {df.index[0].date()} – {df.index[-1].date()})")
            return df

    print(f"Downloading ETF prices: {', '.join(etfs)} …")
    import yfinance as yf
    raw = yf.download(etfs, start="2021-01-01", auto_adjust=True, progress=False)
    if "Close" in raw.columns.get_level_values(0):
        df = raw["Close"].dropna(how="all")
    else:
        df = raw.xs("Close", axis=1, level=0).dropna(how="all")
    ETF_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(ETF_CACHE_PATH)
    print(f"ETF cache saved ({len(df)} days)")
    return df


def load_sector_snapshots() -> dict[str, dict[str, float]]:
    """
    Returns {date_str: {sector: avg_score}} for all sector_snapshots rows.
    Multiple rows per day are averaged (intraday snapshots).
    """
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        "SELECT date(timestamp) as day, sector, avg(avg_score) as score "
        "FROM sector_snapshots GROUP BY day, sector ORDER BY day"
    )
    rows = cur.fetchall()
    conn.close()

    by_day: dict[str, dict[str, float]] = defaultdict(dict)
    for r in rows:
        by_day[r["day"]][r["sector"]] = round(r["score"], 4)
    return dict(by_day)


def load_ticker_history() -> dict[str, dict[str, float]]:
    """
    Returns {ticker: {day_str: signal_score}} for all active tickers.
    """
    import sqlite3
    sectors_cfg = get_sectors()
    active = {t for cfg in sectors_cfg.values() for t in cfg["tickers"]}
    placeholders = ",".join("?" * len(active))

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        f"SELECT ticker, day, signal_score FROM ticker_history "
        f"WHERE ticker IN ({placeholders}) ORDER BY ticker, day",
        list(active),
    )
    rows = cur.fetchall()
    conn.close()

    by_ticker: dict[str, dict[str, float]] = defaultdict(dict)
    for r in rows:
        by_ticker[r["ticker"]][r["day"]] = r["signal_score"]
    return dict(by_ticker)


def load_demo_trades() -> dict[str, int]:
    """Returns {sector: entry_count} from demo_gate_history (TRADE_EXECUTED only)."""
    import sqlite3
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT sector, COUNT(*) as n FROM demo_gate_history "
            "WHERE gate_decision = 'TRADE_EXECUTED' GROUP BY sector"
        )
        rows = cur.fetchall()
        conn.close()
        return {r["sector"]: r["n"] for r in rows}
    except Exception:
        return {}


# ── Signal helpers ────────────────────────────────────────────────────────────

def ticker_balance_from_history(
    sector: str,
    tickers: list[str],
    ticker_hist: dict[str, dict[str, float]],
    today_str: str,
) -> tuple[int, int, int]:
    """
    Approximate Signal 1 using signal_score day-over-day deltas.
    'recovering'    = all 5 daily deltas positive
    'deteriorating' = all 5 daily deltas negative
    """
    recovering    = 0
    deteriorating = 0
    total         = 0
    for ticker in tickers:
        th = ticker_hist.get(ticker, {})
        days_sorted = sorted(d for d in th if d <= today_str)
        if len(days_sorted) < TICKER_RECOVERY_DAYS + 1:
            continue
        window = days_sorted[-(TICKER_RECOVERY_DAYS + 1):]
        deltas = [th[window[i + 1]] - th[window[i]] for i in range(len(window) - 1)]
        if not deltas:
            continue
        total += 1
        if all(d > 0 for d in deltas):
            recovering += 1
        elif all(d < 0 for d in deltas):
            deteriorating += 1
    return recovering, deteriorating, total


def etf_streak(etf_prices: pd.DataFrame, etf: str, today_str: str) -> int:
    """Signal 2: ETF signed streak up to today."""
    if etf not in etf_prices.columns:
        return 0
    mask   = etf_prices.index <= today_str
    closes = etf_prices.loc[mask, etf].dropna().tail(60)
    if len(closes) < 2:
        return 0
    returns = closes.pct_change().dropna().values
    if len(returns) == 0:
        return 0
    direction = 1 if returns[-1] > 0 else (-1 if returns[-1] < 0 else 0)
    if direction == 0:
        return 0
    count = 0
    for ret in reversed(returns):
        if (direction > 0 and ret > 0) or (direction < 0 and ret < 0):
            count += 1
        else:
            break
    return direction * count


def leader_decline(etf_prices: pd.DataFrame, leader: str, etf_map: dict[str, str], today_str: str) -> dict:
    """Signal 3: leader RS divergence."""
    etf = etf_map.get(leader, "")
    if not etf or etf not in etf_prices.columns:
        return {"start_score": 0.0, "end_score": 0.0, "decline_days": 0}
    mask   = etf_prices.index <= today_str
    closes = etf_prices.loc[mask, etf].dropna().tail(30)
    if len(closes) < RS_WINDOW_DAYS + 1:
        return {"start_score": 0.0, "end_score": 0.0, "decline_days": 0}
    start = float(closes.iloc[-(RS_WINDOW_DAYS + 1)])
    end   = float(closes.iloc[-1])
    rets  = closes.pct_change().dropna().values
    decline_days = 0
    for ret in reversed(rets):
        if ret < 0:
            decline_days += 1
        else:
            break
    return {"start_score": start, "end_score": end, "decline_days": decline_days}


def rank_lrs(etf_prices: pd.DataFrame, etf_map: dict[str, str], today_str: str, n_days: int = 10, base: float = 1.5) -> dict[str, float]:
    """Signal 5: cross-sectional ETF return rank."""
    returns: dict[str, float] = {}
    for sector, etf in etf_map.items():
        if etf not in etf_prices.columns:
            continue
        mask   = etf_prices.index <= today_str
        closes = etf_prices.loc[mask, etf].dropna()
        if len(closes) < n_days + 1:
            continue
        returns[sector] = float(closes.iloc[-1] / closes.iloc[-(n_days + 1)] - 1)
    if not returns:
        return {}
    sorted_items = sorted(returns.items(), key=lambda x: x[1], reverse=True)
    n = len(sorted_items)
    if n < 2:
        return {s: 1.0 for s in returns}
    lrs: dict[str, float] = {}
    i = 0
    while i < n:
        j = i
        while j < n and sorted_items[j][1] == sorted_items[i][1]:
            j += 1
        avg_rank = (i + j + 1) / 2
        for k in range(i, j):
            lrs[sorted_items[k][0]] = round(base ** ((n + 1 - 2 * avg_rank) / (n - 1)), 4)
        i = j
    return lrs


# ── Core reconstruction ───────────────────────────────────────────────────────

def reconstruct(start_date: str | None = None) -> pd.DataFrame:
    """
    Replay RegimeBayes forward.  Returns a DataFrame:
      index = date
      columns = {sector}_adj, {sector}_posterior, {sector}_qualified
    """
    sectors_cfg  = get_sectors()
    sector_names = sorted(sectors_cfg.keys())
    etf_map      = {name: cfg["etf"] for name, cfg in sectors_cfg.items()}
    ticker_map   = {name: cfg["tickers"] for name, cfg in sectors_cfg.items()}

    etf_prices     = load_etf_prices(sectors_cfg)
    snapshots      = load_sector_snapshots()
    ticker_hist    = load_ticker_history()

    # Convert ETF price index to string dates for consistent comparisons
    etf_prices.index = etf_prices.index.strftime("%Y-%m-%d")

    trading_days = sorted(snapshots.keys())
    if start_date:
        trading_days = [d for d in trading_days if d >= start_date]

    posteriors: dict[str, float] = {s: BASE_PRIOR for s in sector_names}
    uniform_ipo = {s: round(1.0 / len(sector_names), 4) for s in sector_names}

    records = []

    for today_str in trading_days:
        day_scores = snapshots[today_str]
        if not day_scores:
            continue

        # Current leader = highest aggregate score
        leader = max(day_scores, key=day_scores.get) if day_scores else sector_names[0]

        # Signal 3 + Signal 5 — once per day
        ld      = leader_decline(etf_prices, leader, etf_map, today_str)
        lr_rs   = _lr_rs_divergence(ld["start_score"], ld["end_score"], ld["decline_days"])
        rl      = rank_lrs(etf_prices, etf_map, today_str)

        row = {"date": today_str}

        for sector in sector_names:
            agg_score = day_scores.get(sector, 0.0)

            # Signal 1
            tickers = ticker_map.get(sector, [])
            rec, det, tot = ticker_balance_from_history(sector, tickers, ticker_hist, today_str)
            lr_t = _lr_ticker_balance(rec, det, tot)

            # Signal 2
            streak = etf_streak(etf_prices, etf_map.get(sector, ""), today_str)
            lr_e   = _lr_etf_streak(streak)

            # Signal 4 — uniform throughout (neutral; no historical IPO data)
            lr_ipo  = 1.0

            # Signal 5
            lr_rank = rl.get(sector, 1.0)

            # Decay + update
            prior         = posteriors[sector]
            decayed       = POSTERIOR_DECAY * prior + (1.0 - POSTERIOR_DECAY) * BASE_PRIOR
            trace         = _apply_signals(decayed, lr_t, lr_e, lr_rs, lr_ipo, lr_rank)
            post          = trace["posterior"]
            posteriors[sector] = post

            adj = round(agg_score * post, 4)
            row[f"{sector}_agg"]       = agg_score
            row[f"{sector}_post"]      = round(post, 4)
            row[f"{sector}_adj"]       = adj
            row[f"{sector}_qualified"] = int(adj >= ALLOCATION_FLOOR)

        records.append(row)

    df = pd.DataFrame(records).set_index("date")
    return df


# ── Report ────────────────────────────────────────────────────────────────────

def print_report(df: pd.DataFrame, demo_trades: dict[str, int]) -> None:
    sectors_cfg  = get_sectors()
    sector_names = sorted(sectors_cfg.keys())

    total_days = len(df)
    print(f"\n{'='*72}")
    print(f"BAYESIAN FLOOR RECONSTRUCTION  ({df.index[0]} – {df.index[-1]}, {total_days} trading days)")
    print(f"ALLOCATION_FLOOR = {ALLOCATION_FLOOR}")
    print(f"{'='*72}\n")

    print(f"{'Sector':<16} {'%Above':>7} {'MaxStreak':>9} {'MaxDrought':>11} {'LiveTrades':>11}  {'AvgAdj':>7}")
    print("-" * 72)

    sector_summary = []
    for sector in sector_names:
        qual_col = f"{sector}_qualified"
        adj_col  = f"{sector}_adj"
        if qual_col not in df.columns:
            continue

        qual       = df[qual_col]
        pct_above  = qual.mean() * 100

        # Max qualifying streak
        max_streak = 0
        cur_streak = 0
        for v in qual:
            if v:
                cur_streak += 1
                max_streak = max(max_streak, cur_streak)
            else:
                cur_streak = 0

        # Max drought (consecutive unqualified days)
        max_drought = 0
        cur_drought = 0
        for v in qual:
            if not v:
                cur_drought += 1
                max_drought = max(max_drought, cur_drought)
            else:
                cur_drought = 0

        avg_adj    = df[adj_col].mean()
        live_count = demo_trades.get(sector, 0)
        sector_summary.append((sector, pct_above, max_streak, max_drought, live_count, avg_adj))

    sector_summary.sort(key=lambda x: -x[1])

    for sector, pct_above, max_streak, max_drought, live_count, avg_adj in sector_summary:
        print(f"{sector:<16} {pct_above:>6.1f}% {max_streak:>9}d {max_drought:>10}d {live_count:>11}  {avg_adj:>7.4f}")

    # Monthly qualification heatmap (last 24 months)
    print(f"\n{'─'*72}")
    print("MONTHLY QUALIFICATION (last 24 months — X=any day qualified, .=blocked)")
    months = sorted({d[:7] for d in df.index})[-24:]
    header = "Sector          " + "  ".join(m[2:] for m in months)
    print(header[:120])
    for sector, pct_above, *_ in sorted(sector_summary, key=lambda x: -x[1]):
        qual_col = f"{sector}_qualified"
        row_str  = f"{sector:<16}"
        for m in months:
            month_rows = df[df.index.str.startswith(m)][qual_col]
            if len(month_rows) == 0:
                row_str += "  ?"
            elif month_rows.sum() > 0:
                row_str += "  X"
            else:
                row_str += "  ."
        print(row_str[:120])

    # Scenario diagnosis
    print(f"\n{'─'*72}")
    print("SCENARIO DIAGNOSIS")
    for sector, pct_above, max_streak, max_drought, live_count, avg_adj in sector_summary:
        if pct_above >= 60 and live_count < 3:
            print(f"  {sector}: {pct_above:.0f}% above floor, {live_count} live entries — L1 score or ticker count bottleneck")
        elif pct_above < 30:
            print(f"  {sector}: {pct_above:.0f}% above floor — posterior suppression, rarely qualifies")
        elif max_drought > 60:
            print(f"  {sector}: longest drought {max_drought}d — periodic multi-month blackouts")

    print()


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Bayesian floor reconstruction")
    parser.add_argument("--start", default=None, help="Start date YYYY-MM-DD (default: all history)")
    parser.add_argument("--csv",   default=None, help="Write full time series to CSV path")
    parser.add_argument("--refresh", action="store_true", help="Force re-download ETF prices")
    args = parser.parse_args()

    if args.refresh and ETF_CACHE_PATH.exists():
        ETF_CACHE_PATH.unlink()

    print("Loading data…")
    df           = reconstruct(start_date=args.start)
    demo_trades  = load_demo_trades()

    print_report(df, demo_trades)

    if args.csv:
        df.to_csv(args.csv)
        print(f"Time series written to {args.csv}")


if __name__ == "__main__":
    main()
