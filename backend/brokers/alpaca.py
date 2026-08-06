"""
Alpaca broker client — wraps alpaca-py SDK for live order execution.

All public functions return plain dicts so callers don't need to import
Alpaca SDK types. Requires ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL
in .env.

Paper trading:  ALPACA_BASE_URL=https://paper-api.alpaca.markets
Live trading:   ALPACA_BASE_URL=https://api.alpaca.markets
"""
from loguru import logger

# Imported once at module load — not per-call. alpaca-py is a hard dependency
# (requirements.txt), and importing lazily inside functions let concurrent
# threads (gate runner's ThreadPoolExecutor + FastAPI request threads) race to
# import the same submodule for the first time, which deadlocks CPython's
# per-module import lock (observed 2026-06-23: "deadlock detected by
# _ModuleLock('alpaca.trading.enums')" — once that happens the process is
# poisoned and every subsequent broker call hangs until restart).
from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import (
    GetOrdersRequest,
    GetPortfolioHistoryRequest,
    MarketOrderRequest,
    ReplaceOrderRequest,
    StopLossRequest,
    StopOrderRequest,
    TakeProfitRequest,
)


class OrderTerminalError(Exception):
    """Order is already in a terminal state (filled, cancelled, expired).
    Callers should stop managing the leg and let the exit-reconciliation cycle
    record the close."""


def _client():
    """Return a TradingClient built from current config."""
    from backend.config import ALPACA_API_KEY, ALPACA_BASE_URL, ALPACA_SECRET_KEY
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

def get_orders(limit: int = 50, nested: bool = False) -> list[dict]:
    """Return recent orders (all statuses). Pass nested=True to include bracket legs."""
    try:
        req = GetOrdersRequest(status=QueryOrderStatus.ALL, limit=limit, nested=nested)
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
                "legs": [
                    {
                        "id":               str(leg.id),
                        "side":             str(leg.side),
                        "order_type":       str(leg.order_type),
                        "status":           str(leg.status),
                        "filled_avg_price": float(leg.filled_avg_price) if leg.filled_avg_price else None,
                        "filled_at":        leg.filled_at.isoformat() if leg.filled_at else None,
                    }
                    for leg in (o.legs or [])
                ] if nested else [],
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
    qty = int(notional / current_price)  # floor to whole shares — bracket orders require integer qty
    if qty <= 0:
        raise ValueError(f"Computed qty={qty} for {ticker} @ ${current_price:.2f} notional=${notional:.2f}")

    tp_price = round(current_price * (1 + take_profit_pct), 2)
    sl_price = round(current_price * (1 - stop_loss_pct), 2)

    req = MarketOrderRequest(
        symbol=ticker,
        qty=qty,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.GTC,
        order_class=OrderClass.BRACKET,
        take_profit=TakeProfitRequest(limit_price=tp_price),
        stop_loss=StopLossRequest(stop_price=sl_price),
    )

    order = _client().submit_order(req)
    logger.info(
        f"Alpaca bracket order placed [{ticker}]: qty={qty} @ ~${current_price:.2f} "
        f"TP=${tp_price:.2f} SL=${sl_price:.2f} order_id={order.id}"
    )
    return str(order.id)


def _enum_value(x) -> str | None:
    """
    alpaca-py's OrderStatus/OrderSide/OrderType are str-mixin Enums, but
    str(member) still returns Enum's "ClassName.MEMBER_NAME" repr, not the
    plain value ("OrderStatus.CANCELED", not "canceled") — a well-known
    Python str-Enum gotcha. Every consumer expects Alpaca's real lowercase
    status vocabulary; .value gives that. getattr() falls through cleanly
    if x is already a plain string (or None).
    """
    if x is None:
        return None
    return getattr(x, "value", x)


def get_order_by_id(order_id: str) -> dict:
    """Return a single order with its bracket legs."""
    import uuid
    try:
        order = _client().get_order_by_id(uuid.UUID(order_id))
        legs = []
        for leg in (order.legs or []):
            legs.append({
                "id":               str(leg.id),
                "side":             _enum_value(leg.side),
                "order_type":       _enum_value(leg.order_type),
                "status":           _enum_value(leg.status),
                "limit_price":      float(leg.limit_price)      if leg.limit_price      else None,
                "stop_price":       float(leg.stop_price)       if leg.stop_price       else None,
                "filled_avg_price": float(leg.filled_avg_price) if leg.filled_avg_price else None,
                "filled_at":        leg.filled_at.isoformat()   if leg.filled_at        else None,
            })
        return {
            "id":               str(order.id),
            "ticker":           order.symbol,
            "status":           _enum_value(order.status),
            "filled_avg_price": float(order.filled_avg_price) if order.filled_avg_price else None,
            "filled_qty":       float(order.filled_qty)       if order.filled_qty       else None,
            "legs":             legs,
        }
    except Exception as e:
        logger.error(f"Alpaca get_order_by_id [{order_id}] failed: {e}")
        raise


def get_portfolio_history(period: str = "1M") -> list[dict]:
    """
    Return daily equity curve from Alpaca portfolio history.
    Returns list of {ts, balance} dicts compatible with the demo EquityCurve format.
    Filters out zero-equity days (pre-activity on a fresh account).
    """
    from datetime import datetime, timezone

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


def cancel_open_orders(ticker: str) -> int:
    """Cancel all open orders for ticker. Returns count cancelled."""
    try:
        req = GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[ticker])
        orders = _client().get_orders(filter=req)
        count = 0
        for o in orders:
            try:
                _client().cancel_order_by_id(o.id)
                count += 1
            except Exception as e:
                logger.warning(f"Alpaca cancel_order [{ticker}] order_id={o.id} failed: {e}")
        if count:
            logger.info(f"Alpaca cancel_open_orders [{ticker}]: cancelled {count} order(s)")
        return count
    except Exception as e:
        logger.error(f"Alpaca cancel_open_orders [{ticker}] failed: {e}")
        raise


def replace_stop_leg(leg_id: str, new_stop_price: float, qty: int, ticker: str) -> str:
    """
    Move a bracket SL stop leg to a higher stop price. Returns the new order ID.

    Primary path: PATCH (replace_order_by_id). On Alpaca's side this is a single
    server-side operation — the old leg transitions directly to REPLACED and the
    new leg appears as HELD in one round-trip with no observable gap state.
    Confirmed against paper trading on 2026-05-31: HELD stop leg, PATCH issued,
    old_id → OrderStatus.REPLACED, new_id → OrderStatus.HELD atomically.

    Fallback: cancel old leg + place a new standalone GTC stop order. Used only
    when PATCH is rejected (e.g. leg already in a terminal state). This path has
    an explicit gap window: cancel succeeds, then place is a second API call. A
    failure on the place step raises, leaving the position without a stop until the
    next check cycle. The two paths are not equivalent — PATCH is safer. Do not
    promote the fallback to primary without re-confirming PATCH behaviour on the
    target account type (paper vs live).
    """
    import uuid as _uuid

    try:
        result = _client().replace_order_by_id(
            order_id=_uuid.UUID(leg_id),
            order_data=ReplaceOrderRequest(stop_price=new_stop_price),
        )
        new_id = str(result.id)
        logger.info(
            f"Alpaca replace_stop_leg [{ticker}]: {leg_id[:8]} → {new_id[:8]} "
            f"stop=${new_stop_price:.2f} (PATCH)"
        )
        return new_id
    except APIError as patch_err:
        if patch_err.status_code == 422:
            raise OrderTerminalError(
                f"replace_stop_leg [{ticker}] leg={leg_id[:8]}: "
                f"order in terminal state — {patch_err}"
            ) from patch_err
        logger.warning(
            f"Alpaca replace_stop_leg [{ticker}]: PATCH failed ({patch_err}) "
            f"— falling back to cancel + new stop"
        )
    except Exception as patch_err:
        logger.warning(
            f"Alpaca replace_stop_leg [{ticker}]: PATCH failed ({patch_err}) "
            f"— falling back to cancel + new stop"
        )

    # Fallback: cancel the old leg, place a standalone GTC stop sell order.
    try:
        _client().cancel_order_by_id(_uuid.UUID(leg_id))
    except Exception as cancel_err:
        logger.error(
            f"Alpaca replace_stop_leg [{ticker}] fallback: cancel {leg_id[:8]} failed — {cancel_err}"
        )
        raise

    req = StopOrderRequest(
        symbol=ticker,
        qty=qty,
        side=OrderSide.SELL,
        time_in_force=TimeInForce.GTC,
        stop_price=new_stop_price,
    )
    result = _client().submit_order(req)
    new_id = str(result.id)
    logger.info(
        f"Alpaca replace_stop_leg [{ticker}] fallback: new stop order {new_id[:8]} "
        f"stop=${new_stop_price:.2f} (cancel+new)"
    )
    return new_id


def close_position(ticker: str) -> dict:
    """Cancel open bracket legs then close the entire position for ticker."""
    cancel_open_orders(ticker)
    try:
        order = _client().close_position(ticker)
        logger.info(f"Alpaca close_position [{ticker}]: order_id={order.id}")
        return {
            "order_id":         str(order.id),
            "ticker":           ticker,
            "filled_avg_price": float(order.filled_avg_price) if order.filled_avg_price else None,
        }
    except Exception as e:
        logger.error(f"Alpaca close_position [{ticker}] failed: {e}")
        raise
