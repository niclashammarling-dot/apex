"""
Backtesting engine — replays Lock 1 (quantitative signals) across a historical
date range using the same signal pipeline as the live system.

Lock 2 and Lock 3 are not replayable (live LLM calls), so the backtest uses
Lock 1 as the sole entry filter. This lets you validate whether the quant
signal engine has genuine predictive value before attributing edge to the LLMs.

Two additional lock proxies CAN be backtested from historical data:
  - vix_threshold: skip new entries when VIX > N (Macro lock proxy)
  - use_leading_rs: only enter if ticker outperforms its sector ETF over 5d (Leading lock RS proxy)

Usage:
    from backend.backtest.engine import run
    results = run("2024-01-01", "2024-12-31", initial_balance=10_000)
"""
from __future__ import annotations

import math
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
    SECTOR_THRESHOLD_FLOORS,
    SECTORS,
    SPY_TICKER,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    TIME_STOP_DAYS,
    WIN_RATE_MIN_TRADES,
)
from backend.signals import aggregator, momentum, relative_strength, trend, volume
from backend.signals.ev_kelly import compute as ev_kelly_compute

# ── Types ─────────────────────────────────────────────────────────────────────

class TradeRecord(TypedDict):
    ticker: str
    sector: str
    entry_date: str
    exit_date: str | None
    entry_price: float
    exit_price: float | None
    shares: float
    amount: float
    pnl: float | None
    pnl_pct: float | None
    outcome: str           # OPEN | WIN | LOSS | EXPIRED
    exit_reason: str | None
    signal_score: float


class BacktestResult(TypedDict):
    start_date: str
    end_date: str
    initial_balance: float
    final_balance: float
    total_return_pct: float
    cagr: float
    sharpe: float
    max_drawdown: float
    win_rate: float | None
    total_trades: int
    winning_trades: int
    losing_trades: int
    avg_win_pct: float | None
    avg_loss_pct: float | None
    profit_factor: float | None
    spy_return_pct: float | None
    equity_curve: list[dict]
    trade_log: list[TradeRecord]


# ── Main entry point ──────────────────────────────────────────────────────────

def run(
    start_date: str,
    end_date: str,
    initial_balance: float = 10_000.0,
    take_profit_pct:         float | None = None,
    stop_loss_pct:           float | None = None,
    trailing_stop_pct:       float | None = None,
    profit_lock_trigger_pct: float | None = None,
    profit_lock_trail_pct:   float | None = None,
    atr_exits:          bool        = False,
    earnings_filter_days: int | None = None,
    time_stop_days:     int   | None = None,
    lock1_threshold:    float | None = None,
    max_entries_per_day: int  | None = None,
    vix_threshold:      float | None = None,
    use_leading_rs:     bool        = False,
    max_positions:      int   | None = None,
    sl_cooldown_days:   int         = 5,
    tp_cooldown_days:   int         = 0,
) -> BacktestResult:
    """
    Run a historical backtest from start_date to end_date.

    Args:
        start_date:          ISO date string "YYYY-MM-DD"
        end_date:            ISO date string "YYYY-MM-DD"
        initial_balance:     Starting paper capital
        take_profit_pct:     Override TAKE_PROFIT_PCT (e.g. 0.08)
        stop_loss_pct:       Override STOP_LOSS_PCT (e.g. 0.05)
        trailing_stop_pct:   Trailing stop as fraction of peak price (e.g. 0.05 = 5%)
        atr_exits:           Use per-trade ATR-based TP/SL (2x ATR take-profit, 1x ATR stop-loss)
        earnings_filter_days: Skip entry if earnings within N days (e.g. 3). None disables.
        time_stop_days:      Override TIME_STOP_DAYS (e.g. 15)
        lock1_threshold:     Override LOCK1_THRESHOLD (e.g. 0.72)
        max_entries_per_day: Cap new positions opened on a single day (e.g. 2)
        vix_threshold:       Skip new entries when VIX exceeds this level (e.g. 25). None disables.
        use_leading_rs:      Only enter if ticker's 5d return > its sector ETF's 5d return.
        max_positions:       Override MAX_POSITIONS (e.g. 3). Lower = more concentrated portfolio.

    Returns:
        BacktestResult dict with performance metrics and full trade log.
    """
    tp       = take_profit_pct    if take_profit_pct    is not None else TAKE_PROFIT_PCT
    sl       = stop_loss_pct      if stop_loss_pct      is not None else STOP_LOSS_PCT
    tsl      = trailing_stop_pct  # None means disabled
    pl_trig  = profit_lock_trigger_pct
    pl_trail = profit_lock_trail_pct
    tdays    = time_stop_days     if time_stop_days     is not None else TIME_STOP_DAYS
    l1       = lock1_threshold    if lock1_threshold    is not None else LOCK1_THRESHOLD
    max_epd  = max_entries_per_day  # None means unlimited
    max_pos  = max_positions      if max_positions      is not None else MAX_POSITIONS

    start = date.fromisoformat(start_date)
    end   = date.fromisoformat(end_date)

    if end <= start:
        raise ValueError("end_date must be after start_date")

    logger.info(f"Backtest: loading data {start} → {end} (tp={tp:.0%} sl={sl:.0%} days={tdays} l1={l1})")

    # Download full history for all tickers + SPY + sector ETFs (+ VIX if macro filter active)
    lookback_start = start - timedelta(days=130)  # 130d buffer for 90d rolling indicators + MA50
    etf_tickers    = [cfg["etf"] for cfg in SECTORS.values()]
    vix_ticker     = "^VIX" if vix_threshold is not None else None
    all_tickers    = (
        [SPY_TICKER]
        + ([vix_ticker] if vix_ticker else [])
        + etf_tickers
        + [t for cfg in SECTORS.values() for t in cfg["tickers"]]
    )
    raw_data       = _download_all(all_tickers, lookback_start, end)

    if raw_data.empty:
        raise RuntimeError("Failed to download historical data")

    # Build ticker→sector map and sector→ETF map
    ticker_sector = {
        t: sector
        for sector, cfg in SECTORS.items()
        for t in cfg["tickers"]
    }
    sector_etf = {sector: cfg["etf"] for sector, cfg in SECTORS.items()}

    # Get all trading days in range
    trading_days = _trading_days(start, end, raw_data)
    logger.info(f"Backtest: {len(trading_days)} trading days, {len(all_tickers)-1} tickers")

    # Pre-fetch earnings dates if filter is enabled
    earnings_dates: dict[str, set[date]] = {}
    if earnings_filter_days is not None:
        stock_tickers = [t for cfg in SECTORS.values() for t in cfg["tickers"]]
        earnings_dates = _fetch_earnings_dates(stock_tickers, start, end)
        logger.info(f"Backtest: loaded earnings dates for {len(earnings_dates)} tickers")

    SL_COOLDOWN_DAYS = sl_cooldown_days
    TP_COOLDOWN_DAYS = tp_cooldown_days

    # Simulate day by day
    balance      = initial_balance
    open_trades: list[dict] = []
    closed_trades: list[TradeRecord] = []
    equity_curve = [{"date": start_date, "balance": round(balance, 2)}]
    daily_losses: dict[str, float] = {}  # date_str → realized losses
    sl_cooldown:  dict[str, date]  = {}  # ticker → earliest re-entry date after SL

    for today in trading_days:
        today_str = today.isoformat()

        # 1. Check exits on open positions first
        closed_today = _check_exits(open_trades, raw_data, today, today_str, tp, sl, tdays, tsl, pl_trig, pl_trail)
        for ct in closed_today:
            trade  = ct["_trade"]
            record = ct["record"]
            open_trades.remove(trade)
            closed_trades.append(record)
            # Return principal + realised P&L (not just P&L)
            balance += trade["amount"] + (record["pnl"] or 0)
            if record["outcome"] in ("LOSS", "EXPIRED") and (record["pnl"] or 0) < 0:
                daily_losses[today_str] = daily_losses.get(today_str, 0) + abs(record["pnl"] or 0)
            if record["exit_reason"] in ("SL", "TSL") and SL_COOLDOWN_DAYS > 0:
                sl_cooldown[trade["ticker"]] = today + timedelta(days=SL_COOLDOWN_DAYS)
            elif record["exit_reason"] == "TP" and TP_COOLDOWN_DAYS > 0:
                sl_cooldown[trade["ticker"]] = today + timedelta(days=TP_COOLDOWN_DAYS)

        # 2. Generate signals for today
        spy       = _spy_data_on(raw_data, today)
        # Rolling win rate from trades closed so far (used in Kelly sizing)
        wins_so_far   = sum(1 for t in closed_trades if t["outcome"] == "WIN")
        closed_so_far = len(closed_trades)
        rolling_wr = (wins_so_far / closed_so_far) if closed_so_far >= WIN_RATE_MIN_TRADES else None
        candidates = _score_all_tickers(
            raw_data, today, ticker_sector, sector_etf,
            spy["regime"], spy["return_20d"], l1, rolling_wr,
        )

        # 3. Attempt entries
        open_tickers     = {t["ticker"] for t in open_trades}
        sector_exposure  = _sector_exposure(open_trades, balance)
        daily_loss_today = daily_losses.get(today_str, 0.0)
        entries_today    = 0

        # Macro lock proxy: skip new entries on high-VIX days
        vix_blocked = vix_threshold is not None and _vix_on(raw_data, today) > vix_threshold

        for sig in ([] if vix_blocked else sorted(candidates, key=lambda s: s["signal_score"], reverse=True)):
            if len(open_trades) >= max_pos:
                break
            if max_epd is not None and entries_today >= max_epd:
                break
            if sig["ticker"] in open_tickers:
                continue
            if sl_cooldown.get(sig["ticker"], date.min) > today:
                continue
            if daily_loss_today >= DAILY_LOSS_CAP:
                break

            # Earnings date filter — skip if earnings within N days
            if earnings_filter_days is not None:
                ticker_earnings = earnings_dates.get(sig["ticker"], set())
                if any(today <= ed <= today + timedelta(days=earnings_filter_days) for ed in ticker_earnings):
                    continue

            # Leading RS proxy: ticker must outperform its sector ETF over last 5 days
            if use_leading_rs and not _leading_rs_pass(raw_data, sig["ticker"], sector_etf.get(sig["sector"], ""), today):
                continue

            # Sector exposure check
            sector    = sig["sector"]
            alloc_pct = min(sig["kelly_size"] if sig["kelly_size"] > 0 else 0.10, MAX_POSITION_SIZE)
            amount    = balance * alloc_pct
            projected = sector_exposure.get(sector, 0.0) + (amount / balance)
            if projected > MAX_SECTOR_EXPOSURE:
                continue

            entry_price = _price_on(raw_data, sig["ticker"], today)
            if entry_price is None or entry_price <= 0:
                continue
            if amount < 50:
                continue

            shares = amount / entry_price
            balance -= amount

            # Per-trade ATR exits: TP = 2x ATR, SL = 1x ATR
            if atr_exits and sig.get("atr_pct", 0) > 0:
                atr = sig["atr_pct"]
                trade_tp = atr * 2.0
                trade_sl = atr * 1.0
            else:
                trade_tp = tp
                trade_sl = sl

            trade = {
                "ticker":       sig["ticker"],
                "sector":       sector,
                "entry_date":   today_str,
                "entry_price":  entry_price,
                "peak_price":   entry_price,
                "shares":       shares,
                "amount":       amount,
                "signal_score": sig["signal_score"],
                "tp":           trade_tp,
                "sl":           trade_sl,
            }
            open_trades.append(trade)
            open_tickers.add(sig["ticker"])
            sector_exposure[sector] = sector_exposure.get(sector, 0.0) + (amount / balance)
            entries_today += 1

        # Equity snapshot (open positions marked to market)
        mtm = _mark_to_market(open_trades, raw_data, today)
        equity_curve.append({"date": today_str, "balance": round(balance + mtm, 2)})

    # Force-close any remaining open positions at last available price
    last_day = trading_days[-1] if trading_days else end
    for trade in list(open_trades):
        last_price = _price_on(raw_data, trade["ticker"], last_day)
        if last_price:
            pnl     = trade["amount"] * ((last_price - trade["entry_price"]) / trade["entry_price"])
            pnl_pct = (last_price - trade["entry_price"]) / trade["entry_price"]
            outcome = "WIN" if pnl >= 0 else "LOSS"
        else:
            pnl, pnl_pct, outcome, last_price = 0.0, 0.0, "EXPIRED", trade["entry_price"]

        balance += trade["amount"] + pnl
        closed_trades.append(TradeRecord(
            ticker=trade["ticker"], sector=trade["sector"],
            entry_date=trade["entry_date"], exit_date=end_date,
            entry_price=trade["entry_price"], exit_price=last_price,
            shares=trade["shares"], amount=trade["amount"],
            pnl=round(pnl, 2), pnl_pct=round(pnl_pct, 4),
            outcome=outcome, exit_reason="END_OF_BACKTEST",
            signal_score=trade["signal_score"],
        ))

    return _compute_metrics(
        start_date, end_date, initial_balance, balance,
        closed_trades, equity_curve, raw_data, trading_days,
    )


# ── Signal computation (mirrors live pipeline) ────────────────────────────────

def _score_all_tickers(
    raw_data: pd.DataFrame,
    today: date,
    ticker_sector: dict[str, str],
    sector_etf: dict[str, str],
    spy_regime: float,
    spy_return_20d: float,
    lock1_threshold: float,
    rolling_win_rate: float | None = None,
) -> list[dict]:
    # Compute ETF regime once per sector (not per ticker)
    etf_mults: dict[str, float] = {}
    for sector, etf in sector_etf.items():
        etf_mults[sector] = _etf_regime_on(raw_data, etf, today)

    candidates = []
    for ticker, sector in ticker_sector.items():
        if sector in EXCLUDED_SECTORS:
            continue
        sig = _score_ticker(
            raw_data, ticker, sector, today,
            spy_regime, spy_return_20d,
            etf_mults.get(sector, 1.0), rolling_win_rate,
        )
        effective_threshold = max(lock1_threshold, SECTOR_THRESHOLD_FLOORS.get(sector, 0.0))
        if sig and sig["signal_score"] >= effective_threshold:
            candidates.append(sig)
    return candidates


def _score_ticker(
    raw_data: pd.DataFrame,
    ticker: str,
    sector: str,
    today: date,
    spy_regime: float,
    spy_return_20d: float,
    etf_mult: float = 1.0,
    rolling_win_rate: float | None = None,
) -> dict | None:
    df = _slice_history(raw_data, ticker, today, lookback_days=90)
    if df is None or len(df) < 55:
        return None
    try:
        mom = momentum.compute(df)
        if mom.get("rsi_blocked"):
            return None
        vol = volume.compute(df)
        trd = trend.compute(df)
        rs  = relative_strength.compute(df, spy_return_20d)
        atr_raw = (df["High"] - df["Low"]).rolling(14).mean().iloc[-1]
        if mom["price"] and not math.isnan(atr_raw):
            atr_pct = float(atr_raw / mom["price"])
        else:
            atr_pct = 0.0
        evk   = ev_kelly_compute(spy_regime, rolling_win_rate=rolling_win_rate, atr_pct=atr_pct)
        score = aggregator.compute(
            mom["momentum_score"], vol["volume_score"],
            trd["trend_score"], rs["rs_score"],
        )
        # Apply sector ETF regime multiplier
        score = round(min(max(score * etf_mult, 0.0), 1.0), 4)
        # Scale position size by signal strength
        kelly_scaled = round(evk["kelly_size"] * min(1.0, max(0.5, score)), 4)
        return {
            "ticker":       ticker,
            "sector":       sector,
            "signal_score": score,
            "kelly_size":   kelly_scaled,
            "atr_pct":      atr_pct,
        }
    except Exception as e:
        logger.debug(f"Backtest score [{ticker}] {today}: {e}")
        return None


# ── Exit logic (mirrors wallet.check_exits) ───────────────────────────────────

def _check_exits(
    open_trades: list[dict],
    raw_data: pd.DataFrame,
    today: date,
    today_str: str,
    take_profit_pct: float,
    stop_loss_pct: float,
    time_stop_days: int,
    trailing_stop_pct: float | None = None,
    profit_lock_trigger_pct: float | None = None,
    profit_lock_trail_pct: float | None = None,
) -> list[dict]:
    closed = []
    for trade in list(open_trades):
        current = _price_on(raw_data, trade["ticker"], today)
        if current is None:
            continue

        # Update peak price for trailing stop
        if current > trade.get("peak_price", trade["entry_price"]):
            trade["peak_price"] = current

        pnl_pct = (current - trade["entry_price"]) / trade["entry_price"]
        days_held = _trading_days_count(trade["entry_date"], today_str)

        eff_tp = trade.get("tp", take_profit_pct)
        eff_sl = trade.get("sl", stop_loss_pct)

        if pnl_pct >= eff_tp:
            reason, outcome = "TP", "WIN"
        elif trailing_stop_pct is not None:
            # Trailing stop replaces fixed SL — matches wallet.py / live_trades_tracker behaviour
            peak = trade.get("peak_price", trade["entry_price"])
            trail_pct = (peak - current) / peak
            # Profit-lock: tighten trail once peak gain clears trigger threshold
            eff_tsl = trailing_stop_pct
            if profit_lock_trigger_pct and profit_lock_trail_pct:
                if (peak - trade["entry_price"]) / trade["entry_price"] >= profit_lock_trigger_pct:
                    eff_tsl = profit_lock_trail_pct
            if trail_pct >= eff_tsl:
                reason, outcome = "TSL", "WIN" if pnl_pct >= 0 else "LOSS"
            elif days_held >= time_stop_days:
                reason, outcome = "TIME", "EXPIRED"
            else:
                continue
        elif pnl_pct <= -eff_sl:
            reason, outcome = "SL", "LOSS"
        elif days_held >= time_stop_days:
            reason, outcome = "TIME", "EXPIRED"
        else:
            continue

        pnl = trade["amount"] * pnl_pct
        record = TradeRecord(
            ticker=trade["ticker"], sector=trade["sector"],
            entry_date=trade["entry_date"], exit_date=today_str,
            entry_price=trade["entry_price"], exit_price=round(current, 4),
            shares=trade["shares"], amount=trade["amount"],
            pnl=round(pnl, 2), pnl_pct=round(pnl_pct, 4),
            outcome=outcome, exit_reason=reason,
            signal_score=trade["signal_score"],
        )
        closed.append({"_trade": trade, "record": record})
    return closed


# ── Data helpers ──────────────────────────────────────────────────────────────

def _download_all(tickers: list[str], start: date, end: date) -> pd.DataFrame:
    """
    Download OHLCV data for all tickers over the requested date range.
    Results are cached to disk (Parquet) keyed by tickers + date range so
    repeated backtest runs skip the network entirely. Cache lives in data/backtest_cache/.
    """
    import hashlib
    from pathlib import Path

    cache_dir = Path(__file__).parent.parent.parent / "data" / "backtest_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Stable cache key: hash of sorted tickers + date range
    key_str  = ",".join(sorted(tickers)) + f"|{start}|{end}"
    key_hash = hashlib.md5(key_str.encode()).hexdigest()[:12]
    cache_path = cache_dir / f"{key_hash}.parquet"

    if cache_path.exists():
        try:
            df = pd.read_parquet(cache_path)
            logger.info(f"Backtest: loaded data from cache ({cache_path.name})")
            return df
        except Exception as e:
            logger.warning(f"Backtest cache read failed, re-downloading: {e}")
            cache_path.unlink(missing_ok=True)

    logger.info(f"Backtest: downloading {len(tickers)} tickers from {start} to {end}…")
    try:
        df = yf.download(
            tickers,
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            auto_adjust=True,
            progress=False,
            group_by="ticker",
        )
    except Exception as e:
        logger.error(f"Backtest data download failed: {e}")
        return pd.DataFrame()

    if not df.empty:
        try:
            df.to_parquet(cache_path)
            logger.info(f"Backtest: data cached to {cache_path.name}")
        except Exception as e:
            logger.warning(f"Backtest cache write failed (continuing without cache): {e}")
    return df


def _fetch_earnings_dates(tickers: list[str], start: date, end: date) -> dict[str, set[date]]:
    """
    Pre-fetch earnings dates for all tickers in the backtest range.
    Uses yfinance earnings_dates; falls back to empty set per ticker on error.
    """
    result: dict[str, set[date]] = {}
    for ticker in tickers:
        try:
            tkr = yf.Ticker(ticker)
            ed  = tkr.earnings_dates
            if ed is None or ed.empty:
                result[ticker] = set()
                continue
            dates: set[date] = set()
            for dt in ed.index:
                try:
                    d = dt.date() if hasattr(dt, "date") else dt
                    if start <= d <= end + timedelta(days=30):
                        dates.add(d)
                except Exception:
                    pass
            result[ticker] = dates
        except Exception as e:
            logger.debug(f"Earnings fetch failed for {ticker}: {e}")
            result[ticker] = set()
    return result


def _trading_days(start: date, end: date, raw_data: pd.DataFrame) -> list[date]:
    idx = raw_data.index
    return [d.date() for d in idx if start <= d.date() <= end]


def _slice_history(raw_data: pd.DataFrame, ticker: str, today: date, lookback_days: int) -> pd.DataFrame | None:
    try:
        if ticker in raw_data.columns.get_level_values(0):
            df = raw_data[ticker].copy()
        else:
            return None
        df = df[df.index.date <= today].tail(lookback_days)
        if df.empty:
            return None
        return df
    except Exception:
        return None


def _price_on(raw_data: pd.DataFrame, ticker: str, today: date) -> float | None:
    df = _slice_history(raw_data, ticker, today, lookback_days=5)
    if df is None or df.empty:
        return None
    try:
        return float(df["Close"].iloc[-1])
    except Exception:
        return None


def _spy_data_on(raw_data: pd.DataFrame, today: date) -> dict:
    """Returns SPY regime (+/-0.03) and 20-day return for the given date."""
    df = _slice_history(raw_data, SPY_TICKER, today, lookback_days=60)
    if df is None or len(df) < 50:
        return {"regime": 0.0, "return_20d": 0.0}
    try:
        close  = df["Close"].dropna()
        price  = float(close.iloc[-1])
        ma50   = float(close.rolling(50).mean().iloc[-1])
        regime = 0.03 if price > ma50 else -0.03
        ret20  = float(close.iloc[-1] / close.iloc[-20] - 1) if len(close) >= 20 else 0.0
        return {"regime": regime, "return_20d": ret20}
    except Exception:
        return {"regime": 0.0, "return_20d": 0.0}


def _etf_regime_on(raw_data: pd.DataFrame, etf_ticker: str, today: date) -> float:
    """Returns 1.0 if ETF >= MA20 (uptrend), 0.85 if below (downtrend penalty)."""
    df = _slice_history(raw_data, etf_ticker, today, lookback_days=30)
    if df is None or len(df) < 20:
        return 1.0
    try:
        price = float(df["Close"].iloc[-1])
        ma20  = float(df["Close"].rolling(20).mean().iloc[-1])
        return 1.0 if price >= ma20 else 0.85
    except Exception:
        return 1.0


def _vix_on(raw_data: pd.DataFrame, today: date) -> float:
    """Return VIX closing value on or before today. Returns 0 if unavailable."""
    df = _slice_history(raw_data, "^VIX", today, lookback_days=5)
    if df is None or df.empty:
        return 0.0
    try:
        return float(df["Close"].iloc[-1])
    except Exception:
        return 0.0


def _leading_rs_pass(raw_data: pd.DataFrame, ticker: str, etf: str, today: date) -> bool:
    """Return True if ticker's 5-day return > sector ETF's 5-day return."""
    if not etf:
        return True  # no ETF mapped → don't filter
    t_df   = _slice_history(raw_data, ticker, today, lookback_days=6)
    etf_df = _slice_history(raw_data, etf,    today, lookback_days=6)
    if t_df is None or etf_df is None or len(t_df) < 2 or len(etf_df) < 2:
        return True  # data unavailable → don't filter
    try:
        t_ret   = float(t_df["Close"].iloc[-1]   / t_df["Close"].iloc[0])   - 1
        etf_ret = float(etf_df["Close"].iloc[-1] / etf_df["Close"].iloc[0]) - 1
        return t_ret > etf_ret
    except Exception:
        return True


def _sector_exposure(open_trades: list[dict], balance: float) -> dict[str, float]:
    exp: dict[str, float] = {}
    for t in open_trades:
        exp[t["sector"]] = exp.get(t["sector"], 0.0) + (t["amount"] / balance)
    return exp


def _mark_to_market(open_trades: list[dict], raw_data: pd.DataFrame, today: date) -> float:
    total = 0.0
    for t in open_trades:
        current = _price_on(raw_data, t["ticker"], today)
        total += t["amount"] * (current / t["entry_price"]) if current else t["amount"]
    return total


def _trading_days_count(start_iso: str, end_iso: str) -> int:
    try:
        s = date.fromisoformat(start_iso)
        e = date.fromisoformat(end_iso)
        return max(0, len(pd.bdate_range(start=s, end=e)) - 1)
    except Exception:
        return 0


# ── Metrics ───────────────────────────────────────────────────────────────────

def _compute_metrics(
    start_date: str,
    end_date: str,
    initial_balance: float,
    final_balance: float,
    trades: list[TradeRecord],
    equity_curve: list[dict],
    raw_data: pd.DataFrame | None = None,
    trading_days: list[date] | None = None,
) -> BacktestResult:
    total_return = (final_balance - initial_balance) / initial_balance

    # CAGR
    try:
        days = (date.fromisoformat(end_date) - date.fromisoformat(start_date)).days
        years = days / 365.25
        cagr = (final_balance / initial_balance) ** (1 / years) - 1 if years > 0 else 0.0
    except Exception:
        cagr = 0.0

    # Sharpe (daily returns from equity curve)
    try:
        balances = [p["balance"] for p in equity_curve]
        daily_rets = [(balances[i] - balances[i-1]) / balances[i-1] for i in range(1, len(balances))]
        if len(daily_rets) > 1:
            mean_r = sum(daily_rets) / len(daily_rets)
            variance = sum((r - mean_r) ** 2 for r in daily_rets) / (len(daily_rets) - 1)
            std_r = math.sqrt(variance) if variance > 0 else 0.0
            sharpe = (mean_r / std_r) * math.sqrt(252) if std_r > 0 else 0.0
        else:
            sharpe = 0.0
    except Exception:
        sharpe = 0.0

    # Max drawdown
    try:
        balances = [p["balance"] for p in equity_curve]
        peak = balances[0]
        max_dd = 0.0
        for b in balances:
            if b > peak:
                peak = b
            dd = (peak - b) / peak
            if dd > max_dd:
                max_dd = dd
    except Exception:
        max_dd = 0.0

    # Win/loss stats
    closed = [t for t in trades if t["outcome"] in ("WIN", "LOSS", "EXPIRED")]
    wins   = [t for t in closed if t["outcome"] == "WIN"]
    losses = [t for t in closed if t["outcome"] in ("LOSS", "EXPIRED")]

    win_rate   = len(wins) / len(closed) if closed else None
    avg_win    = sum(t["pnl_pct"] for t in wins)   / len(wins)   if wins   else None
    avg_loss   = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else None

    gross_profit = sum(t["pnl"] for t in wins   if t["pnl"])
    gross_loss   = abs(sum(t["pnl"] for t in losses if t["pnl"]))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None

    # SPY buy-and-hold return over the same period
    spy_return: float | None = None
    if raw_data is not None and trading_days:
        try:
            spy_start = _price_on(raw_data, SPY_TICKER, trading_days[0])
            spy_end   = _price_on(raw_data, SPY_TICKER, trading_days[-1])
            if spy_start and spy_end and spy_start > 0:
                spy_return = round((spy_end - spy_start) / spy_start, 4)
        except Exception:
            pass

    return BacktestResult(
        start_date=start_date,
        end_date=end_date,
        initial_balance=initial_balance,
        final_balance=round(final_balance, 2),
        total_return_pct=round(total_return, 4),
        cagr=round(cagr, 4),
        sharpe=round(sharpe, 3),
        max_drawdown=round(max_dd, 4),
        win_rate=round(win_rate, 3) if win_rate is not None else None,
        total_trades=len(closed),
        winning_trades=len(wins),
        losing_trades=len(losses),
        avg_win_pct=round(avg_win, 4)  if avg_win  is not None else None,
        avg_loss_pct=round(avg_loss, 4) if avg_loss is not None else None,
        profit_factor=round(profit_factor, 3) if profit_factor is not None else None,
        spy_return_pct=spy_return,
        equity_curve=equity_curve,
        trade_log=trades,
    )
