"""
Profit-lock TSL parameter sweep.

Sweeps (profit_lock_trigger_pct, profit_lock_trail_pct) combinations against
a no-profit-lock baseline. Uses the full backtest period with live config
exit parameters (TP=6%, SL=5%, TSL=9%).

Constraint: trail < trigger (otherwise the TSL fires below the activation threshold).

Usage:
    cd /home/promenix/apex
    python -m backend.backtest.profit_lock_sweep
"""
from __future__ import annotations

import sys
from itertools import product

from backend.backtest.engine import run
from backend.config import STOP_LOSS_PCT, TAKE_PROFIT_PCT, TIME_STOP_DAYS

# ── Sweep config ───────────────────────────────────────────────────────────────

START = "2021-01-01"
END   = "2026-05-01"

# TSL must be set — profit-lock only activates when a trailing stop is active
TRAILING_STOP_PCT = 0.09

TRIGGER_VALUES = [0.01, 0.02, 0.03, 0.04, 0.05]   # peak gain that activates tighter trail
TRAIL_VALUES   = [0.005, 0.010, 0.015, 0.020, 0.025]  # drawdown from peak once activated

W = 72


# ── Metrics ────────────────────────────────────────────────────────────────────

def _metrics(result: dict) -> dict:
    trades = result.get("trade_log", [])
    closed = [t for t in trades if t.get("outcome") in ("WIN", "LOSS", "EXPIRED")]
    wins   = [t for t in closed if t.get("outcome") == "WIN"]
    losses = [t for t in closed if t.get("outcome") == "LOSS"]
    tsl_exits = [t for t in closed if t.get("exit_reason") == "TSL"]

    gross_win  = sum(t["pnl"] for t in wins  if t.get("pnl"))
    gross_loss = abs(sum(t["pnl"] for t in losses if t.get("pnl")))
    pf         = round(gross_win / gross_loss, 3) if gross_loss else float("inf")

    avg_win    = round(gross_win  / len(wins),   4) if wins   else 0.0
    avg_loss   = round(gross_loss / len(losses), 4) if losses else 0.0

    avg_hold   = 0.0
    if closed:
        holds = [t.get("days_held") or 0 for t in closed]
        avg_hold = round(sum(holds) / len(holds), 1)

    return {
        "n":        len(closed),
        "wins":     len(wins),
        "losses":   len(losses),
        "win_rate": round(len(wins) / len(closed), 3) if closed else 0.0,
        "pf":       pf,
        "avg_win":  avg_win,
        "avg_loss": avg_loss,
        "avg_hold": avg_hold,
        "tsl_exits": len(tsl_exits),
        "final_bal": round(result.get("final_balance", 0), 2),
    }


# ── Report ─────────────────────────────────────────────────────────────────────

def _row(label: str, m: dict) -> str:
    return (
        f"  {label:<26} n={m['n']:>4}  WR={m['win_rate']:.1%}  "
        f"PF={m['pf']:>5.3f}  avgW={m['avg_win']:>7.2f}  avgL={m['avg_loss']:>7.2f}  "
        f"hold={m['avg_hold']:>5.1f}d  TSL={m['tsl_exits']:>3}  bal=${m['final_bal']:,.0f}"
    )


def main() -> None:
    print("=" * W)
    print("  PROFIT-LOCK TSL SWEEP")
    print(f"  Period: {START} → {END}")
    print(f"  Base exits: TP={TAKE_PROFIT_PCT:.0%}  SL={STOP_LOSS_PCT:.0%}  "
          f"TSL={TRAILING_STOP_PCT:.0%}  TIME={TIME_STOP_DAYS}d")
    print("=" * W)

    # Baseline — TSL with no profit-lock
    print("\nBaseline (TSL only, no profit-lock):")
    baseline_result = run(
        START, END,
        trailing_stop_pct=TRAILING_STOP_PCT,
    )
    baseline = _metrics(baseline_result)
    print(_row("no profit-lock", baseline))

    # Sweep
    print(f"\nSweep — trigger × trail ({len(TRIGGER_VALUES)}×{len(TRAIL_VALUES)} grid):")
    print(f"  {'trigger/trail':<26} {'n':>5}  {'WR':>6}  {'PF':>6}  {'avgW':>8}  {'avgL':>8}  {'hold':>7}  {'TSL':>4}  {'bal':>10}")
    print("  " + "-" * (W - 2))

    best_pf    = baseline["pf"]
    best_label = "no profit-lock"
    best_m     = baseline
    results    = []

    for trigger, trail in product(TRIGGER_VALUES, TRAIL_VALUES):
        if trail >= trigger:
            # Trail ≥ trigger means TSL could fire below activation level — invalid
            continue
        label = f"trig={trigger:.0%}  trail={trail:.1%}"
        result = run(
            START, END,
            trailing_stop_pct=TRAILING_STOP_PCT,
            profit_lock_trigger_pct=trigger,
            profit_lock_trail_pct=trail,
        )
        m = _metrics(result)
        results.append((trigger, trail, m))
        marker = " ◀ best" if m["pf"] > best_pf else ""
        if m["pf"] > best_pf:
            best_pf    = m["pf"]
            best_label = label
            best_m     = m
        print(_row(label, m) + marker)

    print("=" * W)
    print(f"\nBest PF:  {best_label}")
    print(_row(best_label, best_m))
    print(f"Baseline: {_row('no profit-lock', baseline)}")
    delta_pf   = best_m["pf"] - baseline["pf"]
    delta_wr   = best_m["win_rate"] - baseline["win_rate"]
    delta_hold = best_m["avg_hold"] - baseline["avg_hold"]
    print(f"\nDelta vs baseline:  PF {delta_pf:+.3f}  WR {delta_wr:+.1%}  hold {delta_hold:+.1f}d")
    print("=" * W)


if __name__ == "__main__":
    main()
