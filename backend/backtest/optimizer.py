"""
Apex autoresearch optimizer — runs overnight, mutates gate parameters,
keeps improvements, discards regressions.

Inspired by Karpathy's autoresearch loop but adapted for apex:
- Mutation space: Lock1 threshold, TP%, SL%, trailing stop, hold days,
  max positions, VIX threshold (per-run params, not sector-specific yet)
- Scoring: weighted Sharpe − drawdown penalty, with floors on win rate
  and trade frequency to prevent degenerate solutions
- Each experiment uses the fast engine (precomputed signal cache)
- Results saved to data/optimizer_results.json
- Runs Saturday night, feeds best config into Sunday sweep for validation

Usage:
    python -m backend.backtest.optimizer
    # or from scheduler:
    from backend.backtest.optimizer import run_optimizer
    run_optimizer()
"""
from __future__ import annotations

import json
import math
import random
import time
from datetime import date, timedelta
from pathlib import Path
from typing import TypedDict

from loguru import logger

# ── Config ────────────────────────────────────────────────────────────────────

RESULTS_PATH   = Path(__file__).parent.parent.parent / "data" / "optimizer_results.json"
LOOKBACK_DAYS  = 90 * 3   # ~9 months of trading days for scoring
MIN_TRADES     = 10        # discard experiments with too few trades
MAX_EXPERIMENTS = 200      # hard cap per overnight run
TIME_BUDGET_SEC = 6 * 3600 # 6 hours max

# Scoring weights
W_SHARPE    = 0.60
W_DRAWDOWN  = 0.25   # penalty weight — higher = more drawdown aversion
W_WIN_RATE  = 0.15

# Floors — experiment is discarded if it falls below these
FLOOR_WIN_RATE     = 0.30   # minimum 30% win rate
FLOOR_TRADE_FREQ   = 8      # minimum trades over the lookback period

# Mutation bounds — agent cannot move params outside these ranges
BOUNDS = {
    "lock1_threshold":  (0.50, 0.85),
    "take_profit_pct":  (0.05, 0.18),
    "stop_loss_pct":    (0.03, 0.10),
    "trailing_stop_pct": (None, None),   # None = can be disabled
    "time_stop_days":   (10, 50),
    "max_positions":    (2, 8),
    "vix_threshold":    (20, 40),        # None = disabled
}

# Step sizes for each param (how much a single mutation moves it)
STEPS = {
    "lock1_threshold":   0.03,
    "take_profit_pct":   0.01,
    "stop_loss_pct":     0.01,
    "trailing_stop_pct": 0.01,
    "time_stop_days":    5,
    "max_positions":     1,
    "vix_threshold":     5,
}


class ParamSet(TypedDict):
    lock1_threshold:   float
    take_profit_pct:   float
    stop_loss_pct:     float
    trailing_stop_pct: float | None
    time_stop_days:    int
    max_positions:     int
    vix_threshold:     float | None


class ExperimentResult(TypedDict):
    params:        ParamSet
    sharpe:        float
    max_drawdown:  float
    win_rate:      float | None
    total_trades:  int
    total_return:  float
    score:         float   # composite optimisation score
    kept:          bool


# ── Scoring function ──────────────────────────────────────────────────────────

def _composite_score(sharpe: float, max_dd: float, win_rate: float | None, n_trades: int) -> float | None:
    """
    Returns a composite score for optimisation. Higher is better.
    Returns None if the result fails floor checks (experiment discarded).
    """
    if n_trades < FLOOR_TRADE_FREQ:
        return None
    if win_rate is not None and win_rate < FLOOR_WIN_RATE:
        return None
    if not math.isfinite(sharpe):
        return None

    # Drawdown penalty: normalise max_dd (0→0 penalty, 0.5→full penalty)
    dd_penalty = min(max_dd / 0.50, 1.0)

    # Win rate contribution: normalise to 0-1 range (30%→0, 70%→1)
    wr_score = min(max((win_rate or 0.0) - 0.30, 0.0) / 0.40, 1.0)

    # Sharpe contribution: normalise (0→0, 2→1)
    sharpe_score = min(max(sharpe, 0.0) / 2.0, 1.0)

    score = (
        W_SHARPE   * sharpe_score
        - W_DRAWDOWN * dd_penalty
        + W_WIN_RATE * wr_score
    )
    return round(score, 5)


# ── Mutation logic ────────────────────────────────────────────────────────────

def _mutate(params: ParamSet, n_mutations: int = 1) -> ParamSet:
    """
    Return a mutated copy of params. Applies n_mutations independent changes.
    Each change picks a random param and nudges it ±1 step.
    """
    import copy
    p = copy.deepcopy(params)

    mutable_keys = list(STEPS.keys())

    for _ in range(n_mutations):
        key = random.choice(mutable_keys)

        if key == "trailing_stop_pct":
            # Toggle between None and a float, or adjust if already set
            if p["trailing_stop_pct"] is None:
                p["trailing_stop_pct"] = 0.05
            elif random.random() < 0.2:
                p["trailing_stop_pct"] = None
            else:
                step = STEPS[key]
                p["trailing_stop_pct"] = round(
                    max(0.02, min(0.15, p["trailing_stop_pct"] + random.choice([-step, step]))), 3
                )

        elif key == "vix_threshold":
            # Toggle between None (disabled) and a value
            if p["vix_threshold"] is None:
                p["vix_threshold"] = 30.0
            elif random.random() < 0.2:
                p["vix_threshold"] = None
            else:
                step = STEPS[key]
                lo, hi = BOUNDS[key]
                p["vix_threshold"] = float(
                    max(lo, min(hi, p["vix_threshold"] + random.choice([-step, step])))
                )

        elif key == "max_positions":
            step = STEPS[key]
            lo, hi = BOUNDS[key]
            p["max_positions"] = int(
                max(lo, min(hi, p["max_positions"] + random.choice([-step, step])))
            )

        elif key == "time_stop_days":
            step = STEPS[key]
            lo, hi = BOUNDS[key]
            p["time_stop_days"] = int(
                max(lo, min(hi, p["time_stop_days"] + random.choice([-step, step])))
            )

        else:
            step = STEPS[key]
            lo, hi = BOUNDS[key]
            current = p[key]  # type: ignore[literal-required]
            new_val = round(max(lo, min(hi, current + random.choice([-step, step]))), 4)
            p[key] = new_val  # type: ignore[literal-required]

    return p


def _default_params() -> ParamSet:
    """Starting point — mirrors current apex defaults."""
    return ParamSet(
        lock1_threshold=0.70,
        take_profit_pct=0.08,
        stop_loss_pct=0.05,
        trailing_stop_pct=None,
        time_stop_days=25,
        max_positions=4,
        vix_threshold=None,
    )


# ── Date range ────────────────────────────────────────────────────────────────

def _date_range() -> tuple[str, str]:
    today    = date.today()
    days_back = (today.weekday() - 4) % 7   # roll to Friday
    end   = today - timedelta(days=days_back)
    start = end - timedelta(days=LOOKBACK_DAYS)
    return start.isoformat(), end.isoformat()


# ── Main optimizer loop ───────────────────────────────────────────────────────

def run_optimizer(seed: int | None = None) -> None:
    """
    Run the autoresearch optimization loop.
    Starts from current best params (or defaults), mutates, evaluates,
    keeps improvements. Saves all results to RESULTS_PATH.
    """
    from backend.backtest.engine_fast import precompute
    from backend.backtest.engine_fast import run as backtest_run

    if seed is not None:
        random.seed(seed)

    start_date, end_date = _date_range()
    logger.info(f"Optimizer: {start_date} → {end_date} | max {MAX_EXPERIMENTS} experiments")

    # Build all caches once — signal cache is the expensive part (~1-2 min).
    # All experiments share the same date range so there's no reason to rebuild.
    logger.info("Optimizer: building signal caches (once)…")
    pc = precompute(start_date, end_date)

    # Load previous best as starting point if available
    current_params = _load_best_params() or _default_params()
    eval_result    = _evaluate(backtest_run, current_params, start_date, end_date, pc)

    if eval_result is None:
        logger.warning("Optimizer: starting params produced no valid result — using defaults")
        current_params = _default_params()
        eval_result    = _evaluate(backtest_run, current_params, start_date, end_date, pc)

    current_score = eval_result[0] if eval_result is not None else None

    all_results: list[ExperimentResult] = []
    kept = 0
    t0   = time.time()

    logger.info(f"Optimizer: starting score = {current_score}")

    for i in range(1, MAX_EXPERIMENTS + 1):
        if time.time() - t0 > TIME_BUDGET_SEC:
            logger.info(f"Optimizer: time budget reached at experiment {i}")
            break

        # Occasionally make larger jumps (2-3 mutations) to escape local minima
        n_mut     = 1 if random.random() < 0.75 else random.randint(2, 3)
        candidate = _mutate(current_params, n_mutations=n_mut)
        ev        = _evaluate(backtest_run, candidate, start_date, end_date, pc)

        elapsed  = time.time() - t0
        improved = ev is not None and (current_score is None or ev[0] > current_score)

        if ev is not None:
            score, metrics = ev
            all_results.append(ExperimentResult(
                params=candidate,
                sharpe=round(metrics["sharpe"], 3),
                max_drawdown=round(metrics["max_drawdown"], 4),
                win_rate=metrics["win_rate"],
                total_trades=metrics["total_trades"],
                total_return=round(metrics["total_return_pct"], 4),
                score=score,
                kept=improved,
            ))

        if improved:
            current_params = candidate
            current_score  = ev[0]
            kept += 1
            logger.info(
                f"  ✓ [{i}/{MAX_EXPERIMENTS}] kept  score={current_score:.4f}  "
                f"(L1={candidate['lock1_threshold']} "
                f"TP={candidate['take_profit_pct']*100:.0f}% "
                f"SL={candidate['stop_loss_pct']*100:.0f}% "
                f"hold={candidate['time_stop_days']}d "
                f"pos={candidate['max_positions']})"
            )
        else:
            logger.debug(f"  ✗ [{i}/{MAX_EXPERIMENTS}] discarded  score={ev[0] if ev else None}")

        if i % 10 == 0:
            logger.info(
                f"Optimizer: {i}/{MAX_EXPERIMENTS} experiments | "
                f"{kept} kept | best score={current_score:.4f} | "
                f"elapsed={elapsed/60:.1f}min"
            )

    # Final full metrics for the best params (already have them from last kept eval)
    final_ev      = _evaluate(backtest_run, current_params, start_date, end_date, pc)
    final_metrics = final_ev[1] if final_ev is not None else {}

    _save_results(current_params, current_score, all_results, final_metrics, start_date, end_date)
    _notify(current_params, current_score, final_metrics, kept, len(all_results))

    logger.info(
        f"Optimizer complete: {kept}/{len(all_results)} experiments kept. "
        f"Best score={current_score:.4f}"
    )


def _evaluate(
    backtest_run,
    params: ParamSet,
    start_date: str,
    end_date: str,
    precomputed: dict | None = None,
) -> tuple[float, dict] | None:
    """Run backtest and return (composite_score, full_result). Returns None if invalid."""
    try:
        r = backtest_run(
            start_date=start_date,
            end_date=end_date,
            initial_balance=10_000.0,
            lock1_threshold=params["lock1_threshold"],
            take_profit_pct=params["take_profit_pct"],
            stop_loss_pct=params["stop_loss_pct"],
            trailing_stop_pct=params["trailing_stop_pct"],
            time_stop_days=params["time_stop_days"],
            max_positions=params["max_positions"],
            vix_threshold=params["vix_threshold"],
            precomputed=precomputed,
        )
        score = _composite_score(
            r["sharpe"], r["max_drawdown"], r["win_rate"], r["total_trades"]
        )
        if score is None:
            return None
        return score, r
    except Exception as e:
        logger.debug(f"Optimizer evaluate error: {e}")
        return None


# ── Persistence ───────────────────────────────────────────────────────────────

def _load_best_params() -> ParamSet | None:
    if not RESULTS_PATH.exists():
        return None
    try:
        with open(RESULTS_PATH) as f:
            data = json.load(f)
        return data.get("best_params")
    except Exception:
        return None


def _save_results(
    best_params: ParamSet,
    best_score: float | None,
    all_results: list[ExperimentResult],
    final_metrics: dict,
    start_date: str,
    end_date: str,
) -> None:
    from datetime import datetime, timezone

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "start_date":   start_date,
        "end_date":     end_date,
        "best_score":   best_score,
        "best_params":  best_params,
        "final_metrics": {
            "sharpe":          final_metrics.get("sharpe"),
            "max_drawdown":    final_metrics.get("max_drawdown"),
            "win_rate":        final_metrics.get("win_rate"),
            "total_trades":    final_metrics.get("total_trades"),
            "total_return_pct": final_metrics.get("total_return_pct"),
            "cagr":            final_metrics.get("cagr"),
            "profit_factor":   final_metrics.get("profit_factor"),
            "spy_return_pct":  final_metrics.get("spy_return_pct"),
        },
        "experiments_kept":  sum(1 for r in all_results if r["kept"]),
        "experiments_total": len(all_results),
        "experiment_history": [
            {"score": r["score"], "kept": r["kept"], "params": r["params"]}
            for r in all_results[-50:]   # keep last 50 for inspection
        ],
    }
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_PATH, "w") as f:
        json.dump(payload, f, indent=2)
    logger.info(f"Optimizer: results saved to {RESULTS_PATH}")


def _notify(
    best_params: ParamSet,
    best_score: float | None,
    final_metrics: dict,
    kept: int,
    total: int,
) -> None:
    try:
        from backend.alerts import _cfg, _send_email, _send_slack
        cfg = _cfg()

        subject = "[APEX] Overnight Optimizer Complete"
        m = final_metrics
        spy_line = ""
        if m.get("spy_return_pct") is not None:
            alpha = (m.get("total_return_pct") or 0) - m["spy_return_pct"]
            spy_line = f"\n  SPY return:   {m['spy_return_pct']*100:.1f}%  (alpha {alpha*100:+.1f}%)"

        body = f"""Overnight optimizer finished.

Experiments: {kept} kept / {total} total
Best composite score: {best_score:.4f}

Best params:
  Lock1 threshold:  {best_params['lock1_threshold']}
  Take profit:      {best_params['take_profit_pct']*100:.0f}%
  Stop loss:        {best_params['stop_loss_pct']*100:.0f}%
  Trailing stop:    {f"{best_params['trailing_stop_pct']*100:.0f}%" if best_params['trailing_stop_pct'] else 'disabled'}
  Hold days:        {best_params['time_stop_days']}
  Max positions:    {best_params['max_positions']}
  VIX threshold:    {best_params['vix_threshold'] or 'disabled'}

Final metrics on best params:
  Sharpe:       {m.get('sharpe', '—')}
  Max drawdown: {f"{m['max_drawdown']*100:.1f}%" if m.get('max_drawdown') is not None else '—'}
  Win rate:     {f"{m['win_rate']*100:.0f}%" if m.get('win_rate') else '—'}
  Trades:       {m.get('total_trades', '—')}
  Return:       {f"{m['total_return_pct']*100:.1f}%" if m.get('total_return_pct') is not None else '—'}{spy_line}

Full results: data/optimizer_results.json
"""
        if cfg.get("slack_url"):
            _send_slack(cfg["slack_url"], subject, body)
        if cfg.get("email_to") and cfg.get("smtp_user") and cfg.get("smtp_pass"):
            _send_email(cfg, subject, body.replace("\n", "<br>"))
    except Exception as e:
        logger.debug(f"Optimizer notify failed: {e}")


if __name__ == "__main__":
    run_optimizer()
