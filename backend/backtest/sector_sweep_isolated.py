"""
Ticker-isolated L1 threshold sweep — any APEX sector.

Sweeps L1 signal score from 0.55 → 0.90 in 5pp steps. Each trade is evaluated
independently (no portfolio-level slot competition), so per-sector PF reflects
the sector's signal quality, not survivor-set contamination from other sectors.

Methodology note: This is the correct tool for floor decisions on individual
sectors. sector_sweep.py (portfolio sweep) is contaminated by slot competition
and should not be used as the primary input for sector floor settings.

Usage:
    cd /home/promenix/apex
    python -m backend.backtest.sector_sweep_isolated --sector Defense
    python -m backend.backtest.sector_sweep_isolated --sector Semiconductors
"""
from __future__ import annotations

import argparse
import math
from datetime import date, timedelta

import pandas as pd
from loguru import logger

from backend.config import (
    SECTORS,
    SPY_TICKER,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    TIME_STOP_DAYS,
)
from backend.signals import aggregator, momentum, relative_strength, trend, volume
from backend.signals.ev_kelly import compute as ev_kelly_compute
from backend.backtest.engine_fast import (
    _download_all,
    _trading_days,
    _build_spy_cache,
    _build_price_cache,
)

# ── Config ─────────────────────────────────────────────────────────────────────

START      = "2021-01-01"
END        = "2026-05-01"
THRESHOLDS = [0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90]
MIN_TRADES = 5

W = 66


# ── ETF multiplier ─────────────────────────────────────────────────────────────

def _build_etf_mult(
    raw_data: pd.DataFrame,
    trading_days: list[date],
    sector_etf: str,
) -> dict[str, float]:
    cache: dict[str, float] = {}
    try:
        closes = raw_data[sector_etf]["Close"].dropna()
        ma20   = closes.rolling(20).mean()
        for d in trading_days:
            ts = pd.Timestamp(d)
            try:
                price = float(closes.asof(ts))
                ma    = float(ma20.asof(ts))
                cache[d.isoformat()] = 1.0 if price >= ma else 0.85
            except Exception:
                cache[d.isoformat()] = 1.0
    except Exception:
        for d in trading_days:
            cache[d.isoformat()] = 1.0
    return cache


# ── Ticker scoring ─────────────────────────────────────────────────────────────

def _score_tickers_per_day(
    raw_data: pd.DataFrame,
    trading_days: list[date],
    spy_cache: dict[str, dict],
    price_cache: dict[tuple[str, str], float],
    sector_tickers: list[str],
    sector_etf: str,
    l1: float,
) -> dict[tuple[str, str], dict]:
    etf_mult_cache = _build_etf_mult(raw_data, trading_days, sector_etf)
    cache: dict[tuple[str, str], dict] = {}

    for ticker in sector_tickers:
        if ticker not in raw_data.columns.get_level_values(0):
            continue
        df_full = raw_data[ticker].copy()
        if df_full.empty or len(df_full) < 55:
            continue

        for d in trading_days:
            ts       = pd.Timestamp(d)
            date_str = d.isoformat()
            try:
                df = df_full[df_full.index <= ts].tail(90)
                if len(df) < 55:
                    continue

                spy_ret20 = spy_cache.get(date_str, {}).get("return_20d", 0.0)
                spy_reg   = spy_cache.get(date_str, {}).get("regime", 0.0)
                etf_mult  = etf_mult_cache.get(date_str, 1.0)

                mom = momentum.compute(df)
                if mom.get("rsi_blocked"):
                    continue
                vol = volume.compute(df)
                trd = trend.compute(df)
                rs  = relative_strength.compute(df, spy_ret20)

                atr_raw = (df["High"] - df["Low"]).rolling(14).mean().iloc[-1]
                atr_pct = float(atr_raw / mom["price"]) if mom["price"] and not math.isnan(atr_raw) else 0.0

                evk   = ev_kelly_compute(spy_reg, rolling_win_rate=None, atr_pct=atr_pct)
                score = aggregator.compute(
                    mom["momentum_score"], vol["volume_score"], evk["ev_norm"],
                    trd["trend_score"], rs["rs_score"],
                )
                score = round(min(max(score * etf_mult, 0.0), 1.0), 4)

                if score < l1:
                    continue

                entry_price = price_cache.get((ticker, date_str))
                if entry_price is None or entry_price <= 0:
                    continue

                cache[(ticker, date_str)] = {"signal_score": score, "entry_price": entry_price}
            except Exception:
                continue

    return cache


# ── Trade simulation ───────────────────────────────────────────────────────────

def _simulate_trades(
    entries: dict[tuple[str, str], dict],
    price_cache: dict[tuple[str, str], float],
    trading_days: list[date],
) -> list[dict]:
    day_index = {d: i for i, d in enumerate(trading_days)}
    records   = []

    for (ticker, entry_date_str), sig in entries.items():
        entry_price = sig["entry_price"]
        entry_date  = date.fromisoformat(entry_date_str)
        entry_idx   = day_index.get(entry_date)
        if entry_idx is None:
            continue

        outcome     = "EXPIRED"
        exit_price  = entry_price
        pnl_pct     = 0.0

        future_days = trading_days[entry_idx + 1:]
        for i, future in enumerate(future_days):
            future_str = future.isoformat()
            current    = price_cache.get((ticker, future_str))
            if current is None:
                continue

            pnl_pct   = (current - entry_price) / entry_price
            days_held = i + 1

            if pnl_pct >= TAKE_PROFIT_PCT:
                outcome, exit_price = "WIN", current
                break
            elif pnl_pct <= -STOP_LOSS_PCT:
                outcome, exit_price = "LOSS", current
                break
            elif days_held >= TIME_STOP_DAYS:
                outcome, exit_price = "EXPIRED", current
                break

        records.append({
            "ticker":      ticker,
            "entry_date":  entry_date_str,
            "pnl_pct":     round(pnl_pct, 4),
            "outcome":     outcome,
        })

    return records


# ── Stats ──────────────────────────────────────────────────────────────────────

def _stats(records: list[dict]) -> dict:
    wins   = [r for r in records if r["outcome"] == "WIN"]
    losses = [r for r in records if r["outcome"] in ("LOSS", "EXPIRED")]
    closed = wins + losses
    gp = sum(r["pnl_pct"] for r in wins)        if wins   else 0.0
    gl = abs(sum(r["pnl_pct"] for r in losses)) if losses else 0.0
    return {
        "n":        len(records),
        "win_rate": len(wins) / len(closed) if closed else None,
        "pf":       gp / gl if gl > 0 else None,
        "avg_win":  sum(r["pnl_pct"] for r in wins) / len(wins) if wins else None,
        "avg_loss": sum(r["pnl_pct"] for r in losses) / len(losses) if losses else None,
    }

def _pct(v: float | None) -> str:
    if v is None:
        return "   —   "
    return f"{v*100:+.1f}%"

def _bar(char="─", w=W) -> str:
    return char * w

def _verdict(n: int, pf: float | None) -> str:
    if n < MIN_TRADES:
        return "insufficient data"
    if pf is None:
        return "no wins"
    if pf >= 2.0:
        return "<-- strong edge"
    if pf >= 1.0:
        return "<-- PF > 1"
    return ""


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Isolated sector threshold sweep")
    parser.add_argument(
        "--sector",
        default="Defense",
        help="Sector name as defined in config.py SECTORS",
    )
    args = parser.parse_args()

    sector_name = args.sector
    if sector_name not in SECTORS:
        raise ValueError(f"Unknown sector '{sector_name}'. Valid: {list(SECTORS.keys())}")

    sector_etf     = SECTORS[sector_name]["etf"]
    sector_tickers = SECTORS[sector_name]["tickers"]

    logger.info(f"Isolated threshold sweep: {sector_name} ({sector_etf})  {START} → {END}")

    lookback_start = date.fromisoformat(START) - timedelta(days=130)
    end_date       = date.fromisoformat(END)
    etf_tickers    = [cfg["etf"] for cfg in SECTORS.values()]
    all_tickers    = (
        [SPY_TICKER, "^VIX"]
        + etf_tickers
        + [t for cfg in SECTORS.values() for t in cfg["tickers"]]
    )

    logger.info("Loading/downloading OHLCV data…")
    raw_data = _download_all(all_tickers, lookback_start, end_date)
    if raw_data.empty:
        raise RuntimeError("Failed to download historical data")

    trading_days = _trading_days(date.fromisoformat(START), end_date, raw_data)
    logger.info(f"{len(trading_days)} trading days")

    spy_cache   = _build_spy_cache(raw_data, trading_days)
    price_cache = _build_price_cache(raw_data, all_tickers, trading_days)

    print()
    print("═" * W)
    print(f"  ISOLATED THRESHOLD SWEEP — {sector_name} ({sector_etf})")
    print(f"  {START} → {END}  |  Ticker-isolated (no portfolio slot competition)")
    print("═" * W)
    print()
    print(f"  {'Threshold':>10}  {'Trades':>6}  {'WinRate':>8}  {'PF':>6}  {'AvgWin':>8}  {'AvgLoss':>8}  {''}")
    print(_bar())

    for thresh in THRESHOLDS:
        logger.info(f"  Scoring at L1 = {thresh:.2f}…")
        entries = _score_tickers_per_day(
            raw_data, trading_days, spy_cache, price_cache,
            sector_tickers, sector_etf, thresh,
        )
        records = _simulate_trades(entries, price_cache, trading_days)
        s       = _stats(records)

        pf_str  = f"{s['pf']:.3f}" if s["pf"] is not None else "  —"
        marker  = " *" if thresh == 0.70 else "  "
        verdict = _verdict(s["n"], s["pf"])
        print(
            f"{marker} {thresh:.2f}        "
            f"{s['n']:>6}  "
            f"{_pct(s['win_rate']):>8}  "
            f"{pf_str:>6}  "
            f"{_pct(s['avg_win']):>8}  "
            f"{_pct(s['avg_loss']):>8}  "
            f"{verdict}"
        )

    print()
    print("═" * W)

    # Per-ticker breakdown at baseline (0.70)
    entries_70 = _score_tickers_per_day(
        raw_data, trading_days, spy_cache, price_cache,
        sector_tickers, sector_etf, 0.70,
    )
    records_70 = _simulate_trades(entries_70, price_cache, trading_days)
    if records_70:
        print()
        print("  Per-ticker breakdown at L1 = 0.70:")
        print(_bar())
        print(f"  {'Ticker':>8}  {'Trades':>6}  {'WinRate':>8}  {'PF':>6}  {'AvgWin':>8}  {'AvgLoss':>8}")
        print(_bar())
        for ticker in sector_tickers:
            t_recs = [r for r in records_70 if r["ticker"] == ticker]
            if not t_recs:
                continue
            s      = _stats(t_recs)
            pf_str = f"{s['pf']:.3f}" if s["pf"] is not None else "  —"
            print(
                f"  {ticker:>8}  "
                f"{s['n']:>6}  "
                f"{_pct(s['win_rate']):>8}  "
                f"{pf_str:>6}  "
                f"{_pct(s['avg_win']):>8}  "
                f"{_pct(s['avg_loss']):>8}"
            )
        print()
        print("═" * W)

    print()


if __name__ == "__main__":
    main()
