"""APEX ATR-scaled exits card, 2026-10-24: each static exit rule against its ATR twin on the same entries.

Why: S15's open question since 10-21, static or flexible exits. T8's two ATR arms scaled the bracket
but kept the profit lock fixed and were judged against LIVE, so "does scaling to volatility help" was
never isolated. Niclas 2026-10-24: "gör ett atr skalat exitkort", then "bra scope, testa vidare".
The card (pairs, multiples, statistic, rule a-e, synthetic-twin controls) is vault
raw/notes/2026-10/2026-10-24-apex-atr-scaled-exits-card.md, committed (vault 1b087dd0, amendment
f5021a66) before this module. Exploratory: a supported pair joins the shadow book, never live config.

How: python -u -m backend.backtest.exits_atr_2026_10 OUTDIR [--windows IS,OOS] [--workers 3]
     [--battery /home/promenix/apex-battery/2026-10-19]

What: OUTDIR/ATR_<window>.json per window (per-arm stats, per-pair paired tests, quintiles, matched,
live57 in IS), then OUTDIR/ATR_decision.json (controls, power, rule a-e per pair, trial count).
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
from backend.backtest import exits_t8_2026_10 as t8
from backend.backtest import ladder_2026_10 as ld

NONE = ld.NONE
MEDIAN_ATR_IS_PICKS = 0.06 / 2.5233            # T8's logged ATRSYM k (IS picks), card item 3
STATIC: dict[str, dict] = {
    "P1": {"tp": 0.06, "sl": 0.06, "pl": [0.04, 0.01], "tdays": 25},     # LIVE
    "P2": {"tp": 0.06, "sl": NONE, "pl": None, "tdays": 25},             # NOSTOP
    "P3": {"tp": 0.04, "sl": NONE, "pl": None, "tdays": 25},             # TP4NS
}
PAIR_NAMES = {"P1": "LIVE", "P2": "NOSTOP", "P3": "TP4NS"}
LABELS = {"P3": "TP4NS selected from T4's grid after reading"}
COST_GATE_BP = 10
CONTROL_NOISE = 0.5                            # amendment: eta sd = 0.5 x static per-trade sd (chosen)
CONTROL_MULT = 4.0                             # amendment: delta+ = 4 x the delta=0 arm's SE
N_ZERO = 20
SEED = 20261024

_SHARED: dict = {}


def multiples(cfg: dict) -> dict:
    """Each finite leg divided by the IS picks' median atr_pct; NONE legs stay NONE."""
    def m(x):
        return None if x is None or x >= NONE else x / MEDIAN_ATR_IS_PICKS
    return {"tp": m(cfg["tp"]), "sl": m(cfg["sl"]),
            "pl": None if not cfg["pl"] else [cfg["pl"][0] / MEDIAN_ATR_IS_PICKS, cfg["pl"][1] / MEDIAN_ATR_IS_PICKS]}


def twin_cfg(pair: str, atr_pct: float) -> dict:
    """The ATR twin's legs for one entry. No usable ATR: the static legs (T8's engine rule)."""
    s = STATIC[pair]
    if not atr_pct or not np.isfinite(atr_pct) or atr_pct <= 0:
        return dict(s)
    mu = multiples(s)
    return {"tp": NONE if mu["tp"] is None else mu["tp"] * atr_pct,
            "sl": NONE if mu["sl"] is None else mu["sl"] * atr_pct,
            "pl": None if mu["pl"] is None else [mu["pl"][0] * atr_pct, mu["pl"][1] * atr_pct],
            "tdays": s["tdays"]}


def arm_cfg(arm: str, atr_pct: float) -> dict:
    pair, kind = arm.split("_")
    return dict(STATIC[pair]) if kind == "static" else twin_cfg(pair, atr_pct)


def arms() -> list[str]:
    return [f"{p}_{k}" for p in STATIC for k in ("static", "atr")]


def peaks_of(arm: str) -> tuple[str, ...]:
    return t8.PEAKS if STATIC[arm.split("_")[0]]["pl"] else (t8.DECIDING_PEAK,)


def simulate(P: dict, pre: dict, i: int, j: int, arm: str, peak: str) -> dict:
    cfg = arm_cfg(arm, float(P["atr"].iat[i, j]))
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


# ── Statistics ─────────────────────────────────────────────────────────────────

def arm_stats(rows: list[dict], P: dict, E: list) -> dict:
    pnl, hold = t8._pnl(rows), np.maximum(t8._hold(rows), 1)
    win, loss = pnl[pnl > 0], pnl[pnl <= 0]
    reasons = {r: float(np.mean([x["reason"] == r for x in rows])) for r in t8.REASONS}
    return {"n": len(rows), "capital_day": float(pnl.sum() / hold.sum()), "mean": float(pnl.mean()),
            "win_rate": float((pnl > 0).mean()), "avg_win": float(win.mean()) if len(win) else None,
            "avg_loss": float(loss.mean()) if len(loss) else None,
            "profit_factor": float(win.sum() / -loss.sum()) if loss.sum() < 0 else None,
            "mean_hold": float(hold.mean()), "exit_share": reasons, "tail": t8.tail(rows, P, E),
            "costs": t8.costs(rows)}


def net_diff(a: list[dict], ref: list[dict], bp: float) -> float:
    """Capital-day difference (a - ref), each net of bp per round trip."""
    def net(r):
        pnl, hold = t8._pnl(r), np.maximum(t8._hold(r), 1)
        return (pnl.sum() - len(r) * bp / 1e4) / hold.sum()
    return float(net(a) - net(ref))


def by_quintile(P: dict, E: list, a: list[dict], ref: list[dict]) -> dict:
    """Capital-day difference (twin - static) per ATR quintile of the entry: where the scaling acts."""
    q = ld.quintile(P["atr"], P["mask"]).to_numpy()
    qs = np.array([q[i, j] for i, j in E])
    out = {}
    for k in range(5):
        idx = np.flatnonzero(qs == k)
        if len(idx):
            out[str(k)] = net_diff([a[n] for n in idx], [ref[n] for n in idx], 0)
    return out


def paired_trade_t(a: list[dict], ref: list[dict]) -> dict:
    d = t8._pnl(a) - t8._pnl(ref)
    sd = d.std(ddof=1) if len(d) > 1 else 0.0
    return {"n": len(d), "mean": float(d.mean()) if len(d) else None,
            "t": float(d.mean() / (sd / math.sqrt(len(d)))) if sd > 0 else None}


def synthetic(rows: list[dict], delta: float, rng: np.random.Generator) -> list[dict]:
    """Amendment: pnl' = pnl + delta x hold + eta, eta ~ N(0, CONTROL_NOISE x sd(pnl))."""
    pnl = t8._pnl(rows)
    eta = rng.normal(0, CONTROL_NOISE * pnl.std(ddof=1), len(rows))
    return [{**r, "pnl": float(r["pnl"] + delta * max(r["hold"], 1) + e)} for r, e in zip(rows, eta)]


def rule(d_is: dict, d_oos: dict, holm_ok: bool, peaks_same_sign: bool, net10: float,
         live_t: float | None) -> dict:
    """Card item 9 for one pair; d_* are paired_monthly results (twin - static), picks."""
    t_is, t_oos = d_is.get("t"), d_oos.get("t")
    a = bool(holm_ok and t_is is not None and t_is > 0)
    bb = bool(t_oos is not None and t_oos >= 1)
    c, d = bool(peaks_same_sign), bool(net10 > 0)
    e = not (live_t is not None and live_t <= -2)
    if a and bb and c and d and e:
        v = "supported"
    elif a and bb and d and e and not c:
        v = "lock-model-dependent"
    elif a and bb and c and d and not e:
        v = "entry-dependent"
    elif t_is is not None and t_is < 0 and d_is.get("p_neg_holm") and not (t_oos is not None and t_oos > 0):
        v = "contradicted"
    else:
        v = "unanswered"
    return {"verdict": v, "a": a, "b": bb, "c": c, "d": d, "e": e}


def card_window(w: str, P: dict, pre: dict, live_rows: list[dict] | None, workers: int, log) -> tuple[dict, dict]:
    global _SHARED
    E0 = ld.entry_sets(P)
    picks, n_pp = t8.purge(E0["picks"], len(P["days"]))
    matched, n_pm = t8.purge(E0["matched"], len(P["days"]))
    E = {"picks": picks, "matched": matched}
    if live_rows:
        L = t8.live57(P, live_rows, np.random.default_rng(SEED))
        E["live"] = L["E"]
    atr = P["atr"].to_numpy()
    med = float(np.median([atr[i, j] for i, j in picks]))
    out: dict = {"window": w, "labels": LABELS, "median_atr_picks": med,
                 "multiples": {p: multiples(c) for p, c in STATIC.items()},
                 "entries": {**{p: len(v) for p, v in E.items()}, "purged_picks": n_pp, "purged_matched": n_pm}}
    _SHARED = {"P": P, "pre": pre, "E": E}
    jobs = [(a, pk, pop) for a in arms() for pk in peaks_of(a) for pop in E]
    R: dict = {}
    t0 = time.time()
    with mp.get_context("fork").Pool(workers) as pool:
        for n, (a, pk, pop, res) in enumerate(pool.imap_unordered(_job, jobs), 1):
            R[(a, pk, pop)] = res
            if n % 6 == 0 or n == len(jobs):
                log(f"{w} {n}/{len(jobs)} {time.time() - t0:.0f}s")
    out["identity_static_legs_at_median"] = {p: {k: (None if v is None else v)
                                                 for k, v in twin_cfg(p, med).items()} for p in STATIC}
    out["identical_entry_lists"] = all(len(R[(a, pk, pop)]) == len(E[pop]) for (a, pk, pop) in R)
    out["arms"] = {f"{a}|{pk}|{pop}": arm_stats(R[(a, pk, pop)], P, E[pop]) for (a, pk, pop) in R}
    out["pairs"] = {}
    for p in STATIC:
        for pk in peaks_of(f"{p}_static"):
            for pop, ent in E.items():
                tw, st = R[(f"{p}_atr", pk, pop)], R[(f"{p}_static", pk, pop)]
                key = f"{p}|{pk}|{pop}"
                if pop == "live":
                    out["pairs"][key] = {"paired_trade": paired_trade_t(tw, st)}
                    continue
                pm_ = t8.paired_monthly(P, ent, tw, st)
                pm_.pop("series", None)
                out["pairs"][key] = {"paired_monthly": pm_, "net10": net_diff(tw, st, COST_GATE_BP),
                                     "by_quintile": by_quintile(P, ent, tw, st)}
    return out, R


def controls(R_is: dict, R_oos: dict, P_is: dict, P_oos: dict, E_is: list, E_oos: list) -> dict:
    """Amendment: synthetic twins on P1 static picks (deciding peak) through the same test and rule."""
    rng = np.random.default_rng(SEED)
    st_is, st_oos = R_is[("P1_static", t8.DECIDING_PEAK, "picks")], R_oos[("P1_static", t8.DECIDING_PEAK, "picks")]
    z = t8.paired_monthly(P_is, E_is, synthetic(st_is, 0.0, rng), st_is)
    se = z["sd"] / math.sqrt(z["n"])
    delta = CONTROL_MULT * se
    bound = 0.05 / len(STATIC)                        # Holm's first step over the three pairs

    def run(dl):
        di = t8.paired_monthly(P_is, E_is, synthetic(st_is, dl, rng), st_is)
        do = t8.paired_monthly(P_oos, E_oos, synthetic(st_oos, dl, rng), st_oos)
        p_two = 2 * min(di["p_one_sided"], 1 - di["p_one_sided"])
        return rule({**di, "p_neg_holm": p_two <= bound and di["t"] < 0}, do,
                    p_two <= bound and di["t"] > 0, True, 1.0, None)["verdict"]

    zero = [run(0.0) for _ in range(N_ZERO)]
    return {"se_zero": se, "delta_plus": delta, "plus": run(delta), "minus": run(-delta),
            "zero_supported_rate": float(np.mean([v == "supported" for v in zero]))}


def decide(res: dict, R: dict, P: dict, E: dict) -> dict:
    IS, OOS = res["IS"], res["OOS"]
    ctl = controls(R["IS"], R["OOS"], P["IS"], P["OOS"], E["IS"]["picks"], E["OOS"]["picks"])
    power = {p: 2.8 * IS["pairs"][f"{p}|{t8.DECIDING_PEAK}|picks"]["paired_monthly"]["sd"]
             / math.sqrt(IS["pairs"][f"{p}|{t8.DECIDING_PEAK}|picks"]["paired_monthly"]["n"]) for p in STATIC}
    ps = {}
    for p in STATIC:
        t = IS["pairs"][f"{p}|{t8.DECIDING_PEAK}|picks"]["paired_monthly"]["t"]
        ps[p] = None if t is None else math.erfc(abs(t) / math.sqrt(2))      # two-sided
    hm = t8.holm(ps)
    verdicts = {}
    for p in STATIC:
        k = f"{p}|{t8.DECIDING_PEAK}|picks"
        d_is, d_oos = IS["pairs"][k]["paired_monthly"], OOS["pairs"][k]["paired_monthly"]
        signs = {np.sign(IS["pairs"][f"{p}|{pk}|picks"]["paired_monthly"]["t"] or 0) for pk in peaks_of(f"{p}_static")}
        live = IS["pairs"].get(f"{p}|{t8.DECIDING_PEAK}|live", {}).get("paired_trade", {}).get("t")
        verdicts[p] = {"name": PAIR_NAMES[p], **rule({**d_is, "p_neg_holm": hm[p] and (d_is["t"] or 0) < 0},
                                                     d_oos, hm[p], len(signs) == 1, IS["pairs"][k]["net10"], live),
                       "is_mean": d_is["mean"], "is_t": d_is["t"], "oos_mean": d_oos["mean"], "oos_t": d_oos["t"],
                       "live_t": live, "mde": power[p]}
    n_trials = sum(len(peaks_of(a)) for a in arms()) * 2 * 3
    return {"controls": ctl, "power_mde": power, "verdicts": verdicts, "trials": n_trials,
            "criterion_ok": ctl["plus"] == "supported" and ctl["minus"] == "contradicted"}


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
        E[w] = {"picks": t8.purge(ld.entry_sets(P[w])["picks"], len(P[w]["days"]))[0]}
        if w == "IS":
            res[w]["live_sample"] = {"definition": db.LIVE_SAMPLE, "since": since, "n_rows": len(canonical)}
        (a.outdir / f"ATR_{w}.json").write_text(json.dumps(res[w], indent=1, default=str))
        log(f"saved ATR_{w}")
    if {"IS", "OOS"} <= set(res):
        out = {"producer": {"repo": "apex", "branch": "master", "script": "backend/backtest/exits_atr_2026_10.py",
                            "commit": commit, "run": " ".join(sys.argv),
                            "why": "raw/notes/2026-10/2026-10-24-apex-atr-scaled-exits-card.md"},
               **decide(res, R, P, E)}
        (a.outdir / "ATR_decision.json").write_text(json.dumps(out, indent=1, default=str))
        log("saved ATR_decision")
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
