"""
Live trading API — endpoints for the Live tab.
All data comes from Alpaca (account, positions, orders) plus the local
live_gate_history table for gate feed history.
"""
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from loguru import logger

from backend.config import ALPACA_BASE_URL, LIVE_ENABLED

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
        from backend.db import get_open_live_trades
        from backend.live_trades_tracker import _trading_days_since

        positions  = broker.get_positions()
        open_trades = get_open_live_trades()
        days_map   = {t["ticker"]: _trading_days_since(t["timestamp"]) for t in open_trades}
        sl_map     = {t["ticker"]: t["sl_price"] for t in open_trades}
        for p in positions:
            p["days_held"] = days_map.get(p["ticker"])
            p["sl_price"]  = sl_map.get(p["ticker"])
        return positions
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
        from backend.db import get_live_equity_curve
        from backend.live_config import get_live_config

        cfg            = get_live_config()
        since          = cfg.get("live_account_since")
        positions      = broker.get_positions()
        unrealised     = sum(p["unrealized_pnl"] or 0 for p in positions)
        return get_live_equity_curve(unrealised_total=unrealised, since=since)
    except Exception as e:
        logger.error(f"live_equity: {e}")
        raise HTTPException(status_code=502, detail=f"Alpaca error: {e}")


FILL_RECONCILE = Path(__file__).parent.parent.parent / "data" / "fill_reconcile.json"


def live_fees() -> dict:
    """Broker fee activities since live_account_since, as audit/fill_reconcile.py last
    wrote them (publish_state, each weekday night). Shown beside REALIZED, never netted
    into it: no row's P&L holds them (CHECK 89, 2026-10-31). A missing or unreadable
    file is a visible state, not a zero."""
    try:
        d = json.loads(FILL_RECONCILE.read_text())
        return {"total": d["fees_total"], "days": d["fee_days"], "since": d["since"],
                "as_of": d["generated_at"], "missing": None}
    except (OSError, ValueError, KeyError) as e:
        return {"total": None, "days": None, "since": None, "as_of": None,
                "missing": f"data/fill_reconcile.json unreadable ({type(e).__name__}); "
                           "audit/fill_reconcile.py writes it in publish_state"}


@router.get("/stats")
def live_stats():
    """Closed live-trade stats since the account reset, overall and per
    signal_class — what the wallet panel renders (db.live_trade_stats) — plus
    the broker fees the reconcile reports (live_fees)."""
    from backend.db import live_trade_stats
    return {**live_trade_stats(), "fees": live_fees()}


@router.get("/trades")
def live_trades():
    from backend.db import get_all_live_trades
    return get_all_live_trades()


@router.get("/gate/history")
def live_gate_history():
    import json as _json
    from datetime import datetime, time
    from zoneinfo import ZoneInfo

    from backend.db import (
        get_live_gate_funnel_counts,
        get_live_gate_history,
        get_ticker_thresholds,
    )
    from backend.live_config import get_live_config

    ET = ZoneInfo("America/New_York")
    today_et = datetime.now(ET).date()
    market_open = datetime.combine(today_et, time(9, 30), tzinfo=ET).isoformat()

    rows = get_live_gate_history(since=market_open)
    thresholds = get_ticker_thresholds()
    flat = get_live_config().get("l3_score_threshold_min", 0.5)
    for row in rows:
        row["l3_score_threshold_min"] = thresholds.get(row["sector"], flat)
        if row.get("l5_positioning_checks"):
            try:
                row["l5_positioning_checks"] = _json.loads(row["l5_positioning_checks"])
            except Exception:
                row["l5_positioning_checks"] = None
    funnel = get_live_gate_funnel_counts()

    blocked_reason = None
    if LIVE_ENABLED:
        try:
            from backend.brokers import alpaca as broker
            acct = broker.get_account()
            cfg = get_live_config()
            from backend.db import live_sample_rows
            from backend.gate import l0_portfolio
            l0 = l0_portfolio.evaluate(acct, live_sample_rows(), cfg["starting_balance"])
            if l0["reason"] == l0_portfolio.HALT_LOSS_CAP:
                blocked_reason = f"daily loss cap — ${l0['day_loss']:,.0f} loss vs ${l0['l0_portfolio_loss_cap_max']:,.0f} cap"
            elif l0["reason"]:
                blocked_reason = f"L0 {l0['reason']} — realised drawdown {l0['drawdown']['drawdown']:.1%}"
            elif acct.get("trading_blocked") or acct.get("account_blocked"):
                blocked_reason = "Alpaca account blocked"
        except Exception:
            pass

    from backend.gate.lock_codes import as_api, decorate_rows
    return {"rows": decorate_rows(rows, "live"), "funnel": funnel, "blocked_reason": blocked_reason, "locks": as_api()}


@router.post("/config/promote")
def promote_demo_to_live():
    """Copy current demo thresholds into live_config.json.

    Copies strategy parameters only. Account-size-specific keys are excluded
    by demo_thresholds() via _PROMOTE_EXCLUDE:
      - starting_balance: demo=$2k, live=$100k — different denominators;
        leaked into live would corrupt sector exposure checks (notional/2000 = 1500%)
      - l0_portfolio_loss_cap_max: absolute dollars calibrated to account size (demo's wallet reads it; the
        live gate has not since 2026-10-24: L0 Portfolio sets 5 % of equity, l0_portfolio.py), kept
        out of Promote so the live value stays account-scaled if L0's cap is ever removed

    Any new config key must be deliberately placed in _KEYS (promotable) or
    _PROMOTE_EXCLUDE (account-specific). Default-in-_KEYS means it gets promoted.
    """
    _require_live()
    from backend.live_config import demo_thresholds, set_live_config
    new_cfg = set_live_config(demo_thresholds(), source="promote")
    logger.info("Promoted demo thresholds to live config")
    return {"promoted": True, "config": new_cfg}

