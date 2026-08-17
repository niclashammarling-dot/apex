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

from backend.backtest.engine_fast import precompute, run
from backend.config import TIME_STOP_DAYS
from backend.live_config import get_live_config

# ── Sweep config ───────────────────────────────────────────────────────────────
# TP/SL/profit-lock read from the live runtime config (2026-08-18 fix), not
# config.py's static defaults — this sweep was previously running on 6%/5%
# (config.py) while live actually runs 6%/6% (live_config.json), a distinct
# instance of the same defect the ratchet-sweep tooling carried. See
# 2026-08-17-apex-backtest-tooling-incident-four-lessons (Vaultnix), item 2.

START = "2021-01-01"
END   = "2026-06-01"

_cfg = get_live_config()
TAKE_PROFIT_PCT         = _cfg["take_profit_pct"]
STOP_LOSS_PCT           = _cfg["stop_loss_pct"]
PROFIT_LOCK_TRIGGER_PCT = _cfg["profit_lock_trigger_pct"]
PROFIT_LOCK_TRAIL_PCT   = _cfg["profit_lock_trail_pct"]

# No longer a source of distortion now that engine_fast.py's ratchet fires
# independent of trailing_stop_pct when profit-lock is configured (2026-08-18
# engine fix) — kept only as the legacy bare-TSL fallback value, never
# actually reached while profit_lock_trigger_pct/trail_pct are both set below.
TRAILING_STOP_PCT = 0.09

SL_DAYS_VALUES = [0, 1, 3, 5, 7, 10]
TP_DAYS_VALUES = [0, 1, 2, 3, 5, 7]

# Production equivalent: EXIT_COOLOFF_HOURS=24 applies to all exits (no TP/SL split)
# Nearest backtest mapping: SL=1d, TP=1d
PRODUCTION_SL = 1
PRODUCTION_TP = 1

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
        f"  {label:<36} n={m['n']:>4}  WR={m['win_rate']:.1%}  "
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

    print("\nPrecomputing caches...", flush=True)
    pc = precompute(START, END)

    shared = dict(
        take_profit_pct=TAKE_PROFIT_PCT,
        stop_loss_pct=STOP_LOSS_PCT,
        trailing_stop_pct=TRAILING_STOP_PCT,
        profit_lock_trigger_pct=PROFIT_LOCK_TRIGGER_PCT,
        profit_lock_trail_pct=PROFIT_LOCK_TRAIL_PCT,
        precomputed=pc,
    )

    # Reference: backtest default (SL=5d, TP=0d)
    print("\nReference baselines:")
    bkt_result = run(START, END, sl_cooldown_days=5, tp_cooldown_days=0, **shared)
    bkt = _metrics(bkt_result)
    print(_row("SL=5d  TP=0d  (bkt default)", bkt), flush=True)

    # Production equivalent: EXIT_COOLOFF_HOURS=24 → ~SL=1d, TP=1d
    prod_result = run(START, END, sl_cooldown_days=PRODUCTION_SL, tp_cooldown_days=PRODUCTION_TP, **shared)
    prod = _metrics(prod_result)
    print(_row(f"SL={PRODUCTION_SL}d  TP={PRODUCTION_TP}d  (production ~24h)", prod), flush=True)

    print(f"\nSweep — SL days × TP days ({len(SL_DAYS_VALUES)}×{len(TP_DAYS_VALUES)} grid):")
    print(f"  {'label':<36} {'n':>5}  {'WR':>6}  {'PF':>6}  {'avgW':>8}  {'avgL':>8}  {'bal':>10}")
    print("  " + "-" * (W - 2))

    best_pf    = -1.0
    best_label = ""
    best_m: dict = {}

    for sl_days, tp_days in product(SL_DAYS_VALUES, TP_DAYS_VALUES):
        label = f"SL={sl_days}d  TP={tp_days}d"
        result = run(START, END, sl_cooldown_days=sl_days, tp_cooldown_days=tp_days, **shared)
        m = _metrics(result)
        marker = ""
        if m["pf"] > best_pf:
            best_pf    = m["pf"]
            best_label = label
            best_m     = m
            marker     = " ◀ best"
        print(_row(label, m) + marker, flush=True)

    print("=" * W)
    print(f"\nBest PF:       {best_label}")
    print(_row(best_label, best_m))
    print(f"Backtest ref:  {_row('SL=5d  TP=0d', bkt)}")
    print(f"Production eq: {_row(f'SL={PRODUCTION_SL}d  TP={PRODUCTION_TP}d', prod)}")
    delta_pf = best_m["pf"] - prod["pf"]
    delta_wr = best_m["win_rate"] - prod["win_rate"]
    print(f"\nDelta vs production:  PF {delta_pf:+.3f}  WR {delta_wr:+.1%}")
    print("=" * W)


if __name__ == "__main__":
    main()
