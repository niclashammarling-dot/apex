import re
import time
import threading
import uuid
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator
from backend.db import latest_signals, signals_for_sector, get_yesterday_sector_avg_scores, get_yesterday_ticker_scores, get_sector_history
from backend.gate import gate_runner
from backend.ticker_config import get_sectors as _get_sectors, add_ticker, remove_ticker

# ── Backfill job tracking ──────────────────────────────────────────────────────
# Simple in-memory store — only one backfill should run at a time.
_backfill_jobs: dict[str, dict] = {}   # job_id → {status, inserted, error}

router = APIRouter(prefix="/api")

# ── Ticker validation ─────────────────────────────────────────────────────────

_TICKER_RE = re.compile(r"^[A-Z]{1,5}$")

def _validate_ticker(ticker: str) -> str:
    t = ticker.upper().strip()
    if not _TICKER_RE.match(t):
        raise HTTPException(status_code=400, detail=f"Invalid ticker symbol: '{ticker}'. Must be 1–5 uppercase letters.")
    return t


# ── Simple in-memory rate limiter ─────────────────────────────────────────────
# Protects expensive LLM-backed endpoints (gate/run, gate/test) from abuse.

_rate_store: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT   = 10   # max calls
_RATE_WINDOW  = 60   # per N seconds


def _rate_check(key: str) -> None:
    now   = time.time()
    calls = _rate_store[key]
    calls[:] = [t for t in calls if now - t < _RATE_WINDOW]
    if len(calls) >= _RATE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: max {_RATE_LIMIT} requests per {_RATE_WINDOW}s for '{key}'",
        )
    calls.append(now)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/signals/latest")
def get_latest_signals():
    return latest_signals()


@router.get("/signals/sector/{sector_name}")
def get_sector_signals(sector_name: str):
    if sector_name not in _get_sectors():
        raise HTTPException(status_code=404, detail=f"Unknown sector: {sector_name}")
    return signals_for_sector(sector_name)


@router.get("/sectors")
def sectors_summary():
    """
    Returns sector-level summary: avg signal score, top ticker, last updated.
    Aggregates from the most recent signal per ticker.
    """
    signals = latest_signals(limit=200)
    prev_avgs = get_yesterday_sector_avg_scores()
    prev_ticker = get_yesterday_ticker_scores()
    from backend.sector_regime import compute_ticker_signals
    ticker_signals = compute_ticker_signals()

    sector_map: dict[str, list] = {}
    for s in signals:
        sector_map.setdefault(s["sector"], []).append(s)

    result = []
    for sector_name, cfg in _get_sectors().items():
        rows = sector_map.get(sector_name, [])
        if not rows:
            result.append({
                "sector":       sector_name,
                "etf":          cfg["etf"],
                "avg_signal":   None,
                "top_ticker":   None,
                "top_score":    None,
                "ticker_count": 0,
                "last_updated": None,
                "tickers":      [],
                "trend":        "flat",
            })
            continue

        avg_signal   = round(sum(r["signal_score"] for r in rows) / len(rows), 4)
        top          = max(rows, key=lambda r: r["signal_score"])
        last_updated = max(r["timestamp"] for r in rows)

        def _ticker_trend(ticker: str, score: float) -> str:
            prev = prev_ticker.get(ticker)
            if prev is None:
                return "flat"
            if score - prev > 0.01:
                return "up"
            if prev - score > 0.01:
                return "down"
            return "flat"

        tickers = sorted(
            [
                {
                    "ticker":         r["ticker"],
                    "signal_score":   r["signal_score"],
                    "momentum_score": r.get("momentum_score"),
                    "volume_score":   r.get("volume_score"),
                    "rsi":            r.get("rsi"),
                    "price":          r.get("price"),
                    "trend":          _ticker_trend(r["ticker"], r["signal_score"]),
                    "signal":         ticker_signals.get(r["ticker"], {}).get("signal", "weak"),
                    "streak_days":    ticker_signals.get(r["ticker"], {}).get("streak_days", 0),
                }
                for r in rows
            ],
            key=lambda t: t["signal_score"],
            reverse=True,
        )

        prev = prev_avgs.get(sector_name)
        if prev is None:
            trend = "flat"
        elif avg_signal - prev > 0.01:
            trend = "up"
        elif prev - avg_signal > 0.01:
            trend = "down"
        else:
            trend = "flat"

        result.append({
            "sector":       sector_name,
            "etf":          cfg["etf"],
            "avg_signal":   avg_signal,
            "top_ticker":   top["ticker"],
            "top_score":    top["signal_score"],
            "ticker_count": len(rows),
            "last_updated": last_updated,
            "tickers":      tickers,
            "trend":        trend,
        })

    return result


@router.get("/sectors/history")
def sectors_history(days: int = 30):
    """
    Sector avg_score time series.
    days=0 returns all available history.
    days≤7 returns raw 15-min snapshots; days>7 returns daily averages.
    """
    if days < 0 or days > 3650:
        raise HTTPException(status_code=400, detail="days must be 0 (all) or 1–3650")
    return get_sector_history(days)


class BackfillRequest(BaseModel):
    start_date: str
    end_date:   str


@router.get("/sectors/regime")
def sectors_regime():
    """Current sector regime analysis — trend durations, rotation signals, risk-on/off."""
    from backend.sector_regime import compute_sector_regime
    return compute_sector_regime()


@router.get("/sectors/rotation-forecast")
def sectors_rotation_forecast():
    """Sector rotation transition probabilities and current forecast."""
    from backend.sector_transitions import get_rotation_forecast
    return get_rotation_forecast()


@router.post("/sectors/backfill")
def sectors_backfill(req: BackfillRequest):
    """
    Backfill sector_snapshots with historical signal data.
    Runs in a background thread — returns a job_id to poll via GET /sectors/backfill/{job_id}.
    Only one backfill may run at a time.
    """
    # Reject if a backfill is already running
    for job in _backfill_jobs.values():
        if job["status"] == "running":
            raise HTTPException(status_code=409, detail="A backfill is already running")

    job_id = str(uuid.uuid4())[:8]
    _backfill_jobs[job_id] = {"status": "running", "inserted": 0, "error": None,
                               "start_date": req.start_date, "end_date": req.end_date}

    def _run():
        from backend.backtest.sector_backfill import backfill_sector_snapshots
        try:
            n = backfill_sector_snapshots(req.start_date, req.end_date)
            _backfill_jobs[job_id].update({"status": "done", "inserted": n})
        except Exception as e:
            _backfill_jobs[job_id].update({"status": "error", "error": str(e)})

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job_id, "status": "running"}


@router.get("/sectors/backfill/{job_id}")
def sectors_backfill_status(job_id: str):
    job = _backfill_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job_id": job_id, **job}


@router.post("/gate/run")
def run_gate(request: Request):
    """Manually trigger a full gate evaluation cycle."""
    _rate_check("gate/run")
    results = gate_runner.run()
    return {
        "evaluated":     len(results),
        "trades_queued": sum(1 for r in results if r["outcome"] in ("TRADE_QUEUED", "TRADE_EXECUTED")),
        "results":       results,
    }


@router.post("/gate/test")
def test_gate(ticker: str, request: Request):
    """
    Force a single ticker through the full Lock 2 + Lock 3 pipeline,
    bypassing the Lock 1 threshold. Used to verify API keys and LLM
    responses are working without waiting for a real signal.
    """
    _rate_check("gate/test")
    ticker = _validate_ticker(ticker)

    from backend.db import latest_signals, get_wallet_context
    from backend.gate import lock1_quant, lock2_sentiment, lock3_claude

    signals = [s for s in latest_signals(limit=200) if s["ticker"] == ticker]
    if not signals:
        raise HTTPException(status_code=404, detail=f"No signal found for {ticker}. Run a sector poll first.")

    signal     = signals[0]
    wallet_ctx = get_wallet_context()

    l1 = lock1_quant.evaluate(signal)
    l2 = lock2_sentiment.evaluate(signal["ticker"])

    l3 = None
    if l2["passed"]:
        context = gate_runner._build_claude_context(signal, l2, wallet_ctx)
        l3 = lock3_claude.evaluate(context)

    return {
        "ticker":  signal["ticker"],
        "sector":  signal["sector"],
        "lock1":   l1,
        "lock2":   l2,
        "lock3":   l3,
        "note":    "Lock 1 threshold bypassed for testing purposes",
    }


@router.get("/wallet")
def get_wallet():
    from backend.wallet import get_portfolio
    return get_portfolio()


@router.get("/gate/history")
def gate_history(limit: int = 100):
    from backend.db import get_gate_history, get_ticker_thresholds, get_gate_funnel_counts
    from backend.demo_config import get_demo_config
    import json as _json
    rows = get_gate_history(limit)
    thresholds = get_ticker_thresholds()
    flat = get_demo_config().get("lock1_threshold", 0.5)
    for row in rows:
        row["l1_threshold"] = thresholds.get(row["sector"], flat)
        if row.get("lock_leading_checks"):
            try:
                row["lock_leading_checks"] = _json.loads(row["lock_leading_checks"])
            except Exception:
                row["lock_leading_checks"] = None
    return {"rows": rows, "funnel": get_gate_funnel_counts()}


@router.get("/demo/trades")
def get_demo_trades():
    from backend.db import get_all_trades
    return get_all_trades()


@router.get("/wallet/equity")
def wallet_equity():
    from backend.db import get_equity_curve
    return get_equity_curve()


# ── Backtest ──────────────────────────────────────────────────────────────────

class BacktestRequest(BaseModel):
    start_date: str
    end_date: str
    initial_balance: float = 10_000.0
    take_profit_pct:     float | None = None
    stop_loss_pct:       float | None = None
    trailing_stop_pct:   float | None = None
    atr_exits:              bool       = False
    earnings_filter_days:   int | None = None
    time_stop_days:         int | None = None
    lock1_threshold:     float | None = None
    max_entries_per_day: int   | None = None

    @field_validator("initial_balance")
    @classmethod
    def balance_positive(cls, v: float) -> float:
        if v < 100:
            raise ValueError("initial_balance must be at least $100")
        return v

    @field_validator("take_profit_pct")
    @classmethod
    def tp_range(cls, v: float | None) -> float | None:
        if v is not None and not (0.01 <= v <= 1.0):
            raise ValueError("take_profit_pct must be between 0.01 and 1.0")
        return v

    @field_validator("stop_loss_pct")
    @classmethod
    def sl_range(cls, v: float | None) -> float | None:
        if v is not None and not (0.01 <= v <= 0.50):
            raise ValueError("stop_loss_pct must be between 0.01 and 0.50")
        return v

    @field_validator("trailing_stop_pct")
    @classmethod
    def tsl_range(cls, v: float | None) -> float | None:
        if v is not None and not (0.01 <= v <= 0.50):
            raise ValueError("trailing_stop_pct must be between 0.01 and 0.50")
        return v

    @field_validator("time_stop_days")
    @classmethod
    def tdays_positive(cls, v: int | None) -> int | None:
        if v is not None and v < 1:
            raise ValueError("time_stop_days must be >= 1")
        return v

    @field_validator("lock1_threshold")
    @classmethod
    def l1_range(cls, v: float | None) -> float | None:
        if v is not None and not (0.0 <= v <= 1.0):
            raise ValueError("lock1_threshold must be between 0.0 and 1.0")
        return v

    @field_validator("max_entries_per_day")
    @classmethod
    def epd_positive(cls, v: int | None) -> int | None:
        if v is not None and v < 1:
            raise ValueError("max_entries_per_day must be >= 1")
        return v


# ── Settings ──────────────────────────────────────────────────────────────────

@router.get("/tickers")
def get_tickers():
    """Return all sectors with their current ticker lists."""
    sectors = _get_sectors()
    return {
        name: {"etf": cfg["etf"], "tickers": cfg["tickers"]}
        for name, cfg in sectors.items()
    }


@router.post("/tickers/{sector}/add")
def add_ticker_to_sector(sector: str, ticker: str):
    try:
        updated = add_ticker(sector, ticker.upper().strip())
        return {"sector": sector, "tickers": updated[sector]["tickers"]}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/tickers/{sector}/remove")
def remove_ticker_from_sector(sector: str, ticker: str):
    try:
        updated = remove_ticker(sector, ticker.upper().strip())
        return {"sector": sector, "tickers": updated[sector]["tickers"]}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/settings")
def get_settings():
    """Return current demo and live threshold configs."""
    from backend.demo_config import get_demo_config
    from backend.live_config import get_live_config
    return {
        "demo": get_demo_config(),
        "live": get_live_config(),
    }


@router.post("/settings/demo")
def update_demo_settings(updates: dict):
    """Update demo gate thresholds at runtime."""
    from backend.demo_config import set_demo_config
    return {"config": set_demo_config(updates)}


@router.post("/settings/live")
def update_live_settings(updates: dict):
    """Update live gate thresholds at runtime."""
    from backend.live_config import set_live_config
    return {"config": set_live_config(updates)}


@router.post("/backtest/run")
def run_backtest(req: BacktestRequest):
    """
    Run a historical backtest using the Lock 1 quantitative signal engine.
    Downloads historical OHLCV data and simulates the full position lifecycle
    (entry on signal, exit on TP/SL/time-stop) across the requested date range.
    """
    from backend.backtest.engine import run
    try:
        result = run(
            req.start_date,
            req.end_date,
            req.initial_balance,
            take_profit_pct=req.take_profit_pct,
            stop_loss_pct=req.stop_loss_pct,
            trailing_stop_pct=req.trailing_stop_pct,
            atr_exits=req.atr_exits,
            earnings_filter_days=req.earnings_filter_days,
            time_stop_days=req.time_stop_days,
            lock1_threshold=req.lock1_threshold,
            max_entries_per_day=req.max_entries_per_day,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Backtest failed: {e}")


# ── Watchlist ──────────────────────────────────────────────────────────────────

@router.get("/watchlist")
def get_watchlist():
    """Return watchlist tickers enriched with current signal data."""
    from backend.db import get_watchlist
    from backend.sector_regime import compute_ticker_signals
    from backend.sector_transitions import compute_ticker_rotation_scores, get_rotation_forecast
    from backend.db import prev_signals_by_ticker, latest_signals

    entries         = get_watchlist()
    tk_signals      = compute_ticker_signals()
    rotation_scores = compute_ticker_rotation_scores()
    prev_tk         = prev_signals_by_ticker()

    # Sectors currently identified as pre-rotation setups
    try:
        forecast         = get_rotation_forecast()
        watching_sectors = {w["sector"] for w in forecast.get("watching", [])} \
                           if forecast.get("available") else set()
    except Exception:
        watching_sectors = set()

    # Build current score map from latest signals
    latest = {s["ticker"]: s["signal_score"] for s in latest_signals(limit=500)}

    result = []
    for e in entries:
        ticker  = e["ticker"]
        tk      = tk_signals.get(ticker, {})
        score   = latest.get(ticker)
        prev    = prev_tk.get(ticker)
        if score is not None and prev is not None:
            if score - prev > 0.01:   trend = "up"
            elif prev - score > 0.01: trend = "down"
            else:                     trend = "flat"
        else:
            trend = "flat"
        result.append({
            "ticker":         ticker,
            "sector":         e["sector"],
            "added_at":       e["added_at"],
            "source":         e["source"],
            "score":          round(score, 4) if score is not None else None,
            "signal":         tk.get("signal", "weak"),
            "streak_days":    tk.get("streak_days", 0),
            "trend":          trend,
            "velocity":       tk.get("velocity"),           # accelerating/decelerating/flat
            "velocity_5d":    tk.get("velocity_5d"),        # raw 5d score delta
            "rotation_score": rotation_scores.get(ticker),  # composite 0–1 rotation score
            "pre_rotation":   e["sector"] in watching_sectors,  # in a predicted-next sector
        })
    return result


@router.post("/watchlist/{ticker}")
def add_to_watchlist(ticker: str):
    """Manually add a ticker to the watchlist."""
    ticker = _validate_ticker(ticker)
    from backend.db import upsert_watchlist, latest_signals
    signals = [s for s in latest_signals(limit=500) if s["ticker"] == ticker]
    if not signals:
        raise HTTPException(status_code=404, detail=f"No signal found for {ticker}")
    upsert_watchlist(ticker, signals[0]["sector"], source="manual")
    return {"ticker": ticker, "status": "added"}


@router.delete("/watchlist/{ticker}")
def remove_from_watchlist(ticker: str):
    """Remove a ticker from the watchlist."""
    ticker = _validate_ticker(ticker)
    from backend.db import remove_watchlist
    found = remove_watchlist(ticker)
    if not found:
        raise HTTPException(status_code=404, detail=f"{ticker} not in watchlist")
    return {"ticker": ticker, "status": "removed"}


# ── Threshold calibration ──────────────────────────────────────────────────────

@router.post("/calibrate/ticker-thresholds")
def calibrate_ticker_thresholds():
    """
    Recompute per-sector ticker thresholds from ticker_history.
    Run after backfill completes. Takes ~5s.
    """
    from backend.ticker_threshold_calibration import calibrate, print_calibration_report
    thresholds = calibrate()
    return {"thresholds": thresholds, "sectors": len(thresholds)}


@router.get("/calibrate/ticker-thresholds")
def get_ticker_thresholds():
    """Return currently active per-sector thresholds."""
    from backend.db import get_ticker_thresholds
    from backend.sector_regime import TICKER_THRESHOLD_DEFAULT
    thresholds = get_ticker_thresholds()
    return {
        "thresholds": thresholds,
        "default_fallback": TICKER_THRESHOLD_DEFAULT,
        "calibrated": len(thresholds) > 0,
    }
