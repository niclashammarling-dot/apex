"""S12 L0: does the score at close d-1, bought at the open of d, select? (card R057, 2026-11-13)

Why: S12 asks whether entries decided on the prior close and filled at the open select direction,
and so whether Finding 1 (backend/gate/freshness.py: the signal must be written after today's open)
refuses money. The card (vault raw/notes/2026-11/2026-11-13-apex-s12-card-scope.md; R057 a, R058 a
today, Niclas 2026-11-13) was committed (vault 36607bef) before this script read any forward return
from the open. Read-only and unscheduled: measurement under the 11-02 boundary, run on a weekday.

How: venv/bin/python -u scripts/s12_open_entry_probe.py OUTDIR [--windows IS,OOS] [--workers 3]
     [--battery /home/promenix/apex-battery/2026-10-19]
Precomputes each window as the battery does, then:
  D3   identity first: TP/SL off, no lock, time stop 0 (1) => pnl == close d (d+1) / open d - 1; a
       failure stops the window before anything is read.
  C1   within-day Spearman IC, score at close d-1 against open d -> close d+h-1, h 1/5/10/25, the
       ladder's nonoverlap; 200 within-day shuffles; injected IC 0.03 / 0.10.
  sims every pool cell entered at the open of d under live's exit (TP 6, SL 6, lock 4/1, 25 days,
       R9 peak), entry = open x (1 + 0.13 %), bracket anchored to that fill, exits tested from
       bar d itself; picks and matched peers again at 0 and 0.60 %, from bar d+1, and with the
       bracket on the prior close; the close arm (T4's design) for picks and matched peers.
  MDE  from the random-vs-random arm, written to S12_<w>_mde.json before C2 is computed.
  C2   picks minus one same-day same-ATR-quintile random non-pick, 25-day block unit (50-day beside).
  C3   C2 minus the close arm's picks-minus-matched, paired by entry day; confirmatory only if C2 is
       supported (fixed sequence after Holm over C1 x 4 and C2).
  plus the card's descriptive readings and controls (synthetic +0.5 point edge, rejection rate).
What: OUTDIR/S12_<window>_mde.json, then OUTDIR/S12_<window>.json, each saved as it finishes.
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
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.backtest import battery_2026_10 as b      # noqa: E402
from backend.backtest import engine_fast as ef         # noqa: E402
from backend.backtest import ladder_2026_10 as ld      # noqa: E402

PICK = ld.PICK                      # 0.65, live's L3 score threshold
LIVE = ld.LIVE_EXIT                 # TP 6 %, SL 6 %, lock 4/1 %, 25 days
OPEN_COST = 0.0013                  # median fill above the official open, live's 16 pre-open fills (card item 5)
COST_SENS = (0.0, 0.006)            # card item 5: sensitivity readings at 0 and 0.60 %
HORIZONS = (1, 5, 10, 25)
BLOCK, BLOCK_SENS = 25, 50
ALPHA = 0.05
SEED = 20261113
N_SHUFFLE = 200
N_NULL = 200                        # random-vs-random realisations for the rejection rate
SYNTH_EDGE = 0.005                  # +0.5 point
IC_ECON = 0.03
WHY = "vault raw/notes/2026-11/2026-11-13-apex-s12-card-scope.md (R057 a, R058 a today)"

_SHARED: dict = {}


# ── Statistics ─────────────────────────────────────────────────────────────────

def _betacf(a: float, b_: float, x: float) -> float:
    qab, qap, qam = a + b_, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b_ - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d; d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1.0 + aa / c if abs(c) > 1e-300 else 1e300
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d; d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1.0 + aa / c if abs(c) > 1e-300 else 1e300
        de = d * c
        h *= de
        if abs(de - 1.0) < 1e-12:
            break
    return h


def t_sf(t: float, df: int) -> float:
    """One-sided upper tail P(T >= t) of Student's t with df degrees of freedom."""
    x = df / (df + t * t)
    a, b_ = df / 2.0, 0.5
    lb = math.lgamma(a + b_) - math.lgamma(a) - math.lgamma(b_)
    front = math.exp(lb + a * math.log(x) + b_ * math.log(1 - x)) if 0 < x < 1 else 0.0
    ib = front * _betacf(a, b_, x) / a if x < (a + 1) / (a + b_ + 2) else 1 - front * _betacf(b_, a, 1 - x) / b_
    tail = 0.5 * ib                                     # P(|T| >= |t|) / 2
    return tail if t >= 0 else 1 - tail


def onesided(stat: dict) -> dict:
    """Add the one-sided p for 'mean > 0' (and for 'mean < 0') to a nonoverlap-style dict."""
    if stat.get("t") is None or stat.get("n", 0) < 3:
        return {**stat, "p_pos": None, "p_neg": None}
    return {**stat, "p_pos": t_sf(stat["t"], stat["n"] - 1), "p_neg": t_sf(-stat["t"], stat["n"] - 1)}


def block_stat(day: pd.Series, size: int, n_rows: int | None = None) -> dict:
    """Day values indexed by the window's row number; non-overlapping blocks of `size` rows from row 0,
    block mean of the day values present (every day used); t, one-sided p and MDE over the blocks.
    With n_rows, only complete blocks count (the card's 78 IS blocks = 1,959 // 25): the first run
    (10:13) weighted the trailing partial block (9 IS days, 6 OOS days) as a full one, and in OOS
    its last days of December 2018 moved C3 by 0.18 points between costs (diagnostic 10:25)."""
    s = day.dropna()
    if n_rows is not None:
        s = s[np.asarray(s.index) < (n_rows // size) * size]
    g = s.groupby(np.asarray(s.index) // size).mean()
    n = len(g)
    if n < 3:
        return {"n_blocks": n, "mean": None, "t": None, "p_pos": None, "p_neg": None, "mde": None}
    sd = float(g.std(ddof=1))
    se = sd / math.sqrt(n)
    t = float(g.mean() / se) if se > 0 else None
    return {"n_blocks": n, "n_days": int(len(s)), "mean": float(g.mean()), "sd_block": sd, "t": t,
            "p_pos": t_sf(t, n - 1) if t is not None else None, "p_neg": t_sf(-t, n - 1) if t is not None else None,
            "mde": 2.8 * se}


def holm(ps: dict[str, float], alpha: float = ALPHA) -> dict:
    order = sorted(ps, key=lambda k: ps[k])
    out, open_ = {}, True
    for k, name in enumerate(order):
        thr = alpha / (len(order) - k)
        rej = open_ and ps[name] <= thr
        open_ = open_ and rej
        out[name] = {"p": ps[name], "threshold": thr, "reject": bool(rej)}
    return out


# ── Panels and sets ────────────────────────────────────────────────────────────

def open_panels(P: dict, pre: dict) -> dict:
    """Adds the open of each day (not filled), the decision-day frames shifted one row (row i holds
    close d-1's score, ATR and pool), the entry mask (pool at d-1 and an open at d) and the ATR
    quintile within it."""
    oc = pre.get("ohlc_cache") or {}
    days, tick = P["days"], P["tickers"]
    op = pd.DataFrame({t: [getattr(oc.get((t, d)), "open", None) for d in days] for t in tick}, index=days).astype(float)
    ocl = pd.DataFrame({t: [getattr(oc.get((t, d)), "close", None) for d in days] for t in tick}, index=days).astype(float)
    score_prev, atr_prev = P["score"].shift(1), P["atr"].shift(1)
    dmask = P["mask"].shift(1, fill_value=False).astype(bool) & op.notna() & (op > 0)
    return {**P, "open": op, "ohlc_close": ocl, "score_prev": score_prev, "atr_prev": atr_prev, "dmask": dmask,
            "q_prev": ld.quintile(atr_prev, dmask), "q": ld.quintile(P["atr"], P["mask"])}


def assert_no_lookahead(P: dict, pre: dict, picks: list[tuple[int, int]]) -> dict:
    """D5: the score used for entry row i is the signal cache's value keyed on day i-1 < day i."""
    days = P["days"]
    assert all(a < b_ for a, b_ in zip(days, days[1:])), "trading days not strictly increasing"
    sc, bad = pre["signal_cache"], 0
    for i, j in picks:
        key = (P["tickers"][j], days[i - 1])
        if key not in sc or abs(sc[key]["signal_score"] - P["score_prev"].iat[i, j]) > 1e-12 or not days[i - 1] < days[i]:
            bad += 1
    assert bad == 0, f"{bad} picks whose score is not the cache's value at close d-1"
    return {"picks_checked": len(picks), "bad": bad}


def open_sets(P: dict, rng: np.random.Generator) -> dict:
    """Open arm: picks (score >= PICK at close d-1, open at d), one same-row same-quintile random
    non-pick each (matched) and two more independent draws (the random-vs-random arm); a pick with no
    such non-pick is dropped (counted). Close arm (T4's design): picks at close d, one matched each."""
    sp, q, m = P["score_prev"].to_numpy(), P["q_prev"].to_numpy(), P["dmask"].to_numpy()
    picks, matched, r1, r2, dropped = [], [], [], [], 0
    cells: dict[tuple[int, int], np.ndarray] = {}
    for i in range(1, len(P["days"])):
        for j in np.flatnonzero(m[i] & (sp[i] >= PICK)):
            key = (i, int(q[i, j]))
            if key not in cells:
                cells[key] = np.flatnonzero(m[i] & (sp[i] < PICK) & (q[i] == q[i, j]))
            cand = cells[key]
            if len(cand) == 0:
                dropped += 1
                continue
            picks.append((i, int(j)))
            matched.append((i, int(rng.choice(cand))))
            r1.append((i, int(rng.choice(cand))))
            r2.append((i, int(rng.choice(cand))))
    sc, qc, mc = P["score"].to_numpy(), P["q"].to_numpy(), P["mask"].to_numpy()
    cpicks, cmatched, cdropped = [], [], 0
    for i in range(len(P["days"]) - 1):
        for j in np.flatnonzero(mc[i] & (sc[i] >= PICK)):
            cand = np.flatnonzero(mc[i] & (sc[i] < PICK) & (qc[i] == qc[i, j]))
            if len(cand) == 0:
                cdropped += 1
                continue
            cpicks.append((i, int(j)))
            cmatched.append((i, int(rng.choice(cand))))
    pool = [(i, int(j)) for i in range(1, len(P["days"])) for j in np.flatnonzero(m[i])]
    return {"picks": picks, "matched": matched, "r1": r1, "r2": r2, "dropped": dropped, "cells": cells,
            "cpicks": cpicks, "cmatched": cmatched, "cdropped": cdropped, "pool": pool}


# ── Simulation ─────────────────────────────────────────────────────────────────

def simulate_open(P: dict, pre: dict, i: int, j: int, cfg: dict, cost: float = OPEN_COST, first: int = 0,
                  anchor_prev: bool = False) -> dict:
    """One trade bought at the open of row i at open x (1 + cost), through engine_fast._check_exits_fast
    day by day from row i + first (0: bar d itself, the card's primary; 1: from bar d+1, bar d's range
    ignored). The bracket is anchored to the fill, or with anchor_prev to the prior close (what live's
    orders priced from the signal had). days_held counts trading days after the entry day, so the time
    stop exits at the close of d+tdays as in the close arm."""
    days = P["days"]
    t = P["tickers"][j]
    entry = float(P["open"].iat[i, j]) * (1 + cost)
    tp, sl = cfg["tp"], cfg["sl"]
    if anchor_prev:
        pc = float(P["close"].iat[i - 1, j])
        tp, sl = pc * (1 + cfg["tp"]) / entry - 1, 1 - pc * (1 - cfg["sl"]) / entry
    trade = {"ticker": t, "sector": P["sector"][t], "entry_date": days[i], "entry_price": entry,
             "peak_price": entry, "shares": 1.0, "amount": 1.0, "signal_score": 0.0, "tp": tp, "sl": sl}
    pl = cfg.get("pl")
    for k in range(i + first, len(days)):
        ds = days[k]
        closed = ef._check_exits_fast([trade], pre["price_cache"], date.fromisoformat(ds), ds, tp, sl, cfg["tdays"],
                                      None, pl[0] if pl else None, pl[1] if pl else None,
                                      ohlc_cache=pre.get("ohlc_cache"), **ld.PEAK)
        if closed:
            rec = closed[0]["record"]
            return {"pnl": rec["pnl_pct"], "reason": rec["exit_reason"], "exit_idx": k}
    k = len(days) - 1
    return {"pnl": float(P["close"].iat[k, j] / entry - 1), "reason": "END", "exit_idx": k}


def _job(chunk: list[tuple]) -> list[tuple]:
    P, pre, cfg = _SHARED["P"], _SHARED["pre"], _SHARED.get("cfg", LIVE)
    out = []
    for kind, i, j, cost, first, anchor in chunk:
        r = ld._simulate(P, pre, i, j, cfg) if kind == "close" else simulate_open(P, pre, i, j, cfg, cost, first, anchor)
        out.append(((kind, i, j, cost, first, anchor), r["pnl"], r["reason"], r["exit_idx"]))
    return out


def run_sims(jobs: list[tuple], workers: int, log, label: str) -> dict:
    jobs = list(dict.fromkeys(jobs))
    chunks = [jobs[k:k + 2000] for k in range(0, len(jobs), 2000)]
    res, t0 = {}, time.time()
    with mp.get_context("fork").Pool(workers) as pool:
        for n, part in enumerate(pool.imap_unordered(_job, chunks), 1):
            for key, pnl, reason, k in part:
                res[key] = (pnl, reason, k)
            if n % 20 == 0 or n == len(chunks):
                log(f"{label}: {n}/{len(chunks)} chunks, {time.time() - t0:.0f}s")
    return res


def identity(P: dict, pre: dict, picks: list[tuple[int, int]]) -> dict:
    """D3 on the open-entry wrapper: TP and SL off, no lock, cost 0. Time stop 0 exits at the close
    of d (the card's one-day stop), time stop 1 at the close of d+1."""
    cl, op = P["close"].to_numpy(), P["open"].to_numpy()
    out = {}
    for td in (0, 1):
        cfg = {"tp": ld.NONE, "sl": ld.NONE, "pl": None, "tdays": td}
        err = []
        for i, j in picks:
            if i + td >= len(P["days"]):
                continue
            r = simulate_open(P, pre, i, j, cfg, cost=0.0)
            err.append(abs(r["pnl"] - (cl[i + td, j] / op[i, j] - 1)))
        out[f"tdays{td}"] = {"n": len(err), "max_abs_err": float(max(err)), "pass": bool(max(err) < 6e-5)}
    return out


# ── Card readings ──────────────────────────────────────────────────────────────

def day_series(vals: list[tuple[int, float]]) -> pd.Series:
    s = pd.DataFrame(vals, columns=["i", "v"]).groupby("i")["v"].mean()
    s.index = s.index.astype(int)
    return s


def card_c1(P: dict, rng: np.random.Generator) -> dict:
    out = {}
    shuf = [ld.shuffled(P["score_prev"], P["dmask"], rng) for _ in range(N_SHUFFLE)]
    for h in HORIZONS:
        f = P["close"].shift(-(h - 1)) / P["open"] - 1
        ic = ld.row_rank_corr(P["score_prev"], f, P["dmask"])
        r = onesided(ld.nonoverlap(ic, h))
        null = np.array([ld.nonoverlap(ld.row_rank_corr(s, f, P["dmask"]), h)["mean"] for s in shuf])
        r["shuffle"] = {"median": float(np.median(null)), "p5": float(np.percentile(null, 5)),
                        "p95": float(np.percentile(null, 95)),
                        "p_score": float(((null >= r["mean"]).sum() + 1) / (len(null) + 1))}
        r["injected"] = {str(tg): ld.nonoverlap(ld.row_rank_corr(ld.injected(f, P["dmask"], tg, rng), f, P["dmask"]), h)["mean"]
                         for tg in (0.03, 0.10)}
        r["mde_below_ic_econ"] = bool(r["mde"] is not None and r["mde"] <= IC_ECON)
        out[str(h)] = r
    gap = P["open"] / P["close"].shift(1) - 1
    out["descriptive_overnight_gap_ic"] = onesided(ld.nonoverlap(ld.row_rank_corr(P["score_prev"], gap, P["dmask"]), 1))
    return out


def excess_rows(S: dict, R: dict, a: str, b_: str, key) -> list[tuple]:
    return [(i, j, R[key(i, j)][0] - R[key(i, mj)][0]) for (i, j), (_, mj) in zip(S[a], S[b_])]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir", type=Path)
    ap.add_argument("--windows", default="IS,OOS")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--battery", type=Path, default=Path("/home/promenix/apex-battery/2026-10-19"))
    a = ap.parse_args(argv)
    a.outdir.mkdir(parents=True, exist_ok=True)
    repo = Path(__file__).resolve().parents[1]
    git = lambda *c: subprocess.run(["git", *c], cwd=repo, capture_output=True, text=True, check=False).stdout.strip()  # noqa: E731
    commit, dirty = git("rev-parse", "HEAD"), git("status", "--porcelain", "--", "scripts/s12_open_entry_probe.py", "backend")
    if dirty:
        print(f"refused: uncommitted changes under the producer's paths:\n{dirty}")
        return 2
    if git("rev-list", "--count", "origin/master..HEAD") not in ("0", ""):
        print("refused: HEAD is not pushed")
        return 2
    producer = {"repo": "apex", "script": "scripts/s12_open_entry_probe.py", "commit": commit, "run": " ".join(sys.argv),
                "started": time.strftime("%Y-%m-%d %H:%M %Z"), "why": WHY, "peak": "high (R9)", "open_cost": OPEN_COST}
    t0 = time.time()

    def log(msg: str) -> None:
        print(f"[{time.time() - t0:6.0f}s] {msg}", flush=True)

    def save(name: str, obj: dict) -> None:
        (a.outdir / f"{name}.json").write_text(json.dumps(obj, indent=1, default=str))
        log(f"saved {name}")

    global _SHARED
    ef._trading_days_count = functools.lru_cache(maxsize=None)(ef._trading_days_count)   # pure; speed only
    ld._R = b.Runner(b.Store(a.battery), commit[:7])
    for w in a.windows.split(","):
        pre = ld._R._window(w)
        P = open_panels(ld.panels(pre), pre)
        bs = functools.partial(block_stat, n_rows=len(P["days"]))
        rng = np.random.default_rng(SEED)
        S = open_sets(P, rng)
        log(f"{w}: {len(P['days'])} days, {len(P['tickers'])} tickers, open-arm picks {len(S['picks'])} "
            f"(dropped {S['dropped']}), pool cells {len(S['pool'])}, close-arm picks {len(S['cpicks'])}")
        out: dict = {"producer": producer, "window": w, "days": [P["days"][0], P["days"][-1]],
                     "entries": {"open_picks": len(S["picks"]), "open_dropped": S["dropped"], "pool_cells": len(S["pool"]),
                                 "close_picks": len(S["cpicks"]), "close_dropped": S["cdropped"],
                                 "picks_flat_bar_open_eq_close": int(sum(
                                     P["open"].iat[i, j] == P["ohlc_close"].iat[i, j] for i, j in S["picks"])),
                                 "open_days_with_picks": len({i for i, _ in S["picks"]})}}
        out["lookahead_D5"] = assert_no_lookahead(P, pre, S["picks"])
        cl_ok = (P["ohlc_close"] - P["close"]).abs() / P["close"]
        out["parity_price_cache_vs_bar_close"] = {"cells": int(cl_ok.notna().sum().sum()),
                                                  "share_within_1e-6": float((cl_ok < 1e-6).sum().sum() / max(cl_ok.notna().sum().sum(), 1))}
        out["identity_D3"] = identity(P, pre, S["picks"])
        log(f"{w} D3 {out['identity_D3']}")
        if not all(v["pass"] for v in out["identity_D3"].values()):
            save(f"S12_{w}", out)
            log(f"{w}: identity failed; window stopped before any reading")
            continue
        out["C1"] = card_c1(P, rng)
        log(f"{w} C1 done")

        jobs = [("open", i, j, OPEN_COST, 0, False) for i, j in S["pool"]]
        both = S["picks"] + S["matched"]
        jobs += [("open", i, j, c, 0, False) for c in COST_SENS for i, j in both]
        jobs += [("open", i, j, OPEN_COST, 1, False) for i, j in both]
        jobs += [("open", i, j, OPEN_COST, 0, True) for i, j in both]
        jobs += [("close", i, j, 0.0, 0, False) for i, j in S["cpicks"] + S["cmatched"]]
        _SHARED = {"P": P, "pre": pre, "cfg": dict(LIVE)}
        R = run_sims(jobs, a.workers, log, w)
        o = lambda c=OPEN_COST, f=0, an=False: (lambda i, j: ("open", i, j, c, f, an))   # noqa: E731
        cl = lambda i, j: ("close", i, j, 0.0, 0, False)                                   # noqa: E731

        # MDE from the random-vs-random arm, written before C2 is computed.
        rr = excess_rows({"a": S["r1"], "b": S["r2"]}, R, "a", "b", o())
        rr_day = day_series([(i, v) for i, _, v in rr])
        mde = {"block25": bs(rr_day, BLOCK), "block50": bs(rr_day, BLOCK_SENS),
               "per_trade_sd": float(np.std([v for *_, v in rr], ddof=1))}
        null_p, synth_p = [], []
        cells = S["cells"]
        qp = P["q_prev"].to_numpy()
        for _ in range(N_NULL):
            x, y = [], []
            for i, j in S["picks"]:
                cand = cells[(i, int(qp[i, j]))]
                u, v = R[o()(i, int(rng.choice(cand)))][0], R[o()(i, int(rng.choice(cand)))][0]
                x.append((i, u - v)); y.append((i, u - v + SYNTH_EDGE))
            null_p.append(bs(day_series(x), BLOCK)["p_pos"])
            synth_p.append(bs(day_series(y), BLOCK)["p_pos"])
        mde["null_rejection_rate_5pct"] = float(np.mean(np.array(null_p) <= ALPHA))
        mde["synthetic_edge_rejection_rate_5pct"] = float(np.mean(np.array(synth_p) <= ALPHA))
        mde["random_vs_random_reading"] = {k: mde["block25"][k] for k in ("mean", "t", "p_pos")}
        save(f"S12_{w}_mde", {"producer": producer, "window": w, "mde": mde})
        out["mde_random_vs_random"] = mde

        # C2
        ex = excess_rows(S, R, "picks", "matched", o())
        c2_day = day_series([(i, v) for i, _, v in ex])
        out["C2"] = {"block25": bs(c2_day, BLOCK), "block50_sensitivity": bs(c2_day, BLOCK_SENS),
                     "per_trade": {"n": len(ex), "picks_mean": float(np.mean([R[o()(i, j)][0] for i, j in S["picks"]])),
                                   "matched_mean": float(np.mean([R[o()(i, j)][0] for i, j in S["matched"]])),
                                   "excess_mean": float(np.mean([v for *_, v in ex]))}}
        synth = day_series([(i, v + SYNTH_EDGE) for i, _, v in ex])
        out["control_synthetic_edge_on_picks"] = bs(synth, BLOCK)

        # C3 (as written: both open-arm terms pay the cost, so it cancels to first order)
        cex = excess_rows({"a": S["cpicks"], "b": S["cmatched"]}, R, "a", "b", cl)
        cday = day_series([(i, v) for i, _, v in cex])

        def c3(open_day: pd.Series) -> dict:
            d = (open_day - cday).dropna()
            return {"block25": bs(d, BLOCK), "block50_sensitivity": bs(d, BLOCK_SENS)}
        out["C3"] = c3(c2_day)
        out["C3"]["close_arm_C2"] = bs(cday, BLOCK)
        out["C3"]["cost_sensitivity"] = {}
        for c in COST_SENS:
            exc = excess_rows(S, R, "picks", "matched", o(c))
            out["C3"]["cost_sensitivity"][str(c)] = c3(day_series([(i, v) for i, _, v in exc]))["block25"]

        # Holm over C1 x 4 and C2; C3 in fixed sequence after C2
        ps = {f"C1_h{h}": out["C1"][str(h)]["p_pos"] for h in HORIZONS}
        ps["C2"] = out["C2"]["block25"]["p_pos"]
        H = holm(ps)
        c2_sup = H["C2"]["reject"] and out["C2"]["block25"]["mean"] > 0
        out["holm"] = H
        out["C3"]["confirmatory"] = bool(c2_sup)
        out["C3"]["supported"] = bool(c2_sup and out["C3"]["block25"]["p_pos"] <= ALPHA)
        out["verdicts"] = {k: ("supported" if H[k]["reject"] else
                               "contradicted" if (out["C1"][k[4:]] if k.startswith("C1") else out["C2"]["block25"])["p_neg"] <= ALPHA / len(ps)
                               else "not supported") for k in ps}

        # Descriptive
        D: dict = {}
        exa = excess_rows(S, R, "picks", "matched", o(an=True))
        D["C2_bracket_on_prior_close"] = bs(day_series([(i, v) for i, _, v in exa]), BLOCK)
        pw = np.array([R[o()(i, j)][0] > 0 for i, j in S["picks"]])
        mw = np.array([R[o()(i, j)][0] > 0 for i, j in S["matched"]])
        D["win_rate"] = {"picks": float(pw.mean()), "matched_same_exit": float(mw.mean())}
        yr = pd.Series(c2_day.values, index=[P["days"][i][:4] for i in c2_day.index])
        D["C2_by_year"] = {y: {"mean_of_day_means": float(g.mean()), "days": int(len(g))} for y, g in yr.groupby(level=0)}
        D["same_picks_open_minus_close"] = {}
        cpk = {(i, j) for i, j in S["cpicks"]}
        for c in (0.0, OPEN_COST, COST_SENS[1]):
            v = [(i - 1, R[o(c)(i, j)][0] - R[cl(i - 1, j)][0]) for i, j in S["picks"] if (i - 1, j) in cpk]
            D["same_picks_open_minus_close"][str(c)] = {**bs(day_series(v), BLOCK), "n_picks": len(v)}
        sec = P["sector"]
        by_sec: dict[str, float] = {}
        for i, j, v in ex:
            by_sec[sec[P["tickers"][j]]] = by_sec.get(sec[P["tickers"][j]], 0.0) + v
        best = max(by_sec, key=by_sec.get)
        top3 = sorted(range(len(ex)), key=lambda k: ex[k][2], reverse=True)[:3]
        D["group3"] = {"sector_sums": by_sec, "best_sector": best,
                       "without_best_sector": bs(day_series([(i, v) for i, j, v in ex if sec[P["tickers"][j]] != best]), BLOCK),
                       "best3": [(P["days"][ex[k][0]], P["tickers"][ex[k][1]], ex[k][2]) for k in top3],
                       "without_best3": bs(day_series([(i, v) for k, (i, _, v) in enumerate(ex) if k not in top3]), BLOCK)}
        on_d = lambda S_: float(np.mean([R[o()(i, j)][2] == i for i, j in S_]))   # noqa: E731
        exd = excess_rows(S, R, "picks", "matched", o(f=1))
        D["fill_delay_bracket"] = {"share_exit_on_bar_d": {"picks": on_d(S["picks"]), "matched": on_d(S["matched"])},
                                   "C3_exits_from_bar_d": out["C3"]["block25"],
                                   "C3_exits_from_bar_d1": c3(day_series([(i, v) for i, _, v in exd]))["block25"]}
        out["descriptive"] = D
        out["finished"] = time.strftime("%Y-%m-%d %H:%M %Z")
        save(f"S12_{w}", out)
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
