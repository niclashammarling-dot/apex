"""
Regime-conditioned backtest — any APEX sector.

Asks a narrower question than the threshold sweep: what is the PF on entries
filtered to days when the sector's Bayesian regime posterior was above a given
floor?

Posterior reconstruction uses OHLCV-derivable signals only:
  Signal 1 — ticker balance  (5d consecutive returns per ticker)
  Signal 2 — sector ETF signed streak
  Signal 5 — cross-sectional ETF return rank (10-day return)
  Signal 3 — neutral (1.0): leader RS divergence requires historical DB leadership chain
  Signal 4 — neutral (1/n): IPO share requires external IPO data

The posterior is simulated sequentially with POSTERIOR_DECAY=0.90 so conviction
accumulates across days, matching the live regime engine's behaviour.

Trade simulation is ticker-isolated (no portfolio-level caps). Entry = close
price on signal day. Exit follows TP/SL/time-stop from live config.

--matrix mode (posterior × signal_score interaction analysis):
  Extends the standard run to capture sub-threshold signals (floor lowered to
  MATRIX_MIN_SCORE=0.55). Outputs a 2D table: signal_score_band × posterior_band
  → N / WR / PF. Answers whether a high posterior changes the predictive value
  of a given signal score — the prerequisite for any posterior-conditioned
  threshold change.

  Also outputs signal-weighted PF for above-threshold trades (proxy for SIZED_UP
  mode: trades sized proportionally to signal score within the sector/day).
  Interpretation: if signal-weighted PF > equal-weighted PF, concentrating size
  on the stronger signal is beneficial. If not, the Bayesian multiplier is noise.

  Design context — mutual exclusion constraint:
    WIDE_THRESHOLD mode: lower L2 bar, suppress Bayesian multiplier (multiplier=1.0)
    SIZED_UP mode:       keep flat threshold, allow full Bayesian multiplier
  These are the only correct modes at high posterior — using both simultaneously
  is double leverage. The matrix tells you which mode is warranted.

Usage:
    cd /home/promenix/apex
    python -m backend.backtest.regime_conditioned_cs --sector ConsumerStaples
    python -m backend.backtest.regime_conditioned_cs --sector Energy --matrix
    python -m backend.backtest.regime_conditioned_cs --sector Utilities
"""
from __future__ import annotations

import argparse
import math
from datetime import date, timedelta

import pandas as pd
from loguru import logger

from backend.config import (
    LOCK1_THRESHOLD,
    SECTORS,
    SPY_TICKER,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    TIME_STOP_DAYS,
)
from backend.regime.regime_bayes import (
    POSTERIOR_DECAY,
    _bayesian_update,
    _lr_etf_streak,
    _lr_ipo_cluster,
    _lr_ticker_balance,
)
from backend.signals import aggregator, momentum, relative_strength, trend, volume
from backend.signals.ev_kelly import compute as ev_kelly_compute
from backend.backtest.engine_fast import (
    _download_all,
    _trading_days,
    _build_spy_cache,
    _build_price_cache,
)

# ── Config ────────────────────────────────────────────────────────────────────

START  = "2021-01-01"
END    = "2026-05-01"

# Posterior thresholds to stratify results against
POSTERIOR_LEVELS = [0.0, 0.40, 0.45, 0.50, 0.55, 0.60]

# L1 threshold applied to ticker scores (standard run)
L1 = LOCK1_THRESHOLD   # 0.70 — same gate as active sectors

# --matrix mode: score floor and stratification bands
MATRIX_MIN_SCORE   = 0.55
SIGNAL_SCORE_BANDS = [(0.55, 0.60), (0.60, 0.65), (0.65, 0.70), (0.70, 1.01)]
POSTERIOR_BANDS    = [(0.0, 0.50), (0.50, 0.70), (0.70, 0.85), (0.85, 1.01)]

# Bayesian prior constants
N_SECTORS            = len(SECTORS)
BASE_PRIOR           = 1.0 / N_SECTORS
TICKER_RECOVERY_DAYS = 5    # same as regime_bayes.TICKER_RECOVERY_DAYS
RS_RANK_DAYS         = 10   # n_days for cross-sectional rank (Signal 5)

W = 66


# ── Posterior reconstruction ──────────────────────────────────────────────────

def _compute_sector_posterior_series(
    raw_data: pd.DataFrame,
    trading_days: list[date],
    sector_etf: str,
    sector_tickers: list[str],
) -> dict[str, float]:
    """
    Simulate sector Bayesian posterior day-by-day with decay.

    Returns {date_str: posterior} for every trading day in range.
    """
    all_etfs = {sector: cfg["etf"] for sector, cfg in SECTORS.items()}

    persistent_prior = BASE_PRIOR
    posteriors: dict[str, float] = {}

    for today in trading_days:
        today_str = today.isoformat()
        ts        = pd.Timestamp(today)

        # Decay toward base prior
        decayed = POSTERIOR_DECAY * persistent_prior + (1.0 - POSTERIOR_DECAY) * BASE_PRIOR

        # Signal 1: ticker balance
        recovering    = 0
        deteriorating = 0
        total         = 0
        for ticker in sector_tickers:
            try:
                if ticker not in raw_data.columns.get_level_values(0):
                    continue
                closes = raw_data[ticker]["Close"].dropna()
                recent = closes[closes.index <= ts].tail(TICKER_RECOVERY_DAYS + 1)
                if len(recent) < TICKER_RECOVERY_DAYS + 1:
                    continue
                rets = recent.pct_change().dropna()
                total += 1
                if (rets > 0).all():
                    recovering += 1
                elif (rets < 0).all():
                    deteriorating += 1
            except Exception:
                continue
        lr_ticker = _lr_ticker_balance(recovering, deteriorating, total)

        # Signal 2: sector ETF signed streak
        etf_streak = 0
        try:
            if sector_etf in raw_data.columns.get_level_values(0):
                closes = raw_data[sector_etf]["Close"].dropna()
                recent = closes[closes.index <= ts].tail(60)
                if len(recent) >= 2:
                    rets = recent.pct_change().dropna().values
                    if len(rets) > 0:
                        direction = 1 if rets[-1] > 0 else (-1 if rets[-1] < 0 else 0)
                        if direction != 0:
                            count = 0
                            for r in reversed(rets):
                                if (direction > 0 and r > 0) or (direction < 0 and r < 0):
                                    count += 1
                                else:
                                    break
                            etf_streak = direction * count
        except Exception:
            pass
        lr_etf = _lr_etf_streak(etf_streak)

        # Signal 3: RS divergence — neutral (leader RS not reconstructable from OHLCV alone)
        lr_rs = 1.0

        # Signal 4: IPO cluster — neutral (1/n_sectors)
        lr_ipo = _lr_ipo_cluster(BASE_PRIOR)

        # Signal 5: cross-sectional ETF return rank
        lr_rank = _compute_rank_lrs_on(raw_data, all_etfs, today, n_days=RS_RANK_DAYS).get(
            next((s for s, cfg in SECTORS.items() if cfg["etf"] == sector_etf), ""), 1.0
        )

        # Sequential Bayesian update
        p = decayed
        p = _bayesian_update(p, lr_ticker)
        p = _bayesian_update(p, lr_etf)
        p = _bayesian_update(p, lr_rs)
        p = _bayesian_update(p, lr_ipo)
        p = _bayesian_update(p, lr_rank)

        posteriors[today_str] = round(p, 4)
        persistent_prior = p

    return posteriors


def _compute_rank_lrs_on(
    raw_data: pd.DataFrame,
    sector_etf_map: dict[str, str],
    today: date,
    n_days: int = 10,
    base: float = 1.5,
) -> dict[str, float]:
    """Cross-sectional ETF return rank LRs for a specific date."""
    ts = pd.Timestamp(today)
    returns: dict[str, float] = {}
    for sector, etf in sector_etf_map.items():
        try:
            if etf not in raw_data.columns.get_level_values(0):
                continue
            closes = raw_data[etf]["Close"].dropna()
            closes = closes[closes.index <= ts]
            if len(closes) < n_days + 1:
                continue
            returns[sector] = float(closes.iloc[-1] / closes.iloc[-(n_days + 1)] - 1)
        except Exception:
            continue

    if not returns:
        return {}

    sorted_items = sorted(returns.items(), key=lambda x: x[1], reverse=True)
    n = len(sorted_items)
    if n < 2:
        return {s: 1.0 for s in returns}

    lrs: dict[str, float] = {}
    i = 0
    while i < n:
        j = i
        while j < n and sorted_items[j][1] == sorted_items[i][1]:
            j += 1
        avg_rank = (i + j + 1) / 2
        for k in range(i, j):
            sector = sorted_items[k][0]
            lrs[sector] = round(base ** ((n + 1 - 2 * avg_rank) / (n - 1)), 4)
        i = j

    return lrs


# ── Ticker scoring ─────────────────────────────────────────────────────────────

def _score_tickers_per_day(
    raw_data: pd.DataFrame,
    trading_days: list[date],
    spy_cache: dict[str, dict],
    price_cache: dict[tuple[str, str], float],
    sector_tickers: list[str],
    sector_etf: str,
    min_score: float = L1,
) -> dict[tuple[str, str], dict]:
    """
    Returns {(ticker, date_str): {"signal_score", "entry_price"}} for all
    sector tickers × trading days where score >= min_score.
    min_score defaults to L1 (standard run). --matrix mode passes MATRIX_MIN_SCORE.
    """
    etf_mult_cache: dict[str, float] = _build_etf_mult(raw_data, trading_days, sector_etf)
    cache: dict[tuple[str, str], dict] = {}

    for ticker in sector_tickers:
        if ticker not in raw_data.columns.get_level_values(0):
            continue
        df_full = raw_data[ticker].copy()
        if df_full.empty or len(df_full) < 55:
            continue

        for d in trading_days:
            ts       = pd.Timestamp(d)
            date_str = d.isoformat()
            try:
                df = df_full[df_full.index <= ts].tail(90)
                if len(df) < 55:
                    continue

                spy_ret20 = spy_cache.get(date_str, {}).get("return_20d", 0.0)
                spy_reg   = spy_cache.get(date_str, {}).get("regime", 0.0)
                etf_mult  = etf_mult_cache.get(date_str, 1.0)

                mom = momentum.compute(df)
                if mom.get("rsi_blocked"):
                    continue
                vol = volume.compute(df)
                trd = trend.compute(df)
                rs  = relative_strength.compute(df, spy_ret20)

                atr_raw = (df["High"] - df["Low"]).rolling(14).mean().iloc[-1]
                atr_pct = float(atr_raw / mom["price"]) if mom["price"] and not math.isnan(atr_raw) else 0.0

                evk   = ev_kelly_compute(spy_reg, rolling_win_rate=None, atr_pct=atr_pct)
                score = aggregator.compute(
                    mom["momentum_score"], vol["volume_score"], evk["ev_norm"],
                    trd["trend_score"], rs["rs_score"],
                )
                score = round(min(max(score * etf_mult, 0.0), 1.0), 4)

                if score < min_score:
                    continue

                entry_price = price_cache.get((ticker, date_str))
                if entry_price is None or entry_price <= 0:
                    continue

                cache[(ticker, date_str)] = {"signal_score": score, "entry_price": entry_price}
            except Exception:
                continue

    return cache


def _build_etf_mult(
    raw_data: pd.DataFrame,
    trading_days: list[date],
    sector_etf: str,
) -> dict[str, float]:
    """Sector ETF multiplier: 1.0 if price >= MA20, else 0.85."""
    cache: dict[str, float] = {}
    try:
        closes = raw_data[sector_etf]["Close"].dropna()
        ma20   = closes.rolling(20).mean()
        for d in trading_days:
            ts = pd.Timestamp(d)
            try:
                price = float(closes.asof(ts))
                ma    = float(ma20.asof(ts))
                cache[d.isoformat()] = 1.0 if price >= ma else 0.85
            except Exception:
                cache[d.isoformat()] = 1.0
    except Exception:
        for d in trading_days:
            cache[d.isoformat()] = 1.0
    return cache


# ── Trade simulation ──────────────────────────────────────────────────────────

def _simulate_trades(
    entries: dict[tuple[str, str], dict],
    price_cache: dict[tuple[str, str], float],
    trading_days: list[date],
    posteriors: dict[str, float],
) -> list[dict]:
    """
    Simulate trades for each (ticker, entry_date) in entries.
    Each trade is evaluated independently (no portfolio caps).
    Returns list of trade records with entry_date posterior attached.
    """
    day_index = {d: i for i, d in enumerate(trading_days)}
    records   = []

    for (ticker, entry_date_str), sig in entries.items():
        entry_price = sig["entry_price"]
        entry_date  = date.fromisoformat(entry_date_str)
        entry_idx   = day_index.get(entry_date)
        if entry_idx is None:
            continue

        posterior    = posteriors.get(entry_date_str, BASE_PRIOR)
        peak_price   = entry_price
        outcome      = "EXPIRED"
        exit_price   = entry_price
        exit_reason  = "END_OF_BACKTEST"
        pnl_pct      = 0.0

        future_days = trading_days[entry_idx + 1:]
        for i, future in enumerate(future_days):
            future_str = future.isoformat()
            current = price_cache.get((ticker, future_str))
            if current is None:
                continue

            peak_price = max(peak_price, current)
            pnl_pct    = (current - entry_price) / entry_price
            days_held  = i + 1

            if pnl_pct >= TAKE_PROFIT_PCT:
                outcome, exit_price, exit_reason = "WIN", current, "TP"
                break
            elif pnl_pct <= -STOP_LOSS_PCT:
                outcome, exit_price, exit_reason = "LOSS", current, "SL"
                break
            elif days_held >= TIME_STOP_DAYS:
                outcome, exit_price, exit_reason = "EXPIRED", current, "TIME"
                break

        records.append({
            "ticker":       ticker,
            "entry_date":   entry_date_str,
            "entry_price":  round(entry_price, 4),
            "exit_price":   round(exit_price, 4),
            "pnl_pct":      round(pnl_pct, 4),
            "outcome":      outcome,
            "exit_reason":  exit_reason,
            "posterior":    posterior,
            "signal_score": sig["signal_score"],
        })

    return records


# ── Stats & display ───────────────────────────────────────────────────────────

def _stats(records: list[dict]) -> dict:
    wins   = [r for r in records if r["outcome"] == "WIN"]
    losses = [r for r in records if r["outcome"] in ("LOSS", "EXPIRED")]
    closed = wins + losses
    gp = sum(r["pnl_pct"] for r in wins)        if wins   else 0.0
    gl = abs(sum(r["pnl_pct"] for r in losses)) if losses else 0.0
    return {
        "n":        len(records),
        "win_rate": len(wins) / len(closed) if closed else None,
        "pf":       gp / gl if gl > 0 else None,
        "avg_win":  sum(r["pnl_pct"] for r in wins) / len(wins) if wins else None,
        "avg_loss": sum(r["pnl_pct"] for r in losses) / len(losses) if losses else None,
    }

def _pct(v: float | None) -> str:
    if v is None:
        return "   —   "
    return f"{v*100:+.1f}%"

def _bar(char="─", w=W) -> str:
    return char * w


# ── Matrix analysis helpers ───────────────────────────────────────────────────

def _attach_multipliers(
    records: list[dict],
    sector_threshold: float,
) -> None:
    """
    Attach approximate Bayesian multiplier to each record (in-place).

    Multiplier = n_qualifying × score / sum_qualifying_scores, computed from
    same-day trades that would qualify in SIZED_UP mode (score ≥ sector_threshold).
    Sub-threshold trades (WIDE_THRESHOLD candidates) receive multiplier=1.0 —
    the multiplier is suppressed when the threshold is lowered.

    The multiplier here is positional sizing information only — it does not
    change PF in percentage terms. It represents how much larger (or smaller)
    each trade would be under SIZED_UP mode relative to equal-weight baseline.
    """
    from collections import defaultdict
    day_qualifying: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        if r["signal_score"] >= sector_threshold:
            day_qualifying[r["entry_date"]].append(r)

    for r in records:
        if r["signal_score"] < sector_threshold:
            r["multiplier"] = 1.0   # suppressed in WIDE_THRESHOLD mode
            continue
        peers = day_qualifying[r["entry_date"]]
        if len(peers) <= 1:
            r["multiplier"] = 1.0
            continue
        total_score = sum(p["signal_score"] for p in peers)
        if total_score <= 0:
            r["multiplier"] = 1.0
        else:
            r["multiplier"] = round(len(peers) * r["signal_score"] / total_score, 4)


def _signal_weighted_pf(records: list[dict]) -> float | None:
    """
    PF weighted by each trade's Bayesian multiplier.
    Represents dollar-weighted outcome under SIZED_UP mode.
    """
    wins   = [r for r in records if r["outcome"] == "WIN"]
    losses = [r for r in records if r["outcome"] in ("LOSS", "EXPIRED")]
    gp = sum(r["pnl_pct"] * r["multiplier"] for r in wins)
    gl = abs(sum(r["pnl_pct"] * r["multiplier"] for r in losses))
    return round(gp / gl, 3) if gl > 0 else None


def _signal_band(score: float) -> tuple[float, float] | None:
    for lo, hi in SIGNAL_SCORE_BANDS:
        if lo <= score < hi:
            return (lo, hi)
    return None


def _count_episodes(records: list[dict], gap_days: int = 5) -> int:
    """
    Count distinct regime episodes among a set of trade records.

    Episode: a run of entry dates where no consecutive gap exceeds gap_days
    calendar days. One episode = one regime window (e.g., a single ETF streak).
    Multiple episodes = independent regime periods.

    Trade count (N) measures how many entries occurred.
    Episode count (ep) measures how many independent regime events produced them.
    When N >> ep, trades are time-clustered — the cell's PF reflects fewer
    independent data points than N implies, and should be treated with more
    scepticism regardless of the PF value.
    """
    if not records:
        return 0
    dates = sorted(date.fromisoformat(r["entry_date"]) for r in records)
    episodes = 1
    for i in range(1, len(dates)):
        if (dates[i] - dates[i - 1]).days > gap_days:
            episodes += 1
    return episodes


_MATRIX_W = 130   # matrix output width; wider than W to accommodate 4 posterior columns


def _print_matrix(records: list[dict], sector_threshold: float, sector_name: str) -> None:
    """
    2D matrix: signal_score_band × posterior_band → N / ep / WR / PF (equal-weighted).

    ep = distinct regime episodes (groups of entry dates ≤5 calendar days apart,
    scoped to each cell). When N >> ep, trades cluster in time — PF reflects fewer
    independent regime observations than N implies.

    (sw-PF) on above-threshold rows = signal-weighted PF, the SIZED_UP proxy.

    Reading the matrix:
      Horizontal scan of a signal band: does PF improve as posterior rises?
        Yes → posterior adds predictive value at that score level → WIDE_THRESHOLD warranted
        No  → posterior irrelevant for entry decisions → keep flat threshold
      Vertical scan of a posterior band: does score still matter within high-posterior?
        Yes → signal quality still discriminates → threshold should remain above floor
        No  → all scores equivalent at high posterior → threshold is redundant
    """
    col_w = 30
    band_labels = [f"post [{lo:.2f},{hi:.2f})" for lo, hi in POSTERIOR_BANDS]

    print()
    print("═" * _MATRIX_W)
    print(f"  SIGNAL × POSTERIOR MATRIX — {sector_name}")
    print(f"  Calibrated sector threshold: {sector_threshold:.4f}  |  Sweep floor: {MATRIX_MIN_SCORE}")
    print(f"  ↓ rows = WIDE_THRESHOLD candidates (sub-threshold, multiplier suppressed)")
    print(f"  (sw-PF) = signal-weighted PF proxy for SIZED_UP mode (above-threshold rows only)")
    print(f"  ep = distinct regime episodes per cell (N >> ep → time-clustered, fewer independent observations)")
    print("═" * _MATRIX_W)

    header = f"  {'Signal band':>14}  " + "  ".join(f"{lbl:>{col_w}}" for lbl in band_labels)
    print()
    print(header)
    print("─" * _MATRIX_W)

    for lo, hi in SIGNAL_SCORE_BANDS:
        band_recs = [r for r in records if lo <= r["signal_score"] < hi]
        cells = []
        for plo, phi in POSTERIOR_BANDS:
            cell_recs = [r for r in band_recs if plo <= r["posterior"] < phi]
            s         = _stats(cell_recs)
            ep        = _count_episodes(cell_recs)
            pf_str    = f"{s['pf']:.3f}" if s["pf"] is not None else "  —  "
            wr_str    = f"{s['win_rate']*100:.0f}%" if s["win_rate"] is not None else " — "
            if lo >= sector_threshold and s["n"] > 0:
                sw     = _signal_weighted_pf(cell_recs)
                sw_str = f"({sw:.3f})" if sw is not None else "      "
                cell   = f"N={s['n']:>3} ep={ep:>2} {wr_str:>4} PF={pf_str} {sw_str}"
            else:
                cell   = f"N={s['n']:>3} ep={ep:>2} {wr_str:>4} PF={pf_str}       "
            cells.append(cell)
        marker = "  " if lo >= sector_threshold else " ↓"
        label  = f"[{lo:.2f},{hi if hi < 1.0 else '∞':>5})"
        print(f"{marker} {label:>14}  " + "  ".join(f"{c:>{col_w}}" for c in cells))

    print()
    print("  ↓ sub-threshold  |  (sw-PF) signal-weighted PF  |  ep = regime episodes (N >> ep → clustered)")
    print()

    # Sub-threshold summary: the decisive block for the WIDE_THRESHOLD decision.
    # Each row answers: at this posterior level, do sub-threshold entries have edge?
    print("  Sub-threshold summary  [{:.2f}, {:.4f}):".format(MATRIX_MIN_SCORE, sector_threshold))
    print("─" * _MATRIX_W)
    sub = [r for r in records if r["signal_score"] < sector_threshold]
    for plo, phi in POSTERIOR_BANDS:
        cell_recs = [r for r in sub if plo <= r["posterior"] < phi]
        s         = _stats(cell_recs)
        ep        = _count_episodes(cell_recs)
        pf_str    = f"{s['pf']:.3f}" if s["pf"] is not None else "  —  "
        wr_str    = f"{s['win_rate']*100:.0f}%" if s["win_rate"] is not None else " — "
        label     = f"post [{plo:.2f},{phi:.2f})"
        print(f"    {label:>22}  N={s['n']:>3}  ep={ep:>2}  WR={wr_str:>4}  PF={pf_str}")

    print()
    print("  Interpretation:")
    sub_high = [r for r in sub if r["posterior"] >= 0.85]
    sub_low  = [r for r in sub if r["posterior"] <  0.50]
    s_high   = _stats(sub_high)
    s_low    = _stats(sub_low)
    ep_high  = _count_episodes(sub_high)
    if ep_high < 5:
        print(f"    post ≥ 0.85 sub-threshold: ep={ep_high} — too few independent episodes to conclude")
    elif s_high["pf"] is not None and s_low["pf"] is not None:
        if s_high["pf"] > 1.0 and s_high["pf"] > s_low["pf"] * 1.10:
            print("    Sub-threshold PF improves meaningfully at high posterior → WIDE_THRESHOLD warranted")
        elif s_high["pf"] < 1.0:
            print("    Sub-threshold PF < 1.0 even at high posterior → flat threshold correct; wait for arrival")
        else:
            print("    Sub-threshold PF marginally higher at high posterior → inconclusive; require ep ≥ 5 before acting")
    elif s_high["n"] < 10:
        print(f"    post ≥ 0.85 sub-threshold: N={s_high['n']} ep={ep_high} — insufficient data to conclude")

    print()
    print("═" * _MATRIX_W)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Regime-conditioned sector backtest")
    parser.add_argument(
        "--sector",
        default="ConsumerStaples",
        help="Sector name as defined in config.py SECTORS (default: ConsumerStaples)",
    )
    parser.add_argument(
        "--matrix",
        action="store_true",
        help=(
            "2D interaction analysis: signal_score_band × posterior_band → PF. "
            "Lowers score floor to 0.55 to capture sub-threshold candidates. "
            "Use to evaluate whether a posterior-conditioned threshold is warranted."
        ),
    )
    parser.add_argument(
        "--exclude",
        nargs="+",
        default=[],
        metavar="TICKER",
        help="Exclude one or more tickers from the sector sweep (e.g. --exclude AMZN).",
    )
    parser.add_argument(
        "--floor",
        type=float,
        default=None,
        metavar="FLOAT",
        help="Override the L1 score floor (default: LOCK1_THRESHOLD=0.70).",
    )
    args = parser.parse_args()

    sector_name = args.sector
    matrix_mode = args.matrix
    if sector_name not in SECTORS:
        raise ValueError(f"Unknown sector '{sector_name}'. Valid: {list(SECTORS.keys())}")

    sector_etf       = SECTORS[sector_name]["etf"]
    sector_tickers   = [t for t in SECTORS[sector_name]["tickers"] if t not in args.exclude]
    l1_floor         = args.floor if args.floor is not None else L1
    effective_min    = MATRIX_MIN_SCORE if matrix_mode else l1_floor

    logger.info(
        f"Regime-conditioned backtest: {sector_name} ({sector_etf})  {START} → {END}"
        + ("  [MATRIX MODE]" if matrix_mode else "")
    )

    lookback_start = date.fromisoformat(START) - timedelta(days=130)
    end_date       = date.fromisoformat(END)
    etf_tickers    = [cfg["etf"] for cfg in SECTORS.values()]
    all_tickers    = (
        [SPY_TICKER, "^VIX"]
        + etf_tickers
        + [t for cfg in SECTORS.values() for t in cfg["tickers"]]
    )
    logger.info("Loading/downloading OHLCV data…")
    raw_data = _download_all(all_tickers, lookback_start, end_date)
    if raw_data.empty:
        raise RuntimeError("Failed to download historical data")

    trading_days = _trading_days(date.fromisoformat(START), end_date, raw_data)
    logger.info(f"{len(trading_days)} trading days")

    spy_cache   = _build_spy_cache(raw_data, trading_days)
    price_cache = _build_price_cache(raw_data, all_tickers, trading_days)

    # 1. Posterior series
    logger.info(f"Computing {sector_name} regime posteriors…")
    posteriors  = _compute_sector_posterior_series(raw_data, trading_days, sector_etf, sector_tickers)
    post_values = list(posteriors.values())
    logger.info(
        f"Posterior stats: min={min(post_values):.3f}  "
        f"mean={sum(post_values)/len(post_values):.3f}  "
        f"max={max(post_values):.3f}"
    )

    # 2. Score tickers
    logger.info(f"Scoring {sector_name} tickers (this takes ~1–2 min)…")
    entries = _score_tickers_per_day(
        raw_data, trading_days, spy_cache, price_cache,
        sector_tickers, sector_etf, min_score=effective_min,
    )
    logger.info(f"{len(entries)} entry signals passing floor={effective_min}")

    # 3. Simulate trades
    logger.info("Simulating trades…")
    records = _simulate_trades(entries, price_cache, trading_days, posteriors)
    logger.info(f"{len(records)} trades simulated")

    # 4. Display
    # Retrieve the per-sector calibrated threshold for matrix multiplier computation.
    # Falls back to LOCK1_THRESHOLD if not in DB (e.g., during development runs).
    try:
        from backend.db import get_ticker_thresholds
        sector_threshold = get_ticker_thresholds().get(sector_name, float(LOCK1_THRESHOLD))
    except Exception:
        sector_threshold = float(LOCK1_THRESHOLD)

    if matrix_mode:
        _attach_multipliers(records, sector_threshold)
        _print_matrix(records, sector_threshold, sector_name)

    entry_posts = [r["posterior"] for r in records if r["signal_score"] >= sector_threshold] \
        if matrix_mode else [r["posterior"] for r in records]
    buckets = [0.40, 0.45, 0.50, 0.55, 0.60, 0.65]
    print()
    print("═" * W)
    print(f"  REGIME-CONDITIONED BACKTEST — {sector_name} ({sector_etf})")
    print(f"  {START} → {END}  |  threshold = {sector_threshold:.4f}"
          + (f"  (calibrated)  |  sweep floor = {MATRIX_MIN_SCORE}" if matrix_mode else f"  |  L1 = {l1_floor}"))
    print(f"  Signals used: ticker balance, {sector_etf} streak, ETF rank (IPO/RS = neutral)")
    if matrix_mode:
        print(f"  Standard output below uses only trades at score ≥ {sector_threshold:.4f} (above-threshold comparison baseline)")
    print("═" * W)

    print()
    print("  Posterior distribution at entry (days with ≥1 passing signal):")
    print(_bar())
    for lo, hi in zip(buckets, buckets[1:] + [1.01]):
        n = sum(1 for p in entry_posts if lo <= p < hi)
        pct = n / len(entry_posts) * 100 if entry_posts else 0.0
        print(f"    [{lo:.2f}, {hi:.2f})  {n:>4} signals  {pct:.1f}%")
    print()

    display_records = [r for r in records if r["signal_score"] >= sector_threshold] \
        if matrix_mode else records

    print(f"  {'Posterior floor':>18}  {'Trades':>6}  {'WinRate':>8}  {'PF':>6}  {'AvgWin':>8}  {'AvgLoss':>8}")
    print(_bar())
    for floor in POSTERIOR_LEVELS:
        subset = [r for r in display_records if r["posterior"] >= floor]
        s      = _stats(subset)
        pf_str = f"{s['pf']:.3f}" if s["pf"] is not None else "  —"
        label  = "unconditional" if floor == 0.0 else f"post ≥ {floor:.2f}"
        marker = " *" if floor == 0.50 else "  "
        print(
            f"{marker} {label:>18}  "
            f"{s['n']:>6}  "
            f"{_pct(s['win_rate']):>8}  "
            f"{pf_str:>6}  "
            f"{_pct(s['avg_win']):>8}  "
            f"{_pct(s['avg_loss']):>8}"
        )

    print()
    print("═" * W)

    subset_50 = [r for r in display_records if r["posterior"] >= 0.50]
    if subset_50:
        print()
        print("  Per-ticker breakdown (post ≥ 0.50):")
        print(_bar())
        print(f"  {'Ticker':>8}  {'Trades':>6}  {'WinRate':>8}  {'PF':>6}")
        print(_bar())
        for ticker in sector_tickers:
            t_recs = [r for r in subset_50 if r["ticker"] == ticker]
            if not t_recs:
                continue
            s      = _stats(t_recs)
            pf_str = f"{s['pf']:.3f}" if s["pf"] is not None else "  —"
            print(
                f"  {ticker:>8}  "
                f"{s['n']:>6}  "
                f"{_pct(s['win_rate']):>8}  "
                f"{pf_str:>6}"
            )
        print()
        print("═" * W)

    print()


if __name__ == "__main__":
    main()
