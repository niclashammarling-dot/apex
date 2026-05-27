"""
Aggregator weight analysis — Q2: spread budget sweep.

Varies the total weight allocated to spread signals (trend_score + rs_score)
from 0.15 to 0.75 in 0.05 steps (13 values). At each budget level:
  - trend and rs hold their current 4:3 ratio (current 0.20:0.15)
  - compressed signals (momentum, volume, ev_norm) scale proportionally
    from their current 25:20:20 ratio to fill the remaining weight

Baseline: current budget = 0.35 (trend=0.20, rs=0.15, momentum=0.25,
volume=0.20, ev_norm=0.20).

Context (full parameter review series, 2026-05-27):
  - RSI discount:         inversion — closed, PF 1.57 vs 1.36
  - momentum split:       invariant — components co-move post-L1
  - volume divisor:       invariant — source compression, 89% normal-volume
  - ev_norm (Phase 1):    compressed — formula truncation at 0.27 (IQR 1.6%)
  - trend/rs split (Q1):  invariant — 0.65 compressed block suppresses ranking
                          resolution; spread signal delta (~0.09 max) can't
                          reorder candidates through the noise floor

Q2 hypothesis: reducing the compressed block frees signal_score spread,
which restores ranking resolution, which then translates to PF improvement.
The threshold budget level where both signal_score IQR and PF start moving
is the calibration target — if one moves without the other, the weighted-sum
architecture is the mis-specification, not just the weight distribution.

Signal score injection:
  For budget b:
    trend_w    = b * (4/7)
    rs_w       = b * (3/7)
    momentum_w = (1-b) * (25/65)
    volume_w   = (1-b) * (20/65)
    ev_norm_w  = (1-b) * (20/65)

  delta = sum over all five signals of (new_w - current_w) * component_cached
  signal_score_adjusted = base_signal_score + delta

Signal_score spread metric:
  At each budget level, collect adjusted signal_scores for all L1 candidates
  across all simulation days. IQR of that distribution is the ranking
  resolution metric — widening IQR is the leading indicator before PF moves.

Usage:
    cd /home/promenix/apex
    python -m backend.backtest.aggregator_budget_analysis
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
from backend.signals import momentum, volume, trend, relative_strength
from backend.signals.ev_kelly import compute as ev_kelly_compute
from backend.backtest.engine_fast import (
    precompute,
    _check_exits_fast,
    _get_candidates_from_cache,
    _sector_exposure,
)

# Current weights
CW = {
    "momentum": 0.25,
    "volume":   0.20,
    "ev_norm":  0.20,
    "trend":    0.20,
    "rs":       0.15,
}

# trend:rs ratio held fixed (4:3)
_TREND_RATIO = 4 / 7
_RS_RATIO    = 3 / 7

# compressed signals proportional ratio (25:20:20)
_MOM_RATIO = 25 / 65
_VOL_RATIO = 20 / 65
_EV_RATIO  = 20 / 65

CURRENT_BUDGET = 0.35  # trend + rs at current weights

# 0.15, 0.20, 0.25, ..., 0.75 (13 values)
SWEEP_BUDGETS = [round(0.15 + i * 0.05, 2) for i in range(13)]


def _weights_at_budget(b: float) -> dict[str, float]:
    trend_w = round(b * _TREND_RATIO, 4)
    rs_w    = round(b * _RS_RATIO, 4)
    comp    = 1.0 - b
    return {
        "momentum": round(comp * _MOM_RATIO, 4),
        "volume":   round(comp * _VOL_RATIO, 4),
        "ev_norm":  round(comp * _EV_RATIO,  4),
        "trend":    trend_w,
        "rs":       rs_w,
    }


def _build_component_cache(
    raw_data: pd.DataFrame,
    ticker_sector: dict[str, str],
    spy_cache: dict[str, dict],
    trading_days: list[date],
) -> dict[tuple[str, str], dict]:
    """Per (ticker, date): all five signal component values."""
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
                    spy_reg = spy_cache.get(date_str, {}).get("regime", 0.0)
                    rs      = relative_strength.compute(df, spy_ret)
                    atr_raw = float(atr_series.asof(ts) or 0)
                    atr_pct = (atr_raw / mom["price"]) if mom["price"] > 0 and not math.isnan(atr_raw) else 0.0
                    evk     = ev_kelly_compute(spy_reg, rolling_win_rate=None, atr_pct=atr_pct)
                    cache[(ticker, date_str)] = {
                        "momentum": mom["momentum_score"],
                        "volume":   vol["volume_score"],
                        "ev_norm":  evk["ev_norm"],
                        "trend":    trd["trend_score"],
                        "rs":       rs["rs_score"],
                    }
                except Exception:
                    continue
        except Exception:
            continue
    logger.info(f"Component cache: {len(cache)} entries")
    return cache


def _delta(comp: dict, w: dict) -> float:
    return (
        (w["momentum"] - CW["momentum"]) * comp["momentum"] +
        (w["volume"]   - CW["volume"])   * comp["volume"]   +
        (w["ev_norm"]  - CW["ev_norm"])  * comp["ev_norm"]  +
        (w["trend"]    - CW["trend"])    * comp["trend"]    +
        (w["rs"]       - CW["rs"])       * comp["rs"]
    )


def _simulate(
    trading_days, price_cache, spy_cache, signal_cache,
    ticker_sector, sector_etf, etf_cache, component_cache,
    weights: dict[str, float],
) -> tuple[list[dict], list[float]]:
    """Returns (closed_trades, all_candidate_adjusted_scores)."""
    tp    = TAKE_PROFIT_PCT
    sl    = STOP_LOSS_PCT
    tdays = TIME_STOP_DAYS
    l1    = LOCK1_THRESHOLD
    SL_COOLDOWN_DAYS = 5

    balance            = 10_000.0
    open_trades:       list[dict]  = []
    closed_trades:     list[dict]  = []
    daily_losses:      dict[str, float] = {}
    sl_cooldown:       dict[str, date]  = {}
    candidate_scores:  list[float] = []   # all evaluated scores — ranking resolution metric

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

        # Compute adjusted scores for all candidates (ranking resolution metric)
        adjusted_candidates = []
        for sig in candidates:
            comp  = component_cache.get((sig["ticker"], today_str))
            score = (
                min(1.0, max(0.0, sig["signal_score"] + _delta(comp, weights)))
                if comp is not None else sig["signal_score"]
            )
            candidate_scores.append(score)
            adjusted_candidates.append({**sig, "signal_score": score})

        open_tickers     = {t["ticker"] for t in open_trades}
        sector_exp       = _sector_exposure(open_trades, balance)
        daily_loss_today = daily_losses.get(today_str, 0.0)

        for sig in sorted(adjusted_candidates, key=lambda s: s["signal_score"], reverse=True):
            if len(open_trades) >= MAX_POSITIONS:
                break
            if sig["ticker"] in open_tickers:
                continue
            if sl_cooldown.get(sig["ticker"], date.min) > today:
                continue
            if daily_loss_today >= DAILY_LOSS_CAP:
                break

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
                "signal_score": sig["signal_score"],
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
            "ticker":      trade["ticker"],
            "pnl_pct":     round(pnl_pct, 4),
            "outcome":     "WIN" if pnl_pct >= 0 else "LOSS",
            "exit_reason": "OPEN",
        })

    return closed_trades, candidate_scores


def run_analysis(start_date: str = "2023-01-01", end_date: str = "2026-01-01") -> None:
    logger.info(f"Aggregator budget sweep {start_date} → {end_date}")

    pc = precompute(start_date, end_date)
    raw_data      = pc["raw_data"]
    ticker_sector = pc["ticker_sector"]
    sector_etf    = pc["sector_etf"]
    trading_days  = pc["trading_days"]
    price_cache   = pc["price_cache"]
    spy_cache     = pc["spy_cache"]
    etf_cache     = pc["etf_cache"]
    signal_cache  = pc["signal_cache"]

    logger.info("Building five-signal component cache ...")
    component_cache = _build_component_cache(raw_data, ticker_sector, spy_cache, trading_days)

    sim_args = (trading_days, price_cache, spy_cache, signal_cache,
                ticker_sector, sector_etf, etf_cache, component_cache)

    results:      dict[float, list[dict]]  = {}
    score_spread: dict[float, float]       = {}  # budget → candidate score IQR span

    for b in SWEEP_BUDGETS:
        w     = _weights_at_budget(b)
        label = "CURRENT" if abs(b - CURRENT_BUDGET) < 0.001 else ""
        logger.info(f"Simulating budget={b:.2f} "
                    f"(trend={w['trend']:.3f} rs={w['rs']:.3f} "
                    f"mom={w['momentum']:.3f} vol={w['volume']:.3f} ev={w['ev_norm']:.3f}) {label}")
        trades, scores = _simulate(*sim_args, weights=w)
        results[b] = trades
        if scores:
            s_sorted = sorted(scores)
            n        = len(s_sorted)
            score_spread[b] = s_sorted[int(n * 0.75)] - s_sorted[int(n * 0.25)]

    _print_results(results, score_spread, start_date, end_date)


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
    score_spread: dict[float, float],
    start: str,
    end: str,
) -> None:
    print(f"\nAggregator Budget Sweep (Q2)  {start} → {end}")
    print(f"trend:rs ratio fixed at 4:3 — compressed signals scale proportionally\n")

    base_s      = _summary(results[CURRENT_BUDGET])
    base_spread = score_spread.get(CURRENT_BUDGET, 0.0)

    # --- Implied weights table ---
    print("  Implied weights at each budget level:")
    w_header = f"  {'budget':>8}  {'trend':>7}  {'rs':>7}  {'momentum':>10}  {'volume':>8}  {'ev_norm':>9}"
    print(w_header)
    print("  " + "-" * (len(w_header) - 2))
    for b in SWEEP_BUDGETS:
        w      = _weights_at_budget(b)
        marker = " ← current" if abs(b - CURRENT_BUDGET) < 0.001 else ""
        print(f"  {b:>8.2f}  {w['trend']:>7.4f}  {w['rs']:>7.4f}  "
              f"{w['momentum']:>10.4f}  {w['volume']:>8.4f}  {w['ev_norm']:>9.4f}{marker}")

    # --- Results table with signal_score IQR column ---
    print(f"\n  Results (score_IQR = IQR of all candidate adjusted scores per simulation):")
    r_header = (f"  {'budget':>8}  {'N':>5}  {'Win%':>6}  "
                f"{'Avg PnL%':>9}  {'PF':>6}  {'score_IQR':>10}  {'ΔIQR':>8}")
    print(r_header)
    print("  " + "-" * (len(r_header) - 2))

    for b in SWEEP_BUDGETS:
        s      = _summary(results[b])
        iqr    = score_spread.get(b, 0.0)
        d_iqr  = iqr - base_spread
        marker = " ← current" if abs(b - CURRENT_BUDGET) < 0.001 else ""
        pf_str = f"{s['pf']:.3f}" if not math.isinf(s['pf']) else "   inf"
        print(
            f"  {b:>8.2f}  {s['n']:>5}  {s['win_pct']:>5.1f}%  "
            f"{s['avg_pnl']:>+8.2f}%  {pf_str:>6}  {iqr:>10.4f}  {d_iqr:>+8.4f}{marker}"
        )

    # --- Delta block ---
    print(f"\n  Delta from current (budget={CURRENT_BUDGET}):")
    for b in SWEEP_BUDGETS:
        if abs(b - CURRENT_BUDGET) < 0.001:
            continue
        s    = _summary(results[b])
        dpf  = s["pf"] - base_s["pf"]
        dwr  = s["win_pct"] - base_s["win_pct"]
        diqr = score_spread.get(b, 0.0) - base_spread
        print(f"    b={b:.2f}  ΔPF={dpf:+.3f}  ΔWR={dwr:+.1f}%  Δscore_IQR={diqr:+.4f}")

    _print_finding(results, score_spread, base_s, base_spread)


def _print_finding(
    results:      dict[float, list[dict]],
    score_spread: dict[float, float],
    base_s:       dict,
    base_spread:  float,
) -> None:
    print(f"\nFinding Classification  (per apex-parameter-review-skill decision rule)")
    print("=" * 70)

    pf_by_b  = {b: _summary(trades)["pf"] for b, trades in results.items()}
    base_pf   = pf_by_b[CURRENT_BUDGET]
    all_equal = all(abs(pf_by_b[b] - base_pf) < 0.001 for b in SWEEP_BUDGETS)
    any_better = any(pf_by_b[b] > base_pf + 0.02 for b in SWEEP_BUDGETS)

    # IQR movement: does score_IQR widen materially as budget increases?
    iqr_moves = any(
        abs(score_spread.get(b, base_spread) - base_spread) > 0.005
        for b in SWEEP_BUDGETS
    )

    if all_equal and not iqr_moves:
        finding = (
            "ARCHITECTURE MIS-SPECIFICATION — PF flat and signal_score IQR flat across "
            "full budget range; rebalancing weight toward spread signals produces no "
            "ranking resolution improvement; the weighted-sum aggregator cannot surface "
            "the discriminative spread present in trend_score and rs_score regardless of "
            "weight allocation; signal layer architecture requires redesign"
        )
    elif all_equal and iqr_moves:
        finding = (
            "RANKING RESOLUTION WITHOUT OUTCOME IMPROVEMENT — signal_score IQR widens "
            "as spread budget increases but PF stays flat; ranking resolution is not "
            "translating to outcome improvement; the discriminative spread in trend/rs "
            "does not predict trade outcomes in this universe; weight rebalancing has no "
            "calibration target"
        )
    elif any_better:
        best_b  = max(SWEEP_BUDGETS, key=lambda b: pf_by_b[b])
        best_w  = _weights_at_budget(best_b)
        # Find threshold: first budget where PF exceeds current + 0.01
        threshold_b = next(
            (b for b in sorted(SWEEP_BUDGETS) if pf_by_b[b] > base_pf + 0.01),
            best_b
        )
        finding = (
            f"CALIBRATION TARGET — PF improves above budget threshold ~{threshold_b:.2f}; "
            f"best budget={best_b:.2f} "
            f"(trend={best_w['trend']:.3f} rs={best_w['rs']:.3f} "
            f"momentum={best_w['momentum']:.3f} volume={best_w['volume']:.3f} "
            f"ev_norm={best_w['ev_norm']:.3f}); "
            f"PF {pf_by_b[best_b]:.3f} vs {base_pf:.3f} current"
        )
    else:
        finding = f"VALIDATED — current budget={CURRENT_BUDGET} at or near optimum (PF {base_pf:.3f})"

    print(f"\n  {finding}")
    print(f"\n  Complete evidence base — parameter review series 2026-05-27:")
    print(f"    RSI discount:          inversion (closed) — PF 1.57 vs 1.36")
    print(f"    momentum split:        invariant — component co-movement post-L1")
    print(f"    volume divisor:        invariant — source compression, 89% normal-volume")
    print(f"    ev_norm weight:        compressed — formula truncation at 0.27 (IQR 1.6%)")
    print(f"    trend/rs split (Q1):   invariant — compressed block suppresses ranking resolution")
    print(f"    spread budget (Q2):    {finding.split(' — ')[0].lower()}")
    print(f"\n  Required output (apex-parameter-review-skill):")
    print(f"    Parameter:           spread signal budget (trend + rs total weight)")
    print(f"    File:                backend/signals/aggregator.py")
    print(f"    Current value:       {CURRENT_BUDGET} (trend={CW['trend']} rs={CW['rs']})")
    print(f"    Calibration prov.:   undocumented; no survivor-set calibration")
    print(f"    Reference universe:  post-L1 trade distribution, 2023-2026")
    print(f"    N trades (current):  {base_s['n']}")
    print(f"    Finding:             {finding.split(' — ')[0].lower()}")
    print()


if __name__ == "__main__":
    run_analysis()
