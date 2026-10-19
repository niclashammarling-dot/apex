"""
Backtesting engine — optimized version with precomputed signal cache.

Key change: all indicator scores (momentum, volume, trend, RS, ATR, ETF regime,
SPY regime, VIX, prices) are computed ONCE in a vectorized pre-pass before the
simulation loop. The day loop then does O(1) dict lookups instead of repeated
pandas slicing, cutting runtime from ~35 min to ~1-2 min per backtest.

The public API (run()) is identical to the original engine — drop-in replacement.
"""
from __future__ import annotations

import hashlib
import math
import pickle
from datetime import date, timedelta
from pathlib import Path
from typing import TypedDict

import numpy as np
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
from backend.backtest.exit_rules import Bar, ParityStats, build_ohlc_cache, check_intraday_bracket
from backend.backtest.live_shape import (etf_penalty_fails, etf_return_as_live, live_overflow_increment,
                                          overflow_admits, reentry_cooloff_days)
from backend.macro_calendar import macro_status
from backend.signals import aggregator, momentum, relative_strength, trend, volume
from backend.signals.ev_kelly import compute as ev_kelly_compute
from backend.sector_caps import caps_from_factors

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
    days_held: int | None


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
    take_profit_pct:    float | None = None,
    stop_loss_pct:      float | None = None,
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
    sl_cooldown_days:   int   | None = None,   # None: live config (live_shape.live_cooloff_days)
    tp_cooldown_days:   int   | None = None,
    max_sector_exposure: float | None = None,   # None: config MAX_SECTOR_EXPOSURE
    daily_loss_cap:     float | None = None,   # None: DAILY_LOSS_CAP_PCT × initial_balance
    precomputed:        dict  | None = None,
    intraday_stops:     bool        = True,
    ratchet_from_high:  bool        = False,
    stop_slip:          tuple[float, float] | None = None,   # None: exit_rules' measured R7 values (EXIT_PARITY.md)
    intraday_tp:        bool        = True,    # R8: the TP limit leg fills intraday, as live's bracket (False: targets on the close)
    tp_first:           bool        = False,   # R8 ceiling for a bar touching both legs; False books the stop (floor)
    kelly_factor:       dict[str, float] | None = None,      # {date: k} replaces half-Kelly's 0.50 that day (H-V1, 2026-10-17)
    macro_hard_block:   bool        = False,   # also block pre-event days (engine.py's flag)
    macro_pre_event_penalty: float | None = None,   # live 0.05: score × (1 − p) on CPI/NFP eve, re-thresholded
    etf_negative_penalty: float | None = None,      # row 11, live 0.15: sector ETF below the floor → score × (1 − p) must clear the threshold
    etf_negative_floor:   float        = -1.0,      # percent, live -1.0
    overflow_quant_increment: float | None = None,  # row 14: None = live (0.05); math.inf = hard stop at max_positions
    parity_stats:       ParityStats | None = None,
    regime_layer:       bool        = False,
    row2_neutral:       bool        = False,   # 3B: L2 scored with neutral weights; allocation, caps, multiplier unchanged
    random_entries:     dict  | None = None,
) -> BacktestResult:
    tp       = take_profit_pct    if take_profit_pct    is not None else TAKE_PROFIT_PCT
    sl       = stop_loss_pct      if stop_loss_pct      is not None else STOP_LOSS_PCT
    tsl      = trailing_stop_pct
    pl_trig  = profit_lock_trigger_pct
    pl_trail = profit_lock_trail_pct
    tdays    = time_stop_days     if time_stop_days     is not None else TIME_STOP_DAYS
    l1       = lock1_threshold    if lock1_threshold    is not None else LOCK1_THRESHOLD
    max_epd  = max_entries_per_day
    max_pos  = max_positions      if max_positions      is not None else MAX_POSITIONS
    overflow_inc = overflow_quant_increment if overflow_quant_increment is not None else live_overflow_increment()

    start = date.fromisoformat(start_date)
    end   = date.fromisoformat(end_date)

    if end <= start:
        raise ValueError("end_date must be after start_date")

    # Authoritative universe, not the static config.py fallback — see
    # engine.py::run() for the full note on this defect (2026-08-18). Bound
    # before the precomputed/else split since both branches reference it.
    from backend.ticker_config import get_sectors
    SECTORS = get_sectors()

    if precomputed is not None:
        raw_data      = precomputed["raw_data"]
        ticker_sector = precomputed["ticker_sector"]
        sector_etf    = precomputed["sector_etf"]
        trading_days  = window_days(precomputed, start, end)
        price_cache   = precomputed["price_cache"]
        spy_cache     = precomputed["spy_cache"]
        etf_cache     = precomputed["etf_cache"]
        vix_cache     = precomputed["vix_cache"]
        rs_cache      = precomputed["rs_cache"] if use_leading_rs else {}
        signal_cache  = precomputed["signal_cache"]
        ohlc_cache    = precomputed.get("ohlc_cache")
        logger.info(f"Backtest (fast): using prebuilt caches — {len(trading_days)} trading days")
    else:
        logger.info(f"Backtest (fast): loading data {start} → {end}")
        lookback_start = start - timedelta(days=130)
        etf_tickers    = [cfg["etf"] for cfg in SECTORS.values()]
        all_tickers    = (
            [SPY_TICKER, "^VIX"]
            + etf_tickers
            + [t for cfg in SECTORS.values() for t in cfg["tickers"]]
        )
        raw_data = _download_all(all_tickers, lookback_start, end)
        if raw_data.empty:
            raise RuntimeError("Failed to download historical data")

        ticker_sector = {
            t: sector
            for sector, cfg in SECTORS.items()
            for t in cfg["tickers"]
        }
        sector_etf   = {sector: cfg["etf"] for sector, cfg in SECTORS.items()}
        trading_days = _trading_days(start, end, raw_data)
        logger.info(f"Backtest (fast): {len(trading_days)} trading days — building caches…")

        price_cache  = _build_price_cache(raw_data, all_tickers, trading_days)
        ohlc_cache   = build_ohlc_cache(raw_data, all_tickers)
        spy_cache    = _build_spy_cache(raw_data, trading_days)
        etf_cache    = _build_etf_cache(raw_data, etf_tickers, trading_days)
        vix_cache    = _build_vix_cache(raw_data, trading_days)
        rs_cache     = _build_rs_cache(raw_data, ticker_sector, sector_etf, trading_days) if use_leading_rs else {}
        signal_cache = _build_signal_cache(
            raw_data, ticker_sector, sector_etf,
            spy_cache, etf_cache, trading_days,
        )
        logger.info("Backtest (fast): cache built — starting simulation loop")

    # Row 11 (2026-10-18): the sector ETF return live's Lock 4 reads, per ETF and day.
    etf_ret_live: dict[tuple[str, str], float | None] = {}
    if etf_negative_penalty:
        for _etf in set(sector_etf.values()):
            if _etf and _etf in raw_data.columns.get_level_values(0):
                _closes = raw_data[_etf]["Close"]
                for d in trading_days:
                    etf_ret_live[(_etf, d.isoformat())] = etf_return_as_live(_closes, d)
    etf_penalty_blocked: list[dict] = []

    def _etf_blocked(sig: dict, day_str: str) -> bool:
        if not etf_negative_penalty or rnd is not None:
            return False
        return etf_penalty_fails(
            sig["signal_score"], etf_ret_live.get((sector_etf.get(sig["sector"], ""), day_str)),
            etf_negative_penalty, etf_negative_floor,
            max(l1, SECTOR_THRESHOLD_FLOORS.get(sig["sector"], 0.0)))

    # Regime layer (PRODUCTION_GAP rows 2, 5, 20, 21; opt-in, 2026-10-17). Built once
    # per precomputed dict and kept in it, so optimizer and walk-forward runs share it.
    regime_days: dict[str, dict] = {}
    if regime_layer:
        if precomputed is not None:
            if "regime_timeline" not in precomputed:
                precomputed["regime_timeline"] = build_regime_timeline(precomputed)
            regime_days = precomputed["regime_timeline"]["days"]
        else:
            regime_days = build_regime_timeline({
                "raw_data": raw_data, "trading_days": trading_days, "signal_cache": signal_cache,
                "ticker_sector": ticker_sector, "sector_etf": sector_etf,
            })["days"]

    # RND placebo (profit read, 2026-10-17): {"seed": int, "per_day": {date_str: k}}.
    # Entries are drawn at random from every scored ticker of the day (no threshold;
    # allocated sectors only under regime_layer), capped at k entries that day — the
    # reference run's count — with the same exits, cooloffs, caps and Kelly-from-cache
    # sizing. The row 20 multiplier is score-relative, so it belongs to the signal and
    # is not applied. Isolates signal edge from exit rules, sizing and exposure.
    import random as _random
    rnd = _random.Random(random_entries["seed"]) if random_entries else None

    # Pre-fetch earnings if needed
    earnings_dates: dict[str, set[date]] = {}
    if earnings_filter_days is not None:
        stock_tickers = [t for cfg in SECTORS.values() for t in cfg["tickers"]]
        earnings_dates = _fetch_earnings_dates(stock_tickers, start, end)

    if sl_cooldown_days is None or tp_cooldown_days is None:
        from backend.backtest.live_shape import live_cooloff_days
        _live_sl, _live_tp = live_cooloff_days()
        sl_cooldown_days = _live_sl if sl_cooldown_days is None else sl_cooldown_days
        tp_cooldown_days = _live_tp if tp_cooldown_days is None else tp_cooldown_days
    SL_COOLDOWN_DAYS = sl_cooldown_days
    TP_COOLDOWN_DAYS = tp_cooldown_days
    # Daily loss cap as live has it: 5 % of the starting balance (live $500 on $10,000,
    # demo $100 on $2,000). Until 2026-10-17 the engines used config's $100 on a
    # $10,000 simulation: 1 %, five times stricter than live.
    from backend.backtest.live_shape import DAILY_LOSS_CAP_PCT
    loss_cap   = daily_loss_cap if daily_loss_cap is not None else DAILY_LOSS_CAP_PCT * initial_balance
    sector_max = max_sector_exposure if max_sector_exposure is not None else MAX_SECTOR_EXPOSURE

    balance      = initial_balance
    open_trades: list[dict] = []
    closed_trades: list[TradeRecord] = []
    equity_curve = [{"date": start_date, "balance": round(balance, 2)}]
    daily_losses: dict[str, float] = {}
    sl_cooldown:  dict[str, date]  = {}

    for today in trading_days:
        today_str = today.isoformat()

        # 1. Check exits
        closed_today = _check_exits_fast(
            open_trades, price_cache, today, today_str, tp, sl, tdays, tsl, pl_trig, pl_trail,
            ohlc_cache=ohlc_cache if intraday_stops else None,
            parity_stats=parity_stats,
            ratchet_from_high=ratchet_from_high,
            stop_slip=stop_slip,
            intraday_tp=intraday_tp,
            tp_first=tp_first,
        )
        for ct in closed_today:
            trade  = ct["_trade"]
            record = ct["record"]
            open_trades.remove(trade)
            closed_trades.append(record)
            balance += trade["amount"] + (record["pnl"] or 0)
            if record["outcome"] in ("LOSS", "EXPIRED") and (record["pnl"] or 0) < 0:
                daily_losses[today_str] = daily_losses.get(today_str, 0) + abs(record["pnl"] or 0)
            _cool = reentry_cooloff_days(record["exit_reason"], record["outcome"],
                                         SL_COOLDOWN_DAYS, TP_COOLDOWN_DAYS)
            if _cool > 0:
                sl_cooldown[trade["ticker"]] = today + timedelta(days=_cool)

        # 2. Generate candidates from cache
        spy       = spy_cache.get(today_str, {"regime": 0.0, "return_20d": 0.0})
        wins_so_far   = sum(1 for t in closed_trades if t["outcome"] == "WIN")
        closed_so_far = len(closed_trades)
        rolling_wr = (wins_so_far / closed_so_far) if closed_so_far >= WIN_RATE_MIN_TRADES else None

        day_regime = regime_days.get(today_str) if regime_layer else None
        candidates = _get_candidates_from_cache(
            signal_cache, ticker_sector, sector_etf, etf_cache,
            today_str, l1 if rnd is None else None, spy["regime"], rolling_wr,
            regime_state=("neutral" if row2_neutral else day_regime["bucket"]) if day_regime else None,
            sector_allocation=day_regime["allocation"] if day_regime else None,
        )
        if rnd is not None:
            rnd.shuffle(candidates)
        # Rows 7 / 10 (2026-10-17): Lock 1 B2's macro blackout, as engine.py and live —
        # FOMC −2 days through the decision day and CPI/NFP release days block entries;
        # the eve of CPI/NFP takes the pre-event penalty and must clear the threshold again.
        macro_day = macro_status(today)[0]
        if macro_day == "hard_block" or (macro_hard_block and macro_day == "pre_event"):
            candidates = []
        elif macro_day == "pre_event" and macro_pre_event_penalty is not None and rnd is None:
            candidates = [{**sig, "signal_score": round(sig["signal_score"] * (1.0 - macro_pre_event_penalty), 4),
                           "raw_score": sig["signal_score"]}
                          for sig in candidates
                          if sig["signal_score"] * (1.0 - macro_pre_event_penalty)
                          >= max(l1, SECTOR_THRESHOLD_FLOORS.get(sig["sector"], 0.0))]
        # Row 21: the day's cap weights applied to this run's base cap.
        day_caps = caps_from_factors(sector_max, day_regime["cap_factors"]) if day_regime else {}

        # 3. Attempt entries
        open_tickers     = {t["ticker"] for t in open_trades}
        sector_exposure  = _sector_exposure(open_trades, balance)
        daily_loss_today = daily_losses.get(today_str, 0.0)
        entries_today    = 0

        vix_blocked = (
            vix_threshold is not None
            and vix_cache.get(today_str, 0.0) >= vix_threshold   # live: lock1_eligibility `vix >= threshold`
        )

        # Row 20: live sizes each queued trade by n·s/Σs within its sector over the
        # cycle's queued set. The engine's queued set is the day's candidates that
        # pass the per-ticker skips (held, cooloff, earnings, leading RS) — the ones
        # live would evaluate; capacity limits apply later, in the loop.
        size_mult: dict[str, float] = {}
        if day_regime and not vix_blocked and rnd is None:
            from backend.regime.regime_bayes import RegimeBayes
            queued: dict[str, list[tuple[str, float]]] = {}
            for sig in candidates:
                tk = sig["ticker"]
                if tk in open_tickers or sl_cooldown.get(tk, date.min) > today:
                    continue
                if earnings_filter_days is not None and any(
                        today <= ed <= today + timedelta(days=earnings_filter_days)
                        for ed in earnings_dates.get(tk, set())):
                    continue
                if use_leading_rs and not rs_cache.get((tk, today_str), True):
                    continue
                if _etf_blocked(sig, today_str):
                    continue
                queued.setdefault(sig["sector"], []).append((tk, sig["signal_score"]))
            size_mult = RegimeBayes.size_multipliers(day_regime["allocation"], queued)

        day_epd = max_epd if rnd is None else random_entries["per_day"].get(today_str, 0)
        ordered = candidates if rnd is not None else sorted(candidates, key=lambda s: s["signal_score"], reverse=True)
        for sig in ([] if vix_blocked else ordered):
            # Row 14: live has no hard cap; each slot over the roof raises the bar (live_shape).
            if not overflow_admits(sig.get("raw_score", sig["signal_score"]), len(open_trades), max_pos,
                                   max(l1, SECTOR_THRESHOLD_FLOORS.get(sig["sector"], 0.0)), overflow_inc):
                continue
            if day_epd is not None and entries_today >= day_epd:
                break
            if sig["ticker"] in open_tickers:
                continue
            if sl_cooldown.get(sig["ticker"], date.min) > today:
                continue
            if daily_loss_today >= loss_cap:
                break

            if earnings_filter_days is not None:
                ticker_earnings = earnings_dates.get(sig["ticker"], set())
                if any(today <= ed <= today + timedelta(days=earnings_filter_days) for ed in ticker_earnings):
                    continue

            if use_leading_rs and not rs_cache.get((sig["ticker"], today_str), True):
                continue

            # Row 11: live's ETF negative penalty, after Lock 4 (chain.py:156-187); gates only.
            if _etf_blocked(sig, today_str):
                _er = etf_ret_live[(sector_etf[sig["sector"]], today_str)]
                etf_penalty_blocked.append({
                    "date": today_str, "ticker": sig["ticker"], "sector": sig["sector"],
                    "original_score":  round(sig["signal_score"], 4),
                    "penalized_score": round(sig["signal_score"] * (1.0 - etf_negative_penalty), 4),
                    "etf_ret_pct":     round(_er * 100, 2),
                })
                continue

            sector    = sig["sector"]
            _kelly    = sig["kelly_size"]
            if kelly_factor is not None and _kelly > 0:
                # ev_kelly sizes at f* × 0.50; a day's factor k replaces the 0.50 (missing day → 0.50).
                _kelly = _kelly * kelly_factor.get(today_str, 0.5) / 0.5
            alloc_pct = min(_kelly if _kelly > 0 else 0.10, MAX_POSITION_SIZE)
            if day_regime:
                _scale = size_mult.get(sig["ticker"], 1.0)
                if _scale != 1.0:
                    alloc_pct = min(round(alloc_pct * _scale, 4), MAX_POSITION_SIZE)
            amount    = balance * alloc_pct
            projected = sector_exposure.get(sector, 0.0) + (amount / balance)
            sector_cap = day_caps.get(sector, sector_max) if day_regime else sector_max
            if projected > sector_cap:
                continue

            entry_price = price_cache.get((sig["ticker"], today_str))
            if entry_price is None or entry_price <= 0:
                continue
            if amount < 50:
                continue

            shares = amount / entry_price
            balance -= amount

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

        # Equity snapshot
        mtm = _mark_to_market_fast(open_trades, price_cache, today_str)
        equity_curve.append({"date": today_str, "balance": round(balance + mtm, 2)})

    # Force-close remaining positions
    last_day = trading_days[-1] if trading_days else end
    last_str  = last_day.isoformat()
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
            ticker=trade["ticker"], sector=trade["sector"],
            entry_date=trade["entry_date"], exit_date=end_date,
            entry_price=trade["entry_price"], exit_price=last_price,
            shares=trade["shares"], amount=trade["amount"],
            pnl=round(pnl, 2), pnl_pct=round(pnl_pct, 4),
            outcome=outcome, exit_reason="END_OF_BACKTEST",
            signal_score=trade["signal_score"],
            days_held=_trading_days_count(trade["entry_date"], end_date),
        ))

    result = _compute_metrics(
        start_date, end_date, initial_balance, balance,
        closed_trades, equity_curve, raw_data, trading_days,
    )
    result["etf_penalty_blocked"] = etf_penalty_blocked
    return result


# ── Signal cache persistence ──────────────────────────────────────────────────

_SIGNAL_CACHE_DIR = Path(__file__).parent.parent.parent / "data" / "backtest_cache"


def _signal_code_hash() -> str:
    """
    Hash of every source file whose logic decides a cached signal value: this
    engine (the cache builders) and the signal modules it calls. A change to
    any of them yields a new cache key, so an edit reaches the optimizer's next
    run instead of being masked by a pickle built under the old code. Added
    2026-09-16 after a SPY return_20d fix in _build_spy_cache was silently
    ignored by precompute() — the key had no notion of code version.
    """
    import backend.signals as _sig
    sig_dir = Path(_sig.__file__).parent
    files = [Path(__file__)] + sorted(sig_dir.glob("*.py"))
    h = hashlib.md5()
    for f in files:
        h.update(f.read_bytes())
    return h.hexdigest()[:8]


def _signal_cache_key(all_tickers: list[str], start: date, end: date) -> str:
    """
    Hash key for the persisted signal cache.
    Includes tickers, date range, EXCLUDED_SECTORS, and a hash of the signal
    code, so an exclusion change or a signal edit invalidates the cache rather
    than loading values computed under different rules.
    """
    excl = ",".join(sorted(EXCLUDED_SECTORS.keys()))
    key_str = ",".join(sorted(all_tickers)) + f"|{start}|{end}|excl:{excl}|code:{_signal_code_hash()}"
    return hashlib.md5(key_str.encode()).hexdigest()[:12]


def _load_signal_cache(key: str) -> dict | None:
    path = _SIGNAL_CACHE_DIR / f"sig_{key}.pkl"
    if not path.exists():
        return None
    try:
        with open(path, "rb") as f:
            cache = pickle.load(f)
        logger.info(f"Signal cache loaded from disk ({len(cache)} entries, {path.name})")
        return cache
    except Exception as e:
        logger.warning(f"Signal cache load failed, rebuilding: {e}")
        path.unlink(missing_ok=True)
        return None


def _save_signal_cache(cache: dict, key: str) -> None:
    _SIGNAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _SIGNAL_CACHE_DIR / f"sig_{key}.pkl"
    try:
        with open(path, "wb") as f:
            pickle.dump(cache, f, protocol=pickle.HIGHEST_PROTOCOL)
        logger.info(f"Signal cache saved to {path.name}")
    except Exception as e:
        logger.warning(f"Signal cache save failed (continuing without persistence): {e}")


# ── Precompute (optimizer helper) ────────────────────────────────────────────

def window_days(precomputed: dict, start: date, end: date) -> list[date]:
    """The precompute's trading days inside [start, end] (2026-10-19).

    run() used to loop over every day of the precompute whatever dates it was given, so
    a fold run on a full-window precompute would have returned full-window results under
    the fold's label (no caller did; found writing the rerun battery). A window inside
    the precompute is sliced. A window reaching outside it is refused: build
    precompute(start, end) for that window — widening is explicit, never a silent rebuild
    with other caches. The regime timeline is still built on the whole precompute
    (build_regime_timeline reads precomputed["trading_days"]), so its warm-up and state do
    not depend on the slice.
    """
    days = precomputed["trading_days"]
    w0, w1 = precomputed.get("window") or (days[0].isoformat(), days[-1].isoformat())
    if start < date.fromisoformat(w0) or end > date.fromisoformat(w1):
        raise ValueError(f"window {start}..{end} reaches outside the precompute {w0}..{w1}: "
                         f"build precompute('{start}', '{end}') for it")
    return [d for d in days if start <= d <= end]


def precompute(start_date: str, end_date: str) -> dict:
    """
    Build all caches once for a date range.
    Pass the result to run(..., precomputed=...) to skip rebuilding on every call.
    Intended for the optimizer, which runs many experiments over the same date range.
    """
    start = date.fromisoformat(start_date)
    end   = date.fromisoformat(end_date)

    from backend.ticker_config import get_sectors
    SECTORS = get_sectors()

    lookback_start = start - timedelta(days=130)
    etf_tickers    = [cfg["etf"] for cfg in SECTORS.values()]
    all_tickers    = (
        [SPY_TICKER, "^VIX"]
        + etf_tickers
        + [t for cfg in SECTORS.values() for t in cfg["tickers"]]
    )
    raw_data = _download_all(all_tickers, lookback_start, end)
    if raw_data.empty:
        raise RuntimeError("Failed to download historical data for precompute")

    ticker_sector = {
        t: sector
        for sector, cfg in SECTORS.items()
        for t in cfg["tickers"]
    }
    sector_etf   = {sector: cfg["etf"] for sector, cfg in SECTORS.items()}
    trading_days = _trading_days(start, end, raw_data)

    logger.info(f"precompute: {len(trading_days)} trading days — building caches…")

    price_cache = _build_price_cache(raw_data, all_tickers, trading_days)
    ohlc_cache  = build_ohlc_cache(raw_data, all_tickers)
    spy_cache   = _build_spy_cache(raw_data, trading_days)
    etf_cache   = _build_etf_cache(raw_data, etf_tickers, trading_days)
    vix_cache   = _build_vix_cache(raw_data, trading_days)
    rs_cache    = _build_rs_cache(raw_data, ticker_sector, sector_etf, trading_days)

    sig_key      = _signal_cache_key(all_tickers, start, end)
    signal_cache = _load_signal_cache(sig_key)
    if signal_cache is None:
        logger.info("precompute: building signal cache (not cached)…")
        signal_cache = _build_signal_cache(
            raw_data, ticker_sector, sector_etf,
            spy_cache, etf_cache, trading_days,
        )
        _save_signal_cache(signal_cache, sig_key)

    logger.info(f"precompute: complete — {len(signal_cache)} signal cache entries")
    return {
        "window":        (start_date, end_date),
        "raw_data":      raw_data,
        "ticker_sector": ticker_sector,
        "sector_etf":    sector_etf,
        "trading_days":  trading_days,
        "price_cache":   price_cache,
        "ohlc_cache":    ohlc_cache,
        "spy_cache":     spy_cache,
        "etf_cache":     etf_cache,
        "vix_cache":     vix_cache,
        "rs_cache":      rs_cache,
        "signal_cache":  signal_cache,
    }


# ── Cache builders ────────────────────────────────────────────────────────────

def _build_price_cache(
    raw_data: pd.DataFrame,
    tickers: list[str],
    trading_days: list[date],
) -> dict[tuple[str, str], float]:
    """
    Build {(ticker, date_str): close_price} for every ticker × trading day.
    Uses vectorized forward-fill so gaps (halts, weekends) resolve correctly.
    """
    cache: dict[tuple[str, str], float] = {}
    date_strs = [d.isoformat() for d in trading_days]

    for ticker in tickers:
        try:
            if ticker not in raw_data.columns.get_level_values(0):
                continue
            closes = raw_data[ticker]["Close"].dropna()
            # Forward-fill to handle missing trading days
            closes = closes.reindex(
                pd.DatetimeIndex([pd.Timestamp(d) for d in trading_days]),
                method="ffill",
            )
            for d_str, price in zip(date_strs, closes):
                if not math.isnan(price):
                    cache[(ticker, d_str)] = float(price)
        except Exception:
            continue
    return cache


def _build_spy_cache(
    raw_data: pd.DataFrame,
    trading_days: list[date],
) -> dict[str, dict]:
    """Build {date_str: {"regime": float, "return_20d": float}}."""
    cache: dict[str, dict] = {}
    try:
        closes = raw_data[SPY_TICKER]["Close"].dropna()
        ma50   = closes.rolling(50).mean()
        # Production (fetcher_yahoo._spy_context) and engine.py compute
        # close.iloc[-1] / close.iloc[-20] - 1 — a 19-period return under the
        # "20d" name. Match it exactly: pct_change(20) is a different number and
        # was the first undocumented engine divergence CHECK 76 found (2026-09-16).
        ret20  = closes / closes.shift(19) - 1
        for d in trading_days:
            ts = pd.Timestamp(d)
            try:
                price  = float(closes.asof(ts))
                ma     = float(ma50.asof(ts))
                r20    = float(ret20.asof(ts))
                regime = 0.03 if price > ma else -0.03
                cache[d.isoformat()] = {"regime": regime, "return_20d": r20 if not math.isnan(r20) else 0.0}
            except Exception:
                cache[d.isoformat()] = {"regime": 0.0, "return_20d": 0.0}
    except Exception:
        for d in trading_days:
            cache[d.isoformat()] = {"regime": 0.0, "return_20d": 0.0}
    return cache


def _build_etf_cache(
    raw_data: pd.DataFrame,
    etf_tickers: list[str],
    trading_days: list[date],
) -> dict[tuple[str, str], float]:
    """Build {(etf, date_str): multiplier} where multiplier is 1.0 or 0.85."""
    cache: dict[tuple[str, str], float] = {}
    for etf in etf_tickers:
        try:
            if etf not in raw_data.columns.get_level_values(0):
                continue
            closes = raw_data[etf]["Close"].dropna()
            ma20   = closes.rolling(20).mean()
            for d in trading_days:
                ts = pd.Timestamp(d)
                try:
                    price = float(closes.asof(ts))
                    ma    = float(ma20.asof(ts))
                    cache[(etf, d.isoformat())] = 1.0 if price >= ma else 0.85
                except Exception:
                    cache[(etf, d.isoformat())] = 1.0
        except Exception:
            for d in trading_days:
                cache[(etf, d.isoformat())] = 1.0
    return cache


def _build_vix_cache(
    raw_data: pd.DataFrame,
    trading_days: list[date],
) -> dict[str, float]:
    """Build {date_str: vix_close}."""
    cache: dict[str, float] = {}
    try:
        closes = raw_data["^VIX"]["Close"].dropna()
        for d in trading_days:
            try:
                cache[d.isoformat()] = float(closes.asof(pd.Timestamp(d)))
            except Exception:
                cache[d.isoformat()] = 0.0
    except Exception:
        pass
    return cache


def _build_rs_cache(
    raw_data: pd.DataFrame,
    ticker_sector: dict[str, str],
    sector_etf: dict[str, str],
    trading_days: list[date],
) -> dict[tuple[str, str], bool]:
    """Build {(ticker, date_str): bool} for leading RS filter."""
    cache: dict[tuple[str, str], bool] = {}
    for ticker, sector in ticker_sector.items():
        etf = sector_etf.get(sector, "")
        if not etf:
            for d in trading_days:
                cache[(ticker, d.isoformat())] = True
            continue
        try:
            if ticker not in raw_data.columns.get_level_values(0):
                continue
            if etf not in raw_data.columns.get_level_values(0):
                continue
            t_closes   = raw_data[ticker]["Close"].dropna()
            etf_closes = raw_data[etf]["Close"].dropna()
            t_ret5   = t_closes.pct_change(5)
            etf_ret5 = etf_closes.pct_change(5)
            for d in trading_days:
                ts = pd.Timestamp(d)
                try:
                    tr = float(t_ret5.asof(ts))
                    er = float(etf_ret5.asof(ts))
                    cache[(ticker, d.isoformat())] = (
                        tr > er if not (math.isnan(tr) or math.isnan(er)) else True
                    )
                except Exception:
                    cache[(ticker, d.isoformat())] = True
        except Exception:
            for d in trading_days:
                cache[(ticker, d.isoformat())] = True
    return cache


def _build_signal_cache(
    raw_data: pd.DataFrame,
    ticker_sector: dict[str, str],
    sector_etf: dict[str, str],
    spy_cache: dict[str, dict],
    etf_cache: dict[tuple[str, str], float],
    trading_days: list[date],
) -> dict[tuple[str, str], dict]:
    """
    Build {(ticker, date_str): {"signal_score", "kelly_size", "atr_pct"}}
    for every ticker × trading day.

    Key optimisation: for each ticker, compute the full indicator time series
    once as pandas Series, then read off values per day — no repeated slicing.
    Rolling_win_rate is not available at cache-build time (it's updated live
    during simulation), so ev_kelly is called with rolling_win_rate=None here.
    The live loop can apply a small correction factor if needed, but in practice
    the difference is negligible for Sharpe optimisation purposes.
    """
    cache: dict[tuple[str, str], dict] = {}
    total = len(ticker_sector)

    for i, (ticker, sector) in enumerate(ticker_sector.items(), 1):
        if i % 10 == 0:
            logger.debug(f"Signal cache: {i}/{total} tickers")
        if sector in EXCLUDED_SECTORS:
            continue
        etf = sector_etf.get(sector, "")
        try:
            if ticker not in raw_data.columns.get_level_values(0):
                continue
            # The ticker's own bars only: a row with no Close (a holiday row carried by
            # another symbol) froze ATR at its pre-holiday value via asof() for 14
            # sessions and sat in every 90-row window (2026-10-17).
            df_full = raw_data[ticker].dropna(subset=["Close"]).copy()
            if df_full.empty or len(df_full) < 55:
                continue

            # Compute vectorized indicators over the full series
            # ATR (14-period)
            atr_series = (df_full["High"] - df_full["Low"]).rolling(14).mean()

            for d in trading_days:
                ts = pd.Timestamp(d)
                date_str = d.isoformat()
                try:
                    # Slice up to today — the signal modules expect a trailing window
                    mask = df_full.index <= ts
                    df   = df_full[mask].tail(90)
                    if len(df) < 55:
                        continue

                    spy_ret20 = spy_cache.get(date_str, {}).get("return_20d", 0.0)
                    spy_reg   = spy_cache.get(date_str, {}).get("regime", 0.0)
                    etf_mult  = etf_cache.get((etf, date_str), 1.0) if etf else 1.0

                    mom = momentum.compute(df)
                    if mom.get("rsi_blocked"):
                        continue
                    vol = volume.compute(df)
                    trd = trend.compute(df)
                    rs  = relative_strength.compute(df, spy_ret20)

                    atr_val = float(atr_series.asof(ts)) if not math.isnan(float(atr_series.asof(ts) or 0)) else 0.0
                    atr_pct = float(atr_val / mom["price"]) if mom["price"] and mom["price"] > 0 else 0.0

                    evk   = ev_kelly_compute(spy_reg, rolling_win_rate=None, atr_pct=atr_pct)
                    score = aggregator.compute(
                        mom["momentum_score"], vol["volume_score"],
                        trd["trend_score"], rs["rs_score"],
                    )
                    score = round(min(max(score * etf_mult, 0.0), 1.0), 4)
                    kelly_scaled = round(evk["kelly_size"] * min(1.0, max(0.5, score)), 4)

                    cache[(ticker, date_str)] = {
                        "signal_score": score,
                        "kelly_size":   kelly_scaled,
                        "atr_pct":      atr_pct,
                        # Weight-independent inputs (2026-10-17): the regime layer
                        # re-weights them per day; signal_score stays the neutral value.
                        "components":   (mom["momentum_score"], vol["volume_score"],
                                         trd["trend_score"], rs["rs_score"], etf_mult),
                    }
                except Exception:
                    continue
        except Exception:
            continue

    logger.info(f"Signal cache: {len(cache)} entries for {total} tickers × {len(trading_days)} days")
    return cache


# ── Regime layer (PRODUCTION_GAP rows 2, 5, 20, 21; 2026-10-17) ──────────────

# Uniform IPO in the engine (no historical EDGAR feed; never call IpoSentiment here —
# tests/test_engine_regime_layer.py guards it). Any share in (0, 0.1) is LR 1.0 under
# _lr_ipo_cluster's int(share × 10) step for every sector count; 1/n is not (n ≤ 10 → 1.2).
REGIME_IPO_NEUTRAL_SHARE = 0.05
# Sessions of regime history before the first backtest day (ruled 2026-10-16: ≥ 30 at
# decay 0.90, residual 0.9^30 ≈ 4 % of the cold start). The precompute lookback
# (130 calendar days, minus the 55 rows the signal modules need) gives about 34.
REGIME_WARMUP_MIN_SESSIONS = 30


def _regime_score(components: tuple, regime_state: str) -> float:
    """The cached signal_score's formula with that bucket's aggregator weights."""
    mom, vol, trd, rs, etf_mult = components
    score = aggregator.compute(mom, vol, trd, rs, regime_state=regime_state)
    return round(min(max(score * etf_mult, 0.0), 1.0), 4)


def build_regime_timeline(precomputed: dict) -> dict:
    """
    The regime layer's daily inputs for every backtest day, computed once.

    Sequential, as live: state(t−1) → bucket(t−1) → scores(t) under its weights →
    snapshot(t) = per-sector mean score → RegimeBayes.step(t). Decisions on day t
    read the regime from t−1 (live's EOD for t−1 runs before t's open) and the
    snapshot from t (live's caps read the latest intraday snapshot):
      bucket      row 2  aggregator weights for t's scores
      allocation  row 5  L1-A filter (> 0), row 20 multiplier gate
      cap_factors row 21 sector_caps.cap_factors(snapshot(t), streaks ≤ t, result(t−1));
                         the run applies its own base cap (caps_from_factors)
    Cold start from RegimeState.cold (live's uniform fallback), warm-up on the
    precompute lookback before the first day. Pure: RegimeBayes(persisted=False).
    Returns {"days": {date_str: {...}}, "warmup_sessions": int}.
    """
    from collections import defaultdict
    from backend.regime.regime_bayes import RegimeBayes, RegimeState
    from backend.sector_caps import cap_factors
    from backend.sector_regime import _compute_sector_stats

    raw_data      = precomputed["raw_data"]
    trading_days  = precomputed["trading_days"]
    signal_cache  = precomputed["signal_cache"]
    ticker_sector = precomputed["ticker_sector"]
    sector_etf    = precomputed["sector_etf"]
    if not trading_days:
        return {"days": {}, "warmup_sessions": 0}
    start = trading_days[0]

    # Warm-up: the lookback days before the window, scored by the same builder.
    warm_days  = _trading_days(date.min, start - timedelta(days=1), raw_data)
    warm_cache = _build_signal_cache(
        raw_data, ticker_sector, sector_etf,
        _build_spy_cache(raw_data, warm_days),
        _build_etf_cache(raw_data, list(sector_etf.values()), warm_days),
        warm_days,
    )
    scored_warm = sorted({date.fromisoformat(d) for (_, d) in warm_cache})
    if len(scored_warm) < REGIME_WARMUP_MIN_SESSIONS:
        raise ValueError(f"regime warm-up has {len(scored_warm)} scored sessions before {start}, "
                         f"needs ≥ {REGIME_WARMUP_MIN_SESSIONS}: extend the precompute lookback")

    # The universe the caches were built from (precompute: get_sectors()), so the
    # regime and the signals can never disagree on it.
    sectors_cfg: dict[str, dict] = {s: {"tickers": [], "etf": e} for s, e in sector_etf.items()}
    for ticker, sector in ticker_sector.items():
        sectors_cfg[sector]["tickers"].append(ticker)
    rb    = RegimeBayes(sectors_cfg, {s: c["etf"] for s, c in sectors_cfg.items()}, {}, persisted=False)
    state = RegimeState.cold(sectors_cfg)
    ipo   = {s: REGIME_IPO_NEUTRAL_SHARE for s in sectors_cfg}
    index = raw_data.index
    history: list[dict] = []
    days: dict[str, dict] = {}

    for d in scored_warm + list(trading_days):
        ds     = d.isoformat()
        prev   = state.last_result
        bucket = (prev.regime_state if prev else None) or "neutral"
        cache  = signal_cache if d >= start else warm_cache
        by_sector: dict[str, list[float]] = defaultdict(list)
        for ticker, sector in ticker_sector.items():
            e = cache.get((ticker, ds))
            if e is not None:
                by_sector[sector].append(_regime_score(e["components"], bucket))
        snapshot = {s: round(sum(v) / len(v), 4) for s, v in by_sector.items()}
        history.extend({"sector": s, "day": ds, "avg_score": v} for s, v in snapshot.items())
        # Live's streaks read get_sector_history(days=365): the same window here.
        horizon = (d - timedelta(days=365)).isoformat()
        while history and history[0]["day"] <= horizon:
            history.pop(0)

        if d >= start:
            days[ds] = {
                "regime_from": prev.date if prev else None,   # always the previous session
                "bucket":     bucket,
                "allocation": dict(prev.allocation) if prev else {},
                "cap_factors": cap_factors(snapshot, _compute_sector_stats(history), prev),
            }

        # OHLCV as the as_of path sees it: 90 calendar days ending d.
        lo = index.searchsorted(pd.Timestamp(d - timedelta(days=90)), side="right")
        hi = index.searchsorted(pd.Timestamp(d), side="right")
        _, state, _ = rb.step(state, d, raw_data.iloc[lo:hi], snapshot, ipo)

    logger.info(f"Regime timeline: {len(days)} days, warm-up {len(scored_warm)} sessions")
    return {"days": days, "warmup_sessions": len(scored_warm)}


# ── Fast simulation helpers ───────────────────────────────────────────────────

def _get_candidates_from_cache(
    signal_cache: dict,
    ticker_sector: dict[str, str],
    sector_etf: dict[str, str],
    etf_cache: dict,
    today_str: str,
    lock1_threshold: float | None,
    spy_regime: float,
    rolling_win_rate: float | None,
    regime_state: str | None = None,
    sector_allocation: dict[str, float] | None = None,
) -> list[dict]:
    """Return scored candidates above threshold using precomputed cache.

    regime_state (row 2): re-weight the cached components with that bucket's
    aggregator weights instead of reading the neutral signal_score.
    sector_allocation (row 5, L1-A): only sectors with allocation > 0 pass.
    Both None = the engine without the regime layer, unchanged.

    signal_score and atr_pct come from the cache (expensive, stateless).
    kelly_size is recomputed live from spy_regime and rolling_win_rate so the
    sizer sees the actual realized win rate rather than the None baked into the
    cache at build time.
    """
    candidates = []
    for ticker, sector in ticker_sector.items():
        if sector in EXCLUDED_SECTORS:
            continue
        if sector_allocation is not None and sector_allocation.get(sector, 0.0) <= 0:
            continue
        sig = signal_cache.get((ticker, today_str))
        if sig is None:
            continue
        score = sig["signal_score"] if regime_state is None else _regime_score(sig["components"], regime_state)
        # lock1_threshold None: no threshold at all (the RND pool), floors included.
        if lock1_threshold is not None:
            effective_threshold = max(lock1_threshold, SECTOR_THRESHOLD_FLOORS.get(sector, 0.0))
            if score < effective_threshold:
                continue
        atr_pct = sig["atr_pct"]
        evk = ev_kelly_compute(spy_regime, rolling_win_rate=rolling_win_rate, atr_pct=atr_pct)
        kelly_size = round(evk["kelly_size"] * min(1.0, max(0.5, score)), 4)
        candidates.append({
            "ticker":       ticker,
            "sector":       sector,
            "signal_score": score,
            "kelly_size":   kelly_size,
            "atr_pct":      atr_pct,
        })
    return candidates


def _check_exits_fast(
    open_trades: list[dict],
    price_cache: dict[tuple[str, str], float],
    today: date,
    today_str: str,
    take_profit_pct: float,
    stop_loss_pct: float,
    time_stop_days: int,
    trailing_stop_pct:       float | None = None,
    profit_lock_trigger_pct: float | None = None,
    profit_lock_trail_pct:   float | None = None,
    ohlc_cache: dict[tuple[str, str], Bar] | None = None,
    parity_stats: ParityStats | None = None,
    ratchet_from_high: bool = False,
    stop_slip: tuple[float, float] | None = None,
    intraday_tp: bool = True,
    tp_first: bool = False,
) -> list[dict]:
    # See engine.py::_check_exits for the full derivation of this fix
    # (2026-08-17/18) — this function carried the identical defect.
    if bool(profit_lock_trigger_pct) != bool(profit_lock_trail_pct):
        raise ValueError(
            f"profit_lock_trigger_pct and profit_lock_trail_pct must both be "
            f"set or both be None — got trigger={profit_lock_trigger_pct!r} "
            f"trail={profit_lock_trail_pct!r}"
        )

    closed = []
    for trade in list(open_trades):
        current = price_cache.get((trade["ticker"], today_str))
        if current is None:
            continue

        eff_tp = trade.get("tp", take_profit_pct)
        eff_sl = trade.get("sl", stop_loss_pct)

        # ── Intraday bracket: stop leg R1-R7, take-profit leg R8 (EXIT_PARITY.md)
        # Tested BEFORE the close-based peak update on purpose: R4 measures the
        # trail against the peak as it stood at the START of the bar, because
        # live's ratchet is a 5-minute poll that usually never sees the bar's
        # high. Moving this below the peak update silently converts the rule to
        # its optimistic bound.
        if ohlc_cache is not None:
            hit = check_intraday_bracket(
                trade, ohlc_cache.get((trade["ticker"], today_str)),
                eff_sl, eff_tp, trailing_stop_pct,
                profit_lock_trigger_pct, profit_lock_trail_pct,
                today_str=today_str, stats=parity_stats,
                ratchet_from_high=ratchet_from_high,
                **({} if stop_slip is None else {"stop_slip": stop_slip}),
                intraday_tp=intraday_tp, tp_first=tp_first,
            )
            if hit is not None:
                fill, reason, outcome = hit
                # pnl from the unrounded fill; round only for the record.
                pnl_pct = (fill - trade["entry_price"]) / trade["entry_price"]
                closed.append({"_trade": trade, "record": TradeRecord(
                    ticker=trade["ticker"], sector=trade["sector"],
                    entry_date=trade["entry_date"], exit_date=today_str,
                    entry_price=trade["entry_price"], exit_price=round(fill, 4),
                    shares=trade["shares"], amount=trade["amount"],
                    pnl=round(trade["amount"] * pnl_pct, 2),
                    pnl_pct=round(pnl_pct, 4),
                    outcome=outcome, exit_reason=reason,
                    signal_score=trade["signal_score"],
                    days_held=_trading_days_count(trade["entry_date"], today_str),
                )})
                continue

        if current > trade.get("peak_price", trade["entry_price"]):
            trade["peak_price"] = current

        pnl_pct   = (current - trade["entry_price"]) / trade["entry_price"]
        days_held = _trading_days_count(trade["entry_date"], today_str)
        peak      = trade.get("peak_price", trade["entry_price"])
        peak_gain = (peak - trade["entry_price"]) / trade["entry_price"]

        # Fixed SL is the unconditional floor until peak gain clears the
        # profit-lock trigger — matches wallet.py's actual exit chain, not
        # the pre-fix trailing_stop_pct-replaces-SL behaviour this function
        # carried until 2026-08-18.
        ratchet_active = bool(
            profit_lock_trigger_pct and profit_lock_trail_pct
            and peak_gain >= profit_lock_trigger_pct
        )

        # Live order — wallet.py::check_exits is TP -> SL -> profit-lock trail
        # -> TIME, and its docstring calls the fixed SL "a hard floor, always
        # active". This ladder used to test `ratchet_active` BEFORE the fixed
        # stop, which made the SL branch unreachable for the rest of a trade's
        # life once peak gain had cleared the trigger. Restored 2026-09-25;
        # see EXIT_PARITY.md R5 for the before/after. Same `elif`-hides-a-branch
        # shape as CHECK 65's pin/divergence arms.
        legacy_tsl = (
            trailing_stop_pct is not None
            and not (profit_lock_trigger_pct and profit_lock_trail_pct)
        )
        if pnl_pct >= eff_tp:
            reason, outcome = "TP", "WIN"
        elif pnl_pct <= -eff_sl:
            reason, outcome = "SL", "LOSS"
        elif ratchet_active and (peak - current) / peak >= profit_lock_trail_pct:
            reason, outcome = "TSL", "WIN" if pnl_pct >= 0 else "LOSS"
        elif legacy_tsl and (peak - current) / peak >= trailing_stop_pct:
            reason, outcome = "TSL", "WIN" if pnl_pct >= 0 else "LOSS"
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
            days_held=days_held,
        )
        closed.append({"_trade": trade, "record": record})
    return closed


def _mark_to_market_fast(
    open_trades: list[dict],
    price_cache: dict[tuple[str, str], float],
    today_str: str,
) -> float:
    total = 0.0
    for t in open_trades:
        current = price_cache.get((t["ticker"], today_str))
        total += t["amount"] * (current / t["entry_price"]) if current else t["amount"]
    return total


# ── Shared helpers (unchanged from original engine) ───────────────────────────

def _download_all(tickers: list[str], start: date, end: date) -> pd.DataFrame:
    import hashlib
    from pathlib import Path

    cache_dir = Path(__file__).parent.parent.parent / "data" / "backtest_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    key_str   = ",".join(sorted(tickers)) + f"|{start}|{end}"
    key_hash  = hashlib.md5(key_str.encode()).hexdigest()[:12]
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
            logger.warning(f"Backtest cache write failed: {e}")
    return df


def _fetch_earnings_dates(tickers: list[str], start: date, end: date) -> dict[str, set[date]]:
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
    # Sessions only (2026-10-17): the union index carries rows where only ^VIX has a
    # bar (US holidays: 2026-05-25, 2026-09-07). Live never sees them; here they were
    # simulated as trading days and put a NaN row into every ticker's indicator window.
    idx = raw_data.index
    if SPY_TICKER in raw_data.columns.get_level_values(0):
        idx = raw_data[SPY_TICKER]["Close"].dropna().index
    return [d.date() for d in idx if start <= d.date() <= end]


def _sector_exposure(open_trades: list[dict], balance: float) -> dict[str, float]:
    exp: dict[str, float] = {}
    for t in open_trades:
        exp[t["sector"]] = exp.get(t["sector"], 0.0) + (t["amount"] / balance)
    return exp


def _trading_days_count(start_iso: str, end_iso: str) -> int:
    try:
        s = date.fromisoformat(start_iso)
        e = date.fromisoformat(end_iso)
        return max(0, len(pd.bdate_range(start=s, end=e)) - 1)
    except Exception:
        return 0


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

    try:
        days  = (date.fromisoformat(end_date) - date.fromisoformat(start_date)).days
        years = days / 365.25
        cagr  = (final_balance / initial_balance) ** (1 / years) - 1 if years > 0 else 0.0
    except Exception:
        cagr = 0.0

    try:
        balances   = [p["balance"] for p in equity_curve]
        daily_rets = [(balances[i] - balances[i-1]) / balances[i-1] for i in range(1, len(balances))]
        if len(daily_rets) > 1:
            arr    = np.array(daily_rets)
            mean_r = arr.mean()
            std_r  = arr.std(ddof=1)
            sharpe = (mean_r / std_r) * math.sqrt(252) if std_r > 0 else 0.0
        else:
            sharpe = 0.0
    except Exception:
        sharpe = 0.0

    try:
        balances = [p["balance"] for p in equity_curve]
        peak     = balances[0]
        max_dd   = 0.0
        for b in balances:
            if b > peak:
                peak = b
            dd = (peak - b) / peak
            if dd > max_dd:
                max_dd = dd
    except Exception:
        max_dd = 0.0

    closed = [t for t in trades if t["outcome"] in ("WIN", "LOSS", "EXPIRED")]
    wins   = [t for t in closed if t["outcome"] == "WIN"]
    losses = [t for t in closed if t["outcome"] in ("LOSS", "EXPIRED")]

    win_rate  = len(wins) / len(closed) if closed else None
    avg_win   = sum(t["pnl_pct"] for t in wins)   / len(wins)   if wins   else None
    avg_loss  = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else None

    gross_profit  = sum(t["pnl"] for t in wins   if t["pnl"])
    gross_loss    = abs(sum(t["pnl"] for t in losses if t["pnl"]))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None

    spy_return: float | None = None
    if raw_data is not None and trading_days:
        try:
            spy_s = raw_data[SPY_TICKER]["Close"].dropna()
            s_p   = float(spy_s.asof(pd.Timestamp(trading_days[0])))
            e_p   = float(spy_s.asof(pd.Timestamp(trading_days[-1])))
            if s_p and e_p and s_p > 0:
                spy_return = round((e_p - s_p) / s_p, 4)
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
