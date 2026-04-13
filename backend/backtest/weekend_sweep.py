"""
Weekend parameter sweep — runs Saturday night.

Grids over Lock 1 threshold, TP%, SL%, and hold days across the last 90 trading
days. The first run downloads and caches data to disk; all subsequent combos
load from cache, so the full grid completes in a few minutes.

Results saved to data/sweep_results.json.
Top 3 configs emailed/Slacked alongside the weekly report on Sunday.
"""
from __future__ import annotations

import itertools
import json
from datetime import date, timedelta
from pathlib import Path

from loguru import logger

_RESULTS_PATH = Path(__file__).parent.parent.parent / "data" / "sweep_results.json"

# ── Parameter grid ────────────────────────────────────────────────────────────

GRID = {
    "lock1_threshold":  [0.55, 0.60, 0.65, 0.70, 0.75],
    "take_profit_pct":  [0.06, 0.08, 0.10, 0.12],
    "stop_loss_pct":    [0.04, 0.05, 0.06],
    "time_stop_days":   [20, 25, 35],
    "vix_threshold":    [None, 30, 35],
    "use_leading_rs":   [False, True],
    "max_positions":    [3, 4, 6],
}

MIN_TRADES = 5       # discard configs with too few trades
LOOKBACK_DAYS = 90   # trading days to sweep over


def _date_range() -> tuple[str, str]:
    """Last 90 calendar days ending last Friday (most recent completed week)."""
    today = date.today()
    # Roll back to Friday if we're on a weekend
    days_back = (today.weekday() - 4) % 7  # 0 if today is Fri
    end = today - timedelta(days=days_back)
    start = end - timedelta(days=LOOKBACK_DAYS)
    return start.isoformat(), end.isoformat()


# ── Main entry point ──────────────────────────────────────────────────────────

def run_sweep() -> None:
    """Run the full parameter sweep and persist results. Called by scheduler."""
    from backend.backtest.engine_fast import precompute
    from backend.backtest.engine_fast import run as engine_run

    start_date, end_date = _date_range()
    combos = list(itertools.product(
        GRID["lock1_threshold"],
        GRID["take_profit_pct"],
        GRID["stop_loss_pct"],
        GRID["time_stop_days"],
        GRID["vix_threshold"],
        GRID["use_leading_rs"],
        GRID["max_positions"],
    ))
    total = len(combos)
    logger.info(f"Weekend sweep: {total} combos over {start_date} → {end_date}")

    # Build caches once — all combos share the same date range
    logger.info("Weekend sweep: precomputing signal cache…")
    pc = precompute(start_date, end_date)

    results = []
    for i, (l1, tp, sl, hold, vix, rs, mpos) in enumerate(combos, 1):
        try:
            r = engine_run(
                start_date=start_date,
                end_date=end_date,
                initial_balance=10_000.0,
                lock1_threshold=l1,
                take_profit_pct=tp,
                stop_loss_pct=sl,
                time_stop_days=hold,
                vix_threshold=vix,
                use_leading_rs=rs,
                max_positions=mpos,
                precomputed=pc,
            )
            if r["total_trades"] < MIN_TRADES:
                continue
            results.append({
                "lock1_threshold":  l1,
                "take_profit_pct":  tp,
                "stop_loss_pct":    sl,
                "time_stop_days":   hold,
                "vix_threshold":    vix,
                "use_leading_rs":   rs,
                "max_positions":    mpos,
                "sharpe":           r["sharpe"],
                "total_return_pct": r["total_return_pct"],
                "win_rate":         r["win_rate"],
                "profit_factor":    r["profit_factor"],
                "max_drawdown":     r["max_drawdown"],
                "total_trades":     r["total_trades"],
                "spy_return_pct":   r["spy_return_pct"],
            })
        except Exception as e:
            logger.debug(f"Sweep combo {i}/{total} (l1={l1} tp={tp} sl={sl} hold={hold} vix={vix} rs={rs} mpos={mpos}) failed: {e}")

        if i % 20 == 0:
            logger.info(f"Sweep progress: {i}/{total} ({len(results)} valid)")

    if not results:
        logger.warning("Sweep produced no valid results")
        return

    # Sort by Sharpe descending (ties broken by total return)
    results.sort(key=lambda r: (r["sharpe"], r["total_return_pct"]), reverse=True)

    payload = {
        "generated_at": _now_iso(),
        "start_date":   start_date,
        "end_date":     end_date,
        "total_combos": total,
        "valid_combos": len(results),
        "top_configs":  results[:20],
    }
    _RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_RESULTS_PATH, "w") as f:
        json.dump(payload, f, indent=2)

    logger.info(
        f"Sweep complete: {len(results)} valid configs. "
        f"Best: L1={results[0]['lock1_threshold']} "
        f"TP={results[0]['take_profit_pct']*100:.0f}% "
        f"SL={results[0]['stop_loss_pct']*100:.0f}% "
        f"hold={results[0]['time_stop_days']}d "
        f"maxpos={results[0]['max_positions']} "
        f"Sharpe={results[0]['sharpe']:.2f} "
        f"return={results[0]['total_return_pct']*100:.1f}%"
    )

    _notify_sweep(results[:3], start_date, end_date)


def _notify_sweep(top: list[dict], start_date: str, end_date: str) -> None:
    """Send a brief Slack/email alert with the top 3 sweep configs."""
    from backend.alerts import _cfg, _send_email, _send_slack
    from backend.demo_config import get_demo_config

    current = get_demo_config()
    cfg = _cfg()

    subject = "[APEX] Weekend Sweep Complete"

    lines = [
        f"Parameter sweep over {start_date} → {end_date} finished.",
        "",
        f"Current demo config: L1={current['lock1_threshold']} "
        f"TP={current['take_profit_pct']*100:.0f}% "
        f"SL={current['stop_loss_pct']*100:.0f}% "
        f"hold={current['max_hold_days']}d",
        "",
        "Top 3 configs (by Sharpe):",
    ]
    for i, r in enumerate(top, 1):
        alpha = ""
        if r["spy_return_pct"] is not None:
            alpha = f"  alpha={( r['total_return_pct'] - r['spy_return_pct'])*100:+.1f}%"
        lines.append(
            f"  #{i}: L1={r['lock1_threshold']} "
            f"TP={r['take_profit_pct']*100:.0f}% "
            f"SL={r['stop_loss_pct']*100:.0f}% "
            f"hold={r['time_stop_days']}d "
            f"pos={r['max_positions']} | "
            f"Sharpe={r['sharpe']:.2f}  return={r['total_return_pct']*100:.1f}%"
            f"  WR={r['win_rate']*100:.0f}% (n={r['total_trades']})"
            f"{alpha}"
        )
    lines.append("")
    lines.append("Full results at data/sweep_results.json — weekly report includes these.")

    body = "\n".join(lines)

    if cfg["slack_url"]:
        _send_slack(cfg["slack_url"], subject, body)
    if cfg["email_to"] and cfg["smtp_user"] and cfg["smtp_pass"]:
        _send_email(cfg, subject, body.replace("\n", "<br>"))


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
