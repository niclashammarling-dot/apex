"""S21b stressed-day reversal entries, 2026-10-24: the largest residual 5-day losers after a stressed VIX
close against same-day, same-ATR-quintile random entries, isolated trades under live's exit.

Why: S21 found the residual reversal about twice IC_econ after stressed closes (exploratory), the days
the live VIX gate blocks. Niclas 2026-10-24: "a sedan b". The card is vault
raw/notes/2026-10/2026-10-24-apex-s21b-stressed-reversal-entries-card.md, committed (vault 6ed44226)
before this module. Exploratory: no live change rests on it.

How: python -u -m backend.backtest.s21b_stressed_reversal_2026_10 OUTDIR [--windows IS,OOS] [--workers 3]
     [--battery /home/promenix/apex-battery/2026-10-19]

What: OUTDIR/S21b_<window>.json, OUTDIR/S21b_decision.json. Controls are printed before the difference.
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
from backend.backtest import ladder_2026_10 as ld
from backend.backtest import s20b_vol_state_2026_10 as sb
from backend.backtest import s21_reversal_2026_10 as s21

K = 5
N_RND = 200                    # stressed days (the decision)
N_RND_ALL = 20                 # every day (REV_CALM and the block-shift control)
N_BOOT = 2000
N_SHIFT = 200
N_ZERO = 20
BLOCK = 21
HOLD_MAX = 25
COST_BP = 10
MIN_OOS_DAYS = 30
SEED = 20261028
EXITS = {"live": {"tp": 0.06, "sl": 0.06, "pl": [0.04, 0.01], "tdays": 25},
         "time10": {"tp": ld.NONE, "sl": ld.NONE, "pl": None, "tdays": 10}}

_SHARED: dict = {}


def episodes(stressed: np.ndarray) -> list[list[int]]:
    """Contiguous runs of stressed day indices."""
    out, cur = [], []
    for i, s in enumerate(stressed):
        if s:
            cur.append(i)
        elif cur:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def episode_ci(diff: dict[int, float], eps: list[list[int]], rng: np.random.Generator) -> dict:
    """Mean over days of the per-day difference, with a 95 % CI from resampling whole episodes."""
    eps = [[i for i in e if i in diff] for e in eps]
    eps = [e for e in eps if e]
    if not eps:
        return {"mean": None, "lo": None, "hi": None, "n_days": 0, "n_episodes": 0}
    vals = [np.array([diff[i] for i in e]) for e in eps]
    allv = np.concatenate(vals)
    boot = []
    for _ in range(N_BOOT):
        pick = rng.integers(0, len(vals), len(vals))
        boot.append(np.concatenate([vals[p] for p in pick]).mean())
    return {"mean": float(allv.mean()), "lo": float(np.percentile(boot, 2.5)), "hi": float(np.percentile(boot, 97.5)),
            "n_days": len(allv), "n_episodes": len(vals)}


def picks_for_day(i: int, r2: np.ndarray, score: np.ndarray, mask: np.ndarray, q: np.ndarray,
                  n_rnd: int, rng: np.random.Generator) -> dict:
    ok = np.flatnonzero(mask[i] & np.isfinite(r2[i]))
    if len(ok) < 2 * K:
        return {}
    rev = ok[np.argsort(r2[i, ok])[:K]]
    sc_ok = np.flatnonzero(mask[i] & np.isfinite(score[i]))
    top = sc_ok[np.argsort(-score[i, sc_ok])[:K]] if len(sc_ok) >= K else np.array([], dtype=int)
    rnd = []
    for j in rev:
        cand = ok[q[i, ok] == q[i, j]]
        cand = cand[cand != j] if len(cand) > 1 else ok
        rnd.append(rng.choice(cand, n_rnd))
    return {"rev": rev.tolist(), "score": top.tolist(), "rnd": [r.tolist() for r in rnd]}


def _day_job(job: tuple) -> tuple:
    i, picks = job
    P, pre = _SHARED["P"], _SHARED["pre"]
    before = ld.PEAK
    ld.PEAK = ld.PEAK_MODELS["polled"]
    out = {}
    try:
        cache: dict = {}

        def sim(j, ex):
            if (j, ex) not in cache:
                r = ld._simulate(P, pre, i, int(j), EXITS[ex])
                cache[(j, ex)] = (r["pnl"] - COST_BP / 1e4, max(r["hold"], 1))
            return cache[(j, ex)]
        for ex in EXITS:
            rev = [sim(j, ex) for j in picks["rev"]]
            rnd = [[sim(j, ex) for j in row] for row in picks["rnd"]]
            sco = [sim(j, ex) for j in picks["score"]]
            out[ex] = {"rev": rev, "rnd_mean": float(np.mean([[p for p, _ in row] for row in rnd])),
                       "rnd_win": float(np.mean([[p > 0 for p, _ in row] for row in rnd])),
                       "rnd_first": [row[0] for row in rnd], "score": sco}
    finally:
        ld.PEAK = before
    return i, out


def run_days(P: dict, pre: dict, picks: dict[int, dict], workers: int, log) -> dict[int, dict]:
    global _SHARED
    _SHARED = {"P": P, "pre": pre}
    res, t0 = {}, time.time()
    with mp.get_context("fork").Pool(workers) as pool:
        for n, (i, out) in enumerate(pool.imap_unordered(_day_job, list(picks.items()), chunksize=4), 1):
            res[i] = out
            if n % 200 == 0 or n == len(picks):
                log(f"days {n}/{len(picks)} {time.time() - t0:.0f}s")
    return res


def arm_summary(sim: dict[int, dict], days: list[int], ex: str, arm: str) -> dict:
    rows = [x for i in days if i in sim for x in sim[i][ex][arm]]
    if not rows:
        return {"n": 0}
    p = np.array([r[0] for r in rows])
    h = np.array([r[1] for r in rows])
    w, lo = p[p > 0], p[p <= 0]
    return {"n": len(p), "mean": float(p.mean()), "capital_day": float(p.sum() / h.sum()), "win_rate": float((p > 0).mean()),
            "pf": float(w.sum() / -lo.sum()) if lo.sum() < 0 else None, "p1": float(np.percentile(p, 1)),
            "mean_hold": float(h.mean())}


def card_window(w: str, P: dict, pre: dict, workers: int, log) -> dict:
    rng = np.random.default_rng(SEED)
    classes = sb.classes_for(pre, P["days"])
    stressed = sb.stressed_of(classes).to_numpy()
    last = len(P["days"]) - 1
    r2 = s21.predictors(P)["R2"].to_numpy()
    score = P["score"].where(P["mask"]).to_numpy()
    mask = P["mask"].to_numpy()
    q = ld.quintile(P["atr"], P["mask"]).to_numpy()
    days = [i for i in range(last) if i + HOLD_MAX <= last]
    picks = {i: picks_for_day(i, r2, score, mask, q, N_RND if stressed[i] else N_RND_ALL, rng) for i in days}
    picks = {i: p for i, p in picks.items() if p}
    sim = run_days(P, pre, picks, workers, log)
    st_days = [i for i in sim if stressed[i]]
    calm = [i for i in sim if not stressed[i] and classes.iloc[i] == "NEITHER"]
    eps = episodes(stressed)

    def diff(i, ex):
        return float(np.mean([p for p, _ in sim[i][ex]["rev"]]) - sim[i][ex]["rnd_mean"])

    out = {"window": w, "n_stressed_days": len(st_days), "n_episodes": len(eps), "n_calm_days": len(calm), "exits": {}}
    for ex in EXITS:
        d_st = {i: diff(i, ex) for i in st_days}
        # Controls first (card item 7): delta = 0 (a random entry as REV), its CI half-width sets delta+.
        zero = {i: float(sim[i][ex]["rnd_first"][0][0] - sim[i][ex]["rnd_mean"]) for i in st_days}
        z = episode_ci(zero, eps, rng)
        half = (z["hi"] - z["lo"]) / 2 if z["mean"] is not None else None
        zero_rate = []
        for _ in range(N_ZERO):
            draw = {i: float(sim[i][ex]["rnd_first"][int(rng.integers(0, K))][0] - sim[i][ex]["rnd_mean"]) for i in st_days}
            c = episode_ci(draw, eps, rng)
            zero_rate.append(c["lo"] is not None and (c["lo"] > 0 or c["hi"] < 0))
        plus = episode_ci({i: v + 2 * half for i, v in zero.items()}, eps, rng) if half else None
        all_d = {i: diff(i, ex) for i in sim}
        shifts = []
        for _ in range(N_SHIFT):
            m = int(rng.integers(1, max(2, len(stressed) // BLOCK))) * BLOCK
            sh = np.roll(stressed, m)
            c = episode_ci({i: all_d[i] for i in all_d if sh[i]}, episodes(sh), rng)
            shifts.append(c["lo"] is not None and (c["lo"] > 0 or c["hi"] < 0))
        ctl = {"zero": z, "zero_reject_rate": float(np.mean(zero_rate)), "half_width": half,
               "plus": plus, "plus_supported": bool(plus and plus["lo"] > 0),
               "shift_reject_rate": float(np.mean(shifts))}
        log(f"{w} {ex} controls: half-width {half:.5f}, +delta supported {ctl['plus_supported']}, "
            f"zero rate {ctl['zero_reject_rate']:.2f}, shift rate {ctl['shift_reject_rate']:.2f}")
        res = episode_ci(d_st, eps, rng)
        out["exits"][ex] = {"controls": ctl, "rev_minus_rnd": res,
                            "rev_minus_rnd_calm_mean": float(np.mean([all_d[i] for i in calm])) if calm else None,
                            "arms": {"REV": arm_summary(sim, st_days, ex, "rev"),
                                     "SCORE": arm_summary(sim, st_days, ex, "score"),
                                     "REV_CALM": arm_summary(sim, calm, ex, "rev"),
                                     "RND_win_rate": float(np.mean([sim[i][ex]["rnd_win"] for i in st_days])),
                                     "RND_mean": float(np.mean([sim[i][ex]["rnd_mean"] for i in st_days]))}}
    return out


def verdict(IS: dict, OOS: dict) -> dict:
    out = {}
    for ex in EXITS:
        r, ro = IS["exits"][ex]["rev_minus_rnd"], OOS["exits"][ex]["rev_minus_rnd"]
        ctl_ok = IS["exits"][ex]["controls"]["plus_supported"]
        if r["mean"] is None or not ctl_ok:
            v = "unanswered (control)"
        elif r["lo"] > 0:
            v = "supported" if (ro["mean"] or 0) > 0 else "unanswered (OOS sign check fails)"
            if v == "supported" and OOS["n_stressed_days"] < MIN_OOS_DAYS:
                v = "supported, OOS unpowered"
        elif r["hi"] < 0:
            v = "contradicted"
        else:
            v = "unanswered"
        out[ex] = {"verdict": v, "decides": ex == "live", "is": r, "oos": ro}
    return out


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
    runner = b.Runner(b.Store(a.battery), commit[:7])
    res = {}
    for w in a.windows.split(","):
        pre = runner._window(w)
        P = ld.panels(pre)
        res[w] = card_window(w, P, pre, a.workers, log)
        (a.outdir / f"S21b_{w}.json").write_text(json.dumps(res[w], indent=1, default=str))
        log(f"saved S21b_{w}")
    if {"IS", "OOS"} <= set(res):
        out = {"producer": {"repo": "apex", "branch": "master", "script": "backend/backtest/s21b_stressed_reversal_2026_10.py",
                            "commit": commit, "run": " ".join(sys.argv),
                            "why": "raw/notes/2026-10/2026-10-24-apex-s21b-stressed-reversal-entries-card.md"},
               "verdicts": verdict(res["IS"], res["OOS"])}
        (a.outdir / "S21b_decision.json").write_text(json.dumps(out, indent=1, default=str))
        log("saved S21b_decision")
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
