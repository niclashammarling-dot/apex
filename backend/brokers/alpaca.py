"""
Alpaca broker client — wraps alpaca-py SDK for live order execution.

All public functions return plain dicts so callers don't need to import
Alpaca SDK types. Requires ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL
in .env.

Paper trading:  ALPACA_BASE_URL=https://paper-api.alpaca.markets
Live trading:   ALPACA_BASE_URL=https://api.alpaca.markets
"""
from loguru import logger


def _client():
    """Return a cached TradingClient. Imported lazily to avoid startup errors
    when alpaca-py is not yet installed."""
    from alpaca.trading.client import TradingClient
    from backend.config import ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL
    paper = "paper-api" in ALPACA_BASE_URL
    return TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=paper)


# ── Account ───────────────────────────────────────────────────────────────────

def get_account() -> dict:
    """Return account summary (equity, cash, buying_power, day P&L)."""
    try:
        acct = _client().get_account()
        return {
            "equity":            float(acct.equity),
            "cash":              float(acct.cash),
            "buying_power":      float(acct.buying_power),
            "last_equity":       float(acct.last_equity),
            "day_pnl":           round(float(acct.equity) - float(acct.last_equity), 2),
            "day_pnl_pct":       round((float(acct.equity) - float(acct.last_equity)) / float(acct.last_equity), 4)
                                 if float(acct.last_equity) > 0 else 0.0,
            "pattern_day_trader": acct.pattern_day_trader,
            "trading_blocked":   acct.trading_blocked,
            "account_blocked":   acct.account_blocked,
            "status":            str(acct.status),
        }
    except Exception as e:
        logger.error(f"Alpaca get_account failed: {e}")
        raise


# ── Positions ─────────────────────────────────────────────────────────────────

def get_positions() -> list[dict]:
    """Return all open positions."""
    try:
        positions = _client().get_all_positions()
        return [
            {
                "ticker":           p.symbol,
                "qty":              float(p.qty),
                "side":             str(p.side),
                "avg_entry_price":  float(p.avg_entry_price),
                "current_price":    float(p.current_price) if p.current_price else None,
                "market_value":     float(p.market_value) if p.market_value else None,
                "cost_basis":       float(p.cost_basis),
                "unrealized_pnl":   float(p.unrealized_pl) if p.unrealized_pl else None,
                "unrealized_pct":   float(p.unrealized_plpc) if p.unrealized_plpc else None,
                "change_today":     float(p.change_today) if p.change_today else None,
            }
            for p in positions
        ]
    except Exception as e:
        logger.error(f"Alpaca get_positions failed: {e}")
        raise


# ── Orders ────────────────────────────────────────────────────────────────────

def get_orders(limit: int = 50) -> list[dict]:
    """Return recent orders (all statuses)."""
    try:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        req = GetOrdersRequest(status=QueryOrderStatus.ALL, limit=limit)
        orders = _client().get_orders(filter=req)
        return [
            {
                "id":           str(o.id),
                "ticker":       o.symbol,
                "side":         str(o.side),
                "type":         str(o.order_type),
                "qty":          float(o.qty) if o.qty else None,
                "filled_qty":   float(o.filled_qty) if o.filled_qty else None,
                "filled_price": float(o.filled_avg_price) if o.filled_avg_price else None,
                "status":       str(o.status),
                "submitted_at": o.submitted_at.isoformat() if o.submitted_at else None,
                "filled_at":    o.filled_at.isoformat() if o.filled_at else None,
            }
            for o in orders
        ]
    except Exception as e:
        logger.error(f"Alpaca get_orders failed: {e}")
        raise


# ── Order placement ───────────────────────────────────────────────────────────

def place_bracket_order(
    ticker: str,
    notional: float,
    current_price: float,
    take_profit_pct: float,
    stop_loss_pct: float,
) -> str:
    """
    Place a market bracket order (entry + TP limit + SL stop).
    Returns the Alpaca order ID string.

    Uses notional → qty conversion because Alpaca bracket orders require qty.
    Alpaca bracket orders do not support fractional shares — qty is floored to
    the nearest whole share. Prices rounded to 2dp (sufficient for US equities).
    """
    from alpaca.trading.requests import MarketOrderRequest, TakeProfitRequest, StopLossRequest
    from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

    qty = int(notional / current_price)  # floor to whole shares — bracket orders require integer qty
    if qty <= 0:
        raise ValueError(f"Computed qty={qty} for {ticker} @ ${current_price:.2f} notional=${notional:.2f}")

    tp_price = round(current_price * (1 + take_profit_pct), 2)
    sl_price = round(current_price * (1 - stop_loss_pct), 2)

    req = MarketOrderRequest(
        symbol=ticker,
        qty=qty,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
        order_class=OrderClass.BRACKET,
        take_profit=TakeProfitRequest(limit_price=tp_price),
        stop_loss=StopLossRequest(stop_price=sl_price),
    )

    order = _client().submit_order(req)
    logger.info(
        f"Alpaca bracket order placed [{ticker}]: qty={qty:.4f} @ ~${current_price:.2f} "
        f"TP=${tp_price:.2f} SL=${sl_price:.2f} order_id={order.id}"
    )
    return str(order.id)


def get_order_by_id(order_id: str) -> dict:
    """Return a single order with its bracket legs."""
    import uuid
    order = _client().get_order_by_id(uuid.UUID(order_id))
    legs = []
    for leg in (order.legs or []):
        legs.append({
            "id":               str(leg.id),
            "side":             str(leg.side),
            "order_type":       str(leg.order_type),
            "status":           str(leg.status),
            "limit_price":      float(leg.limit_price)      if leg.limit_price      else None,
            "stop_price":       float(leg.stop_price)       if leg.stop_price       else None,
            "filled_avg_price": float(leg.filled_avg_price) if leg.filled_avg_price else None,
            "filled_at":        leg.filled_at.isoformat()   if leg.filled_at        else None,
        })
    return {
        "id":               str(order.id),
        "ticker":           order.symbol,
        "status":           str(order.status),
        "filled_avg_price": float(order.filled_avg_price) if order.filled_avg_price else None,
        "filled_qty":       float(order.filled_qty)       if order.filled_qty       else None,
        "legs":             legs,
    }


def get_portfolio_history(period: str = "1M") -> list[dict]:
    """
    Return daily equity curve from Alpaca portfolio history.
    Returns list of {ts, balance} dicts compatible with the demo EquityCurve format.
    Filters out zero-equity days (pre-activity on a fresh account).
    """
    from datetime import datetime, timezone
    from alpaca.trading.requests import GetPortfolioHistoryRequest
    try:
        req  = GetPortfolioHistoryRequest(period=period, timeframe="1D")
        hist = _client().get_portfolio_history(history_filter=req)
        base = float(hist.base_value) if hist.base_value else 0.0

        points = [{"ts": "Start", "balance": base}]
        for ts, eq in zip(hist.timestamp or [], hist.equity or []):
            if not eq or eq == 0.0:
                continue
            date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            points.append({"ts": date_str, "balance": round(float(eq), 2)})

        return points
    except Exception as e:
        logger.error(f"Alpaca get_portfolio_history failed: {e}")
        raise


def close_position(ticker: str) -> dict:
    """Close the entire position for a ticker. Returns the closing order dict."""
    try:
        order = _client().close_position(ticker)
        logger.info(f"Alpaca close_position [{ticker}]: order_id={order.id}")
        return {"order_id": str(order.id), "ticker": ticker}
    except Exception as e:
        logger.error(f"Alpaca close_position [{ticker}] failed: {e}")
        raise
