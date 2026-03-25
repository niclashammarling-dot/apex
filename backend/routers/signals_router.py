import re
import time
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator
from backend.db import latest_signals, signals_for_sector, prev_signals_avg_by_sector
from backend.config import SECTORS
from backend.gate import gate_runner

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
    if sector_name not in SECTORS:
        raise HTTPException(status_code=404, detail=f"Unknown sector: {sector_name}")
    return signals_for_sector(sector_name)


@router.get("/sectors")
def get_sectors():
    """
    Returns sector-level summary: avg signal score, top ticker, last updated.
    Aggregates from the most recent signal per ticker.
    """
    signals = latest_signals(limit=200)
    prev_avgs = prev_signals_avg_by_sector()

    sector_map: dict[str, list] = {}
    for s in signals:
        sector_map.setdefault(s["sector"], []).append(s)

    result = []
    for sector_name, cfg in SECTORS.items():
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

        tickers = sorted(
            [
                {
                    "ticker":         r["ticker"],
                    "signal_score":   r["signal_score"],
                    "momentum_score": r.get("momentum_score"),
                    "volume_score":   r.get("volume_score"),
                    "rsi":            r.get("rsi"),
                    "price":          r.get("price"),
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
def gate_history(limit: int = 30):
    from backend.db import get_gate_history
    return get_gate_history(limit)


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
