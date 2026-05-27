"""
Aggregator weight analysis — Phase 1: component distribution.

Builds a five-signal component cache over the post-L1 survivor set and prints
IQR spread for each signal with compression verdicts. Simulation sweep (Phase 2)
is only warranted for signals with IQR > 30% of [0, 1] theoretical range.

Context from prior sweeps (2026-05-27):
  - momentum_score (weight 0.25): sub-weight split invariant — components co-move post-L1
  - volume_score (weight 0.20): divisor invariant — 89% normal-volume days, IQR 9.9% of range
  - Combined 0.45 of aggregator weight confirmed near-zero discriminative spread

  Three hypotheses going in:
    ev_norm (0.20):    expected invariant — per-ticker variation is ATR-driven, but
                       L1 selects steady-trending names; system-level terms dominate
    trend_score (0.20): expected invariant — ma50_above + macd_positive near-universally
                        true post-L1; discrete 11-level signal clusters at 0.70-1.0
    rs_score (0.15):   genuine spread candidate — ±10% excess saturation wide relative
                       to typical 20d momentum outperformance

If four or five signals confirm as compressed or invariant, the finding shifts from
parameter calibration to signal layer architecture: the five-signal weighted sum was
designed for a universe with spread across all dimensions; the post-L1 universe has
spread in at most one or two.

Theoretical range for all five signals: [0, 1] — clipped or bounded by construction
in each signal module. IQR compression threshold: < 30% of [0, 1] = IQR span < 0.30.

Usage:
    cd /home/promenix/apex
    python -m backend.backtest.aggregator_weight_analysis
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date

import pandas as pd
from loguru import logger

from backend.config import (
    EXCLUDED_SECTORS,
)
from backend.signals import momentum, volume, trend, relative_strength
from backend.signals.ev_kelly import compute as ev_kelly_compute
from backend.backtest.engine_fast import precompute

AGGREGATOR_WEIGHTS = {
    "momentum_score": 0.25,
    "volume_score":   0.20,
    "ev_norm":        0.20,
    "trend_score":    0.20,
    "rs_score":       0.15,
}

# IQR span threshold for "material discriminative spread" — warrants simulation sweep
SPREAD_THRESHOLD = 0.30

# Prior sweep results — used in final verdict block
PRIOR_FINDINGS = {
    "momentum_score": "prior-confirmed invariant (sub-weight split sweep 2026-05-27; co-movement post-L1)",
    "volume_score":   "prior-confirmed invariant (divisor sweep 2026-05-27; 89% normal-volume days)",
}


def _build_component_cache(
    raw_data: pd.DataFrame,
    ticker_sector: dict[str, str],
    spy_cache: dict[str, dict],
    trading_days: list[date],
) -> dict[tuple[str, str], dict]:
    """
    Per (ticker, date): {momentum_score, volume_score, ev_norm, trend_score, rs_score}.

    rolling_win_rate=None — consistent with signal_cache build in engine_fast.py.
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

            atr_series = (df_full["High"] - df_full["Low"]).rolling(14).mean()

            for d in trading_days:
                ts       = pd.Timestamp(d)
                date_str = d.isoformat()
                try:
                    mask = df_full.index <= ts
                    df   = df_full[mask].tail(90)
                    if len(df) < 55:
                        continue

                    mom = momentum.compute(df)
                    if mom.get("rsi_blocked"):
                        continue

                    vol     = volume.compute(df)
                    trd     = trend.compute(df)
                    spy_ret = spy_cache.get(date_str, {}).get("return_20d", 0.0)
                    rs      = relative_strength.compute(df, spy_ret)

                    atr_raw = float(atr_series.asof(ts) or 0)
                    atr_pct = (atr_raw / mom["price"]) if mom["price"] > 0 and not math.isnan(atr_raw) else 0.0
                    spy_reg = spy_cache.get(date_str, {}).get("regime", 0.0)
                    evk     = ev_kelly_compute(spy_reg, rolling_win_rate=None, atr_pct=atr_pct)

                    cache[(ticker, date_str)] = {
                        "momentum_score": mom["momentum_score"],
                        "volume_score":   vol["volume_score"],
                        "ev_norm":        evk["ev_norm"],
                        "trend_score":    trd["trend_score"],
                        "rs_score":       rs["rs_score"],
                    }
                except Exception:
                    continue
        except Exception:
            continue

    logger.info(f"Component cache: {len(cache)} entries")
    return cache


def run_analysis(start_date: str = "2023-01-01", end_date: str = "2026-01-01") -> None:
    logger.info(f"Aggregator weight analysis — Phase 1: component distribution {start_date} → {end_date}")

    pc = precompute(start_date, end_date)
    raw_data      = pc["raw_data"]
    ticker_sector = pc["ticker_sector"]
    trading_days  = pc["trading_days"]
    spy_cache     = pc["spy_cache"]

    logger.info("Building five-signal component cache ...")
    component_cache = _build_component_cache(raw_data, ticker_sector, spy_cache, trading_days)

    _print_distribution_report(component_cache, start_date, end_date)


def _iqr_stats(vals: list[float]) -> dict:
    n      = len(vals)
    sorted_v = sorted(vals)
    p25    = sorted_v[int(n * 0.25)]
    p75    = sorted_v[int(n * 0.75)]
    mn     = sorted_v[0]
    mx     = sorted_v[-1]
    mean_v = sum(vals) / n
    return {"n": n, "p25": p25, "p75": p75, "mn": mn, "mx": mx, "mean": mean_v,
            "iqr_span": p75 - p25}


def _print_signal_block(
    signal: str,
    vals: list[float],
    weight: float,
    prior_finding: str | None,
) -> None:
    s = _iqr_stats(vals)
    iqr_pct    = s["iqr_span"] / 1.0 * 100  # theoretical range [0,1]
    compressed = iqr_pct < (SPREAD_THRESHOLD * 100)
    verdict    = "COMPRESSED" if compressed else "SPREAD"

    print(f"\n  {signal}  (aggregator weight {weight})")
    if prior_finding:
        print(f"    Prior:  {prior_finding}")
    print(f"    N:      {s['n']}")
    print(f"    Range:  [{s['mn']:.3f}, {s['mx']:.3f}]")
    print(f"    IQR:    [{s['p25']:.3f}, {s['p75']:.3f}]  span={s['iqr_span']:.3f}  ({iqr_pct:.1f}% of [0,1])")
    print(f"    Mean:   {s['mean']:.3f}")
    print(f"    Verdict: {verdict}  (threshold: IQR span > {SPREAD_THRESHOLD:.0%})")

    # Distribution histogram (8 buckets over [0,1])
    NBUCKETS = 8
    step     = 1.0 / NBUCKETS
    buckets: dict[int, int] = defaultdict(int)
    for v in vals:
        idx = min(int(v / step), NBUCKETS - 1)
        buckets[idx] += 1
    max_count = max(buckets.values(), default=1)
    for b in range(NBUCKETS):
        lo_b  = b * step
        hi_b  = lo_b + step
        count = buckets[b]
        bar   = "#" * (count * 40 // max_count)
        print(f"      [{lo_b:.2f}, {hi_b:.2f})  {count:>6}  {bar}")


def _print_distribution_report(
    component_cache: dict[tuple[str, str], dict],
    start: str,
    end: str,
) -> None:
    signals = list(AGGREGATOR_WEIGHTS.keys())
    all_vals = {sig: [v[sig] for v in component_cache.values()] for sig in signals}

    print(f"\nAggregator Weight Analysis — Phase 1: Component Distribution")
    print(f"Period: {start} → {end}   N entries: {len(component_cache)}")
    print(f"Compression threshold: IQR span < {SPREAD_THRESHOLD:.0%} of [0,1] theoretical range")
    print("=" * 70)

    spread_signals   = []
    compressed_signals = []

    for sig in signals:
        vals = all_vals[sig]
        s    = _iqr_stats(vals)
        iqr_pct = s["iqr_span"] / 1.0 * 100
        _print_signal_block(sig, vals, AGGREGATOR_WEIGHTS[sig], PRIOR_FINDINGS.get(sig))
        if iqr_pct >= SPREAD_THRESHOLD * 100:
            spread_signals.append((sig, AGGREGATOR_WEIGHTS[sig], iqr_pct))
        else:
            compressed_signals.append((sig, AGGREGATOR_WEIGHTS[sig], iqr_pct))

    # --- Summary verdict ---
    print(f"\n{'=' * 70}")
    print(f"Summary")
    print(f"{'=' * 70}")

    total_compressed_weight = sum(w for _, w, _ in compressed_signals)
    total_spread_weight     = sum(w for _, w, _ in spread_signals)

    print(f"\n  COMPRESSED signals ({len(compressed_signals)})  —  "
          f"total aggregator weight: {total_compressed_weight:.2f}")
    for sig, w, iqr_pct in compressed_signals:
        prior = " [prior-confirmed]" if sig in PRIOR_FINDINGS else " [Phase 1 finding]"
        print(f"    {sig:<18}  weight={w:.2f}  IQR={iqr_pct:.1f}%{prior}")

    print(f"\n  SPREAD signals ({len(spread_signals)})  —  "
          f"total aggregator weight: {total_spread_weight:.2f}")
    for sig, w, iqr_pct in spread_signals:
        print(f"    {sig:<18}  weight={w:.2f}  IQR={iqr_pct:.1f}%  → simulation sweep warranted")

    if not spread_signals:
        print(f"\n  ** NO signal warrants simulation sweep **")
        print(f"  All five aggregator signals are compressed or invariant in the post-L1 universe.")
        print(f"  The aggregator is mis-specified for this universe: a five-signal weighted sum")
        print(f"  designed for a universe with spread across all dimensions is operating on a")
        print(f"  universe where all ranking work falls on noise and cross-day state variation.")
        print(f"  This is a signal layer architecture question, not a parameter calibration question.")
    else:
        print(f"\n  {len(compressed_signals)} of 5 signals compressed ({total_compressed_weight:.2f} of weight).")
        if total_compressed_weight >= 0.45:
            print(f"  Majority of aggregator weight ({total_compressed_weight:.2f}) on signals with near-zero spread.")
            print(f"  Weight sweep for spread signals will surface miscalibration toward compressed signals.")

    print(f"\n  Evidence base for architectural finding:")
    print(f"    RSI discount (closed):     inversion — system penalised cohort it selected for")
    print(f"    momentum_score (0.25):     invariant — sub-weight co-movement post-L1")
    print(f"    volume_score (0.20):       invariant — source compression, 89% normal-volume days")
    for sig, w, iqr_pct in compressed_signals:
        if sig not in PRIOR_FINDINGS:
            print(f"    {sig} ({w:.2f}):  compressed — IQR {iqr_pct:.1f}% (Phase 1, {start}→{end})")
    for sig, w, iqr_pct in spread_signals:
        print(f"    {sig} ({w:.2f}):     spread candidate — IQR {iqr_pct:.1f}% (Phase 1, {start}→{end})")
    print()


if __name__ == "__main__":
    run_analysis()
