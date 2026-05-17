"""
Regime-conditioned backtest for ConsumerStaples.

The 2021–2026 threshold sweep showed PF < 1.0 for ConsumerStaples across all
viable L1 thresholds — that result is unconditional. This script asks a narrower
question: what is the PF on ConsumerStaples entries filtered to days when the
sector's Bayesian regime posterior was above a given floor?

Posterior reconstruction uses OHLCV-derivable signals only:
  Signal 1 — ticker balance  (5d consecutive returns per ticker)
  Signal 2 — XLP signed streak
  Signal 5 — cross-sectional ETF return rank (10-day return)
  Signal 3 — neutral (1.0): leader RS divergence requires historical DB leadership chain
  Signal 4 — neutral (1/n): IPO share requires external IPO data

The posterior is simulated sequentially with POSTERIOR_DECAY=0.90 so conviction
accumulates across days, matching the live regime engine's behaviour.

Trade simulation is ticker-isolated (no portfolio-level caps). Entry = close
price on signal day. Exit follows TP/SL/time-stop from live config.

Usage:
    cd /home/promenix/apex
    python -m backend.backtest.regime_conditioned_cs
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import pandas as pd
from loguru import logger

from backend.config import (
    LOCK1_THRESHOLD,
    SECTORS,
    SPY_TICKER,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    TIME_STOP_DAYS,
)
from backend.regime.regime_bayes import (
    POSTERIOR_DECAY,
    _bayesian_update,
    _lr_etf_streak,
    _lr_ipo_cluster,
    _lr_ticker_balance,
)
from backend.signals import aggregator, momentum, relative_strength, trend, volume
from backend.signals.ev_kelly import compute as ev_kelly_compute
from backend.backtest.engine_fast import (
    _download_all,
    _trading_days,
    _build_spy_cache,
    _build_price_cache,
)

# ── Config ────────────────────────────────────────────────────────────────────

START  = "2021-01-01"
END    = "2026-05-01"

CS_SECTOR  = "ConsumerStaples"
CS_ETF     = "XLP"
CS_TICKERS = SECTORS[CS_SECTOR]["tickers"]

# Posterior thresholds to stratify results against
POSTERIOR_LEVELS = [0.0, 0.40, 0.45, 0.50, 0.55, 0.60]

# L1 threshold applied to CS ticker scores
L1 = LOCK1_THRESHOLD   # 0.70 — same gate as active sectors

# Bayesian prior constants
N_SECTORS   = len(SECTORS)          # uniform base prior denominator
BASE_PRIOR  = 1.0 / N_SECTORS
TICKER_RECOVERY_DAYS = 5            # same as regime_bayes.TICKER_RECOVERY_DAYS
RS_RANK_DAYS         = 10           # n_days for cross-sectional rank (Signal 5)

W = 66


# ── Posterior reconstruction ──────────────────────────────────────────────────

def _compute_cs_posterior_series(
    raw_data: pd.DataFrame,
    trading_days: list[date],
) -> dict[str, float]:
    """
    Simulate ConsumerStaples Bayesian posterior day-by-day with decay.

    Returns {date_str: posterior} for every trading day in range.
    """
    all_etfs    = {sector: cfg["etf"] for sector, cfg in SECTORS.items()}
    cs_tickers  = CS_TICKERS

    persistent_prior = BASE_PRIOR
    posteriors: dict[str, float] = {}

    for today in trading_days:
        today_str = today.isoformat()
        ts        = pd.Timestamp(today)

        # Decay toward base prior
        decayed = POSTERIOR_DECAY * persistent_prior + (1.0 - POSTERIOR_DECAY) * BASE_PRIOR

        # Signal 1: ticker balance
        recovering    = 0
        deteriorating = 0
        total         = 0
        for ticker in cs_tickers:
            try:
                if ticker not in raw_data.columns.get_level_values(0):
                    continue
                closes = raw_data[ticker]["Close"].dropna()
                recent = closes[closes.index <= ts].tail(TICKER_RECOVERY_DAYS + 1)
                if len(recent) < TICKER_RECOVERY_DAYS + 1:
                    continue
                rets = recent.pct_change().dropna()
                total += 1
                if (rets > 0).all():
                    recovering += 1
                elif (rets < 0).all():
                    deteriorating += 1
            except Exception:
                continue
        lr_ticker = _lr_ticker_balance(recovering, deteriorating, total)

        # Signal 2: XLP signed streak
        etf_streak = 0
        try:
            if CS_ETF in raw_data.columns.get_level_values(0):
                closes = raw_data[CS_ETF]["Close"].dropna()
                recent = closes[closes.index <= ts].tail(60)
                if len(recent) >= 2:
                    rets = recent.pct_change().dropna().values
                    if len(rets) > 0:
                        direction = 1 if rets[-1] > 0 else (-1 if rets[-1] < 0 else 0)
                        if direction != 0:
                            count = 0
                            for r in reversed(rets):
                                if (direction > 0 and r > 0) or (direction < 0 and r < 0):
                                    count += 1
                                else:
                                    break
                            etf_streak = direction * count
        except Exception:
            pass
        lr_etf = _lr_etf_streak(etf_streak)

        # Signal 3: RS divergence — neutral (leader RS not reconstructable from OHLCV alone)
        lr_rs = 1.0

        # Signal 4: IPO cluster — neutral (1/n_sectors)
        lr_ipo = _lr_ipo_cluster(BASE_PRIOR)

        # Signal 5: cross-sectional ETF return rank
        lr_rank = _compute_rank_lrs_on(raw_data, all_etfs, today, n_days=RS_RANK_DAYS).get(CS_SECTOR, 1.0)

        # Sequential Bayesian update
        p = decayed
        p = _bayesian_update(p, lr_ticker)
        p = _bayesian_update(p, lr_etf)
        p = _bayesian_update(p, lr_rs)
        p = _bayesian_update(p, lr_ipo)
        p = _bayesian_update(p, lr_rank)

        posteriors[today_str] = round(p, 4)
        persistent_prior = p

    return posteriors


def _compute_rank_lrs_on(
    raw_data: pd.DataFrame,
    sector_etf_map: dict[str, str],
    today: date,
    n_days: int = 10,
    base: float = 1.5,
) -> dict[str, float]:
    """Cross-sectional ETF return rank LRs for a specific date."""
    ts = pd.Timestamp(today)
    returns: dict[str, float] = {}
    for sector, etf in sector_etf_map.items():
        try:
            if etf not in raw_data.columns.get_level_values(0):
                continue
            closes = raw_data[etf]["Close"].dropna()
            closes = closes[closes.index <= ts]
            if len(closes) < n_days + 1:
                continue
            returns[sector] = float(closes.iloc[-1] / closes.iloc[-(n_days + 1)] - 1)
        except Exception:
            continue

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
            sector = sorted_items[k][0]
            lrs[sector] = round(base ** ((n + 1 - 2 * avg_rank) / (n - 1)), 4)
        i = j

    return lrs


# ── CS ticker scoring ─────────────────────────────────────────────────────────

def _score_cs_tickers_per_day(
    raw_data: pd.DataFrame,
    trading_days: list[date],
    spy_cache: dict[str, dict],
    price_cache: dict[tuple[str, str], float],
) -> dict[tuple[str, str], dict]:
    """
    Returns {(ticker, date_str): {"signal_score", "entry_price"}} for all
    ConsumerStaples tickers × trading days where score >= L1.
    """
    cs_etf_mult_cache: dict[str, float] = _build_cs_etf_mult(raw_data, trading_days)
    cache: dict[tuple[str, str], dict] = {}

    for ticker in CS_TICKERS:
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
                etf_mult  = cs_etf_mult_cache.get(date_str, 1.0)

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

                if score < L1:
                    continue

                entry_price = price_cache.get((ticker, date_str))
                if entry_price is None or entry_price <= 0:
                    continue

                cache[(ticker, date_str)] = {"signal_score": score, "entry_price": entry_price}
            except Exception:
                continue

    return cache


def _build_cs_etf_mult(raw_data: pd.DataFrame, trading_days: list[date]) -> dict[str, float]:
    """XLP ETF multiplier: 1.0 if price >= MA20, else 0.85."""
    cache: dict[str, float] = {}
    try:
        closes = raw_data[CS_ETF]["Close"].dropna()
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


# ── Trade simulation ──────────────────────────────────────────────────────────

def _simulate_trades(
    entries: dict[tuple[str, str], dict],
    price_cache: dict[tuple[str, str], float],
    trading_days: list[date],
    posteriors: dict[str, float],
) -> list[dict]:
    """
    Simulate trades for each (ticker, entry_date) in entries.
    Each trade is evaluated independently (no portfolio caps).
    Returns list of trade records with entry_date posterior attached.
    """
    day_index = {d: i for i, d in enumerate(trading_days)}
    records   = []

    for (ticker, entry_date_str), sig in entries.items():
        entry_price = sig["entry_price"]
        entry_date  = date.fromisoformat(entry_date_str)
        entry_idx   = day_index.get(entry_date)
        if entry_idx is None:
            continue

        posterior    = posteriors.get(entry_date_str, BASE_PRIOR)
        peak_price   = entry_price
        outcome      = "EXPIRED"
        exit_price   = entry_price
        exit_reason  = "END_OF_BACKTEST"
        pnl_pct      = 0.0

        future_days = trading_days[entry_idx + 1:]
        for i, future in enumerate(future_days):
            future_str = future.isoformat()
            current = price_cache.get((ticker, future_str))
            if current is None:
                continue

            peak_price = max(peak_price, current)
            pnl_pct    = (current - entry_price) / entry_price
            days_held  = i + 1

            if pnl_pct >= TAKE_PROFIT_PCT:
                outcome, exit_price, exit_reason = "WIN", current, "TP"
                break
            elif pnl_pct <= -STOP_LOSS_PCT:
                outcome, exit_price, exit_reason = "LOSS", current, "SL"
                break
            elif days_held >= TIME_STOP_DAYS:
                outcome, exit_price, exit_reason = "EXPIRED", current, "TIME"
                break

        records.append({
            "ticker":      ticker,
            "entry_date":  entry_date_str,
            "entry_price": round(entry_price, 4),
            "exit_price":  round(exit_price, 4),
            "pnl_pct":     round(pnl_pct, 4),
            "outcome":     outcome,
            "exit_reason": exit_reason,
            "posterior":   posterior,
            "signal_score": sig["signal_score"],
        })

    return records


# ── Stats & display ───────────────────────────────────────────────────────────

def _stats(records: list[dict]) -> dict:
    wins   = [r for r in records if r["outcome"] == "WIN"]
    losses = [r for r in records if r["outcome"] in ("LOSS", "EXPIRED")]
    closed = wins + losses
    gp = sum(r["pnl_pct"] for r in wins)   if wins   else 0.0
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


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    logger.info(f"Regime-conditioned CS backtest: {START} → {END}")

    # Load OHLCV data (reuses Parquet cache from sector_sweep)
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

    # 1. Posterior series
    logger.info("Computing ConsumerStaples regime posteriors…")
    posteriors = _compute_cs_posterior_series(raw_data, trading_days)
    post_values = list(posteriors.values())
    logger.info(
        f"Posterior stats: min={min(post_values):.3f}  "
        f"mean={sum(post_values)/len(post_values):.3f}  "
        f"max={max(post_values):.3f}"
    )

    # 2. Score CS tickers
    logger.info("Scoring ConsumerStaples tickers (this takes ~1–2 min)…")
    entries = _score_cs_tickers_per_day(raw_data, trading_days, spy_cache, price_cache)
    logger.info(f"{len(entries)} CS entry signals passing L1={L1}")

    # 3. Simulate trades
    logger.info("Simulating trades…")
    records = _simulate_trades(entries, price_cache, trading_days, posteriors)
    logger.info(f"{len(records)} trades simulated")

    # 4. Posterior distribution of entry signals
    entry_posts = [r["posterior"] for r in records]
    buckets = [0.40, 0.45, 0.50, 0.55, 0.60, 0.65]
    print()
    print("═" * W)
    print(f"  REGIME-CONDITIONED BACKTEST — ConsumerStaples (XLP)")
    print(f"  {START} → {END}  |  L1 threshold = {L1}")
    print(f"  Signals used: ticker balance, XLP streak, ETF rank (IPO/RS = neutral)")
    print("═" * W)

    # Posterior distribution of entry days
    print()
    print("  Posterior distribution at entry (days with ≥1 passing signal):")
    print(_bar())
    for lo, hi in zip(buckets, buckets[1:] + [1.01]):
        n = sum(1 for p in entry_posts if lo <= p < hi)
        print(f"    [{lo:.2f}, {hi:.2f})  {n:>4} signals  {n/len(entry_posts)*100:.1f}%")
    print()

    # Stratified PF/win rate
    print(f"  {'Posterior floor':>18}  {'Trades':>6}  {'WinRate':>8}  {'PF':>6}  {'AvgWin':>8}  {'AvgLoss':>8}")
    print(_bar())
    for floor in POSTERIOR_LEVELS:
        subset = [r for r in records if r["posterior"] >= floor]
        s = _stats(subset)
        pf_str = f"{s['pf']:.3f}" if s["pf"] is not None else "  —"
        label  = "unconditional" if floor == 0.0 else f"post ≥ {floor:.2f}"
        marker = " *" if floor == 0.50 else "  "
        print(
            f"{marker} {label:>18}  "
            f"{s['n']:>6}  "
            f"{_pct(s['win_rate']):>8}  "
            f"{pf_str:>6}  "
            f"{_pct(s['avg_win']):>8}  "
            f"{_pct(s['avg_loss']):>8}"
        )

    print()
    print("═" * W)

    # Per-ticker breakdown at post >= 0.50
    subset_50 = [r for r in records if r["posterior"] >= 0.50]
    if subset_50:
        print()
        print("  Per-ticker breakdown (post ≥ 0.50):")
        print(_bar())
        print(f"  {'Ticker':>8}  {'Trades':>6}  {'WinRate':>8}  {'PF':>6}")
        print(_bar())
        for ticker in CS_TICKERS:
            t_recs = [r for r in subset_50 if r["ticker"] == ticker]
            if not t_recs:
                continue
            s = _stats(t_recs)
            pf_str = f"{s['pf']:.3f}" if s["pf"] is not None else "  —"
            print(
                f"  {ticker:>8}  "
                f"{s['n']:>6}  "
                f"{_pct(s['win_rate']):>8}  "
                f"{pf_str:>6}"
            )
        print()
        print("═" * W)

    print()


if __name__ == "__main__":
    main()
