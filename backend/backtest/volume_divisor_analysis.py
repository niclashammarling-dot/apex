"""
Volume scoring divisor sweep analysis.

Sweeps the volume normalisation divisor from 1.0 to 3.0 in 0.25 steps (nine values)
against the post-L1 trade distribution to check whether the 2.0 divisor compresses
volume scores into a narrow band, reducing signal differentiation.

Failure mode (from apex-parameter-review-skill):
  If entries cluster well below 2.0 (e.g., volume_ratio 1.2–1.6), divisor=2.0 maps
  all scores to 0.60–0.80 — differentiation is lost. A lower divisor spreads scores
  across the actual distribution range and restores the signal's contribution.

This is different from the momentum split finding (invariant due to component
co-movement). Here the question is whether the divisor is set too high for the actual
post-L1 volume distribution, compressing scores before they reach the aggregator.

Signal score injection (one component):
  volume_score_new = min(volume_ratio / divisor, 1.0)
  signal_score_adjusted = signal_score
      - VOLUME_AGGREGATOR_WEIGHT * volume_score_cached
      + VOLUME_AGGREGATOR_WEIGHT * volume_score_new

  volume_score_cached stored at production divisor=2.0 to keep subtraction exact.

Usage:
    cd /home/promenix/apex
    python -m backend.backtest.volume_divisor_analysis
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
from backend.signals import volume
from backend.backtest.engine_fast import (
    precompute,
    _check_exits_fast,
    _get_candidates_from_cache,
    _sector_exposure,
    _trading_days_count,
)

# Aggregator weight for volume_score — confirmed from aggregator.py
VOLUME_AGGREGATOR_WEIGHT = 0.20

CURRENT_DIVISOR = 2.0

# 1.00, 1.25, ..., 4.00 (13 values; baseline 2.00 in the middle)
SWEEP_DIVISORS = [round(1.0 + i * 0.25, 2) for i in range(13)]


def _build_component_cache(
    raw_data: pd.DataFrame,
    ticker_sector: dict[str, str],
    trading_days: list[date],
) -> dict[tuple[str, str], dict]:
    """
    Cache per (ticker, date): volume_ratio and volume_score at production divisor=2.0.

    volume_score stored at production divisor so the injection subtraction is exact.
    """
    cache: dict[tuple[str, str], dict] = {}
    total = len(ticker_sector)
    for i, (ticker, sector) in enumerate(ticker_sector.items(), 1):
        if i % 10 == 0:
            logger.debug(f"Volume cache: {i}/{total}")
        if sector in EXCLUDED_SECTORS:
            continue
        try:
            if ticker not in raw_data.columns.get_level_values(0):
                continue
            df_full = raw_data[ticker].copy()
            if df_full.empty or len(df_full) < 35:
                continue
            for d in trading_days:
                ts       = pd.Timestamp(d)
                date_str = d.isoformat()
                try:
                    mask = df_full.index <= ts
                    df   = df_full[mask].tail(60)
                    if len(df) < 35:
                        continue
                    result = volume.compute(df)
                    cache[(ticker, date_str)] = {
                        "volume_ratio": result["volume_ratio"],
                        "volume_score": result["volume_score"],
                    }
                except Exception:
                    continue
        except Exception:
            continue
    logger.info(f"Volume cache: {len(cache)} entries")
    return cache


def _volume_score_at_divisor(volume_ratio: float, divisor: float) -> float:
    return min(volume_ratio / divisor, 1.0)


def _adjusted_signal_score(
    base_signal_score: float,
    comp: dict,
    divisor: float,
) -> float:
    new_score = _volume_score_at_divisor(comp["volume_ratio"], divisor)
    delta = VOLUME_AGGREGATOR_WEIGHT * (new_score - comp["volume_score"])
    return min(1.0, max(0.0, base_signal_score + delta))


def _simulate(
    trading_days, price_cache, spy_cache, signal_cache,
    ticker_sector, sector_etf, etf_cache, component_cache,
    divisor: float,
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
            closed_trades.append({**record, "entry_divisor": trade["entry_divisor"]})
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
            score = (
                _adjusted_signal_score(sig["signal_score"], comp, divisor)
                if comp is not None else sig["signal_score"]
            )

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
                "entry_divisor": divisor,
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
            "ticker":        trade["ticker"],
            "pnl_pct":       round(pnl_pct, 4),
            "outcome":       "WIN" if pnl_pct >= 0 else "LOSS",
            "exit_reason":   "OPEN",
            "entry_divisor": trade["entry_divisor"],
        })

    return closed_trades


def run_analysis(start_date: str = "2023-01-01", end_date: str = "2026-01-01") -> None:
    logger.info(f"Volume divisor sweep {start_date} → {end_date}")

    pc = precompute(start_date, end_date)
    raw_data      = pc["raw_data"]
    ticker_sector = pc["ticker_sector"]
    sector_etf    = pc["sector_etf"]
    trading_days  = pc["trading_days"]
    price_cache   = pc["price_cache"]
    spy_cache     = pc["spy_cache"]
    etf_cache     = pc["etf_cache"]
    signal_cache  = pc["signal_cache"]

    logger.info("Building volume component cache ...")
    component_cache = _build_component_cache(raw_data, ticker_sector, trading_days)

    sim_args = (trading_days, price_cache, spy_cache, signal_cache,
                ticker_sector, sector_etf, etf_cache, component_cache)

    results: dict[float, list[dict]] = {}
    for d in SWEEP_DIVISORS:
        label = "CURRENT" if abs(d - CURRENT_DIVISOR) < 0.001 else ""
        logger.info(f"Simulating divisor={d:.2f} {label}...")
        results[d] = _simulate(*sim_args, divisor=d)

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
    print(f"\nVolume Divisor Sweep  {start} → {end}")
    print(f"Aggregator weight for volume_score: {VOLUME_AGGREGATOR_WEIGHT}")
    print(f"Formula: volume_score = min(volume_ratio / divisor, 1.0)\n")

    base_s = _summary(results[CURRENT_DIVISOR])

    # Precompute saturation counts per divisor (trades where volume_ratio >= divisor → score=1.0)
    ratio_vals = [comp["volume_ratio"] for comp in component_cache.values()]

    header = f"  {'Divisor':>8}  {'N':>5}  {'Win%':>6}  {'Avg PnL%':>9}  {'PF':>6}  {'Sat%':>6}  {'':>10}"
    print(header)
    print("  " + "-" * (len(header) - 2))

    for d in SWEEP_DIVISORS:
        s       = _summary(results[d])
        marker  = " ← current" if abs(d - CURRENT_DIVISOR) < 0.001 else ""
        pf_str  = f"{s['pf']:.2f}" if not math.isinf(s['pf']) else "  inf"
        sat_n   = sum(1 for r in ratio_vals if r >= d)
        sat_pct = sat_n / len(ratio_vals) * 100 if ratio_vals else 0
        print(
            f"  {d:>7.2f}   {s['n']:>5}  {s['win_pct']:>5.1f}%  "
            f"{s['avg_pnl']:>+8.2f}%  {pf_str:>6}  {sat_pct:>5.1f}%{marker}"
        )

    print(f"\n  Delta from current (divisor={CURRENT_DIVISOR}) — PF and WR:")
    for d in SWEEP_DIVISORS:
        if abs(d - CURRENT_DIVISOR) < 0.001:
            continue
        s   = _summary(results[d])
        dpf = s["pf"] - base_s["pf"]
        dwr = s["win_pct"] - base_s["win_pct"]
        print(f"    d={d:.2f}  ΔPF={dpf:+.3f}  ΔWR={dwr:+.1f}%")

    _print_component_distribution(component_cache)
    _print_finding(results, base_s)


def _print_component_distribution(
    component_cache: dict[tuple[str, str], dict],
) -> None:
    if not component_cache:
        return

    ratios = [v["volume_ratio"] for v in component_cache.values()]
    scores = [v["volume_score"] for v in component_cache.values()]

    def _dist_stats(vals: list[float], lo: float, hi: float, name: str, nbuckets: int = 8) -> None:
        n        = len(vals)
        mn       = min(vals)
        mx       = max(vals)
        mean_v   = sum(vals) / n
        p25      = sorted(vals)[int(n * 0.25)]
        p75      = sorted(vals)[int(n * 0.75)]
        span_pct = (mx - mn) / (hi - lo) * 100 if (hi - lo) > 0 else 0

        # Compression: if 75% of entries fall in <30% of the range
        iqr_span  = (p75 - p25) / (hi - lo) * 100
        compressed = iqr_span < 30

        print(f"\n  {name}  (N={n})")
        print(f"    Range (min/max):   [{mn:.3f}, {mx:.3f}]")
        print(f"    IQR (p25/p75):     [{p25:.3f}, {p75:.3f}]  ({iqr_span:.1f}% of [{lo:.1f}, {hi:.1f}] theoretical)")
        print(f"    Mean: {mean_v:.3f}")
        if compressed:
            print(f"    ** COMPRESSED: IQR in {iqr_span:.1f}% of theoretical range "
                  f"— signal differentiation reduced **")

        step = (hi - lo) / nbuckets
        buckets: dict[int, int] = defaultdict(int)
        for v in vals:
            idx = min(int((v - lo) / step), nbuckets - 1)
            buckets[idx] += 1
        max_count = max(buckets.values(), default=1)
        print(f"    Distribution:")
        for b in range(nbuckets):
            lo_b  = lo + b * step
            hi_b  = lo_b + step
            count = buckets[b]
            bar   = "#" * (count * 40 // max_count)
            print(f"      [{lo_b:.2f}, {hi_b:.2f})  {count:>5}  {bar}")

    # Saturation check: fraction of entries where ratio >= current divisor (score = 1.0)
    saturated = sum(1 for r in ratios if r >= CURRENT_DIVISOR)
    sat_pct   = saturated / len(ratios) * 100 if ratios else 0

    print(f"\nComponent Distribution — post-L1 survivor set (volume_ratio)")
    print("=" * 65)
    print(f"  Saturation at current divisor ({CURRENT_DIVISOR}): "
          f"{saturated}/{len(ratios)} entries ({sat_pct:.1f}%) score 1.0")
    _dist_stats(ratios, 0.0, 4.0, "volume_ratio  (entry day vs 30d avg)", nbuckets=8)
    _dist_stats(scores, 0.0, 1.0, f"volume_score  (at divisor={CURRENT_DIVISOR})", nbuckets=8)


def _print_finding(results: dict[float, list[dict]], base_s: dict) -> None:
    print(f"\nFinding Classification  (per apex-parameter-review-skill decision rule)")
    print("=" * 65)

    pf_by_d = {d: _summary(trades)["pf"] for d, trades in results.items()}
    base_pf  = pf_by_d[CURRENT_DIVISOR]

    lower_divisors = [d for d in SWEEP_DIVISORS if d < CURRENT_DIVISOR]
    higher_divisors = [d for d in SWEEP_DIVISORS if d > CURRENT_DIVISOR]

    lower_better  = all(pf_by_d[d] > base_pf for d in lower_divisors) if lower_divisors else False
    higher_worse  = all(pf_by_d[d] < base_pf for d in higher_divisors) if higher_divisors else False
    inverted      = lower_better and higher_worse

    any_better    = any(pf_by_d[d] > base_pf + 0.02 for d in SWEEP_DIVISORS)
    local_min     = any_better and not inverted

    all_equal     = all(abs(pf_by_d[d] - base_pf) < 0.001 for d in SWEEP_DIVISORS)

    if all_equal:
        finding = (
            f"INVARIANT — all divisors produce identical results (PF {base_pf:.3f}); "
            f"volume_score delta too small to reorder candidates after 0.20 aggregator weight"
        )
    elif inverted:
        best_d  = min(lower_divisors, key=lambda d: abs(pf_by_d[d] - max(pf_by_d[d2] for d2 in lower_divisors)))
        finding = (
            f"INVERSION — lower divisor improves PF monotonically; "
            f"divisor is set too high for the post-L1 volume distribution; "
            f"best candidate divisor={best_d:.2f} (PF {pf_by_d[best_d]:.3f} vs {base_pf:.3f}); "
            f"change with survivor-set justification"
        )
    elif local_min:
        best_d = max(SWEEP_DIVISORS, key=lambda d: pf_by_d[d])
        finding = (
            f"LOCAL MINIMUM — current divisor={CURRENT_DIVISOR} underperforms neighbors; "
            f"best candidate divisor={best_d:.2f} (PF {pf_by_d[best_d]:.3f} vs {base_pf:.3f}); "
            f"flag for accumulation — do not change until N ≥ 30 trades at candidate value"
        )
    else:
        finding = f"VALIDATED — current divisor={CURRENT_DIVISOR} at or near local optimum (PF {base_pf:.3f})"

    print(f"\n  {finding}")
    print(f"\n  Required output (apex-parameter-review-skill):")
    print(f"    Parameter:           volume_score normalising divisor")
    print(f"    File:                backend/signals/volume.py")
    print(f"    Current value:       {CURRENT_DIVISOR}")
    print(f"    Calibration prov.:   undocumented; no survivor-set calibration")
    print(f"    Reference universe:  post-L1 trade distribution, 2023-2026")
    print(f"    N trades (current):  {base_s['n']}")
    print(f"    Finding:             {finding.split(' — ')[0].lower()}")
    print()


if __name__ == "__main__":
    run_analysis()
