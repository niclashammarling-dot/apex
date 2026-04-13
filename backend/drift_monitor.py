"""
drift_monitor.py — Daily parameter drift detection for APEX.

Runs every trading day at market close. Compares rolling live-trade metrics
against historical baselines per sector and flags degradation before it
compounds into bad trades.

Architecture:
  - First run: seeds baselines from a historical backtest (2020-2024)
  - Every subsequent run: computes rolling metrics from live trades,
    compares to baseline, writes severity-ranked alerts to drift_alerts table
  - Lock 3 reads top alerts as structured context before each decision

Metrics tracked per sector:
  win_rate              — fraction of closed trades that won
  avg_signal_score      — average Lock 1 score at entry
  avg_pnl_pct           — average pnl/amount for closed trades
  signal_win_corr       — correlation between signal_score and outcome (proxy for Kelly accuracy)
  avg_hold_days         — average days held to close

Confidence weighting: min(1.0, n_trades / 50)
  50 trades = full confidence, fewer scales linearly.
  Severity (LOW/MEDIUM/HIGH) reflects magnitude; confidence reflects reliability.
  Both are visible in the dashboard so you don't over-react to early alerts.

Severity thresholds:
  LOW    — |deviation| < 15%
  MEDIUM — 15% ≤ |deviation| < 30%
  HIGH   — |deviation| ≥ 30%
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone

from loguru import logger

SEED_START  = "2020-01-01"
SEED_END    = "2024-12-31"
ROLLING_N   = 30    # live trades per sector for rolling window
CONFIDENCE_FULL = 50  # n_trades for confidence = 1.0

SEVERITY_MEDIUM = 0.15
SEVERITY_HIGH   = 0.30


# ── Entry point ───────────────────────────────────────────────────────────────

def run_drift_monitor() -> None:
    """Called by scheduler at market close every trading day."""
    from backend.db import has_drift_baselines

    if not has_drift_baselines():
        logger.info("drift_monitor: no baselines found — seeding from backtest")
        seed_baselines()

    _compute_and_alert()
    logger.info("drift_monitor: complete")


# ── Baseline seeding ──────────────────────────────────────────────────────────

def seed_baselines(start: str = SEED_START, end: str = SEED_END) -> dict:
    """
    Run a historical backtest and write per-sector metrics to drift_baselines.
    Returns a summary dict of {sector: {metric: value}}.
    Can be called manually via /api/drift/seed to re-seed with a custom range.
    """
    logger.info(f"drift_monitor: seeding baselines from {start} → {end}")
    from backend.backtest.engine_fast import precompute, run

    pc     = precompute(start, end)
    result = run(start, end, precomputed=pc)
    trades = result.get("trade_log", [])

    closed = [t for t in trades if t["outcome"] in ("WIN", "LOSS")]
    if not closed:
        logger.warning("drift_monitor: no closed trades in seed range — baselines not written")
        return {}

    summary = _write_baselines(closed, source="backtest")
    logger.info(f"drift_monitor: seeded {len(summary)} sectors from {len(closed)} trades")
    return summary


def _write_baselines(trades: list[dict], source: str = "backtest") -> dict:
    """Compute per-sector metrics and write to drift_baselines table."""
    from backend.db import upsert_drift_baseline

    by_sector: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by_sector[t["sector"]].append(t)

    summary = {}
    for sector, rows in by_sector.items():
        metrics = _compute_metrics(rows)
        for metric, value in metrics.items():
            upsert_drift_baseline("signal_chain", sector, metric, value, len(rows))
        summary[sector] = metrics

    return summary


# ── Nightly comparison ────────────────────────────────────────────────────────

def _compute_and_alert() -> None:
    """Pull live trades, compute metrics per sector, diff against baseline, write alerts."""
    from backend.db import (
        get_closed_trades_for_drift,
        get_drift_baselines,
        upsert_drift_alert,
    )

    baselines = get_drift_baselines()
    if not baselines:
        logger.warning("drift_monitor: baselines empty — skipping alert computation")
        return

    live_trades = get_closed_trades_for_drift(limit=500)
    if not live_trades:
        logger.info("drift_monitor: no live trades yet — nothing to diff")
        return

    by_sector: dict[str, list[dict]] = defaultdict(list)
    for t in live_trades:
        by_sector[t["sector"]].append(t)

    alerts_written = 0

    for sector, rows in by_sector.items():
        # Use most recent ROLLING_N trades per sector
        recent = sorted(rows, key=lambda r: r.get("exited_at") or "", reverse=True)[:ROLLING_N]
        n      = len(recent)
        conf   = min(1.0, n / CONFIDENCE_FULL)

        metrics = _compute_metrics(recent)

        for metric, current_val in metrics.items():
            key = ("signal_chain", sector, metric)
            if key not in baselines:
                continue

            baseline_val = baselines[key]["baseline_value"]
            if baseline_val == 0:
                continue

            deviation = (current_val - baseline_val) / abs(baseline_val)
            abs_dev   = abs(deviation)

            if abs_dev < 0.05:   # ignore trivial noise
                continue

            severity = (
                "HIGH"   if abs_dev >= SEVERITY_HIGH   else
                "MEDIUM" if abs_dev >= SEVERITY_MEDIUM else
                "LOW"
            )

            upsert_drift_alert({
                "parameter":      "signal_chain",
                "sector":         sector,
                "metric":         metric,
                "baseline_value": round(baseline_val, 4),
                "current_value":  round(current_val, 4),
                "deviation_pct":  round(deviation * 100, 2),
                "confidence":     round(conf, 3),
                "severity":       severity,
                "n_trades":       n,
            })
            alerts_written += 1

    logger.info(f"drift_monitor: {alerts_written} alerts written across {len(by_sector)} sectors")


# ── Metric computation ────────────────────────────────────────────────────────

def _compute_metrics(trades: list[dict]) -> dict[str, float]:
    """
    Compute the five drift metrics from a list of closed trade dicts.
    Handles both TradeRecord (from backtest) and trades-table rows (from DB).
    """
    results: dict[str, float] = {}

    wins   = [t for t in trades if t["outcome"] == "WIN"]
    losses = [t for t in trades if t["outcome"] == "LOSS"]
    closed = wins + losses
    n      = len(closed)

    if n == 0:
        return results

    # 1. win_rate
    results["win_rate"] = len(wins) / n

    # 2. avg_signal_score
    scores = [t["signal_score"] for t in closed if t.get("signal_score") is not None]
    if scores:
        results["avg_signal_score"] = sum(scores) / len(scores)

    # 3. avg_pnl_pct — from pnl/amount or pnl_pct depending on source
    pnl_pcts = []
    for t in closed:
        if t.get("pnl_pct") is not None:
            pnl_pcts.append(t["pnl_pct"])
        elif t.get("pnl") is not None and t.get("amount") and t["amount"] > 0:
            pnl_pcts.append(t["pnl"] / t["amount"])
    if pnl_pcts:
        results["avg_pnl_pct"] = sum(pnl_pcts) / len(pnl_pcts)

    # 4. signal_win_corr — correlation between signal_score and win (1) / loss (0)
    #    Proxy for Kelly accuracy: if signal scores stop predicting wins, something shifted
    scored = [(t["signal_score"], 1.0 if t["outcome"] == "WIN" else 0.0)
              for t in closed if t.get("signal_score") is not None]
    if len(scored) >= 5:
        xs = [s[0] for s in scored]
        ys = [s[1] for s in scored]
        corr = _pearson(xs, ys)
        if corr is not None:
            results["signal_win_corr"] = corr

    # 5. avg_hold_days
    hold_days = []
    for t in closed:
        entry = t.get("entry_date") or t.get("timestamp")
        exit_ = t.get("exit_date")  or t.get("exited_at")
        if entry and exit_:
            try:
                e = datetime.fromisoformat(entry.replace("Z", "+00:00"))
                x = datetime.fromisoformat(exit_.replace("Z", "+00:00"))
                hold_days.append(abs((x - e).days))
            except Exception:
                pass
    if hold_days:
        results["avg_hold_days"] = sum(hold_days) / len(hold_days)

    return results


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx  = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy  = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


# ── Lock 3 context formatter ──────────────────────────────────────────────────

def format_drift_context(limit: int = 5) -> str | None:
    """
    Return a structured natural-language block for Lock 3 context injection.
    Returns None if there are no active alerts.

    Format:
        DRIFT ALERTS (as of YYYY-MM-DD):
        [HIGH] Technology / signal_win_corr: -31% vs baseline (confidence 0.84, n=42)
        [MEDIUM] Energy / win_rate: -18% vs baseline (confidence 0.61, n=31)
    """
    from backend.db import get_drift_alerts

    alerts = get_drift_alerts(acknowledged=False, limit=limit)
    if not alerts:
        return None

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"DRIFT ALERTS (as of {today}):"]
    for a in alerts:
        sign  = "+" if a["deviation_pct"] >= 0 else ""
        lines.append(
            f"[{a['severity']}] {a['sector']} / {a['metric']}: "
            f"{sign}{a['deviation_pct']:.1f}% vs baseline "
            f"(confidence {a['confidence']:.2f}, n={a['n_trades']})"
        )
    return "\n".join(lines)
