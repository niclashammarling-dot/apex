"""
Aggregator weight analysis — Q1: trend/rs split sweep.

Holds the three compressed signals at their current weights (momentum 0.25,
volume 0.20, ev_norm 0.20 = 0.65 total) and sweeps the split between the two
spread signals (trend_score, rs_score) across the fixed 0.35 budget.

One degree of freedom: trend_weight 0.05 → 0.30 in 0.05 steps (six values).
rs_weight = 0.35 - trend_weight implicitly at every step.

Context:
  Phase 1 (aggregator_weight_analysis.py, 2026-05-27) confirmed:
    - momentum_score (0.25): compressed, IQR 17.6% — prior-confirmed invariant
    - volume_score (0.20):   compressed, IQR 19.7% — prior-confirmed invariant
    - ev_norm (0.20):        compressed, IQR 1.6%  — formula truncation at 0.27
    - trend_score (0.20):    spread, IQR 50.0%     — bimodal, simulation warranted
    - rs_score (0.15):       spread, IQR 44.4%     — simulation warranted

  0.65 of aggregator weight is on near-zero-spread signals. This sweep asks:
  within the 0.35 budget that is doing real ranking work, is the current 20:15
  split between trend and rs the right calibration?

Signal score injection:
  delta = (new_trend_weight - 0.20) * trend_cached
        + (new_rs_weight   - 0.15) * rs_cached
  signal_score_adjusted = base_signal_score + delta

  No stored production score needed — delta uses a single cached component
  value for both old and new weight terms; floating-point cancels exactly.

Usage:
    cd /home/promenix/apex
    python -m backend.backtest.aggregator_split_analysis
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta

import pandas as pd
from loguru import logger

from backend.config import (
    DAILY_LOSS_CAP,
    EXCLUDED_SECTORS,
    LOCK1_THRESHOLD,
    MAX_POSITION_SIZE,
    MAX_POSITIONS,
    MAX_SECTOR_EXPOSURE,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    TIME_STOP_DAYS,
    WIN_RATE_MIN_TRADES,
)
from backend.signals import trend, relative_strength
from backend.backtest.engine_fast import (
    precompute,
    _check_exits_fast,
    _get_candidates_from_cache,
    _sector_exposure,
)

CURRENT_TREND_WEIGHT = 0.20
CURRENT_RS_WEIGHT    = 0.15
SPREAD_BUDGET        = 0.35   # held fixed; varies only the split

# trend_weight 0.05 → 0.30; rs_weight = SPREAD_BUDGET - trend_weight
SWEEP_TREND_WEIGHTS = [round(0.05 + i * 0.05, 2) for i in range(6)]


def _build_component_cache(
    raw_data: pd.DataFrame,
    ticker_sector: dict[str, str],
    spy_cache: dict[str, dict],
    trading_days: list[date],
) -> dict[tuple[str, str], dict]:
    """Per (ticker, date): {trend_score, rs_score}."""
    cache: dict[tuple[str, str], dict] = {}
    total = len(ticker_sector)
    for i, (ticker, sector) in enumerate(ticker_sector.items(), 1):
        if i % 10 == 0:
            logger.debug(f"Component cache: {i}/{total}")
        if sector in EXCLUDED_SECTORS:
            continue
        try:
            if ticker not in raw_data.columns.get_level_values(0):
                continue
            df_full = raw_data[ticker].copy()
            if df_full.empty or len(df_full) < 55:
                continue
            for d in trading_days:
                ts       = pd.Timestamp(d)
                date_str = d.isoformat()
                try:
                    mask = df_full.index <= ts
                    df   = df_full[mask].tail(90)
                    if len(df) < 55:
                        continue
                    spy_ret = spy_cache.get(date_str, {}).get("return_20d", 0.0)
                    trd     = trend.compute(df)
                    rs      = relative_strength.compute(df, spy_ret)
                    cache[(ticker, date_str)] = {
                        "trend_score": trd["trend_score"],
                        "rs_score":    rs["rs_score"],
                    }
                except Exception:
                    continue
        except Exception:
            continue
    logger.info(f"Component cache: {len(cache)} entries")
    return cache


def _adjusted_signal_score(
    base: float,
    comp: dict,
    trend_w: float,
    rs_w: float,
) -> float:
    delta = (
        (trend_w - CURRENT_TREND_WEIGHT) * comp["trend_score"] +
        (rs_w    - CURRENT_RS_WEIGHT)    * comp["rs_score"]
    )
    return min(1.0, max(0.0, base + delta))


def _simulate(
    trading_days, price_cache, spy_cache, signal_cache,
    ticker_sector, sector_etf, etf_cache, component_cache,
    trend_w: float, rs_w: float,
) -> list[dict]:
    tp    = TAKE_PROFIT_PCT
    sl    = STOP_LOSS_PCT
    tdays = TIME_STOP_DAYS
    l1    = LOCK1_THRESHOLD
    SL_COOLDOWN_DAYS = 5

    balance        = 10_000.0
    open_trades:   list[dict] = []
    closed_trades: list[dict] = []
    daily_losses:  dict[str, float] = {}
    sl_cooldown:   dict[str, date]  = {}

    for today in trading_days:
        today_str = today.isoformat()

        closed_today = _check_exits_fast(open_trades, price_cache, today, today_str, tp, sl, tdays)
        for ct in closed_today:
            trade  = ct["_trade"]
            record = ct["record"]
            open_trades.remove(trade)
            closed_trades.append(record)
            balance += trade["amount"] + (record["pnl"] or 0)
            if record["outcome"] in ("LOSS", "EXPIRED") and (record["pnl"] or 0) < 0:
                daily_losses[today_str] = daily_losses.get(today_str, 0) + abs(record["pnl"] or 0)
            if record["exit_reason"] in ("SL", "TSL"):
                sl_cooldown[trade["ticker"]] = today + timedelta(days=SL_COOLDOWN_DAYS)

        spy_reg       = spy_cache.get(today_str, {}).get("regime", 0.0)
        wins_so_far   = sum(1 for t in closed_trades if t["outcome"] == "WIN")
        closed_so_far = len(closed_trades)
        rolling_wr    = (wins_so_far / closed_so_far) if closed_so_far >= WIN_RATE_MIN_TRADES else None

        candidates = _get_candidates_from_cache(
            signal_cache, ticker_sector, sector_etf, etf_cache,
            today_str, l1, spy_reg, rolling_wr,
        )

        open_tickers     = {t["ticker"] for t in open_trades}
        sector_exp       = _sector_exposure(open_trades, balance)
        daily_loss_today = daily_losses.get(today_str, 0.0)

        for sig in sorted(candidates, key=lambda s: s["signal_score"], reverse=True):
            if len(open_trades) >= MAX_POSITIONS:
                break
            if sig["ticker"] in open_tickers:
                continue
            if sl_cooldown.get(sig["ticker"], date.min) > today:
                continue
            if daily_loss_today >= DAILY_LOSS_CAP:
                break

            comp  = component_cache.get((sig["ticker"], today_str))
            score = (
                _adjusted_signal_score(sig["signal_score"], comp, trend_w, rs_w)
                if comp is not None else sig["signal_score"]
            )

            alloc_pct   = min(sig["kelly_size"] if sig["kelly_size"] > 0 else 0.10, MAX_POSITION_SIZE)
            amount      = balance * alloc_pct
            sector      = sig["sector"]
            projected   = sector_exp.get(sector, 0.0) + (amount / balance)
            if projected > MAX_SECTOR_EXPOSURE:
                continue

            entry_price = price_cache.get((sig["ticker"], today_str))
            if not entry_price or entry_price <= 0 or amount < 50:
                continue

            balance -= amount
            open_trades.append({
                "ticker":       sig["ticker"],
                "sector":       sector,
                "entry_date":   today_str,
                "entry_price":  entry_price,
                "peak_price":   entry_price,
                "shares":       amount / entry_price,
                "amount":       amount,
                "signal_score": score,
                "tp":           tp,
                "sl":           sl,
            })
            open_tickers.add(sig["ticker"])
            sector_exp[sector] = sector_exp.get(sector, 0.0) + (amount / balance)

    last_day = trading_days[-1]
    last_str = last_day.isoformat()
    for trade in open_trades:
        price = price_cache.get((trade["ticker"], last_str))
        if price is None:
            continue
        pnl_pct = (price - trade["entry_price"]) / trade["entry_price"]
        closed_trades.append({
            "ticker":     trade["ticker"],
            "pnl_pct":    round(pnl_pct, 4),
            "outcome":    "WIN" if pnl_pct >= 0 else "LOSS",
            "exit_reason": "OPEN",
        })

    return closed_trades


def run_analysis(start_date: str = "2023-01-01", end_date: str = "2026-01-01") -> None:
    logger.info(f"Aggregator split sweep {start_date} → {end_date}")

    pc = precompute(start_date, end_date)
    raw_data      = pc["raw_data"]
    ticker_sector = pc["ticker_sector"]
    sector_etf    = pc["sector_etf"]
    trading_days  = pc["trading_days"]
    price_cache   = pc["price_cache"]
    spy_cache     = pc["spy_cache"]
    etf_cache     = pc["etf_cache"]
    signal_cache  = pc["signal_cache"]

    logger.info("Building trend/rs component cache ...")
    component_cache = _build_component_cache(raw_data, ticker_sector, spy_cache, trading_days)

    sim_args = (trading_days, price_cache, spy_cache, signal_cache,
                ticker_sector, sector_etf, etf_cache, component_cache)

    results: dict[float, list[dict]] = {}
    for tw in SWEEP_TREND_WEIGHTS:
        rw    = round(SPREAD_BUDGET - tw, 2)
        label = "CURRENT" if abs(tw - CURRENT_TREND_WEIGHT) < 0.001 else ""
        logger.info(f"Simulating trend={tw:.2f} rs={rw:.2f} {label}...")
        results[tw] = _simulate(*sim_args, trend_w=tw, rs_w=rw)

    _print_results(results, start_date, end_date)


def _summary(trades: list[dict]) -> dict:
    n       = len(trades)
    wins    = [t for t in trades if t["outcome"] == "WIN"]
    losses  = [t for t in trades if t["outcome"] in ("LOSS", "EXPIRED")]
    win_pct = len(wins) / n * 100 if n else 0
    avg_pnl = sum(t["pnl_pct"] for t in trades) / n * 100 if n else 0
    gw      = sum(t["pnl_pct"] for t in wins   if t["pnl_pct"] > 0)
    gl      = abs(sum(t["pnl_pct"] for t in losses if t["pnl_pct"] < 0))
    pf      = gw / gl if gl > 0 else float("inf")
    return {"n": n, "win_pct": win_pct, "avg_pnl": avg_pnl, "pf": pf}


def _print_results(results: dict[float, list[dict]], start: str, end: str) -> None:
    print(f"\nAggregator Split Sweep (Q1)  {start} → {end}")
    print(f"Fixed:   momentum=0.25  volume=0.20  ev_norm=0.20  (total 0.65)")
    print(f"Varying: trend + rs split across fixed budget of {SPREAD_BUDGET}\n")

    base_s = _summary(results[CURRENT_TREND_WEIGHT])

    header = (f"  {'trend_w':>8}  {'rs_w':>6}  {'N':>5}  "
              f"{'Win%':>6}  {'Avg PnL%':>9}  {'PF':>6}")
    print(header)
    print("  " + "-" * (len(header) - 2))

    for tw in SWEEP_TREND_WEIGHTS:
        rw     = round(SPREAD_BUDGET - tw, 2)
        s      = _summary(results[tw])
        marker = " ← current" if abs(tw - CURRENT_TREND_WEIGHT) < 0.001 else ""
        pf_str = f"{s['pf']:.2f}" if not math.isinf(s['pf']) else "  inf"
        print(
            f"  {tw:>8.2f}  {rw:>6.2f}  {s['n']:>5}  "
            f"{s['win_pct']:>5.1f}%  {s['avg_pnl']:>+8.2f}%  {pf_str:>6}{marker}"
        )

    print(f"\n  Delta from current (trend={CURRENT_TREND_WEIGHT}, rs={CURRENT_RS_WEIGHT}):")
    for tw in SWEEP_TREND_WEIGHTS:
        if abs(tw - CURRENT_TREND_WEIGHT) < 0.001:
            continue
        rw  = round(SPREAD_BUDGET - tw, 2)
        s   = _summary(results[tw])
        dpf = s["pf"] - base_s["pf"]
        dwr = s["win_pct"] - base_s["win_pct"]
        print(f"    trend={tw:.2f} rs={rw:.2f}  ΔPF={dpf:+.3f}  ΔWR={dwr:+.1f}%")

    _print_finding(results, base_s)


def _print_finding(results: dict[float, list[dict]], base_s: dict) -> None:
    print(f"\nFinding Classification  (per apex-parameter-review-skill decision rule)")
    print("=" * 65)

    pf_by_tw = {tw: _summary(trades)["pf"] for tw, trades in results.items()}
    base_pf   = pf_by_tw[CURRENT_TREND_WEIGHT]
    all_equal = all(abs(pf_by_tw[tw] - base_pf) < 0.001 for tw in SWEEP_TREND_WEIGHTS)
    any_better = any(pf_by_tw[tw] > base_pf + 0.02 for tw in SWEEP_TREND_WEIGHTS)

    if all_equal:
        finding = (
            f"INVARIANT — all split values produce identical results (PF {base_pf:.3f}); "
            f"both trend and rs scores have IQR spread but their weight ratio doesn't "
            f"affect trade selection within this budget"
        )
    elif any_better:
        best_tw = max(SWEEP_TREND_WEIGHTS, key=lambda tw: pf_by_tw[tw])
        best_rw = round(SPREAD_BUDGET - best_tw, 2)
        finding = (
            f"CALIBRATION TARGET — split trend={best_tw:.2f}/rs={best_rw:.2f} "
            f"outperforms current {CURRENT_TREND_WEIGHT}/{CURRENT_RS_WEIGHT} "
            f"(PF {pf_by_tw[best_tw]:.3f} vs {base_pf:.3f})"
        )
    else:
        finding = f"VALIDATED — current split at or near local optimum (PF {base_pf:.3f})"

    print(f"\n  {finding}")
    print(f"\n  Required output (apex-parameter-review-skill):")
    print(f"    Parameter:           trend_score / rs_score split within 0.35 budget")
    print(f"    File:                backend/signals/aggregator.py")
    print(f"    Current value:       trend={CURRENT_TREND_WEIGHT} / rs={CURRENT_RS_WEIGHT}")
    print(f"    Calibration prov.:   undocumented; no survivor-set calibration")
    print(f"    Reference universe:  post-L1 trade distribution, 2023-2026")
    print(f"    N trades (current):  {base_s['n']}")
    print(f"    Finding:             {finding.split(' — ')[0].lower()}")
    print()


if __name__ == "__main__":
    run_analysis()
