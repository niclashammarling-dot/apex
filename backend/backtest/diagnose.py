"""
Backtest diagnostic report — 2021-2026, year-by-year + full sweep.

Prints per-year summary, per-sector breakdown, and per-exit-reason breakdown.
Precomputes signal/price caches once; each year sub-run reuses them.

Usage:
    cd /home/promenix/apex
    python -m backend.backtest.diagnose
"""
from __future__ import annotations

from datetime import date
from typing import TypedDict

from loguru import logger

from backend.backtest.engine_fast import precompute, run as backtest_run

# ── Periods ───────────────────────────────────────────────────────────────────

FULL_START = "2021-01-01"
FULL_END   = "2026-05-01"

YEARS = [
    ("2021", "2021-01-01", "2021-12-31"),
    ("2022", "2022-01-01", "2022-12-31"),
    ("2023", "2023-01-01", "2023-12-31"),
    ("2024", "2024-01-01", "2024-12-31"),
    ("2025", "2025-01-01", "2025-12-31"),
]

# 2022 quarterly breakdown — the bear-market year where rolling win rate matters most
QUARTERS_2022 = [
    ("2022-Q1", "2022-01-01", "2022-03-31"),
    ("2022-Q2", "2022-04-01", "2022-06-30"),
    ("2022-Q3", "2022-07-01", "2022-09-30"),
    ("2022-Q4", "2022-10-01", "2022-12-31"),
]


# ── Cache slicer ──────────────────────────────────────────────────────────────

def _slice(pc: dict, start_str: str, end_str: str) -> dict:
    """Return precomputed with trading_days filtered to [start, end]."""
    start = date.fromisoformat(start_str)
    end   = date.fromisoformat(end_str)
    sliced = dict(pc)
    sliced["trading_days"] = [d for d in pc["trading_days"] if start <= d <= end]
    return sliced


# ── Trade-log analysis ────────────────────────────────────────────────────────

def _by_sector(trade_log: list) -> dict[str, dict]:
    sectors: dict[str, list] = {}
    for t in trade_log:
        sectors.setdefault(t["sector"], []).append(t)

    out: dict[str, dict] = {}
    for sector, trades in sorted(sectors.items()):
        wins   = [t for t in trades if t["outcome"] == "WIN"]
        losses = [t for t in trades if t["outcome"] in ("LOSS", "EXPIRED")]
        closed = wins + losses
        gp = sum(t["pnl"] for t in wins   if t["pnl"]) if wins   else 0.0
        gl = abs(sum(t["pnl"] for t in losses if t["pnl"])) if losses else 0.0
        out[sector] = {
            "trades":   len(trades),
            "win_rate": len(wins) / len(closed) if closed else None,
            "avg_win":  sum(t["pnl_pct"] for t in wins)   / len(wins)   if wins   else None,
            "avg_loss": sum(t["pnl_pct"] for t in losses) / len(losses) if losses else None,
            "pf":       gp / gl if gl > 0 else None,
        }
    return out


def _by_exit(trade_log: list) -> dict[str, dict]:
    reasons: dict[str, list] = {}
    for t in trade_log:
        r = t["exit_reason"] or "UNKNOWN"
        reasons.setdefault(r, []).append(t)

    out: dict[str, dict] = {}
    for reason, trades in sorted(reasons.items()):
        wins = [t for t in trades if t["outcome"] == "WIN"]
        out[reason] = {
            "count":    len(trades),
            "avg_pnl":  sum(t["pnl_pct"] for t in trades) / len(trades) if trades else None,
            "win_rate": len(wins) / len(trades) if trades else None,
        }
    return out


# ── Formatting ────────────────────────────────────────────────────────────────

W = 68  # line width

def _bar(char="─", width=W) -> str:
    return char * width

def _pct(v: float | None, decimals: int = 1) -> str:
    if v is None:
        return "  —  "
    sign = "+" if v >= 0 else ""
    return f"{sign}{v*100:.{decimals}f}%"

def _f(v: float | None, fmt: str = ".3f") -> str:
    return f"{v:{fmt}}" if v is not None else "  —"

def _print_header(label: str) -> None:
    print()
    print(_bar("═"))
    print(f"  {label}")
    print(_bar("═"))

def _print_summary(r: dict) -> None:
    spy = f"  SPY {_pct(r['spy_return_pct'])}" if r.get("spy_return_pct") is not None else ""
    alpha = ""
    if r.get("spy_return_pct") is not None:
        a = r["total_return_pct"] - r["spy_return_pct"]
        alpha = f"  alpha {_pct(a)}"
    print(
        f"  CAGR {_pct(r['cagr'])}  "
        f"Sharpe {_f(r['sharpe'], '.3f')}  "
        f"MaxDD {_pct(-r['max_drawdown'])}  "
        f"WinRate {_pct(r['win_rate'])}"
    )
    print(
        f"  Trades {r['total_trades']}  "
        f"PF {_f(r['profit_factor'], '.2f')}  "
        f"AvgWin {_pct(r['avg_win_pct'])}  "
        f"AvgLoss {_pct(r['avg_loss_pct'])}"
        f"{spy}{alpha}"
    )

def _print_year_table(year_results: list[tuple[str, dict]]) -> None:
    print()
    print(_bar())
    print(f"  {'Year':<6}  {'CAGR':>7}  {'Sharpe':>7}  {'MaxDD':>7}  {'WinRate':>8}  {'Trades':>6}  {'PF':>5}  {'SPY':>7}")
    print(_bar())
    for label, r in year_results:
        spy_str = _pct(r.get("spy_return_pct")) if r.get("spy_return_pct") is not None else "  —  "
        print(
            f"  {label:<6}  "
            f"{_pct(r['cagr']):>7}  "
            f"{_f(r['sharpe'], '.2f'):>7}  "
            f"{_pct(-r['max_drawdown']):>7}  "
            f"{_pct(r['win_rate']):>8}  "
            f"{r['total_trades']:>6}  "
            f"{_f(r['profit_factor'], '.2f'):>5}  "
            f"{spy_str:>7}"
        )

def _print_sector_table(sectors: dict[str, dict]) -> None:
    print()
    print(_bar())
    print(f"  {'Sector':<24}  {'Trades':>6}  {'WinRate':>8}  {'AvgWin':>7}  {'AvgLoss':>8}  {'PF':>5}")
    print(_bar())
    for sector, s in sectors.items():
        print(
            f"  {sector:<24}  "
            f"{s['trades']:>6}  "
            f"{_pct(s['win_rate']):>8}  "
            f"{_pct(s['avg_win']):>7}  "
            f"{_pct(s['avg_loss']):>8}  "
            f"{_f(s['pf'], '.2f'):>5}"
        )

def _print_exit_table(exits: dict[str, dict]) -> None:
    print()
    print(_bar())
    print(f"  {'Exit':<20}  {'Count':>6}  {'AvgP&L':>8}  {'WinRate':>8}")
    print(_bar())
    for reason, e in exits.items():
        print(
            f"  {reason:<20}  "
            f"{e['count']:>6}  "
            f"{_pct(e['avg_pnl']):>8}  "
            f"{_pct(e['win_rate']):>8}"
        )


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    logger.info(f"Diagnosing: {FULL_START} → {FULL_END}")
    logger.info("Building signal/price caches (one-time — may take a few minutes)…")
    pc = precompute(FULL_START, FULL_END)

    # Full sweep
    logger.info("Running full sweep…")
    full = backtest_run(FULL_START, FULL_END, precomputed=_slice(pc, FULL_START, FULL_END))

    # Year-by-year
    year_results: list[tuple[str, dict]] = []
    all_year_trades: list = []
    for label, y_start, y_end in YEARS:
        logger.info(f"Running {label}…")
        r = backtest_run(y_start, y_end, precomputed=_slice(pc, y_start, y_end))
        year_results.append((label, r))
        all_year_trades.extend(r["trade_log"])

    # 2022 quarterly breakdown
    q22_results: list[tuple[str, dict]] = []
    for label, q_start, q_end in QUARTERS_2022:
        logger.info(f"Running {label}…")
        r = backtest_run(q_start, q_end, precomputed=_slice(pc, q_start, q_end))
        q22_results.append((label, r))

    # ── Print ──────────────────────────────────────────────────────────────────
    _print_header(f"APEX BACKTEST DIAGNOSTIC  {FULL_START} → {FULL_END}")
    print()
    print("  FULL SWEEP")
    _print_summary(full)

    print()
    print("  YEAR-BY-YEAR")
    _print_year_table(year_results)

    _print_header("2022 QUARTERLY BREAKDOWN")
    _print_year_table(q22_results)

    _print_header("PER-SECTOR  (full sweep trade log)")
    _print_sector_table(_by_sector(full["trade_log"]))

    _print_header("PER-EXIT-REASON  (full sweep)")
    _print_exit_table(_by_exit(full["trade_log"]))

    print()
    print(_bar("═"))
    print()


if __name__ == "__main__":
    main()
