"""
RSI-at-entry performance analysis.

Runs two passes over 2023-01-01 → 2026-01-01 using the same precomputed cache:
  Pass 1 — baseline (current behaviour)
  Pass 2 — RSI filter applied: hard cap at >=80, linear discount 70-80

Usage:
    cd /home/promenix/apex
    python -m backend.backtest.rsi_entry_analysis
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta

import pandas as pd
from loguru import logger

from backend.config import (
    DAILY_LOSS_CAP,
    EXCLUDED_SECTORS,
    LOCK1_THRESHOLD,
    MAX_POSITION_SIZE,
    MAX_POSITIONS,
    MAX_SECTOR_EXPOSURE,
    SECTOR_THRESHOLD_FLOORS,
    SECTORS,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    TIME_STOP_DAYS,
    WIN_RATE_MIN_TRADES,
)
from backend.signals import momentum
from backend.backtest.engine_fast import (
    precompute,
    _check_exits_fast,
    _get_candidates_from_cache,
    _sector_exposure,
    _trading_days_count,
)

RSI_BUCKETS = [
    (0,  30,  "<30  (oversold)"),
    (30, 40,  "30-40"),
    (40, 50,  "40-50"),
    (50, 60,  "50-60"),
    (60, 70,  "60-70"),
    (70, 80,  "70-80"),
    (80, 101, ">80  (overbought)"),
]

RSI_DISCOUNT_START = 70
RSI_HARD_CAP       = 80


def _build_rsi_cache(
    raw_data: pd.DataFrame,
    ticker_sector: dict[str, str],
    trading_days: list[date],
) -> dict[tuple[str, str], float]:
    cache: dict[tuple[str, str], float] = {}
    total = len(ticker_sector)
    for i, (ticker, sector) in enumerate(ticker_sector.items(), 1):
        if i % 10 == 0:
            logger.debug(f"RSI cache: {i}/{total}")
        if sector in EXCLUDED_SECTORS:
            continue
        try:
            if ticker not in raw_data.columns.get_level_values(0):
                continue
            df_full = raw_data[ticker].copy()
            if df_full.empty or len(df_full) < 55:
                continue
            for d in trading_days:
                ts       = pd.Timestamp(d)
                date_str = d.isoformat()
                try:
                    mask = df_full.index <= ts
                    df   = df_full[mask].tail(90)
                    if len(df) < 55:
                        continue
                    mom = momentum.compute(df)
                    cache[(ticker, date_str)] = mom["rsi"]
                except Exception:
                    continue
        except Exception:
            continue
    logger.info(f"RSI cache: {len(cache)} entries")
    return cache


def _rsi_blocked(rsi: float | None) -> bool:
    return rsi is not None and rsi >= RSI_HARD_CAP


def _rsi_score_discounted(rsi: float | None, base_signal_score: float) -> float:
    """Apply linear discount to signal_score for RSI in discount zone."""
    if rsi is None or rsi < RSI_DISCOUNT_START:
        return base_signal_score
    discount = 1.0 - (rsi - RSI_DISCOUNT_START) / (RSI_HARD_CAP - RSI_DISCOUNT_START)
    discount = max(0.0, discount)
    # Discount only the RSI contribution (≈10% of signal_score = 0.4 * 0.25)
    # Approximate: scale the full score proportionally by the discount factor
    return round(base_signal_score * (0.9 + 0.1 * discount), 4)


def _simulate(
    trading_days, price_cache, spy_cache, signal_cache,
    ticker_sector, sector_etf, etf_cache, rsi_cache,
    rsi_filter: bool,
) -> list[dict]:
    tp    = TAKE_PROFIT_PCT
    sl    = STOP_LOSS_PCT
    tdays = TIME_STOP_DAYS
    l1    = LOCK1_THRESHOLD
    SL_COOLDOWN_DAYS = 5

    balance       = 10_000.0
    open_trades:  list[dict] = []
    closed_trades: list[dict] = []
    daily_losses: dict[str, float] = {}
    sl_cooldown:  dict[str, date]  = {}

    for today in trading_days:
        today_str = today.isoformat()

        closed_today = _check_exits_fast(open_trades, price_cache, today, today_str, tp, sl, tdays)
        for ct in closed_today:
            trade  = ct["_trade"]
            record = ct["record"]
            open_trades.remove(trade)
            closed_trades.append({**record, "entry_rsi": trade["entry_rsi"]})
            balance += trade["amount"] + (record["pnl"] or 0)
            if record["outcome"] in ("LOSS", "EXPIRED") and (record["pnl"] or 0) < 0:
                daily_losses[today_str] = daily_losses.get(today_str, 0) + abs(record["pnl"] or 0)
            if record["exit_reason"] in ("SL", "TSL"):
                sl_cooldown[trade["ticker"]] = today + timedelta(days=SL_COOLDOWN_DAYS)

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

            entry_rsi = rsi_cache.get((sig["ticker"], today_str))

            if rsi_filter and _rsi_blocked(entry_rsi):
                continue

            sector    = sig["sector"]
            score     = _rsi_score_discounted(entry_rsi, sig["signal_score"]) if rsi_filter else sig["signal_score"]
            alloc_pct = min(sig["kelly_size"] if sig["kelly_size"] > 0 else 0.10, MAX_POSITION_SIZE)
            amount    = balance * alloc_pct
            projected = sector_exp.get(sector, 0.0) + (amount / balance)
            if projected > MAX_SECTOR_EXPOSURE:
                continue

            entry_price = price_cache.get((sig["ticker"], today_str))
            if not entry_price or entry_price <= 0 or amount < 50:
                continue

            balance -= amount
            open_trades.append({
                "ticker":       sig["ticker"],
                "sector":       sector,
                "entry_date":   today_str,
                "entry_price":  entry_price,
                "peak_price":   entry_price,
                "shares":       amount / entry_price,
                "amount":       amount,
                "signal_score": score,
                "entry_rsi":    entry_rsi,
                "tp":           tp,
                "sl":           sl,
            })
            open_tickers.add(sig["ticker"])
            sector_exp[sector] = sector_exp.get(sector, 0.0) + (amount / balance)

    last_day = trading_days[-1]
    last_str = last_day.isoformat()
    for trade in open_trades:
        price = price_cache.get((trade["ticker"], last_str))
        if price is None:
            continue
        pnl_pct = (price - trade["entry_price"]) / trade["entry_price"]
        closed_trades.append({
            "ticker":      trade["ticker"],
            "pnl_pct":     round(pnl_pct, 4),
            "outcome":     "WIN" if pnl_pct >= 0 else "LOSS",
            "exit_reason": "OPEN",
            "entry_rsi":   trade["entry_rsi"],
        })

    return closed_trades


def run_analysis(start_date: str = "2023-01-01", end_date: str = "2026-01-01") -> None:
    logger.info(f"RSI entry analysis {start_date} → {end_date}")

    pc = precompute(start_date, end_date)
    raw_data      = pc["raw_data"]
    ticker_sector = pc["ticker_sector"]
    sector_etf    = pc["sector_etf"]
    trading_days  = pc["trading_days"]
    price_cache   = pc["price_cache"]
    spy_cache     = pc["spy_cache"]
    etf_cache     = pc["etf_cache"]
    signal_cache  = pc["signal_cache"]

    logger.info("Building RSI cache ...")
    rsi_cache = _build_rsi_cache(raw_data, ticker_sector, trading_days)

    sim_args = (trading_days, price_cache, spy_cache, signal_cache,
                ticker_sector, sector_etf, etf_cache, rsi_cache)

    logger.info("Pass 1: baseline ...")
    baseline = _simulate(*sim_args, rsi_filter=False)

    logger.info("Pass 2: RSI filter (cap >=80, discount 70-80) ...")
    filtered = _simulate(*sim_args, rsi_filter=True)

    _print_comparison(baseline, filtered, start_date, end_date)


def _bucket_label(rsi: float | None) -> str:
    if rsi is None:
        return "unknown"
    for lo, hi, label in RSI_BUCKETS:
        if lo <= rsi < hi:
            return label
    return "unknown"


def _summary(trades: list[dict]) -> dict:
    n        = len(trades)
    wins     = [t for t in trades if t["outcome"] == "WIN"]
    losses   = [t for t in trades if t["outcome"] in ("LOSS", "EXPIRED")]
    win_pct  = len(wins) / n * 100 if n else 0
    avg_pnl  = sum(t["pnl_pct"] for t in trades) / n * 100 if n else 0
    gw       = sum(t["pnl_pct"] for t in wins   if t["pnl_pct"] > 0)
    gl       = abs(sum(t["pnl_pct"] for t in losses if t["pnl_pct"] < 0))
    pf       = gw / gl if gl > 0 else float("inf")
    return {"n": n, "win_pct": win_pct, "avg_pnl": avg_pnl, "pf": pf}


def _print_comparison(baseline: list[dict], filtered: list[dict], start: str, end: str) -> None:
    print(f"\nRSI-at-Entry Analysis  {start} → {end}")

    b = _summary(baseline)
    f = _summary(filtered)

    print(f"\n{'':30} {'BASELINE':>10}  {'FILTERED':>10}  {'DELTA':>10}")
    print("-" * 65)
    print(f"{'Total trades':<30} {b['n']:>10}  {f['n']:>10}  {f['n']-b['n']:>+10}")
    print(f"{'Win rate':<30} {b['win_pct']:>9.1f}%  {f['win_pct']:>9.1f}%  {f['win_pct']-b['win_pct']:>+9.1f}%")
    print(f"{'Avg PnL%':<30} {b['avg_pnl']:>+9.2f}%  {f['avg_pnl']:>+9.2f}%  {f['avg_pnl']-b['avg_pnl']:>+9.2f}%")
    print(f"{'Profit factor':<30} {b['pf']:>10.2f}  {f['pf']:>10.2f}  {f['pf']-b['pf']:>+10.2f}")

    print(f"\nBaseline — RSI bucket breakdown:")
    _print_bucket_table(baseline)

    print(f"\nFiltered — RSI bucket breakdown:")
    _print_bucket_table(filtered)


def _print_bucket_table(trades: list[dict]) -> None:
    buckets: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        buckets[_bucket_label(t.get("entry_rsi"))].append(t)

    header = f"  {'RSI bucket':<22} {'N':>5}  {'Win%':>6}  {'Avg PnL%':>9}  {'PF':>6}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for _, _, label in RSI_BUCKETS:
        bucket = buckets[label]
        if not bucket:
            print(f"  {label:<22} {'—':>5}")
            continue
        s = _summary(bucket)
        print(f"  {label:<22} {s['n']:>5}  {s['win_pct']:>5.1f}%  {s['avg_pnl']:>+8.2f}%  {s['pf']:>6.2f}")
    print()


if __name__ == "__main__":
    run_analysis()
