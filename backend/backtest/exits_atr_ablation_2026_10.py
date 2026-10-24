"""APEX ATR leg ablation of live's exit, 2026-10-24: which leg carries the ATR twin's gain, and the tail.

Why: the ATR exits card found live's rule scaled to volatility ahead in both windows (under its MDE)
with a worse tail from the wider stop. Niclas 2026-10-24: "Om live fortfarande ligger före så ta reda
på varför och hur vi kan nyttja det mer ... det bör finnas ett tak. Vad är rimligt?" and "testa a".
The card is vault raw/notes/2026-10/2026-10-24-apex-atr-leg-ablation-card.md, committed (vault
368b3b64) before this module. Exploratory: no live change rests on it.

How: python -u -m backend.backtest.exits_atr_ablation_2026_10 OUTDIR [--windows IS,OOS] [--workers 3]
     [--battery /home/promenix/apex-battery/2026-10-19]

What: OUTDIR/ABL_<window>.json (per-arm stats, paired tests, ATR deciles), OUTDIR/ABL_decision.json.
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

from backend.backtest import battery_2026_10 as b
from backend.backtest import engine_fast as ef
from backend.backtest import exits_atr_2026_10 as xa
from backend.backtest import exits_t8_2026_10 as t8
from backend.backtest import ladder_2026_10 as ld

LIVE = xa.STATIC["P1"]
SCALED = {"ALL": ("tp", "sl", "pl"), "TPLOCK": ("tp", "pl"), "TP": ("tp",), "LOCK": ("pl",), "SL": ("sl",)}
ABLATION = list(SCALED)
DECILE_ARMS = ("STATIC", "ALL", "TPLOCK", "P3_static", "P3_atr")
TAIL_TOL = 0.01                                # card item 5: p1 not worse by more than 1 point
SEED = 20261026

_SHARED: dict = {}


def leg_cfg(arm: str, atr_pct: float) -> dict:
    """Live's legs with the arm's legs scaled to the entry's ATR (xa's multiples); P3 arms via xa."""
    if arm.startswith("P3_"):
        return xa.arm_cfg(arm, atr_pct)
    if arm == "STATIC":
        return dict(LIVE)
    tw = xa.twin_cfg("P1", atr_pct)
    return {"tp": tw["tp"] if "tp" in SCALED[arm] else LIVE["tp"],
            "sl": tw["sl"] if "sl" in SCALED[arm] else LIVE["sl"],
            "pl": tw["pl"] if "pl" in SCALED[arm] else LIVE["pl"], "tdays": LIVE["tdays"]}


def all_arms() -> list[str]:
    return ["STATIC", *ABLATION, "P3_static", "P3_atr"]


def peaks_of(arm: str) -> tuple[str, ...]:
    return (t8.DECIDING_PEAK,) if arm.startswith("P3_") else t8.PEAKS


def simulate(P: dict, pre: dict, i: int, j: int, arm: str, peak: str) -> dict:
    cfg = leg_cfg(arm, float(P["atr"].iat[i, j]))
    before = ld.PEAK
    ld.PEAK = ld.PEAK_MODELS[peak]
    try:
        return ld._simulate(P, pre, i, j, cfg)
    finally:
        ld.PEAK = before


def _job(job: tuple) -> tuple:
    arm, peak, pop = job
    P, pre, E = _SHARED["P"], _SHARED["pre"], _SHARED["E"]
    return arm, peak, pop, [simulate(P, pre, i, j, arm, peak) for i, j in E[pop]]


def deciles(P: dict, E: list, rows: list[dict]) -> dict:
    """Per ATR decile of the entry (within the population): n, capital-day, PF, p1, MAE p10."""
    atr = P["atr"].to_numpy()
    a = np.array([atr[i, j] for i, j in E])
    edges = np.nanpercentile(a, np.arange(10, 100, 10))
    d = np.digitize(a, edges)
    out = {}
    for k in range(10):
        idx = np.flatnonzero(d == k)
        if len(idx) < 20:
            continue
        sub, subE = [rows[n] for n in idx], [E[n] for n in idx]
        s = xa.arm_stats(sub, P, subE)
        out[str(k)] = {"atr_lo": float(a[idx].min()), "atr_hi": float(a[idx].max()), "n": len(idx),
                       "capital_day": s["capital_day"], "profit_factor": s["profit_factor"],
                       "win_rate": s["win_rate"], "p1": s["tail"]["p1"], "mae_p10": s["tail"]["mae_p10"]}
    return out


def card_window(w: str, P: dict, pre: dict, live_rows: list[dict] | None, workers: int, log) -> tuple[dict, dict]:
    global _SHARED
    E0 = ld.entry_sets(P)
    picks, n_pp = t8.purge(E0["picks"], len(P["days"]))
    matched, n_pm = t8.purge(E0["matched"], len(P["days"]))
    E = {"picks": picks, "matched": matched}
    if live_rows:
        E["live"] = t8.live57(P, live_rows, np.random.default_rng(SEED))["E"]
    out: dict = {"window": w, "entries": {**{p: len(v) for p, v in E.items()}, "purged": n_pp + n_pm}}
    _SHARED = {"P": P, "pre": pre, "E": E}
    jobs = [(a, pk, pop) for a in all_arms() for pk in peaks_of(a) for pop in E]
    R: dict = {}
    t0 = time.time()
    with mp.get_context("fork").Pool(workers) as pool:
        for n, (a, pk, pop, res) in enumerate(pool.imap_unordered(_job, jobs), 1):
            R[(a, pk, pop)] = res
            if n % 8 == 0 or n == len(jobs):
                log(f"{w} {n}/{len(jobs)} {time.time() - t0:.0f}s")
    out["arms"] = {f"{a}|{pk}|{pop}": xa.arm_stats(R[(a, pk, pop)], P, E[pop]) for (a, pk, pop) in R}
    out["pairs"] = {}
    for a in ABLATION:
        for pk in t8.PEAKS:
            for pop, ent in E.items():
                arm, st = R[(a, pk, pop)], R[("STATIC", pk, pop)]
                if pop == "live":
                    out["pairs"][f"{a}|{pk}|{pop}"] = {"paired_trade": xa.paired_trade_t(arm, st)}
                    continue
                pm_ = t8.paired_monthly(P, ent, arm, st)
                pm_.pop("series", None)
                out["pairs"][f"{a}|{pk}|{pop}"] = {"paired_monthly": pm_, "net10": xa.net_diff(arm, st, xa.COST_GATE_BP)}
    out["deciles"] = {a: deciles(P, E["picks"], R[(a, t8.DECIDING_PEAK, "picks")]) for a in DECILE_ARMS}
    return out, R


def tail_ok(IS: dict, OOS: dict, arm: str) -> bool:
    k, s = f"{arm}|{t8.DECIDING_PEAK}|picks", f"STATIC|{t8.DECIDING_PEAK}|picks"
    return all(R["arms"][k]["tail"]["p1"] >= R["arms"][s]["tail"]["p1"] - TAIL_TOL for R in (IS, OOS))


def decide(res: dict, R: dict, P: dict, E: dict) -> dict:
    IS, OOS = res["IS"], res["OOS"]
    pk = t8.DECIDING_PEAK
    ps = {}
    for a in ABLATION:
        t = IS["pairs"][f"{a}|{pk}|picks"]["paired_monthly"]["t"]
        ps[a] = None if t is None else math.erfc(abs(t) / math.sqrt(2))
    hm = t8.holm(ps)
    v = {}
    for a in ABLATION:
        d_is, d_oos = IS["pairs"][f"{a}|{pk}|picks"]["paired_monthly"], OOS["pairs"][f"{a}|{pk}|picks"]["paired_monthly"]
        signs = {np.sign(IS["pairs"][f"{a}|{p}|picks"]["paired_monthly"]["t"] or 0) for p in t8.PEAKS}
        live = IS["pairs"].get(f"{a}|{pk}|live", {}).get("paired_trade", {}).get("t")
        r = xa.rule({**d_is, "p_neg_holm": hm[a] and (d_is["t"] or 0) < 0}, d_oos, hm[a], len(signs) == 1,
                    IS["pairs"][f"{a}|{pk}|picks"]["net10"], live)
        v[a] = {**r, "tail_ok": tail_ok(IS, OOS, a), "is_mean": d_is["mean"], "is_t": d_is["t"],
                "oos_mean": d_oos["mean"], "oos_t": d_oos["t"], "live_t": live,
                "mde": 2.8 * d_is["sd"] / math.sqrt(d_is["n"])}
    # Controls: the ATR card's synthetic twins on STATIC (P1_static rows are the same rule).
    R2 = {w: {("P1_static", pk, "picks"): R[w][("STATIC", pk, "picks")]} for w in R}
    ctl = xa.controls(R2["IS"], R2["OOS"], P["IS"], P["OOS"], E["IS"], E["OOS"])
    ctl["tail_static_passes"] = tail_ok(IS, OOS, "STATIC")
    return {"verdicts": v, "controls": ctl,
            "criterion_ok": ctl["plus"] == "supported" and ctl["minus"] == "contradicted" and ctl["tail_static_passes"],
            "trials": len(ABLATION) * len(t8.PEAKS) * 2 * 3}


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
    from backend import db
    live_db = Path("/home/promenix/apex/data/apex.db")
    db.DB_PATH = live_db
    since = json.loads((live_db.parent / "live_config.json").read_text()).get("live_account_since")
    canonical = db.live_sample_rows(since=since)
    res, R, P, E = {}, {}, {}, {}
    for w in a.windows.split(","):
        pre = ld._R._window(w)
        P[w] = ld.panels(pre)
        res[w], R[w] = card_window(w, P[w], pre, canonical if w == "IS" else None, a.workers, log)
        E[w] = t8.purge(ld.entry_sets(P[w])["picks"], len(P[w]["days"]))[0]
        (a.outdir / f"ABL_{w}.json").write_text(json.dumps(res[w], indent=1, default=str))
        log(f"saved ABL_{w}")
    if {"IS", "OOS"} <= set(res):
        out = {"producer": {"repo": "apex", "branch": "master", "script": "backend/backtest/exits_atr_ablation_2026_10.py",
                            "commit": commit, "run": " ".join(sys.argv),
                            "why": "raw/notes/2026-10/2026-10-24-apex-atr-leg-ablation-card.md"},
               **decide(res, R, P, E)}
        (a.outdir / "ABL_decision.json").write_text(json.dumps(out, indent=1, default=str))
        log("saved ABL_decision")
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
