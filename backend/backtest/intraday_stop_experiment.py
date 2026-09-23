"""
How much of a backtest result is the engine not seeing its own stops?

Both engines decide exits on the daily CLOSE: engine_fast reads
price_cache[(ticker, day)] (closes only, _build_price_cache), engine.py reads
_price_on() -> df["Close"].iloc[-1]. Live does not work that way. APEX places
Alpaca BRACKET orders whose StopLossRequest leg rests at the exchange (GTC
since 2026-05-08), so a live stop triggers INTRADAY the moment price trades
through it, and then fills at market.

So a day that trades to -9% and closes at -3% is a realised stop-out live and
invisible to both engines. That is not a softer drawdown — it is a different
trade, and every parameter tuned on these engines was tuned without it.

This script measures the size of that gap on frozen data. It monkeypatches the
exit check in-process; it does NOT modify either engine. Approximation, three
cases, in the conservative-but-still-optimistic direction:

    gap-through (open <= stop)  -> fill at the open
    intraday breach (low <= stop) -> fill at the stop price
    otherwise                    -> hand over to the shipped close-based logic

Still optimistic vs live: a real stop fills at MARKET after trigger, worse than
the stop price in fast conditions, and no slippage or commission is modelled
anywhere in backend/backtest/.

First run, 2026-09-23, on the 2026-09-21 optimizer result (window 2025-12-22 →
2026-09-18, its own cached parquet, best params SL 5%):

    close-only  score 1.0934  sharpe 3.263  maxDD 2.6%  win 64%   75 trades  +31.3%
    intraday    score 0.5654  sharpe 1.792  maxDD 4.6%  win 43.5% 85 trades  +11.4%

41 stop exits appeared (30 intraday, 11 gap-through); 27 of them were entirely
invisible to the close-only engine — traded through the stop, closed above it.
SPY over the same window returned 12.1%.

Stop-width grid, same window, other params fixed at the 09-21 best:

    SL     close    intraday   delta
    0.04   0.612    0.073     +0.539
    0.05   1.093    0.565     +0.528
    0.06   1.021    0.329     +0.692
    0.07   0.647    1.079     -0.432

    argmax   close-only: SL 5%      intraday: SL 7%

The bias is differential, not a level shift: it flatters tight stops and (at
7%) penalises the loosest, so it acts on exactly the axis a stop-width search
is trying to resolve. The 09-21 run recommended tightening live's 6% to 5%;
with stops visible, the best of the four is looser than live.

Not established, and stated here so the numbers are not read as cleaner than
they are: the 7% row IMPROVES under the treatment, which can only be trade-path
effects (freed slots change later entries), so no single cell is a clean read
and the argmax shift is suggestive. Trade counts move (75 -> 85, 101 at SL 4%),
so this is a compounding counterfactual, not a one-variable delta. One window,
four stop widths. Neither arm models profit-lock: ParamSet has no field for it
and optimizer._evaluate never passes it, so both arms omit live's 0.04/0.01
ratchet entirely.

Usage:
    python -m backend.backtest.intraday_stop_experiment            # 09-21 params
    python -m backend.backtest.intraday_stop_experiment --grid     # stop-width grid
"""
from __future__ import annotations

import glob
import json
import os
import sys
from pathlib import Path

import pandas as pd

import backend.backtest.engine_fast as ef
from backend.backtest import optimizer as o

REPO         = Path(__file__).parent.parent.parent
RESULTS_PATH = REPO / "data/optimizer_results.json"
CACHE_GLOB   = str(REPO / "data/backtest_cache/*.parquet")

_orig_check_exits = ef._check_exits_fast
STATS: dict = {"intraday_sl": 0, "gap_sl": 0, "close_sl": 0, "missed_by_close": []}


def _load_ohlc(parquet_path: str | None = None) -> tuple[dict, dict, str]:
    """{(ticker, 'YYYY-MM-DD'): price} for Low and Open, from the engine's own cache."""
    path = parquet_path or max(glob.glob(CACHE_GLOB), key=os.path.getmtime)
    raw  = pd.read_parquet(path)
    low, opn = {}, {}
    for ticker in set(raw.columns.get_level_values(0)):
        try:
            lo, op = raw[ticker]["Low"].dropna(), raw[ticker]["Open"].dropna()
        except KeyError:
            continue
        for ts, v in lo.items():
            low[(ticker, ts.date().isoformat())] = float(v)
        for ts, v in op.items():
            opn[(ticker, ts.date().isoformat())] = float(v)
    return low, opn, os.path.basename(path)


def make_patched(low: dict, opn: dict):
    """Exit check with intraday stop triggering, delegating everything else."""

    def patched(open_trades, price_cache, today, today_str, take_profit_pct,
                stop_loss_pct, time_stop_days, trailing_stop_pct=None,
                profit_lock_trigger_pct=None, profit_lock_trail_pct=None):
        pre = []
        for trade in list(open_trades):
            eff_sl = trade.get("sl", stop_loss_pct)
            if eff_sl is None:
                continue
            stop_price = trade["entry_price"] * (1 - eff_sl)
            lo    = low.get((trade["ticker"], today_str))
            op    = opn.get((trade["ticker"], today_str))
            close = price_cache.get((trade["ticker"], today_str))
            if lo is None or close is None or lo > stop_price:
                continue

            gapped = op is not None and op <= stop_price
            fill   = op if gapped else stop_price
            STATS["gap_sl" if gapped else "intraday_sl"] += 1

            # Would the shipped close-based logic have exited today at all?
            if (close - trade["entry_price"]) / trade["entry_price"] > -eff_sl:
                STATS["missed_by_close"].append(
                    (trade["ticker"], today_str, round(close, 2),
                     round(stop_price, 2), round(fill, 2)))

            pnl_pct = (fill - trade["entry_price"]) / trade["entry_price"]
            pre.append({"_trade": trade, "record": ef.TradeRecord(
                ticker=trade["ticker"], sector=trade["sector"],
                entry_date=trade["entry_date"], exit_date=today_str,
                entry_price=trade["entry_price"], exit_price=round(fill, 4),
                shares=trade["shares"], amount=trade["amount"],
                pnl=round(trade["amount"] * pnl_pct, 2), pnl_pct=round(pnl_pct, 4),
                outcome="LOSS", exit_reason="SL",
                signal_score=trade["signal_score"],
                days_held=ef._trading_days_count(trade["entry_date"], today_str),
            )})

        # engine_fast.py:198 — the CALLER removes closed trades. Do not remove
        # here, or the trade is removed twice. Hide them from the close-based
        # pass by identity, never equality: two trade dicts can compare equal.
        dropped   = {id(c["_trade"]) for c in pre}
        remaining = [t for t in open_trades if id(t) not in dropped]
        rest = _orig_check_exits(remaining, price_cache, today, today_str,
                                 take_profit_pct, stop_loss_pct, time_stop_days,
                                 trailing_stop_pct, profit_lock_trigger_pct,
                                 profit_lock_trail_pct)
        for c in rest:
            if c["record"]["exit_reason"] == "SL":
                STATS["close_sl"] += 1
        return pre + rest

    return patched


def _reset():
    STATS.update({"intraday_sl": 0, "gap_sl": 0, "close_sl": 0})
    STATS["missed_by_close"] = []


def _row(label, sc, r):
    print(f"{label:>12} {sc:>8} {r['sharpe']:>7} {r['max_drawdown']:>7} "
          f"{r['win_rate']:>6} {r['total_trades']:>7} {r['total_return_pct']:>8}")


def main(grid: bool = False) -> None:
    data   = json.load(open(RESULTS_PATH))
    params = dict(data["best_params"])
    start, end = data["start_date"], data["end_date"]
    low, opn, cache_name = _load_ohlc()
    patched = make_patched(low, opn)
    print(f"window {start} → {end} | OHLC from {cache_name} ({len(low)} lows)")

    pc = ef.precompute(start, end)
    print(f"{'mode':>12} {'score':>8} {'sharpe':>7} {'maxDD':>7} {'win':>6} {'trades':>7} {'return':>8}")

    widths = (0.04, 0.05, 0.06, 0.07) if grid else (params["stop_loss_pct"],)
    scores: dict = {}
    for sl in widths:
        p = dict(params, stop_loss_pct=sl)
        for mode in ("close", "intraday"):
            _reset()
            ef._check_exits_fast = patched if mode == "intraday" else _orig_check_exits
            got = o._evaluate(ef.run, p, start, end, pc)
            if got is None:
                print(f"{f'SL {sl} {mode}':>12}   (no valid result — floor check)")
                continue
            sc, r = got
            scores[(sl, mode)] = sc
            _row(f"SL {sl} {mode}", sc, r)
            if mode == "intraday" and not grid:
                print(f"  stop exits: intraday {STATS['intraday_sl']}, gap {STATS['gap_sl']}, "
                      f"close-path {STATS['close_sl']}")
                print(f"  invisible to the close-only engine: {len(STATS['missed_by_close'])}")
                for m in STATS["missed_by_close"][:10]:
                    print("     ", m)
    ef._check_exits_fast = _orig_check_exits

    if grid:
        print("\ndelta (close - intraday) — how much the blindness flatters each width:")
        for sl in widths:
            if (sl, "close") in scores and (sl, "intraday") in scores:
                print(f"  SL {sl}: {round(scores[(sl, 'close')] - scores[(sl, 'intraday')], 5)}")
        for mode in ("close", "intraday"):
            cand = {k[0]: v for k, v in scores.items() if k[1] == mode}
            if cand:
                print(f"  argmax {mode}: SL {max(cand, key=cand.get)} ({max(cand.values())})")


if __name__ == "__main__":
    main(grid="--grid" in sys.argv)
