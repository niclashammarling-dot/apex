"""
Re-entry cooldown parameter sweep.

Tests combinations of sl_cooldown_days (after SL/TSL) and tp_cooldown_days
(after TP) against the current baseline (SL=5, TP=0).

Uses validated profit-lock parameters (trigger=4%, trail=1%) throughout.

Usage:
    cd /home/promenix/apex
    python -m backend.backtest.cooldown_sweep
"""
from __future__ import annotations

from itertools import product

from backend.backtest.engine import run
from backend.config import (
    PROFIT_LOCK_TRAIL_PCT,
    PROFIT_LOCK_TRIGGER_PCT,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    TIME_STOP_DAYS,
)

# ── Sweep config ───────────────────────────────────────────────────────────────

START = "2021-01-01"
END   = "2026-05-01"

TRAILING_STOP_PCT = 0.09

SL_DAYS_VALUES = [0, 3, 5, 7, 10]
TP_DAYS_VALUES = [0, 2, 3, 5, 7]

W = 78


# ── Metrics ────────────────────────────────────────────────────────────────────

def _metrics(result: dict) -> dict:
    trades = result.get("trade_log", [])
    closed = [t for t in trades if t.get("outcome") in ("WIN", "LOSS", "EXPIRED")]
    wins   = [t for t in closed if t.get("outcome") == "WIN"]
    losses = [t for t in closed if t.get("outcome") == "LOSS"]

    gross_win  = sum(t["pnl"] for t in wins   if t.get("pnl"))
    gross_loss = abs(sum(t["pnl"] for t in losses if t.get("pnl")))
    pf         = round(gross_win / gross_loss, 3) if gross_loss else float("inf")

    avg_win  = round(gross_win  / len(wins),   4) if wins   else 0.0
    avg_loss = round(gross_loss / len(losses), 4) if losses else 0.0

    return {
        "n":        len(closed),
        "wins":     len(wins),
        "losses":   len(losses),
        "win_rate": round(len(wins) / len(closed), 3) if closed else 0.0,
        "pf":       pf,
        "avg_win":  avg_win,
        "avg_loss": avg_loss,
        "final_bal": round(result.get("final_balance", 0), 2),
    }


def _row(label: str, m: dict) -> str:
    return (
        f"  {label:<30} n={m['n']:>4}  WR={m['win_rate']:.1%}  "
        f"PF={m['pf']:>5.3f}  avgW={m['avg_win']:>7.2f}  avgL={m['avg_loss']:>7.2f}  "
        f"bal=${m['final_bal']:,.0f}"
    )


def main() -> None:
    print("=" * W)
    print("  RE-ENTRY COOLDOWN SWEEP")
    print(f"  Period: {START} → {END}")
    print(f"  Base exits: TP={TAKE_PROFIT_PCT:.0%}  SL={STOP_LOSS_PCT:.0%}  "
          f"TSL={TRAILING_STOP_PCT:.0%}  TIME={TIME_STOP_DAYS}d")
    print(f"  Profit-lock: trigger={PROFIT_LOCK_TRIGGER_PCT:.0%}  trail={PROFIT_LOCK_TRAIL_PCT:.1%}")
    print("=" * W)

    shared = dict(
        trailing_stop_pct=TRAILING_STOP_PCT,
        profit_lock_trigger_pct=PROFIT_LOCK_TRIGGER_PCT,
        profit_lock_trail_pct=PROFIT_LOCK_TRAIL_PCT,
    )

    # Baseline — current production behaviour
    print("\nBaseline (SL=5d, TP=0d):")
    baseline_result = run(START, END, sl_cooldown_days=5, tp_cooldown_days=0, **shared)
    baseline = _metrics(baseline_result)
    print(_row("SL=5d  TP=0d", baseline))

    print(f"\nSweep — SL days × TP days ({len(SL_DAYS_VALUES)}×{len(TP_DAYS_VALUES)} grid):")
    print(f"  {'label':<30} {'n':>5}  {'WR':>6}  {'PF':>6}  {'avgW':>8}  {'avgL':>8}  {'bal':>10}")
    print("  " + "-" * (W - 2))

    best_pf    = baseline["pf"]
    best_label = "SL=5d  TP=0d"
    best_m     = baseline

    for sl_days, tp_days in product(SL_DAYS_VALUES, TP_DAYS_VALUES):
        if sl_days == 5 and tp_days == 0:
            continue  # already printed as baseline
        label = f"SL={sl_days}d  TP={tp_days}d"
        result = run(START, END, sl_cooldown_days=sl_days, tp_cooldown_days=tp_days, **shared)
        m = _metrics(result)
        marker = ""
        if m["pf"] > best_pf:
            best_pf    = m["pf"]
            best_label = label
            best_m     = m
            marker     = " ◀ best"
        print(_row(label, m) + marker)

    print("=" * W)
    print(f"\nBest PF:  {best_label}")
    print(_row(best_label, best_m))
    print(f"Baseline: {_row('SL=5d  TP=0d', baseline)}")
    delta_pf = best_m["pf"] - baseline["pf"]
    delta_wr = best_m["win_rate"] - baseline["win_rate"]
    print(f"\nDelta vs baseline:  PF {delta_pf:+.3f}  WR {delta_wr:+.1%}")
    print("=" * W)


if __name__ == "__main__":
    main()
