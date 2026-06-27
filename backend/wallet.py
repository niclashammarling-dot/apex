"""
Wallet — demo trade execution and exit management.

Starting balance: $2,000 (STARTING_BALANCE in config).
All trades are simulated — no real money involved.

Execute flow:
  gate_runner calls execute_trade() when outcome == TRADE_QUEUED
  → validates position limits and sector exposure
  → inserts BUY trade into DB

Exit flow (scheduler every 5 min during market hours):
  check_exits() fetches all open trades
  → gets current price via yfinance
  → closes on TP, SL, trailing stop, or time-stop (all configurable via demo_config)
"""
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf
from loguru import logger

from backend.config import (
    DAILY_LOSS_CAP,
    MAX_POSITION_SIZE,
    MAX_POSITIONS,
    MAX_SECTOR_EXPOSURE,
    STARTING_BALANCE,
)
from backend.db import (
    close_trade,
    get_db,
    get_open_trades,
    get_portfolio_summary,
    insert_trade,
    update_trade_peak_price,
)
from backend.demo_config import get_demo_config

# ── Execution ────────────────────────────────────────────────────────────────

def execute_trade(gate_result: dict, price: float, dynamic_caps: dict | None = None,
                  overflow: bool = False) -> dict | None:
    """
    Open a BUY position for a gate-approved ticker.
    Returns the trade record dict, or None if rejected by risk checks.

    dynamic_caps: optional {sector: cap_fraction} from sector_caps.compute_dynamic_caps().
    If provided, the per-sector cap is used instead of the flat max_sector_exposure.

    overflow: if True, skip the max_positions check. The gate runner has already
    verified that the ticker cleared the escalating overflow quant threshold.
    """
    ticker   = gate_result["ticker"]
    sector   = gate_result["sector"]
    position = gate_result["lock3"]["position_size_pct"] if gate_result.get("lock3") else 0.10
    portfolio = get_portfolio_summary()

    cfg = get_demo_config()
    base_cap       = cfg.get("max_sector_exposure", MAX_SECTOR_EXPOSURE)
    max_sector_exp = dynamic_caps.get(sector, base_cap) if dynamic_caps else base_cap
    max_positions  = cfg.get("max_positions", MAX_POSITIONS)

    # Risk checks
    if not overflow and portfolio["open_position_count"] >= max_positions:
        logger.warning(f"Trade rejected [{ticker}]: max positions ({max_positions}) reached")
        return None

    # Check projected sector exposure (current + this trade).
    # sector_exposure fractions are stored as amount / STARTING_BALANCE,
    # so compute the new trade's contribution on the same denominator.
    new_trade_exposure = (portfolio["cash"] * position) / STARTING_BALANCE
    projected_exp = portfolio["sector_exposure"].get(sector, 0.0) + new_trade_exposure
    if projected_exp > max_sector_exp:
        logger.warning(
            f"Trade rejected [{ticker}]: {sector} projected at {projected_exp:.0%} "
            f"(cap {max_sector_exp:.0%})"
        )
        return None

    # Daily loss cap
    daily_loss     = _daily_realized_loss()
    daily_loss_cap = cfg.get("daily_loss_cap", DAILY_LOSS_CAP)
    if daily_loss >= daily_loss_cap:
        logger.warning(f"Trade rejected [{ticker}]: daily loss cap hit (${daily_loss:.2f} >= ${daily_loss_cap})")
        return None

    # Already holding this ticker?
    open_tickers = {t["ticker"] for t in get_open_trades()}
    if ticker in open_tickers:
        logger.info(f"Trade skipped [{ticker}]: position already open")
        return None

    max_position_size = cfg.get("max_position_size", MAX_POSITION_SIZE)
    amount = portfolio["cash"] * min(position, max_position_size)
    if amount < 50:
        logger.warning(f"Trade rejected [{ticker}]: insufficient cash (${portfolio['cash']:.2f})")
        return None

    if not price:
        logger.warning(f"Trade rejected [{ticker}]: price is {price!r}")
        return None

    shares = amount / price
    now    = datetime.now(timezone.utc).isoformat()

    trade_id = insert_trade({
        "timestamp":          now,
        "ticker":             ticker,
        "sector":             sector,
        "action":             "BUY",
        "price":              price,
        "shares":             shares,
        "amount":             amount,
        "signal_score":       gate_result["lock1"]["score"],
        "sentiment_score":    gate_result.get("sentiment_score"),
        "claude_confidence":  gate_result.get("claude_confidence"),
        "claude_reasoning":   gate_result.get("claude_reasoning"),
        "outcome":            "OPEN",
        "pnl":                None,
    })

    logger.info(
        f"Trade executed [{ticker}]: BUY {shares:.4f} shares @ ${price:.2f} "
        f"= ${amount:.2f} ({position:.0%} of cash)"
    )
    return {"id": trade_id, "ticker": ticker, "amount": amount, "shares": shares}


# ── Exit checks ──────────────────────────────────────────────────────────────

def check_exits() -> list[dict]:
    """
    Check all open positions against TP, SL, profit-lock trail, and time-stop.
    Reads thresholds from demo_config.json at call time so UI changes take effect immediately.
    Returns list of closed trade records.

    Exit priority:
      1. TP: pnl >= take_profit_pct
      2. SL: pnl <= -stop_loss_pct (hard floor, always active)
      3. Profit-lock trail: once peak gain >= profit_lock_trigger_pct, fires when
         drawdown from peak >= profit_lock_trail_pct
      4. Time-stop: held >= max_hold_days trading days
    """
    open_trades = get_open_trades()
    if not open_trades:
        return []

    cfg               = get_demo_config()
    tp_pct            = cfg["take_profit_pct"]
    sl_pct            = cfg["stop_loss_pct"]
    trigger_pct       = cfg.get("profit_lock_trigger_pct")
    lock_trail        = cfg.get("profit_lock_trail_pct")
    max_hold_days     = cfg["max_hold_days"]

    tickers        = list({t["ticker"] for t in open_trades})
    current_prices = _batch_prices(tickers)
    closed         = []

    for trade in open_trades:
        ticker      = trade["ticker"]
        entry_price = trade["price"]
        current     = current_prices.get(ticker)

        if current is None:
            logger.warning(f"Exit check [{ticker}]: could not fetch price — skipping")
            continue

        # Update peak price
        peak = trade.get("peak_price") or entry_price
        if current > peak:
            peak = current
            update_trade_peak_price(trade["id"], peak)

        pnl_pct = (current - entry_price) / entry_price

        if pnl_pct >= tp_pct:
            reason  = "TP"
            outcome = "WIN"
        elif pnl_pct <= -sl_pct:
            reason  = "SL"
            outcome = "LOSS"
        elif (
            trigger_pct and lock_trail
            and (peak - entry_price) / entry_price >= trigger_pct
            and (peak - current) / peak >= lock_trail
        ):
            reason  = "TSL"
            outcome = "WIN" if pnl_pct >= 0 else "LOSS"
        elif _trading_days_since(trade["timestamp"]) >= max_hold_days:
            reason  = "TIME"
            outcome = "EXPIRED"
        else:
            continue

        pnl = trade["amount"] * pnl_pct
        close_trade(
            trade_id  = trade["id"],
            price_exit = current,
            pnl        = pnl,
            outcome    = outcome,
            exit_reason = reason,
            exited_at  = datetime.now(timezone.utc).isoformat(),
        )

        logger.info(
            f"Exit [{ticker}]: {reason} entry=${entry_price:.2f} "
            f"peak=${peak:.2f} exit=${current:.2f} pnl={pnl:+.2f} ({pnl_pct:+.1%})"
        )
        closed.append({
            "ticker":      ticker,
            "outcome":     outcome,
            "exit_reason": reason,
            "pnl":         pnl,
            "pnl_pct":     pnl_pct,
        })

    return closed


# ── Regime-driven exits ───────────────────────────────────────────────────────

def check_regime_exits() -> list[dict]:
    """
    Exit open demo positions whose sector has dropped out of the leaderboard
    AND the ticker has closed down for TICKER_RECOVERY_DAYS consecutive trading days.

    Rationale: sector displacement alone can be relative noise (another sector
    improved); ticker deterioration alone may be idiosyncratic. Together they are
    high-conviction — macro and micro both confirming weakness.

    The fixed SL / trailing stop handle sharp moves. This catches slow grinding
    deterioration that neither fires on early enough.

    Guards:
      - Regime result must be ≤ 1 trading day old (calendar-day guard fails over
        weekends — uses bdate_range). Stale result = no action.
      - Only fires during market hours (caller's responsibility).
    """
    from datetime import date
    from backend.regime.regime_bayes import TICKER_RECOVERY_DAYS
    from backend.scheduler import _get_regime_bayes

    rb = _get_regime_bayes()
    if rb is None:
        return []

    result = rb.last_result()
    if result is None:
        return []

    try:
        result_date = date.fromisoformat(result.date)
        if len(pd.bdate_range(result_date, date.today())) - 1 > 1:
            logger.debug(f"Regime exit check: result is stale ({result.date}) — skipping")
            return []
    except ValueError:
        return []

    leaderboard_sectors: set[str] = {e.sector for e in result.leaderboard}

    open_trades = get_open_trades()
    if not open_trades:
        return []

    flagged = [
        t for t in open_trades
        if t["sector"] not in leaderboard_sectors
        and _ticker_consecutive_down_days(t["ticker"], TICKER_RECOVERY_DAYS)
    ]
    if not flagged:
        return []

    tickers        = list({t["ticker"] for t in flagged})
    current_prices = _batch_prices(tickers)
    closed         = []

    for trade in flagged:
        ticker      = trade["ticker"]
        sector      = trade["sector"]
        entry_price = trade["price"]
        current     = current_prices.get(ticker)

        if current is None:
            logger.warning(f"Regime exit [{ticker}]: could not fetch price — skipping")
            continue

        pnl_pct = (current - entry_price) / entry_price
        pnl     = trade["amount"] * pnl_pct
        outcome = "WIN" if pnl_pct >= 0 else "LOSS"

        close_trade(
            trade_id    = trade["id"],
            price_exit  = current,
            pnl         = pnl,
            outcome     = outcome,
            exit_reason = "REGIME",
            exited_at   = datetime.now(timezone.utc).isoformat(),
        )

        logger.info(
            f"Regime exit [{ticker}] sector={sector} "
            f"off leaderboard + {TICKER_RECOVERY_DAYS}d down | "
            f"entry=${entry_price:.2f} exit=${current:.2f} pnl={pnl:+.2f} ({pnl_pct:+.1%})"
        )
        closed.append({
            "ticker":           ticker,
            "sector":           sector,
            "outcome":          outcome,
            "exit_reason":      "REGIME",
            "entry_price":      entry_price,
            "exit_price":       current,
            "notional":         trade["amount"],
            "held_days":        _trading_days_since(trade["timestamp"]),
            "sector_avg_score": _sector_avg_score(sector),
            "pnl":              pnl,
            "pnl_pct":          pnl_pct,
        })

    if closed:
        from backend.alerts import alert_regime_exits
        alert_regime_exits(closed, mode="DEMO")

    return closed


# ── Portfolio snapshot ────────────────────────────────────────────────────────

def get_portfolio() -> dict:
    """
    Full portfolio snapshot with live unrealized P&L on open positions.
    Used by /api/wallet endpoint.
    """
    from backend.db import get_all_trades

    trades        = get_all_trades()
    open_trades   = [t for t in trades if t["outcome"] == "OPEN"]
    closed_trades = [t for t in trades if t["outcome"] in ("WIN", "LOSS", "EXPIRED")]

    invested     = sum(t["amount"] for t in open_trades)
    realized_pnl = sum(t["pnl"] or 0 for t in closed_trades)
    cash         = STARTING_BALANCE - invested + realized_pnl

    # Enrich open positions with live prices
    tickers        = [t["ticker"] for t in open_trades]
    current_prices = _batch_prices(tickers) if tickers else {}

    positions = []
    unrealized_total = 0.0
    for t in open_trades:
        current = current_prices.get(t["ticker"])
        if current is not None:
            unreal     = t["amount"] * ((current - t["price"]) / t["price"])
            unreal_pct = (current - t["price"]) / t["price"]
        else:
            unreal     = None
            unreal_pct = None
        unrealized_total += 0 if pd.isna(unreal) else unreal
        positions.append({
            **t,
            "current_price":    current,
            "unrealized_pnl":   round(unreal, 2)    if unreal is not None     else None,
            "unrealized_pct":   round(unreal_pct, 4) if unreal_pct is not None else None,
            "days_held":        _trading_days_since(t["timestamp"]),
        })

    total_pnl     = realized_pnl + unrealized_total
    wins          = sum(1 for t in closed_trades if t["outcome"] == "WIN")
    win_rate      = wins / len(closed_trades) if closed_trades else None

    return {
        "balance":          round(cash + invested + unrealized_total, 2),
        "cash":             round(cash, 2),
        "invested":         round(invested, 2),
        "starting_balance": STARTING_BALANCE,
        "total_pnl":        round(total_pnl, 2),
        "total_pnl_pct":    round(total_pnl / STARTING_BALANCE, 4),
        "realized_pnl":     round(realized_pnl, 2),
        "unrealized_pnl":   round(unrealized_total, 2),
        "open_positions":   positions,
        "closed_trades":    closed_trades[-20:],  # last 20
        "win_rate":         round(win_rate, 3) if win_rate is not None else None,
        "total_trades":     len(closed_trades),
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _daily_realized_loss() -> float:
    """Absolute value of today's realized losses (losses only, not gains)."""
    import sqlite3

    from backend.db import DB_PATH
    today = datetime.now(timezone.utc).date().isoformat()
    conn  = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("""
            SELECT COALESCE(SUM(pnl), 0) AS total
            FROM trades
            WHERE outcome IN ('LOSS', 'EXPIRED')
              AND pnl < 0
              AND DATE(exited_at) = ?
        """, (today,)).fetchone()
        return abs(row["total"])
    finally:
        conn.close()


def _ticker_consecutive_down_days(ticker: str, n: int) -> bool:
    """Return True if ticker has closed down for n consecutive trading days."""
    try:
        hist = yf.Ticker(ticker).history(period=f"{n + 5}d")
        if hist.empty or len(hist) < n + 1:
            return False
        returns = hist["Close"].pct_change().dropna().values
        return len(returns) >= n and all(r < 0 for r in returns[-n:])
    except Exception:
        return False


def _batch_prices(tickers: list[str]) -> dict[str, float]:
    """Fetch latest close prices for a list of tickers."""
    if not tickers:
        return {}
    try:
        if len(tickers) == 1:
            hist = yf.Ticker(tickers[0]).history(period="2d")
            if hist.empty:
                return {}
            v = hist["Close"].iloc[-1]
            if pd.isna(v):
                return {}
            return {tickers[0]: float(v)}
        data   = yf.download(tickers, period="1d", auto_adjust=True, progress=False)
        closes = data["Close"]
        result = {}
        for t in tickers:
            if t not in closes.columns:
                continue
            v = closes[t].iloc[-1]
            if not pd.isna(v):
                result[t] = float(v)
        return result
    except Exception as e:
        logger.warning(f"Batch price fetch failed: {e}")
        return {}


def _trading_days_since(timestamp_iso: str) -> int:
    """Count business days (Mon–Fri) between entry date and today."""
    entry = datetime.fromisoformat(timestamp_iso).date()
    today = datetime.now(timezone.utc).date()
    return max(0, len(pd.bdate_range(start=entry, end=today)) - 1)


def _sector_avg_score(sector: str) -> float | None:
    """Most recent avg_score for the sector from sector_snapshots."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT avg_score FROM sector_snapshots WHERE sector=? ORDER BY timestamp DESC LIMIT 1",
            (sector,),
        ).fetchone()
        return row["avg_score"] if row else None
    finally:
        conn.close()
