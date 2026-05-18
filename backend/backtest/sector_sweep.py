"""
Threshold sweep for underperforming sectors.

For each target sector, sweeps L1 from 0.55 → 0.90 in 5pp steps.
At each level, re-runs the full 2021-2026 backtest and slices the trade log
to that sector. Reports trades / win rate / PF and verdicts:
  - PF > 1.0 with ≥ MIN_TRADES → viable threshold found
  - PF never crosses 1.0 (or trades dry up first) → exclude

Reuses the precomputed cache from diagnose.py if the date range matches;
otherwise builds it fresh (cached to Parquet on first run).

METHODOLOGY NOTE (2026-05-18):
This script runs full portfolio backtests and slices the trade log per sector.
Per-sector PF is therefore contaminated by portfolio slot competition: changing
the L1 threshold shifts which trades across ALL sectors compete for max_positions
slots, so a sector's trade set and PnL are not isolated to its own signals. This
produces non-monotonic trade counts and inflated/deflated per-sector PF at
thresholds far from baseline.

For threshold decisions on individual sectors, run a ticker-isolated sweep
(see regime_conditioned_cs.py for the pattern: no portfolio caps, each trade
evaluated independently). Use this script only for portfolio-level sanity checks,
not as the primary input for sector floor decisions.

Usage:
    cd /home/promenix/apex
    python -m backend.backtest.sector_sweep
"""
from __future__ import annotations

from datetime import date

from loguru import logger

from backend.backtest.engine_fast import precompute, run as backtest_run

# ── Config ────────────────────────────────────────────────────────────────────

START       = "2021-01-01"
END         = "2026-05-01"

TARGET_SECTORS = ["Homebuilders", "Transportation"]
THRESHOLDS     = [0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90]
MIN_TRADES     = 5   # below this, verdict is "insufficient data"

W = 62


# ── Cache slicer (same as diagnose.py) ───────────────────────────────────────

def _slice(pc: dict, start_str: str, end_str: str) -> dict:
    start = date.fromisoformat(start_str)
    end   = date.fromisoformat(end_str)
    sliced = dict(pc)
    sliced["trading_days"] = [d for d in pc["trading_days"] if start <= d <= end]
    return sliced


# ── Sector stats from trade log ───────────────────────────────────────────────

def _sector_stats(trade_log: list, sector: str) -> dict:
    trades = [t for t in trade_log if t["sector"] == sector]
    wins   = [t for t in trades if t["outcome"] == "WIN"]
    losses = [t for t in trades if t["outcome"] in ("LOSS", "EXPIRED")]
    closed = wins + losses
    gp = sum(t["pnl"] for t in wins   if t["pnl"]) if wins   else 0.0
    gl = abs(sum(t["pnl"] for t in losses if t["pnl"])) if losses else 0.0
    return {
        "n":        len(trades),
        "win_rate": len(wins) / len(closed) if closed else None,
        "pf":       gp / gl if gl > 0 else None,
    }


# ── Formatting ────────────────────────────────────────────────────────────────

def _bar(char="─", w=W) -> str:
    return char * w

def _pct(v: float | None) -> str:
    if v is None:
        return "  —  "
    sign = "+" if v >= 0 else ""
    return f"{sign}{v*100:.1f}%"

def _verdict(n: int, pf: float | None) -> str:
    if n < MIN_TRADES:
        return "too few trades"
    if pf is None:
        return "no wins"
    if pf >= 1.0:
        return "<-- PF > 1"
    return ""


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    logger.info(f"Sector sweep: {START} → {END}")
    logger.info("Building/loading signal caches…")
    pc = precompute(START, END)
    sliced_pc = _slice(pc, START, END)

    # One backtest per threshold level — ~1 sec each with precomputed caches
    results: dict[float, list] = {}
    for thresh in THRESHOLDS:
        logger.info(f"  L1 = {thresh:.2f}…")
        r = backtest_run(START, END, lock1_threshold=thresh, precomputed=sliced_pc)
        results[thresh] = r["trade_log"]

    # Print per sector
    print()
    print("═" * W)
    print(f"  SECTOR THRESHOLD SWEEP  {START} → {END}")
    print(f"  (baseline L1 = 0.70  |  MIN_TRADES for verdict = {MIN_TRADES})")
    print("═" * W)

    for sector in TARGET_SECTORS:
        print()
        print(f"  {sector}")
        print(_bar())
        print(f"  {'Thresh':>6}  {'Trades':>6}  {'WinRate':>8}  {'PF':>6}  {'':}")
        print(_bar())
        for thresh in THRESHOLDS:
            s = _sector_stats(results[thresh], sector)
            marker = " *" if thresh == 0.70 else "  "
            verdict = _verdict(s["n"], s["pf"])
            pf_str = f"{s['pf']:.2f}" if s["pf"] is not None else "  —"
            print(
                f"{marker} {thresh:.2f}    "
                f"{s['n']:>6}  "
                f"{_pct(s['win_rate']):>8}  "
                f"{pf_str:>6}  "
                f"{verdict}"
            )

    print()
    print("═" * W)
    print()


if __name__ == "__main__":
    main()
