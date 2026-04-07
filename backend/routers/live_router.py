"""
Live trading API — endpoints for the Live tab.
All data comes from Alpaca (account, positions, orders) plus the local
live_gate_history table for gate feed history.
"""
from fastapi import APIRouter, HTTPException
from loguru import logger

from backend.config import LIVE_ENABLED, ALPACA_BASE_URL

router = APIRouter(prefix="/api/live")


def _require_live() -> None:
    if not LIVE_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Live trading is disabled. Set LIVE_ENABLED=true in .env to enable.",
        )


@router.get("/status")
def live_status():
    """Returns whether live trading is enabled and Alpaca connectivity."""
    is_paper = "paper-api" in ALPACA_BASE_URL
    if not LIVE_ENABLED:
        return {"enabled": False, "connected": False, "paper": is_paper, "message": "LIVE_ENABLED=false"}
    try:
        from backend.brokers import alpaca as broker
        acct = broker.get_account()
        return {
            "enabled":         True,
            "connected":       True,
            "paper":           is_paper,
            "trading_blocked": acct["trading_blocked"],
            "account_blocked": acct["account_blocked"],
            "status":          acct["status"],
        }
    except Exception as e:
        return {"enabled": True, "connected": False, "paper": is_paper, "message": str(e)}


@router.get("/account")
def live_account():
    _require_live()
    try:
        from backend.brokers import alpaca as broker
        return broker.get_account()
    except Exception as e:
        logger.error(f"live_account: {e}")
        raise HTTPException(status_code=502, detail=f"Alpaca error: {e}")


@router.get("/positions")
def live_positions():
    _require_live()
    try:
        from backend.brokers import alpaca as broker
        return broker.get_positions()
    except Exception as e:
        logger.error(f"live_positions: {e}")
        raise HTTPException(status_code=502, detail=f"Alpaca error: {e}")


@router.get("/orders")
def live_orders(limit: int = 50):
    _require_live()
    try:
        from backend.brokers import alpaca as broker
        return broker.get_orders(limit=limit)
    except Exception as e:
        logger.error(f"live_orders: {e}")
        raise HTTPException(status_code=502, detail=f"Alpaca error: {e}")


@router.get("/equity")
def live_equity(period: str = "1M"):
    _require_live()
    try:
        from backend.brokers import alpaca as broker

        points = broker.get_portfolio_history(period=period)

        # Annotate the last point as MTM with unrealised P&L — Alpaca's daily series
        # already reflects current equity, so we annotate in place rather than appending
        # a second point (which would use a different equity calculation and cause a gap).
        if points:
            positions  = broker.get_positions()
            unrealised = round(sum(p["unrealized_pnl"] or 0 for p in positions), 2)
            points[-1] = {**points[-1], "mtm": True, "unrealised": unrealised}

        return points
    except Exception as e:
        logger.error(f"live_equity: {e}")
        raise HTTPException(status_code=502, detail=f"Alpaca error: {e}")


@router.get("/trades")
def live_trades():
    from backend.db import get_all_live_trades
    return get_all_live_trades()


@router.get("/gate/history")
def live_gate_history(limit: int = 100):
    from backend.db import get_live_gate_history, get_live_gate_funnel_counts, get_ticker_thresholds
    from backend.live_config import get_live_config
    import json as _json
    rows = get_live_gate_history(limit)
    thresholds = get_ticker_thresholds()
    flat = get_live_config().get("lock1_threshold", 0.5)
    for row in rows:
        row["l1_threshold"] = thresholds.get(row["sector"], flat)
        if row.get("lock_leading_checks"):
            try:
                row["lock_leading_checks"] = _json.loads(row["lock_leading_checks"])
            except Exception:
                row["lock_leading_checks"] = None
    funnel = get_live_gate_funnel_counts()
    return {"rows": rows, "funnel": funnel}


@router.get("/compare")
def compare_performance():
    """
    Side-by-side Demo vs Live performance stats.
    Live stats come from Alpaca. Demo stats come from local trades table.
    Returns nulls for live side when LIVE_ENABLED=false.
    """
    from backend.db import get_all_trades
    from backend.config import STARTING_BALANCE

    # ── Demo stats ────────────────────────────────────────────────────────────
    trades        = get_all_trades()
    closed        = [t for t in trades if t["outcome"] in ("WIN", "LOSS", "EXPIRED")]
    open_trades   = [t for t in trades if t["outcome"] == "OPEN"]
    wins          = [t for t in closed if t["outcome"] == "WIN"]
    losses        = [t for t in closed if t["outcome"] in ("LOSS", "EXPIRED")]

    realized_pnl  = sum(t["pnl"] or 0 for t in closed)
    invested      = sum(t["amount"] for t in open_trades)
    demo_balance  = STARTING_BALANCE - invested + realized_pnl

    gross_wins    = sum(t["pnl"] for t in wins if t["pnl"])
    gross_losses  = abs(sum(t["pnl"] for t in losses if t["pnl"]))
    profit_factor = round(gross_wins / gross_losses, 2) if gross_losses > 0 else None

    demo = {
        "balance":         round(demo_balance, 2),
        "starting_balance": STARTING_BALANCE,
        "total_return_pct": round((demo_balance - STARTING_BALANCE) / STARTING_BALANCE * 100, 2),
        "realized_pnl":    round(realized_pnl, 2),
        "total_trades":    len(closed),
        "open_positions":  len(open_trades),
        "win_rate":        round(len(wins) / len(closed) * 100, 1) if closed else None,
        "profit_factor":   profit_factor,
        "avg_win":         round(gross_wins / len(wins), 2)   if wins   else None,
        "avg_loss":        round(gross_losses / len(losses), 2) if losses else None,
    }

    # ── Live stats ────────────────────────────────────────────────────────────
    live = None
    if LIVE_ENABLED:
        try:
            from backend.brokers import alpaca as broker
            from backend.db import get_all_live_trades
            acct     = broker.get_account()
            lt       = get_all_live_trades()
            closed_lt = [t for t in lt if t["outcome"] in ("WIN", "LOSS")]
            open_lt   = [t for t in lt if t["outcome"] == "OPEN"]
            wins      = [t for t in closed_lt if t["outcome"] == "WIN"]
            losses    = [t for t in closed_lt if t["outcome"] == "LOSS"]

            realized_pnl  = sum(t["pnl"] or 0 for t in closed_lt)
            gross_wins    = sum(t["pnl"] for t in wins   if t["pnl"])
            gross_losses  = abs(sum(t["pnl"] for t in losses if t["pnl"]))
            profit_factor = round(gross_wins / gross_losses, 2) if gross_losses > 0 else None

            live = {
                "balance":          round(float(acct["equity"]), 2),
                "starting_balance": 100_000.0,
                "total_return_pct": round((float(acct["equity"]) - 100_000.0) / 100_000.0 * 100, 2),
                "realized_pnl":     round(realized_pnl, 2),
                "total_trades":     len(closed_lt),
                "open_positions":   len(open_lt),
                "day_pnl":          acct["day_pnl"],
                "day_pnl_pct":      round(acct["day_pnl_pct"] * 100, 2),
                "win_rate":         round(len(wins) / len(closed_lt) * 100, 1) if closed_lt else None,
                "profit_factor":    profit_factor,
                "avg_win":          round(gross_wins  / len(wins),   2) if wins   else None,
                "avg_loss":         round(gross_losses / len(losses), 2) if losses else None,
            }
        except Exception as e:
            logger.error(f"compare: live stats failed — {e}")
            live = None

    return {"demo": demo, "live": live}


@router.get("/config")
def get_live_config_endpoint():
    """Return current live config alongside demo thresholds for comparison."""
    from backend.live_config import get_live_config, demo_thresholds
    return {
        "live":  get_live_config(),
        "demo":  demo_thresholds(),
    }


@router.post("/config/promote")
def promote_demo_to_live():
    """Copy current demo thresholds into live_config.json."""
    _require_live()
    from backend.live_config import demo_thresholds, set_live_config
    new_cfg = set_live_config(demo_thresholds())
    logger.info("Promoted demo thresholds to live config")
    return {"promoted": True, "config": new_cfg}


@router.post("/config/update")
def update_live_config(updates: dict):
    """Partially update live_config.json with the provided values."""
    _require_live()
    from backend.live_config import set_live_config
    new_cfg = set_live_config(updates)
    return {"config": new_cfg}


@router.post("/gate/run")
def run_live_gate():
    """Manually trigger a live gate evaluation cycle."""
    _require_live()
    from backend.gate import gate_runner_live
    results = gate_runner_live.run()
    return {
        "evaluated":     len(results),
        "trades_placed": sum(1 for r in results if r["outcome"] == "TRADE_EXECUTED"),
        "results":       results,
    }
