"""APEX candidate ledger, 2026-10-24: every R1 Lock 1 candidate before penalties, with its isolated-trade
outcome under live's exit.

Why: INDEX "APEX candidate ledger" (scoped 2026-10-11 from the macro penalty re-test; consumers: the S14
and S13 R1 cards, the earnings and ETF penalty sizes, CHECK 80's retirement). The portfolio sweep could not
separate a penalty's value from path noise, because a 5 % score cut reshuffles 25-38 % of all trades; an
isolated outcome per candidate can. Niclas 2026-10-24: "placeringen måste bli engine fast" and "var noga
med att spara resultaten innan cachen byggs om" (each run writes its own directory; cache files are
never overwritten, a new key is a new file).

How: python -u -m backend.backtest.candidate_ledger_2026_10 OUTDIR [--windows IS,OOS] [--workers 3]
     [--battery /home/promenix/apex-battery/2026-10-19]

What: OUTDIR/ledger_<window>.csv (one row per candidate-day: the engine's fields, entered, outcome under
live's exit with the polled peak) and OUTDIR/ledger_summary.json (counts and mean outcomes per class with
95 % CIs, the macro penalty band read). Descriptive: cards read it.
"""
from __future__ import annotations

import argparse
import functools
import json
import math
import multiprocessing as mp
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from backend.backtest import battery_2026_10 as b
from backend.backtest import engine_fast as ef
from backend.backtest import ladder_2026_10 as ld

LIVE_EXIT = {"tp": 0.06, "sl": 0.06, "pl": [0.04, 0.01], "tdays": 25}
MACRO_P = 0.05                   # live's macro_pre_event_penalty (the band read, INDEX scope)
VIX_LEVEL = 30.0                 # live's vix_threshold

_SHARED: dict = {}


def run_with_ledger(w: str) -> tuple[list[dict], dict, dict]:
    """ladder._engine's R1 start-0 run with the ledger switched on; returns rows, the run, the precompute."""
    from backend.backtest.engine_fast import run
    u = b.unit(w, b.R1, 0)
    pre = ld._R._window(w)
    days = pre["trading_days"]
    pre_u = dict(pre)
    if u.regime_layer:
        pre_u["regime_timeline"] = ld._R._timeline(w, u.regime)
    led: list = []
    with b.regime_constants(dict(u.regime)):
        res = run(days[0].isoformat(), days[-1].isoformat(), initial_balance=10_000.0, precomputed=pre_u,
                  candidate_ledger=led, **{**ld._R.incumbent(), **u.kwargs})
    return led, res, pre


def _sim_job(chunk: list[tuple[int, int, int]]) -> list[tuple[int, float, int, str]]:
    P, pre = _SHARED["P"], _SHARED["pre"]
    ld.PEAK = ld.PEAK_MODELS["polled"]
    out = []
    for n, i, j in chunk:
        r = ld._simulate(P, pre, i, j, LIVE_EXIT)
        out.append((n, float(r["pnl"]), int(r["hold"]), r["reason"]))
    return out


def outcomes(led: list[dict], P: dict, pre: dict, workers: int, log) -> pd.DataFrame:
    global _SHARED
    df = pd.DataFrame(led)
    di = {d: k for k, d in enumerate(P["days"])}
    ti = {t: k for k, t in enumerate(P["tickers"])}
    last = len(P["days"]) - 1
    jobs = [(n, di[r.date], ti[r.ticker]) for n, r in enumerate(df.itertuples())
            if r.date in di and r.ticker in ti and di[r.date] < last]
    _SHARED = {"P": P, "pre": pre}
    chunks = [jobs[k:k + 500] for k in range(0, len(jobs), 500)]
    res: dict[int, tuple] = {}
    t0 = time.time()
    with mp.get_context("fork").Pool(workers) as pool:
        for c, part in enumerate(pool.imap_unordered(_sim_job, chunks), 1):
            res.update({n: (p, h, rs) for n, p, h, rs in part})
            if c % 20 == 0 or c == len(chunks):
                log(f"outcomes {c}/{len(chunks)} {time.time() - t0:.0f}s")
    df["pnl_live"] = [res.get(n, (np.nan,))[0] for n in range(len(df))]
    df["hold_live"] = [res.get(n, (np.nan, np.nan))[1] for n in range(len(df))]
    df["exit_live"] = [res.get(n, (None, None, None))[2] for n in range(len(df))]
    return df


def ci(x: pd.Series) -> dict:
    x = x.dropna()
    n = len(x)
    if n < 2:
        return {"n": n, "mean": float(x.mean()) if n else None}
    se = float(x.std(ddof=1)) / math.sqrt(n)
    return {"n": n, "mean": float(x.mean()), "lo": float(x.mean() - 1.96 * se), "hi": float(x.mean() + 1.96 * se),
            "win_rate": float((x > 0).mean())}


def summary(df: pd.DataFrame, floor_etf: float = -1.0) -> dict:
    """Counts and mean live-exit outcomes per class (descriptive; per-trade CIs ignore same-day correlation)."""
    pre_ev = df["macro_day"] == "pre_event"
    band = pre_ev & (df["score"] < df["threshold"] / (1 - MACRO_P))
    etf_low = df["etf_ret"].notna() & (df["etf_ret"] * 100 < floor_etf)
    return {"rows": len(df), "days": int(df["date"].nunique()), "entered": int(df["entered"].sum()),
            "with_outcome": int(df["pnl_live"].notna().sum()),
            "all": ci(df["pnl_live"]),
            "by_macro_day": {str(k): ci(g["pnl_live"]) for k, g in df.groupby(df["macro_day"].fillna("clear"))},
            "macro_band": {"in_band_pre_event": ci(df.loc[band, "pnl_live"]),
                           "pre_event_above_band": ci(df.loc[pre_ev & ~band, "pnl_live"]),
                           "clear_days": ci(df.loc[df["macro_day"].isna() | (df["macro_day"] == "clear"), "pnl_live"])},
            "vix_ge_30": ci(df.loc[df["vix"] >= VIX_LEVEL, "pnl_live"]),
            "vix_lt_30": ci(df.loc[df["vix"] < VIX_LEVEL, "pnl_live"]),
            "etf_below_floor": ci(df.loc[etf_low, "pnl_live"]),
            "etf_not_below": ci(df.loc[~etf_low & df["etf_ret"].notna(), "pnl_live"]),
            "entered_vs_not": {"entered": ci(df.loc[df["entered"], "pnl_live"]),
                               "not_entered": ci(df.loc[~df["entered"], "pnl_live"])},
            "earnings_near_known": bool(df["earnings_near"].notna().any())}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir", type=Path)
    ap.add_argument("--windows", default="IS,OOS")
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
    out = {"producer": {"repo": "apex", "branch": "master", "script": "backend/backtest/candidate_ledger_2026_10.py",
                        "commit": commit, "run": " ".join(sys.argv),
                        "why": "raw/notes/2026-10/2026-10-24-apex-candidate-ledger-built.md"}, "windows": {}}
    for w in a.windows.split(","):
        led, res, pre = run_with_ledger(w)
        log(f"{w}: {len(led)} ledger rows, {len(res['trade_log'])} trades")
        P = ld.panels(pre)
        df = outcomes(led, P, pre, a.workers, log)
        entered = {(t["entry_date"], t["ticker"]) for t in res["trade_log"]}
        df["entered"] = [(d, t) in entered for d, t in zip(df["date"], df["ticker"])]
        df.to_csv(a.outdir / f"ledger_{w}.csv", index=False)
        out["windows"][w] = summary(df)
        out["windows"][w]["trades_in_run"] = len(res["trade_log"])
        out["windows"][w]["every_trade_has_a_row"] = bool(df["entered"].sum() == len(entered))
        (a.outdir / "ledger_summary.json").write_text(json.dumps(out, indent=1, default=str))
        log(f"saved ledger_{w}")
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
