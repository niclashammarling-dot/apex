"""
Wallet — demo trade execution and exit management.

Starting balance: $10,000 (STARTING_BALANCE in config).
All trades are simulated — no real money involved.

Execute flow:
  gate_runner calls execute_trade() when outcome == TRADE_QUEUED
  → validates position limits and sector exposure
  → inserts BUY trade into DB

Exit flow (scheduler every 5 min during market hours):
  check_exits() fetches all open trades
  → gets current price via yfinance
  → closes on TP (+15%), SL (-5%), or time-stop (5 trading days)
"""
import yfinance as yf
import pandas as pd
from datetime import datetime, timezone
from loguru import logger

from backend.config import (
    STARTING_BALANCE, MAX_POSITIONS, MAX_SECTOR_EXPOSURE, MAX_POSITION_SIZE,
    TAKE_PROFIT_PCT, STOP_LOSS_PCT, TIME_STOP_DAYS, DAILY_LOSS_CAP,
)
from backend.db import (
    get_open_trades, insert_trade, close_trade, get_portfolio_summary,
)


# ── Execution ────────────────────────────────────────────────────────────────

def execute_trade(gate_result: dict, price: float) -> dict | None:
    """
    Open a BUY position for a gate-approved ticker.
    Returns the trade record dict, or None if rejected by risk checks.
    """
    ticker   = gate_result["ticker"]
    sector   = gate_result["sector"]
    position = gate_result["lock3"]["position_size_pct"] if gate_result.get("lock3") else 0.10
    portfolio = get_portfolio_summary()

    # Risk checks
    if portfolio["open_position_count"] >= MAX_POSITIONS:
        logger.warning(f"Trade rejected [{ticker}]: max positions ({MAX_POSITIONS}) reached")
        return None

    # Check projected sector exposure (current + this trade).
    # sector_exposure fractions are stored as amount / STARTING_BALANCE,
    # so compute the new trade's contribution on the same denominator.
    new_trade_exposure = (portfolio["cash"] * position) / STARTING_BALANCE
    projected_exp = portfolio["sector_exposure"].get(sector, 0.0) + new_trade_exposure
    if projected_exp > MAX_SECTOR_EXPOSURE:
        logger.warning(
            f"Trade rejected [{ticker}]: {sector} projected at {projected_exp:.0%} "
            f"(cap {MAX_SECTOR_EXPOSURE:.0%})"
        )
        return None

    # Daily loss cap
    daily_loss = _daily_realized_loss()
    if daily_loss >= DAILY_LOSS_CAP:
        logger.warning(f"Trade rejected [{ticker}]: daily loss cap hit (${daily_loss:.2f} >= ${DAILY_LOSS_CAP})")
        return None

    # Already holding this ticker?
    open_tickers = {t["ticker"] for t in get_open_trades()}
    if ticker in open_tickers:
        logger.info(f"Trade skipped [{ticker}]: position already open")
        return None

    amount = portfolio["cash"] * min(position, MAX_POSITION_SIZE)
    if amount < 50:
        logger.warning(f"Trade rejected [{ticker}]: insufficient cash (${portfolio['cash']:.2f})")
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
    Check all open positions against TP, SL, and time-stop.
    Returns list of closed trade records.
    """
    open_trades = get_open_trades()
    if not open_trades:
        return []

    tickers       = list({t["ticker"] for t in open_trades})
    current_prices = _batch_prices(tickers)
    closed         = []

    for trade in open_trades:
        ticker      = trade["ticker"]
        entry_price = trade["price"]
        current     = current_prices.get(ticker)

        if current is None:
            logger.warning(f"Exit check [{ticker}]: could not fetch price — skipping")
            continue

        pnl_pct = (current - entry_price) / entry_price

        if pnl_pct >= TAKE_PROFIT_PCT:
            reason  = "TP"
            outcome = "WIN"
        elif pnl_pct <= -STOP_LOSS_PCT:
            reason  = "SL"
            outcome = "LOSS"
        elif _trading_days_since(trade["timestamp"]) >= TIME_STOP_DAYS:
            reason  = "TIME"
            outcome = "EXPIRED"
        else:
            continue

        pnl = trade["amount"] * pnl_pct
        close_trade(
            trade_id      = trade["id"],
            price_exit    = current,
            pnl           = pnl,
            outcome       = outcome,
            exit_reason   = reason,
            exited_at     = datetime.now(timezone.utc).isoformat(),
        )

        logger.info(
            f"Exit [{ticker}]: {reason} entry=${entry_price:.2f} "
            f"exit=${current:.2f} pnl={pnl:+.2f} ({pnl_pct:+.1%})"
        )
        closed.append({
            "ticker":      ticker,
            "outcome":     outcome,
            "exit_reason": reason,
            "pnl":         pnl,
            "pnl_pct":     pnl_pct,
        })

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
        if current:
            unreal     = t["amount"] * ((current - t["price"]) / t["price"])
            unreal_pct = (current - t["price"]) / t["price"]
        else:
            unreal     = None
            unreal_pct = None
        unrealized_total += unreal or 0
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
        "balance":          round(cash + unrealized_total, 2),
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


def _batch_prices(tickers: list[str]) -> dict[str, float]:
    """Fetch latest close prices for a list of tickers."""
    if not tickers:
        return {}
    try:
        if len(tickers) == 1:
            hist = yf.Ticker(tickers[0]).history(period="2d")
            if hist.empty:
                return {}
            return {tickers[0]: float(hist["Close"].iloc[-1])}
        data   = yf.download(tickers, period="1d", auto_adjust=True, progress=False)
        closes = data["Close"]
        return {
            t: float(closes[t].iloc[-1])
            for t in tickers
            if t in closes.columns and not closes[t].isna().all()
        }
    except Exception as e:
        logger.warning(f"Batch price fetch failed: {e}")
        return {}


def _trading_days_since(timestamp_iso: str) -> int:
    """Count business days (Mon–Fri) between entry date and today."""
    entry = datetime.fromisoformat(timestamp_iso).date()
    today = datetime.now(timezone.utc).date()
    return max(0, len(pd.bdate_range(start=entry, end=today)) - 1)
