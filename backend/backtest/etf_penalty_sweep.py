"""
Sweep: ETF negative penalty — floor × rate grid calibration.

Context
-------
The ETF negative penalty was activated at rate=0.10, floor=-1.0% on 2026-06-26.
Three weeks of live rejections (AAPL, NOW) accumulated without a calibration run.
The sweep was scheduled for ~2026-07-04 and slipped. This script is the commitment
artifact — it runs and produces the calibration output, not prose.

What the penalty does
---------------------
After L4 passes, if the sector ETF's 5-day return < floor, signal_score is
multiplied by (1 − rate). If the penalised score falls below the quant threshold,
the trade is rejected. Floor and rate interact: a deep floor with a low rate and a
shallow floor with a high rate can produce similar rejection counts but admit
different trade sets. The sweep tests the full 2D grid.

Pre-committed success criterion (decide before running, not after)
-----------------------------------------------------------------
The penalty earns its place if, relative to the null (no penalty):
  - profit_factor improves by ≥ 2% (primary)  OR
  - CAGR improves by ≥ 0.5pp AND max_drawdown does not worsen by more than 1pp

"Earns its place" is the bar. If no cell in the grid clears it, the finding is
"remove the penalty" — that is a valid and important result, not a failure of the
sweep.

Counterfactual note
-------------------
The engine records which trades were blocked in `result["etf_penalty_blocked"]`,
but does not compute per-trade counterfactual P&L (what those trades would have
returned). That gap is explanatory, not verdict-blocking: the null and mild cells
*already take* the trades that harsher cells block, so the cross-cell PF/CAGR
comparison incorporates blocked-trade outcomes implicitly. The verdict question
("does this penalty configuration earn its place?") is answerable from the grid
as built. Per-trade counterfactual P&L adds explanation — which specific blocks
paid and which cost — and is worth building for post-decision understanding, but
the sweep's conclusion does not depend on it.

Post-sweep step
---------------
Once a floor/rate pair is chosen, FILTERED_ETF_PENALTY labels (available from
2026-07-16) give cleanly-attributed live rejections. A week or two of labeled
log accumulation lets you check whether the historical optimum behaves as designed
in the current regime.

Run from apex/:
    python -m backend.backtest.etf_penalty_sweep
"""
from __future__ import annotations

from datetime import date
from itertools import product

from backend.backtest.engine import run

START = "2021-01-01"
END   = date.today().isoformat()

BASE_KWARGS = dict(
    start_date               = START,
    end_date                 = END,
    stop_loss_pct            = 0.06,
    take_profit_pct          = 0.06,
    profit_lock_trigger_pct  = 0.04,
    profit_lock_trail_pct    = 0.01,
    sl_cooldown_days         = 1,
    tp_cooldown_days         = 7,
    max_positions            = 10,
    use_leading_rs           = True,  # L4 RS proxy — keep active; ETF penalty is on top
)

# Grid axes. null (no penalty) and incumbent (0.10, -1.0) must be explicit cells.
RATES  = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30]   # incumbent rate = 0.10
FLOORS = [-0.5, -1.0, -1.5, -2.0]                 # incumbent floor = -1.0 (% points)


def _fmt(r: dict) -> str:
    pf   = r["profit_factor"]
    wr   = r["win_rate"]
    n    = r["total_trades"]
    bl   = len(r.get("etf_penalty_blocked", []))
    cagr = r["cagr"]
    dd   = r["max_drawdown"]
    pf_s   = f"{pf:.3f}"  if pf   is not None else "  N/A"
    wr_s   = f"{wr:.1%}"  if wr   is not None else "  N/A"
    cagr_s = f"{cagr:.1%}" if cagr is not None else "N/A"
    return f"PF={pf_s}  WR={wr_s}  N={n:>3}(bl={bl:>2})  CAGR={cagr_s}  MaxDD={dd:.1%}"


def _meets_criterion(candidate: dict, null: dict) -> bool:
    """Pre-committed criterion — see module docstring."""
    pf_null  = null["profit_factor"]
    pf_cand  = candidate["profit_factor"]
    cagr_null = null["cagr"]
    cagr_cand = candidate["cagr"]
    dd_null  = null["max_drawdown"]
    dd_cand  = candidate["max_drawdown"]
    if pf_null is None or pf_cand is None:
        return False
    pf_ok   = (pf_cand / pf_null - 1) >= 0.02
    cagr_ok = (cagr_cand is not None and cagr_null is not None
               and (cagr_cand - cagr_null) >= 0.005
               and (dd_cand - dd_null) <= 0.01)
    return pf_ok or cagr_ok


def main() -> None:
    grid: dict[tuple[float | None, float | None], dict] = {}

    # Null baseline — no ETF penalty
    print("Running null baseline (no ETF penalty) …")
    r = run(**BASE_KWARGS, etf_negative_penalty=None)
    grid[(None, None)] = r

    # Full 2D grid
    for rate, floor in product(RATES, FLOORS):
        label = f"rate={rate:.2f} floor={floor:.1f}%"
        print(f"Running {label} …")
        r = run(**BASE_KWARGS, etf_negative_penalty=rate, etf_negative_floor=floor)
        grid[(rate, floor)] = r

    null = grid[(None, None)]

    # ── Output ────────────────────────────────────────────────────────────────

    print()
    print(f"{'':>22}  {'Results'}")
    print(f"{'Variant':<22}  PF       WR       N(bl)       CAGR    MaxDD")
    print("-" * 85)

    print(f"{'NULL (no penalty)':<22}  {_fmt(null)}")
    print()

    # Matrix: rows = floors, cols = rates
    print(f"{'floor \\ rate':<12}", end="")
    for rate in RATES:
        marker = "*" if rate == 0.10 else " "
        print(f"  {rate:.2f}{marker}", end="")
    print()
    print("-" * (12 + 7 * len(RATES)))

    for floor in FLOORS:
        marker = "*" if floor == -1.0 else " "
        print(f"{floor:.1f}%{marker}       ", end="")
        for rate in RATES:
            r = grid[(rate, floor)]
            pf = r["profit_factor"]
            pf_s = f"{pf:.3f}" if pf is not None else " N/A "
            flag = "✓" if _meets_criterion(r, null) else " "
            print(f"  {pf_s}{flag}", end="")
        print()

    print()
    print("(* marks incumbent pair: rate=0.10, floor=-1.0%; ✓ = meets pre-committed criterion)")

    # Incumbent comparison
    incumbent = grid.get((0.10, -1.0))
    if incumbent:
        print()
        print("── Incumbent vs null ──")
        print(f"  null:      {_fmt(null)}")
        print(f"  incumbent: {_fmt(incumbent)}")
        if _meets_criterion(incumbent, null):
            print("  VERDICT: incumbent earns its place (criterion met)")
        else:
            print("  VERDICT: incumbent does NOT meet criterion — recalibrate or remove")

    # Best cell by profit factor
    cells = [(k, v) for k, v in grid.items() if k != (None, None)]
    best_k, best_r = max(cells, key=lambda x: x[1]["profit_factor"] or 0)
    if best_k != (0.10, -1.0):
        print()
        print(f"  Best PF cell: rate={best_k[0]:.2f} floor={best_k[1]:.1f}%  {_fmt(best_r)}")

    # Blocked-trade summary per cell
    print()
    print("── Blocked trade counts (what's in etf_penalty_blocked) ──")
    print("NOTE: blocked trades' counterfactual P&L not computed — see TODO in module docstring.")
    for (rate, floor), r in sorted(cells, key=lambda x: (x[0][1], x[0][0])):
        bl = r.get("etf_penalty_blocked", [])
        if bl:
            tickers = sorted({b["ticker"] for b in bl})
            print(f"  rate={rate:.2f} floor={floor:.1f}%: {len(bl)} blocked  {tickers[:8]}")


if __name__ == "__main__":
    main()
