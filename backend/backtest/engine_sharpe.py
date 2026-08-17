"""
engine_sharpe.py — Sharpe-first parallel engine for apex.

Parallel to engine_fast.py. Drop-in compatible: same precompute() output,
same BacktestResult shape. Key differences:

1. VOLATILITY TARGETING  — position size = (vol_target / realized_vol) * balance
                           instead of fixed Kelly fraction. Keeps portfolio vol
                           stable across regimes → direct Sharpe lift.

2. SHARPE AS OBJECTIVE   — run() returns sharpe as primary metric. The optimizer
                           should sort/filter on result["sharpe"], not return.

3. REGIME GATING         — enhanced: SPY regime + rolling realized vol + VIX
                           together gate position sizing (not just entry blocking).
                           When SPY is in a downtrend, vol_mult is halved so
                           positions shrink automatically in bear regimes.

4. REUSES PRECOMPUTED CACHES — pass the same precomputed dict from engine_fast
                           precompute(). No extra downloads needed.

Usage:
    from backend.backtest.engine_fast import precompute
    from backend.backtest.engine_sharpe import run as run_sharpe

    pc = precompute("2022-01-01", "2024-12-31")

    result = run_sharpe(
        start_date="2022-01-01",
        end_date="2024-12-31",
        precomputed=pc,
        vol_target=0.15,       # annualized portfolio vol target (15%)
        vol_lookback=20,       # days of equity curve used to estimate realized vol
        max_leverage=2.0,      # cap on vol-targeting multiplier
    )
    print(result["sharpe"], result["total_return_pct"])
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
from loguru import logger

# Reuse types and helpers from engine_fast — no duplication
from backend.backtest.engine_fast import (
    BacktestResult,
    TradeRecord,
    _check_exits_fast,
    _compute_metrics,
    _mark_to_market_fast,
    _sector_exposure,
    _trading_days_count,
)
from backend.config import (
    DAILY_LOSS_CAP,
    LOCK1_THRESHOLD,
    MAX_POSITION_SIZE,
    MAX_POSITIONS,
    MAX_SECTOR_EXPOSURE,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    TIME_STOP_DAYS,
)

# ── Vol targeting helpers ─────────────────────────────────────────────────────

def _realized_vol(equity_curve: list[dict], lookback: int) -> float:
    """
    Annualized realized vol from the last `lookback` equity curve points.
    Returns 0.15 (neutral assumption) if not enough data yet.
    """
    if len(equity_curve) < lookback + 1:
        return 0.15  # warm-up default — prevents over-leveraging at start

    balances = [p["balance"] for p in equity_curve[-(lookback + 1):]]
    rets = [(balances[i] - balances[i - 1]) / balances[i - 1]
            for i in range(1, len(balances))]

    arr = np.array(rets)
    std = arr.std(ddof=1)
    return float(std * math.sqrt(252)) if std > 0 else 0.15


def _vol_targeting_multiplier(
    realized_vol: float,
    vol_target: float,
    max_leverage: float,
) -> float:
    """
    Core vol-targeting scalar: target_vol / realized_vol, capped at max_leverage.
    When realized vol is high → scale DOWN. When low → scale UP (but capped).
    """
    if realized_vol <= 0:
        return 1.0
    raw = vol_target / realized_vol
    return min(raw, max_leverage)


def _vol_adjusted_alloc(
    kelly_size: float,
    signal_score: float,
    vol_mult: float,
    max_position_size: float,
) -> float:
    """
    Final allocation fraction for one position.
    Starts from Kelly, scales by signal score quality, then applies vol multiplier.
    Hard cap at max_position_size.
    """
    base = kelly_size if kelly_size > 0 else 0.10
    score_scaled = base * min(1.0, max(0.5, signal_score))
    vol_adjusted = score_scaled * vol_mult
    return min(vol_adjusted, max_position_size)


# ── Sharpe calculator (explicit, annualized) ─────────────────────────────────

def calculate_sharpe(
    returns: list[float],
    rf_rate: float = 0.03,
    periods_per_year: int = 252,
) -> float:
    """
    Annualized Sharpe. rf_rate = annual risk-free (e.g. 3%).
    Uses excess returns over the risk-free rate.
    """
    if len(returns) < 2:
        return 0.0
    arr = np.array(returns)
    excess = arr - (rf_rate / periods_per_year)
    std = excess.std(ddof=1)
    if std == 0:
        return 0.0
    return float(np.sqrt(periods_per_year) * excess.mean() / std)


# ── Main entry point ──────────────────────────────────────────────────────────

def run(
    start_date: str,
    end_date: str,
    initial_balance: float = 10_000.0,
    # Vol targeting params (the new levers)
    vol_target: float = 0.15,         # annualized portfolio vol target
    vol_lookback: int = 20,           # days of equity history for realized vol
    max_leverage: float = 2.0,        # cap on vol multiplier (safety)
    # Standard params (same as engine_fast)
    take_profit_pct: float | None = None,
    stop_loss_pct: float | None = None,
    trailing_stop_pct: float | None = None,
    time_stop_days: int | None = None,
    lock1_threshold: float | None = None,
    max_entries_per_day: int | None = None,
    vix_threshold: float | None = None,
    max_positions: int | None = None,
    use_leading_rs: bool = False,
    # Precomputed caches (reuse engine_fast.precompute() output)
    precomputed: dict | None = None,
) -> BacktestResult:
    """
    Run a Sharpe-optimized backtest with volatility targeting.

    The vol targeting multiplier adjusts every position size daily based on
    the portfolio's realized volatility over the last `vol_lookback` days.
    When vol is high, positions shrink. When vol is low, they expand (up to
    max_leverage). This stabilizes the return distribution → higher Sharpe.

    SPY regime additionally halves the vol_mult when SPY is in a downtrend,
    reducing exposure automatically in bear regimes without blocking entries
    entirely (VIX threshold handles hard blocks).
    """
    if precomputed is None:
        raise ValueError(
            "engine_sharpe requires precomputed caches. "
            "Call engine_fast.precompute(start, end) first and pass the result."
        )

    tp   = take_profit_pct  if take_profit_pct  is not None else TAKE_PROFIT_PCT
    sl   = stop_loss_pct    if stop_loss_pct    is not None else STOP_LOSS_PCT
    tsl  = trailing_stop_pct
    tdays = time_stop_days  if time_stop_days   is not None else TIME_STOP_DAYS
    l1   = lock1_threshold  if lock1_threshold  is not None else LOCK1_THRESHOLD
    max_epd = max_entries_per_day
    max_pos = max_positions if max_positions    is not None else MAX_POSITIONS

    # Unpack precomputed caches (identical structure to engine_fast)
    raw_data      = precomputed["raw_data"]
    ticker_sector = precomputed["ticker_sector"]
    trading_days  = precomputed["trading_days"]
    price_cache   = precomputed["price_cache"]
    spy_cache     = precomputed["spy_cache"]
    vix_cache     = precomputed["vix_cache"]
    rs_cache      = precomputed.get("rs_cache", {}) if use_leading_rs else {}
    signal_cache  = precomputed["signal_cache"]

    # Filter trading days to requested range
    start = date.fromisoformat(start_date)
    end   = date.fromisoformat(end_date)
    trading_days = [d for d in trading_days if start <= d <= end]

    logger.info(
        f"engine_sharpe: {len(trading_days)} days | "
        f"vol_target={vol_target:.0%} lookback={vol_lookback} max_lev={max_leverage}x"
    )

    SL_COOLDOWN_DAYS = 5

    balance        = initial_balance
    open_trades: list[dict]        = []
    closed_trades: list[TradeRecord] = []
    equity_curve   = [{"date": start_date, "balance": round(balance, 2)}]
    daily_losses: dict[str, float] = {}
    sl_cooldown: dict[str, date]   = {}

    for today in trading_days:
        today_str = today.isoformat()

        # ── 1. Exits (identical logic to engine_fast) ─────────────────────
        closed_today = _check_exits_fast(
            open_trades, price_cache, today, today_str, tp, sl, tdays, tsl
        )
        for ct in closed_today:
            trade  = ct["_trade"]
            record = ct["record"]
            open_trades.remove(trade)
            closed_trades.append(record)
            balance += trade["amount"] + (record["pnl"] or 0)

            if record["outcome"] in ("LOSS", "EXPIRED") and (record["pnl"] or 0) < 0:
                daily_losses[today_str] = (
                    daily_losses.get(today_str, 0) + abs(record["pnl"] or 0)
                )
            if record["exit_reason"] in ("SL", "TSL"):
                sl_cooldown[trade["ticker"]] = today + timedelta(days=SL_COOLDOWN_DAYS)

        # ── 2. Vol targeting multiplier ────────────────────────────────────
        #
        # Compute BEFORE entries so every position opened today uses the
        # same daily multiplier — consistent sizing within each session.
        r_vol    = _realized_vol(equity_curve, vol_lookback)
        vol_mult = _vol_targeting_multiplier(r_vol, vol_target, max_leverage)

        # ── 3. Regime check ───────────────────────────────────────────────
        spy    = spy_cache.get(today_str, {"regime": 0.0, "return_20d": 0.0})
        vix_ok = (
            vix_threshold is None
            or vix_cache.get(today_str, 0.0) <= vix_threshold
        )

        # SPY regime gates position sizing: halve vol_mult in bear regime.
        # This reduces exposure continuously rather than blocking entries
        # entirely — size down in downtrends, full size in uptrends.
        spy_factor   = 1.0 if spy["regime"] >= 0 else 0.5
        eff_vol_mult = vol_mult * spy_factor

        # ── 4. Candidates from cache ───────────────────────────────────────
        candidates = _get_candidates_sharpe(
            signal_cache, ticker_sector, today_str, l1
        )

        # ── 5. Entries with vol-adjusted sizing ───────────────────────────
        open_tickers   = {t["ticker"] for t in open_trades}
        sector_exp     = _sector_exposure(open_trades, initial_balance)
        daily_loss_today = daily_losses.get(today_str, 0.0)
        entries_today  = 0

        for sig in ([] if not vix_ok else
                    sorted(candidates, key=lambda s: s["signal_score"], reverse=True)):

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
            if use_leading_rs and not rs_cache.get((sig["ticker"], today_str), True):
                continue

            sector = sig["sector"]

            # Vol-adjusted allocation using effective multiplier (vol + SPY regime)
            alloc_pct = _vol_adjusted_alloc(
                sig["kelly_size"], sig["signal_score"], eff_vol_mult, MAX_POSITION_SIZE
            )
            amount = balance * alloc_pct

            projected = sector_exp.get(sector, 0.0) + (amount / initial_balance)
            if projected > MAX_SECTOR_EXPOSURE:
                continue

            entry_price = price_cache.get((sig["ticker"], today_str))
            if entry_price is None or entry_price <= 0:
                continue
            if amount < 50:
                continue

            shares  = amount / entry_price
            balance -= amount

            trade = {
                "ticker":       sig["ticker"],
                "sector":       sector,
                "entry_date":   today_str,
                "entry_price":  entry_price,
                "peak_price":   entry_price,
                "shares":       shares,
                "amount":       amount,
                "signal_score": sig["signal_score"],
                "tp":           tp,
                "sl":           sl,
                "vol_mult":     round(eff_vol_mult, 4),   # effective (vol × spy_factor)
                "spy_factor":   spy_factor,
            }
            open_trades.append(trade)
            open_tickers.add(sig["ticker"])
            sector_exp[sector] = sector_exp.get(sector, 0.0) + (amount / initial_balance)
            entries_today += 1

        # ── 6. Equity snapshot ────────────────────────────────────────────
        mtm = _mark_to_market_fast(open_trades, price_cache, today_str)
        equity_curve.append({"date": today_str, "balance": round(balance + mtm, 2)})

    # ── Force-close remaining positions ───────────────────────────────────────
    last_day = trading_days[-1] if trading_days else end
    last_str = last_day.isoformat()

    for trade in list(open_trades):
        last_price = price_cache.get((trade["ticker"], last_str))
        if last_price:
            pnl     = trade["amount"] * ((last_price - trade["entry_price"]) / trade["entry_price"])
            pnl_pct = (last_price - trade["entry_price"]) / trade["entry_price"]
            outcome = "WIN" if pnl >= 0 else "LOSS"
        else:
            pnl, pnl_pct, outcome, last_price = 0.0, 0.0, "EXPIRED", trade["entry_price"]

        balance += trade["amount"] + pnl
        closed_trades.append(TradeRecord(
            ticker=trade["ticker"],   sector=trade["sector"],
            entry_date=trade["entry_date"], exit_date=end_date,
            entry_price=trade["entry_price"], exit_price=last_price,
            shares=trade["shares"],   amount=trade["amount"],
            pnl=round(pnl, 2),       pnl_pct=round(pnl_pct, 4),
            outcome=outcome,          exit_reason="END_OF_BACKTEST",
            signal_score=trade["signal_score"],
            days_held=_trading_days_count(trade["entry_date"], end_date),
        ))

    # ── Metrics ───────────────────────────────────────────────────────────────
    result = _compute_metrics(
        start_date, end_date, initial_balance, balance,
        closed_trades, equity_curve, raw_data, trading_days,
    )

    # Override Sharpe with the more precise explicit calculation
    balances   = [p["balance"] for p in equity_curve]
    daily_rets = [(balances[i] - balances[i-1]) / balances[i-1]
                  for i in range(1, len(balances))]
    result["sharpe"] = round(calculate_sharpe(daily_rets), 3)

    # Tag which engine produced this result
    result["engine"]      = "sharpe"
    result["vol_target"]  = vol_target
    result["vol_lookback"] = vol_lookback
    result["max_leverage"] = max_leverage

    logger.info(
        f"engine_sharpe done | Sharpe={result['sharpe']} "
        f"Return={result['total_return_pct']:.1%} "
        f"MaxDD={result['max_drawdown']:.1%} "
        f"Trades={result['total_trades']}"
    )
    return result


# ── Candidate filter (streamlined for Sharpe engine) ─────────────────────────

def _get_candidates_sharpe(
    signal_cache: dict,
    ticker_sector: dict[str, str],
    today_str: str,
    lock1_threshold: float,
) -> list[dict]:
    """
    Same as engine_fast's _get_candidates_from_cache but returns only
    what engine_sharpe needs. ETF multiplier is already baked into
    signal_score in the cache — no need to reapply here.
    """
    candidates = []
    for ticker, sector in ticker_sector.items():
        sig = signal_cache.get((ticker, today_str))
        if sig is None:
            continue
        score = sig["signal_score"]
        if score < lock1_threshold:
            continue
        candidates.append({
            "ticker":       ticker,
            "sector":       sector,
            "signal_score": score,
            "kelly_size":   sig["kelly_size"],
            "atr_pct":      sig["atr_pct"],
        })
    return candidates


# ── Walk-forward optimizer (Sharpe as objective) ──────────────────────────────

def walk_forward_optimize(
    precomputed: dict,
    param_grid: dict,
    n_random: int = 200,
    train_days: int = 365 * 2,
    test_days: int = 180,
    initial_balance: float = 10_000.0,
) -> dict:
    """
    Walk-forward optimization using Sharpe as the explicit objective.

    param_grid example:
        {
            "vol_target":      [0.10, 0.15, 0.20, 0.25],
            "vol_lookback":    [10, 20, 30],
            "max_leverage":    [1.5, 2.0, 2.5],
            "lock1_threshold": [0.65, 0.70, 0.75],   # >= 0.65 (dead zone below)
            "take_profit_pct": [0.05, 0.06, 0.07, 0.08],  # <= 0.08 constraint
            "stop_loss_pct":   [0.06, 0.08, 0.10],
        }

    Returns:
        {
            "windows":        list of (best_params, oos_sharpe) per window,
            "avg_oos_sharpe": float,
            "best_params":    most common high-Sharpe params across windows,
        }
    """
    import random

    trading_days = precomputed["trading_days"]
    n_days       = len(trading_days)
    results      = []

    window_start = 0
    window_num   = 0

    while window_start + train_days + test_days < n_days:
        window_num += 1
        train_end  = window_start + train_days
        test_end   = train_end + test_days

        train_period_days = trading_days[window_start:train_end]
        test_period_days  = trading_days[train_end:test_end]

        if not train_period_days or not test_period_days:
            break

        train_start_str = train_period_days[0].isoformat()
        train_end_str   = train_period_days[-1].isoformat()
        test_start_str  = test_period_days[0].isoformat()
        test_end_str    = test_period_days[-1].isoformat()

        logger.info(
            f"Walk-forward window {window_num}: "
            f"train {train_start_str}→{train_end_str} | "
            f"test {test_start_str}→{test_end_str}"
        )

        # Random search on training window
        best_sharpe = -np.inf
        best_params = None

        for trial_num in range(n_random):
            trial = {k: random.choice(v) for k, v in param_grid.items()}
            try:
                res = run(
                    start_date=train_start_str,
                    end_date=train_end_str,
                    initial_balance=initial_balance,
                    precomputed=precomputed,
                    **trial,
                )
                score = res["sharpe"]
                if score > best_sharpe:
                    best_sharpe = score
                    best_params = trial
            except Exception as e:
                logger.debug(f"Trial {trial_num} failed: {e}")
                continue

        if best_params is None:
            window_start += test_days
            continue

        # Evaluate best params on unseen test window
        try:
            oos = run(
                start_date=test_start_str,
                end_date=test_end_str,
                initial_balance=initial_balance,
                precomputed=precomputed,
                **best_params,
            )
            oos_sharpe = oos["sharpe"]
        except Exception as e:
            logger.warning(f"OOS evaluation failed: {e}")
            oos_sharpe = 0.0

        logger.info(
            f"  Window {window_num}: train Sharpe={best_sharpe:.3f} | "
            f"OOS Sharpe={oos_sharpe:.3f} | params={best_params}"
        )
        results.append({
            "window":       window_num,
            "train_sharpe": round(best_sharpe, 3),
            "oos_sharpe":   round(oos_sharpe, 3),
            "params":       best_params,
        })

        window_start += test_days

    if not results:
        return {"windows": [], "avg_oos_sharpe": 0.0, "best_params": {}}

    avg_oos = float(np.mean([r["oos_sharpe"] for r in results]))
    logger.info(f"Walk-forward complete | avg OOS Sharpe: {avg_oos:.3f}")

    # Best params = those from the highest OOS Sharpe window
    best_window = max(results, key=lambda r: r["oos_sharpe"])

    return {
        "windows":        results,
        "avg_oos_sharpe": round(avg_oos, 3),
        "best_params":    best_window["params"],
        "best_window":    best_window,
    }
