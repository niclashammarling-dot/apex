"""APEX scaled exits at L3 with M4, 2026-10-24: live, ALL, ALLCAP8 and TPLOCK in the full engine at five
starts, with return per capital-day, Calmar and M4's realised drawdown (the 6 % halt).

Why: the capped-stop card found the stop's extra return and its tail are the same trades, and asked how
much tail that return is worth at portfolio level. Niclas 2026-10-24: "l3 och m4 först". The card is
vault raw/notes/2026-10/2026-10-24-apex-exits-l3-m4-card.md, committed (vault 0d5d64ff) before this
module; the engine support (per-trade ATR lock, stop ceiling) is apex ad8bfdd. Exploratory.

How: python -u -m backend.backtest.exits_l3_m4_2026_10 OUTDIR [--workers 3]
     [--battery /home/promenix/apex-battery/2026-10-19]

What: OUTDIR/L3M4.json (per arm and start: metrics, M4 realised drawdown; controls; the rule).
"""
from __future__ import annotations

import argparse
import functools
import json
import multiprocessing as mp
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from backend.backtest import battery_2026_10 as b
from backend.backtest import engine_fast as ef
from backend.backtest import exits_atr_2026_10 as xa
from backend.backtest import ladder_2026_10 as ld
from backend.backtest.archive_guard import fingerprint
from backend.backtest.exit_rules import POLLED_PEAK_GAP
from backend.config import M4_DRAWDOWN_PCT as M4_DD  # the live L0 halt, one source

START_BALANCE = 10_000.0         # ladder._engine's initial balance
DD_TOL = 0.01                    # card item 5: realised drawdown at most 1 point deeper
SHIFT = 0.002                    # card item 6: the synthetic "better" arm, +0.2 % P&L per trade
ARMS = ("STATIC", "ALL", "ALLCAP8", "TPLOCK")


def over(arm: str) -> dict:
    """Engine overrides per arm; polled peak as T8's L3."""
    m = xa.MEDIAN_ATR_IS_PICKS
    o = {"peak_from_high": True, "peak_haircut": POLLED_PEAK_GAP, "time_stop_days": 25,
         "take_profit_pct": 0.06, "stop_loss_pct": 0.06,
         "profit_lock_trigger_pct": 0.04, "profit_lock_trail_pct": 0.01}
    if arm == "STATIC":
        return o
    o.update(atr_exits=True, atr_lock_mult=(0.04 / m, 0.01 / m))
    o["atr_exit_mult"] = (0.06 / m, None) if arm == "TPLOCK" else (0.06 / m, 0.06 / m)
    if arm == "ALLCAP8":
        o["atr_sl_cap"] = 0.08
    return o


def realised_drawdown(trade_log: list[dict], start: float = START_BALANCE, halt: float = M4_DD) -> dict:
    """M4's measure: realised equity (closed P&L in exit order), max peak-to-trough, first halt date."""
    closed = sorted((t for t in trade_log if t.get("pnl") is not None and t.get("exit_date")),
                    key=lambda t: t["exit_date"])
    eq = peak = start
    mdd, first = 0.0, None
    for t in closed:
        eq += t["pnl"]
        peak = max(peak, eq)
        dd = (peak - eq) / peak
        mdd = max(mdd, dd)
        if first is None and dd >= halt:
            first = t["exit_date"]
    return {"max_drawdown": mdd, "m4_halt_date": first, "final_equity": eq}


def metrics(w: str, res: dict) -> dict:
    m = ld.l3_metrics(w, res)
    m["m4"] = realised_drawdown(res["trade_log"])
    m["entries"] = len(res["trade_log"])
    return m


def shifted(res: dict) -> dict:
    """Card item 6: every closed trade's P&L raised by SHIFT of its amount; the equity curve kept."""
    tl = [{**t, "pnl": t["pnl"] + SHIFT * t["amount"]} if t.get("pnl") is not None else t for t in res["trade_log"]]
    return {**res, "trade_log": tl}


def better(arm: dict[int, dict], ref: dict[int, dict]) -> str:
    """Card item 5 over the starts both have."""
    starts = sorted(set(arm) & set(ref))
    up = sum(arm[s]["pnl_per_capital_day"] > ref[s]["pnl_per_capital_day"] for s in starts)
    cal = sum((arm[s]["calmar"] or -np.inf) >= (ref[s]["calmar"] or -np.inf) for s in starts)
    dd_ok = sum(arm[s]["m4"]["max_drawdown"] <= ref[s]["m4"]["max_drawdown"] + DD_TOL for s in starts)
    down = sum(arm[s]["pnl_per_capital_day"] < ref[s]["pnl_per_capital_day"] for s in starts)
    dd_bad = len(starts) - dd_ok
    need = 4 if len(starts) >= 5 else len(starts)
    if up >= need and cal >= need and dd_ok >= need:
        return "better"
    if down >= need or dd_bad >= need:
        return "worse"
    return "mixed"


def _job(job: tuple) -> tuple:
    arm, w, start, tag = job
    res = ld._engine(w, b.R1, start, over(arm) if arm != "R1UNIT" else {})
    out = {"m": metrics(w, res), "fp": fingerprint(res["trade_log"])}
    if tag == "shift":
        out["shift"] = metrics(w, shifted(res))
    return arm, w, start, tag, out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir", type=Path)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--battery", type=Path, default=Path("/home/promenix/apex-battery/2026-10-19"))
    a = ap.parse_args(argv)
    a.outdir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False).stdout.strip()

    def log(msg: str) -> None:
        print(f"[{time.time() - t0:6.0f}s] {msg}", flush=True)

    ef._trading_days_count = functools.lru_cache(maxsize=None)(ef._trading_days_count)   # pure; speed only
    ld._R = b.Runner(b.Store(a.battery), commit[:7])
    for w in ("IS", "OOS"):
        ld._R._window(w)
    ld._R.frozen = True
    jobs = [(arm, "IS", s, "shift" if arm == "STATIC" else "") for arm in ARMS for s in b.RND_STARTS]
    jobs += [(arm, "OOS", 0, "") for arm in ARMS] + [("STATIC", "IS", 0, "twice"), ("R1UNIT", "IS", 0, "")]
    R: dict = {}
    with mp.get_context("fork").Pool(a.workers) as pool:
        for n, (arm, w, s, tag, out) in enumerate(pool.imap_unordered(_job, jobs), 1):
            R[(arm, w, s, tag)] = out
            log(f"{n}/{len(jobs)} {arm} {w} start {s} {tag}")
    IS = {arm: {s: R[(arm, "IS", s, "shift" if arm == "STATIC" else "")]["m"] for s in b.RND_STARTS} for arm in ARMS}
    synth = {s: R[("STATIC", "IS", s, "shift")]["shift"] for s in b.RND_STARTS}
    controls = {"static_twice_identical": R[("STATIC", "IS", 0, "shift")]["fp"] == R[("STATIC", "IS", 0, "twice")]["fp"],
                "static_equals_r1_unit": R[("STATIC", "IS", 0, "shift")]["fp"] == R[("R1UNIT", "IS", 0, "")]["fp"],
                "synthetic_better": better(synth, IS["STATIC"]), "static_vs_itself": better(IS["STATIC"], IS["STATIC"])}
    log(f"controls {controls}")
    out = {"producer": {"repo": "apex", "branch": "master", "script": "backend/backtest/exits_l3_m4_2026_10.py",
                        "commit": commit, "run": " ".join(sys.argv),
                        "why": "raw/notes/2026-10/2026-10-24-apex-exits-l3-m4-card.md"},
           "controls": controls,
           "overrides": {arm: {k: (list(v) if isinstance(v, tuple) else v) for k, v in over(arm).items()} for arm in ARMS},
           "IS": {arm: {str(s): m for s, m in d.items()} for arm, d in IS.items()},
           "OOS_start0": {arm: R[(arm, "OOS", 0, "")]["m"] for arm in ARMS},
           "verdicts": {arm: better(IS[arm], IS["STATIC"]) for arm in ARMS if arm != "STATIC"}}
    (a.outdir / "L3M4.json").write_text(json.dumps(out, indent=1, default=str))
    log("saved L3M4")
    return 0


if __name__ == "__main__":
    sys.exit(main())
