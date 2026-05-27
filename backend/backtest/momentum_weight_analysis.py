"""
Momentum sub-weight sweep analysis.

Sweeps momentum_weight from 0.3 to 0.7 in 0.05 steps (nine values) against the
post-L1 trade distribution to check whether 0.6/0.4 (price momentum vs RSI) reflects
the actual predictive weighting on the survivor set.

Founding instance: RSI discount removal 2026-05-24 — standard overbought prior inverted
the signal on APEX's gate-selected universe. This sweep checks whether the 0.6/0.4
split carries the same class of error.

Analysis Protocol (from apex-parameter-review-skill):
  - One parameter at a time
  - Post-L1 survivor set as reference universe
  - Distribution compression check on raw components (momentum_clipped, rsi_score)
  - Inversion criterion: higher weight → lower PF where encoding assumes higher = better
  - Local minimum: adjacent values outperform but no inversion — flag, accumulate, do not change

Signal score injection (two-step):
  signal_score_adjusted = signal_score
      - MOMENTUM_AGGREGATOR_WEIGHT * mom_score_cached
      + MOMENTUM_AGGREGATOR_WEIGHT * momentum_score(w, mom_clipped, rsi_score)

  Storing mom_score in the component cache avoids floating-point drift on the subtraction.

Usage:
    cd /home/promenix/apex
    python -m backend.backtest.momentum_weight_analysis
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
    SECTOR_THRESHOLD_FLOORS,
    SECTORS,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    TIME_STOP_DAYS,
    WIN_RATE_MIN_TRADES,
)
from backend.signals import momentum
from backend.backtest.engine_fast import (
    precompute,
    _check_exits_fast,
    _get_candidates_from_cache,
    _sector_exposure,
    _trading_days_count,
)

# Aggregator weight for momentum_score — confirmed 2026-05-27 from aggregator.py
MOMENTUM_AGGREGATOR_WEIGHT = 0.25

# Current production split
CURRENT_MOMENTUM_WEIGHT = 0.6

# Sweep range: 0.30, 0.35, ..., 0.70 — excludes degenerate single-signal extremes
SWEEP_WEIGHTS = [round(w * 0.05 + 0.30, 2) for w in range(9)]


def _build_component_cache(
    raw_data: pd.DataFrame,
    ticker_sector: dict[str, str],
    trading_days: list[date],
) -> dict[tuple[str, str], dict]:
    """
    Cache per (ticker, date): mom_clipped, rsi_score, mom_score (at production 0.6/0.4).

    mom_score stored at production weight so the two-step injection subtraction is exact.
    rsi_score stored as the normalised scalar ((rsi - 50) / 50), not raw RSI.
    """
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
                    result = momentum.compute(df)
                    if result["rsi_blocked"]:
                        continue
                    cache[(ticker, date_str)] = {
                        "mom_clipped": result["momentum_clipped"],
                        "rsi_score":   (result["rsi"] - 50) / 50,
                        "mom_score":   result["momentum_score"],
                    }
                except Exception:
                    continue
        except Exception:
            continue
    logger.info(f"Component cache: {len(cache)} entries")
    return cache


def _momentum_score_at_weight(mom_clipped: float, rsi_score: float, w: float) -> float:
    combined = w * mom_clipped + (1.0 - w) * rsi_score
    return max(0.0, min(1.0, combined + 0.5))


def _adjusted_signal_score(
    base_signal_score: float,
    comp: dict,
    weight: float,
) -> float:
    new_mom_score = _momentum_score_at_weight(comp["mom_clipped"], comp["rsi_score"], weight)
    delta = MOMENTUM_AGGREGATOR_WEIGHT * (new_mom_score - comp["mom_score"])
    return min(1.0, max(0.0, base_signal_score + delta))


def _simulate(
    trading_days, price_cache, spy_cache, signal_cache,
    ticker_sector, sector_etf, etf_cache, component_cache,
    weight: float,
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
            closed_trades.append({**record, "entry_weight": trade["entry_weight"]})
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

            comp = component_cache.get((sig["ticker"], today_str))
            if comp is None:
                score = sig["signal_score"]
            else:
                score = _adjusted_signal_score(sig["signal_score"], comp, weight)

            alloc_pct = min(sig["kelly_size"] if sig["kelly_size"] > 0 else 0.10, MAX_POSITION_SIZE)
            amount    = balance * alloc_pct
            sector    = sig["sector"]
            projected = sector_exp.get(sector, 0.0) + (amount / balance)
            if projected > MAX_SECTOR_EXPOSURE:
                continue

            entry_price = price_cache.get((sig["ticker"], today_str))
            if not entry_price or entry_price <= 0 or amount < 50:
                continue

            balance -= amount
            open_trades.append({
                "ticker":        sig["ticker"],
                "sector":        sector,
                "entry_date":    today_str,
                "entry_price":   entry_price,
                "peak_price":    entry_price,
                "shares":        amount / entry_price,
                "amount":        amount,
                "signal_score":  score,
                "entry_weight":  weight,
                "tp":            tp,
                "sl":            sl,
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
            "ticker":       trade["ticker"],
            "pnl_pct":      round(pnl_pct, 4),
            "outcome":      "WIN" if pnl_pct >= 0 else "LOSS",
            "exit_reason":  "OPEN",
            "entry_weight": trade["entry_weight"],
        })

    return closed_trades


def run_analysis(start_date: str = "2023-01-01", end_date: str = "2026-01-01") -> None:
    logger.info(f"Momentum weight sweep {start_date} → {end_date}")

    pc = precompute(start_date, end_date)
    raw_data      = pc["raw_data"]
    ticker_sector = pc["ticker_sector"]
    sector_etf    = pc["sector_etf"]
    trading_days  = pc["trading_days"]
    price_cache   = pc["price_cache"]
    spy_cache     = pc["spy_cache"]
    etf_cache     = pc["etf_cache"]
    signal_cache  = pc["signal_cache"]

    logger.info("Building component cache ...")
    component_cache = _build_component_cache(raw_data, ticker_sector, trading_days)

    sim_args = (trading_days, price_cache, spy_cache, signal_cache,
                ticker_sector, sector_etf, etf_cache, component_cache)

    results: dict[float, list[dict]] = {}
    for w in SWEEP_WEIGHTS:
        label = "CURRENT" if abs(w - CURRENT_MOMENTUM_WEIGHT) < 0.001 else ""
        logger.info(f"Simulating weight={w:.2f} {label}...")
        results[w] = _simulate(*sim_args, weight=w)

    _print_results(results, component_cache, start_date, end_date)


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


def _print_results(
    results: dict[float, list[dict]],
    component_cache: dict[tuple[str, str], dict],
    start: str,
    end: str,
) -> None:
    print(f"\nMomentum Weight Sweep  {start} → {end}")
    print(f"Aggregator weight for momentum_score: {MOMENTUM_AGGREGATOR_WEIGHT}")
    print(f"Formula: combined = w * momentum_clipped + (1-w) * rsi_score\n")

    # --- Per-weight results table ---
    header = f"  {'Weight':>8}  {'N':>5}  {'Win%':>6}  {'Avg PnL%':>9}  {'PF':>6}  {'':>10}"
    print(header)
    print("  " + "-" * (len(header) - 2))

    baseline = results[CURRENT_MOMENTUM_WEIGHT]
    base_s   = _summary(baseline)

    for w in SWEEP_WEIGHTS:
        trades = results[w]
        s      = _summary(trades)
        marker = " ← current" if abs(w - CURRENT_MOMENTUM_WEIGHT) < 0.001 else ""
        pf_str = f"{s['pf']:.2f}" if not math.isinf(s['pf']) else "  inf"
        print(
            f"  {w:>7.2f}   {s['n']:>5}  {s['win_pct']:>5.1f}%  "
            f"{s['avg_pnl']:>+8.2f}%  {pf_str:>6}{marker}"
        )

    # --- Delta from current (0.6) ---
    print(f"\n  Delta from current ({CURRENT_MOMENTUM_WEIGHT}) — PF and WR:")
    for w in SWEEP_WEIGHTS:
        if abs(w - CURRENT_MOMENTUM_WEIGHT) < 0.001:
            continue
        s   = _summary(results[w])
        dpf = s["pf"] - base_s["pf"]
        dwr = s["win_pct"] - base_s["win_pct"]
        print(f"    w={w:.2f}  ΔPF={dpf:+.3f}  ΔWR={dwr:+.1f}%")

    # --- Component distribution block ---
    # Shows raw momentum_clipped and rsi_score distributions in the post-L1 universe.
    # A compressed distribution (entries in <30% of theoretical range) invalidates
    # standard prior assumptions about the weight's operating range.
    _print_component_distribution(component_cache)

    # --- Finding classification ---
    _print_finding(results, base_s)


def _print_component_distribution(
    component_cache: dict[tuple[str, str], dict],
) -> None:
    if not component_cache:
        return

    mom_vals = [v["mom_clipped"] for v in component_cache.values()]
    rsi_vals = [v["rsi_score"]   for v in component_cache.values()]

    def _dist_stats(vals: list[float], lo: float, hi: float, name: str) -> None:
        n        = len(vals)
        mn       = min(vals)
        mx       = max(vals)
        mean_v   = sum(vals) / n
        span_pct = (mx - mn) / (hi - lo) * 100 if (hi - lo) > 0 else 0
        compressed = span_pct < 30

        print(f"\n  {name}  (theoretical range [{lo:.2f}, {hi:.2f}], N={n})")
        print(f"    Observed range:  [{mn:.3f}, {mx:.3f}]  ({span_pct:.1f}% of theoretical)")
        print(f"    Mean: {mean_v:.3f}")
        if compressed:
            print(f"    ** COMPRESSED: entries in {span_pct:.1f}% of theoretical range "
                  f"— standard prior assumptions about this range are invalid **")

        NBUCKETS = 8
        step     = (hi - lo) / NBUCKETS
        buckets: dict[int, int] = defaultdict(int)
        for v in vals:
            idx = min(int((v - lo) / step), NBUCKETS - 1)
            buckets[idx] += 1
        print(f"    Distribution:")
        for b in range(NBUCKETS):
            lo_b  = lo + b * step
            hi_b  = lo_b + step
            count = buckets[b]
            bar   = "#" * (count * 40 // max(buckets.values(), default=1))
            print(f"      [{lo_b:+.2f}, {hi_b:+.2f})  {count:>4}  {bar}")

    print(f"\nComponent Distribution — post-L1 survivor set")
    print("=" * 65)
    _dist_stats(mom_vals, -0.20, 0.20, "momentum_clipped")
    _dist_stats(rsi_vals, -1.0,  1.0,  "rsi_score  (= (RSI-50)/50)")


def _print_finding(results: dict[float, list[dict]], base_s: dict) -> None:
    print(f"\nFinding Classification  (per apex-parameter-review-skill decision rule)")
    print("=" * 65)

    pf_by_w = {w: _summary(trades)["pf"] for w, trades in results.items()}
    base_pf  = pf_by_w[CURRENT_MOMENTUM_WEIGHT]

    # Inversion: monotonically worse as w increases (higher w = worse PF across majority of range)
    higher_weights = [w for w in SWEEP_WEIGHTS if w > CURRENT_MOMENTUM_WEIGHT]
    lower_weights  = [w for w in SWEEP_WEIGHTS if w < CURRENT_MOMENTUM_WEIGHT]

    higher_worse = all(pf_by_w[w] < base_pf for w in higher_weights) if higher_weights else False
    lower_better = all(pf_by_w[w] > base_pf for w in lower_weights) if lower_weights else False
    inverted     = higher_worse and lower_better

    all_worse = all(
        abs(pf_by_w[w] - base_pf) < 0.02 or pf_by_w[w] >= base_pf
        for w in SWEEP_WEIGHTS
    )
    any_better = any(pf_by_w[w] > base_pf + 0.02 for w in SWEEP_WEIGHTS)
    local_min  = any_better and not inverted

    if inverted:
        finding = "INVERSION — higher momentum weight reduces PF monotonically; change immediately"
    elif local_min:
        best_w = max(SWEEP_WEIGHTS, key=lambda w: pf_by_w[w])
        finding = (
            f"LOCAL MINIMUM — current 0.6 underperforms adjacent values; "
            f"best weight={best_w:.2f} (PF {pf_by_w[best_w]:.3f} vs {base_pf:.3f}). "
            f"Flag for accumulation — do not change until N ≥ 30 trades at candidate value."
        )
    else:
        finding = f"VALIDATED — current 0.6/0.4 split at or near local optimum (PF {base_pf:.3f})"

    print(f"\n  {finding}")
    print(f"\n  Required output (apex-parameter-review-skill):")
    print(f"    Parameter:           momentum_weight (momentum vs RSI split)")
    print(f"    File:                backend/signals/momentum.py")
    print(f"    Current value:       {CURRENT_MOMENTUM_WEIGHT} / {1.0 - CURRENT_MOMENTUM_WEIGHT}")
    print(f"    Calibration prov.:   undocumented; literature-derived")
    print(f"    Reference universe:  post-L1 trade distribution, 2023-2026")
    print(f"    N trades (current):  {base_s['n']}")
    print(f"    Finding:             {finding.split(' — ')[0].lower()}")
    print()


if __name__ == "__main__":
    run_analysis()
