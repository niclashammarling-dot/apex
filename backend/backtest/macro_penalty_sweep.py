"""
Sweep: macro pre-event penalty vs hard-block vs no-filter.

Tests whether converting the day-before-CPI/NFP from a hard block to a score
penalty improves portfolio performance. FOMC pre-event window stays as a hard
block in all variants (it's architecturally separate).

Variants:
  no_filter    — no macro calendar awareness (engine baseline)
  hard_block   — block on day-of AND day-before CPI/NFP (+ FOMC 2d window)
  penalty_0.05 … penalty_0.40 — day-of hard block; day-before = score * (1-p)

Run from apex/:
    python -m backend.backtest.macro_penalty_sweep
"""
from __future__ import annotations

import sys
from datetime import date

from backend.backtest.engine import run

START = "2021-01-01"
END   = date.today().isoformat()

BASE_KWARGS = dict(
    start_date        = START,
    end_date          = END,
    stop_loss_pct     = 0.06,
    take_profit_pct   = 0.06,
    profit_lock_trigger_pct = 0.04,
    profit_lock_trail_pct   = 0.01,
    sl_cooldown_days  = 1,
    tp_cooldown_days  = 7,
    max_positions     = 10,
)

PENALTIES = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40]


def _fmt(r: dict) -> str:
    pf  = r["profit_factor"]
    wr  = r["win_rate"]
    n   = r["total_trades"]
    cagr = r["cagr"]
    dd  = r["max_drawdown"]
    pf_s  = f"{pf:.3f}" if pf  is not None else "  N/A"
    wr_s  = f"{wr:.1%}"  if wr  is not None else "  N/A"
    cagr_s = f"{cagr:.1%}" if cagr is not None else "N/A"
    return f"PF={pf_s}  WR={wr_s}  N={n:>3}  CAGR={cagr_s}  MaxDD={dd:.1%}"


def main() -> None:
    results: list[tuple[str, dict]] = []

    print("Running no_filter baseline …")
    r = run(**BASE_KWARGS, macro_hard_block=False, macro_pre_event_penalty=None)
    results.append(("no_filter   ", r))

    print("Running hard_block baseline …")
    r = run(**BASE_KWARGS, macro_hard_block=True, macro_pre_event_penalty=None)
    results.append(("hard_block  ", r))

    for p in PENALTIES:
        label = f"penalty_{p:.2f}"
        print(f"Running {label} …")
        r = run(**BASE_KWARGS, macro_hard_block=False, macro_pre_event_penalty=p)
        results.append((label, r))

    print()
    print(f"{'Variant':<16}  {'Results'}")
    print("-" * 72)
    for label, r in results:
        print(f"{label}  {_fmt(r)}")


if __name__ == "__main__":
    main()
