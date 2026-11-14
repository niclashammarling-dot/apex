"""S24 volume accumulation as a direction signal among the picks, L0 card, 2026-11-14: run as written.

Why: VA decides live L5 on 43 of 169 ticker-days (09-01..11-11, M11) and was never tested (R054 a). The card
(question, statistics, nulls, controls, Holm rule, outcomes, the L3 arm map) is vault
raw/notes/2026-11/2026-11-12-apex-s24-va-l0-card.md, pre-registered 0acbb58b, Amendment 1 (R059 b, 8339e4c6) before
this script was written. Statistics, nulls and the Holm rule follow s20_vol_2026_10; the injected-IC gate follows
Amendment 1 (K draws per cell against the construction's own reference).

How: python -u -m backend.backtest.s24_va_2026_11 OUTDIR [--windows IS,OOS]
     [--battery /home/promenix/apex-battery/2026-10-19]

What: OUTDIR/S24_<window>.json per window, then OUTDIR/S24_verdict.json (the item-9 rule over both windows, the
per-criterion controls, the validity gates, the L3 arm pair from item 9's map).
"""
from __future__ import annotations

import argparse
import functools
import json
import math
import subprocess
import sys
import time
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd

from backend.backtest import battery_2026_10 as b
from backend.backtest import engine_fast as ef
from backend.backtest import ladder_2026_10 as ld
from backend.backtest import s20_vol_2026_10 as s20
from backend.gate.l5_positioning import VOL_ACCUM_THRESHOLD, VOL_ACCUM_WINDOW

CARD = "vault raw/notes/2026-11/2026-11-12-apex-s24-va-l0-card.md (0acbb58b, Amendment 1 8339e4c6)"
HORIZONS = ld.L0_HORIZONS                     # 1, 5, 10, 25
STATS = ("C1", "C2")
PICK = 0.65                                   # the live threshold (shared definitions)
IC_ECON = 0.030
N_SHUFFLE = 200
N_ZERO_DRAWS = 20
K_START = 20                                  # Amendment 1
BAND = 0.01
REF_DRAWS = 200_000
SEED = 20261114
BINS = [-np.inf, 0.8, 1.0, 1.2, 1.4, 1.8, 2.5, np.inf]
SCOPE_SHARE_ABOVE = 0.797                     # scope probe, IS share of picks above 1.2 (D3 reproduction)


# ── Predictors (bars at or before d only) ─────────────────────────────────────

def va_series(close: pd.Series, volume: pd.Series) -> pd.Series:
    """VA on each bar: the 20 most recent bars with both Close and Volume, up volume over down volume on their 19
    close-to-close changes, as l5_positioning._check_volume_accumulation; down volume 0 gives +inf; fewer than 20 bars
    gives NaN."""
    d = pd.DataFrame({"c": close, "v": volume}).dropna()
    ret = d["c"].diff()
    up = d["v"].where(ret > 0, 0.0)
    dn = d["v"].where(ret < 0, 0.0)
    w = VOL_ACCUM_WINDOW - 1
    up_s, dn_s = up.rolling(w).sum(), dn.rolling(w).sum()
    ratio = (up_s / dn_s).where(dn_s != 0, np.inf)
    ratio.iloc[:VOL_ACCUM_WINDOW - 1] = np.nan
    return ratio


def va_panel(pre: dict, P: dict) -> pd.DataFrame:
    """Days x tickers: VA as of each day d, from the latest bar at or before d."""
    days = pd.Index(P["days"])
    cols = {}
    for t in P["tickers"]:
        df = pre["raw_data"].get(t)
        if df is None or "Close" not in df or "Volume" not in df:
            cols[t] = pd.Series(np.nan, index=days)
            continue
        s = va_series(df["Close"], df["Volume"])
        idx = pd.DatetimeIndex(s.index)
        s.index = (idx.tz_localize(None) if idx.tz is not None else idx).strftime("%Y-%m-%d")
        s = s[~s.index.duplicated(keep="last")]
        cols[t] = s.reindex(s.index.union(days)).ffill().reindex(days)
    return pd.DataFrame(cols, index=days)


def rs_panel(P: dict) -> pd.DataFrame:
    """The ticker's 5-day close return minus its sector ETF's; NaN when either is missing."""
    r = P["close"] / P["close"].shift(5) - 1
    e = P["etf_close"] / P["etf_close"].shift(5) - 1
    return pd.DataFrame({t: r[t] - e[P["etf_of"][t]] if P["etf_of"].get(t) in e else np.nan for t in P["tickers"]},
                        index=r.index)


# ── Statistics ────────────────────────────────────────────────────────────────

def c2_series(va: pd.DataFrame, f: pd.DataFrame, cls: pd.DataFrame) -> tuple[pd.Series, int]:
    """Per day, among the class (RS-fail picks with VA and f): mean f of VA > 1.2 minus VA <= 1.2, on days with a
    pick on each side; the second value counts the class days dropped for lacking a side."""
    ok = cls & va.notna() & f.notna()
    above, below = ok & (va > VOL_ACCUM_THRESHOLD), ok & (va <= VOL_ACCUM_THRESHOLD)
    both = (above.sum(axis=1) > 0) & (below.sum(axis=1) > 0)
    d = (f.where(above).mean(axis=1) - f.where(below).mean(axis=1)).where(both)
    return d, int(((ok.sum(axis=1) > 0) & ~both).sum())


def c2_stats(va, f, cls, h) -> dict:
    d, dropped = c2_series(va, f, cls)
    s = ld.nonoverlap(d, h)
    s["p"] = s20.p_two_sided(s.get("t"))
    s["days_dropped_one_side"] = dropped
    return s


def labels_from(pred: pd.DataFrame, va: pd.DataFrame, cls: pd.DataFrame) -> pd.DataFrame:
    """Item 7's synthetic C2 labels: each day's class picks marked 'above' by the top share of `pred`, the share
    equal to that day's real VA > 1.2 share. Returned as a VA-like frame (2.0 above, 1.0 below)."""
    ok = cls & va.notna()
    share = (ok & (va > VOL_ACCUM_THRESHOLD)).sum(axis=1) / ok.sum(axis=1).replace(0, np.nan)
    pr = pred.where(ok).rank(axis=1, ascending=False, pct=True)
    return (pr.le(share, axis=0)).astype(float).where(ok) + 1.0


def injection_z(f: pd.DataFrame, mask: pd.DataFrame) -> pd.DataFrame:
    """ladder.injected's deterministic part (normal scores of f's within-day rank); a draw is a * z + unit noise."""
    pr = f.where(mask).rank(axis=1, pct=True)
    n = f.where(mask).notna().sum(axis=1)
    u = (pr.mul(n, axis=0) - 0.5).div(n, axis=0).to_numpy()
    zz = np.full(u.shape, np.nan)
    ok = ~np.isnan(u)
    zz[ok] = np.vectorize(NormalDist().inv_cdf)(u[ok])
    return pd.DataFrame(zz, index=f.index, columns=f.columns)


def expected_spearman(n: int, target: float, draws: int = REF_DRAWS, seed: int = SEED) -> float:
    """Amendment 1's reference, apart from the runner: the construction's expected daily Spearman at n picks, from
    ranks 1..n (normal scores times target/sqrt(1-target^2) plus unit noise), in batches."""
    rng = np.random.default_rng(seed + n)
    nd = NormalDist()
    z = np.array([nd.inv_cdf((k - 0.5) / n) for k in range(1, n + 1)])
    a = target / math.sqrt(1 - target ** 2)
    xc = np.arange(1, n + 1) - (n + 1) / 2
    tot, done = 0.0, 0
    while done < draws:
        m = min(50_000, draws - done)
        s = a * z + rng.standard_normal((m, n))
        rc = s.argsort(axis=1).argsort(axis=1) + 1.0
        rc -= rc.mean(axis=1, keepdims=True)
        tot += float(((rc @ xc) / np.sqrt((rc ** 2).sum(axis=1) * (xc ** 2).sum())).sum())
        done += m
    return tot / draws


def injected_cell(f, mask, h, target, rng, ref_cache) -> dict:
    """Amendment 1 for one cell (C1 x h x window) and one target: K draws through the runner's C1 path."""
    z = injection_z(f, mask)
    a = target / math.sqrt(1 - target ** 2)

    def one() -> float:
        return ld.nonoverlap(ld.row_rank_corr(a * z + rng.standard_normal(z.shape), f, mask), h)["mean"]
    means = [one() for _ in range(K_START)]
    sd1 = float(np.std(means, ddof=1))
    k = max(K_START, math.ceil((3 * sd1 / BAND) ** 2))
    means += [one() for _ in range(k - K_START)]
    days = ld.row_rank_corr(z, f, mask).dropna().iloc[::max(h, 1)].index
    counts = (mask & f.notna()).loc[days].sum(axis=1)
    for n in sorted(set(int(c) for c in counts)):
        if (n, target) not in ref_cache:
            ref_cache[(n, target)] = expected_spearman(n, target)
    ref = float(np.mean([ref_cache[(int(c), target)] for c in counts]))
    mean = float(np.mean(means))
    return {"target": target, "K": k, "mean": mean, "se_k": float(np.std(means, ddof=1) / math.sqrt(k)),
            "reference": ref, "pass": abs(mean - ref) <= BAND}


def card_window(P: dict, pre: dict, w: str, log=print) -> dict:
    rng = np.random.default_rng(SEED)
    va, rs = va_panel(pre, P), rs_panel(P)
    picks = P["mask"] & (P["score"] >= PICK)
    cls = picks & (rs <= 0) & rs.notna()
    pick_days = picks.sum(axis=1)
    out = {"window": w, "picks": int(picks.sum().sum()), "pick_days": int((pick_days > 0).sum()),
           "median_picks_per_pick_day": float(pick_days[pick_days > 0].median()),
           "va_computable_share": float((va.notna() & picks).sum().sum() / picks.sum().sum()),
           "share_picks_above": float((va.where(picks) > VOL_ACCUM_THRESHOLD).sum().sum() / (va.notna() & picks).sum().sum()),
           "rs_fail_picks": int(cls.sum().sum()),
           "rs_missing_picks": int((picks & rs.isna()).sum().sum()), "h": {}}
    ref_cache: dict = {}
    for h in HORIZONS:
        f = ld.fwd(P["close"], h)
        r = {"C1": s20.ic_stats(va, f, picks, h), "C2": c2_stats(va, f, cls, h)}
        r["shuffle"] = {}
        c1_null = [s20.ic_stats(ld.shuffled(va, picks, rng), f, picks, h) for _ in range(N_SHUFFLE)]
        c2_null = [c2_stats(ld.shuffled(va, cls, rng), f, cls, h) for _ in range(N_SHUFFLE)]
        for k, null in (("C1", c1_null), ("C2", c2_null)):
            means = np.array([x["mean"] for x in null if x["mean"] is not None])
            r["shuffle"][k] = {"p5": float(np.percentile(means, 5)), "p95": float(np.percentile(means, 95)),
                               "reject_rate_5pct": float(np.mean([x["t"] is not None and abs(x["t"]) > 1.96 for x in null]))}
        r["injected"] = {str(t): injected_cell(f, picks, h, t, rng, ref_cache) for t in (0.03, 0.10)}

        def arm(target):
            pred = ld.injected(f, picks, target, rng)
            return {"C1": s20.ic_stats(pred, f, picks, h), "C2": c2_stats(labels_from(pred, va, cls), f, cls, h)}
        zero = [arm(0.0) for _ in range(N_ZERO_DRAWS)]
        r["criterion"] = {"plus": arm(IC_ECON), "minus": arm(-IC_ECON),
                          "zero": {k: [z[k] for z in zero] for k in STATS}}
        r["descriptive"] = {
            "C1_whole_pool": s20.ic_stats(va, f, P["mask"], h),
            "score_ic_picks": s20.ic_stats(P["score"], f, picks, h)["mean"],
            "atr_ic_picks": s20.ic_stats(P["atr"], f, picks, h)["mean"],
            "rs5_ic_in_class": s20.ic_stats(rs, f, cls, h)["mean"],
            "bins": {f"{lo}..{hi}": {"n": int(m.sum().sum()), "mean_fwd": float(f.where(m).stack().mean()) if m.sum().sum() else None}
                     for lo, hi in zip(BINS[:-1], BINS[1:])
                     for m in [picks & va.notna() & f.notna() & (va > lo) & (va <= hi) & ~np.isinf(va)]},
            "bin_inf": {"n": int((picks & np.isinf(va)).sum().sum())},
        }
        out["h"][str(h)] = r
        log(f"{w} h{h}: C1 {r['C1'].get('mean')} t {r['C1'].get('t')}; C2 {r['C2'].get('mean')} t {r['C2'].get('t')}")
    out["corr_picks"] = {k: float(ld.row_rank_corr(va, x, picks).mean())
                         for k, x in (("score", P["score"]), ("atr", P["atr"]), ("ret5", P["close"] / P["close"].shift(5) - 1))}
    return out


# ── Rule ──────────────────────────────────────────────────────────────────────

def criterion_controls(IS: dict, OOS: dict) -> dict:
    """Item 7 per criterion, per statistic and h (S20's form; Holm's first step bound for synthetic arms)."""
    bound = s20.ALPHA / (len(STATS) * len(HORIZONS))
    out = {}
    for h in map(str, HORIZONS):
        for k in STATS:
            ci, co = IS["h"][h]["criterion"], OOS["h"][h]["criterion"]
            pi, po, mi = ci["plus"][k], co["plus"][k], ci["minus"][k]
            plus = (pi["p"] is not None and pi["p"] <= bound and pi["t"] > 0
                    and po["t"] is not None and po["t"] > 0 and abs(po["t"]) >= 1)
            out[f"{k}_h{h}"] = {"plus_supported": bool(plus),
                                "minus_contradicted": bool(mi["p"] is not None and mi["p"] <= bound and mi["t"] < 0),
                                "zero_reject_rate": float(np.mean([z["p"] is not None and z["p"] <= bound for z in ci["zero"][k]]))}
    return out


def validity(IS: dict, OOS: dict) -> dict:
    g = {}
    for w, R in (("IS", IS), ("OOS", OOS)):
        g[f"median_picks_{w}"] = R["median_picks_per_pick_day"] >= 5
        g[f"va_computable_{w}"] = R["va_computable_share"] >= 0.95
        for h, r in R["h"].items():
            for t, cell in r["injected"].items():
                g[f"inject_{t}_{w}_h{h}"] = cell["pass"]
    for k in STATS:
        rates = [R["h"][h]["shuffle"][k]["reject_rate_5pct"] for R in (IS, OOS) for h in R["h"]]
        g[f"shuffle_{k}"] = float(np.mean(rates)) < 0.10
    g["reproduction_share_above_IS"] = abs(IS["share_picks_above"] - SCOPE_SHARE_ABOVE) <= 0.03
    return g


def rule(IS: dict, OOS: dict) -> dict:
    ctrl = criterion_controls(IS, OOS)
    gates = validity(IS, OOS)
    pvals = {(k, h): IS["h"][h][k]["p"] for k in STATS for h in map(str, HORIZONS)}
    rej = s20.holm(pvals)
    v = {}
    for (k, h), r in rej.items():
        s_is, s_oos = IS["h"][h][k], OOS["h"][h][k]
        cells_ok = all(gates.get(f"inject_{t}_{w}_h{h}", True) for t in ("0.03", "0.1") for w in ("IS", "OOS"))
        verdict = (s20.verdict(s_is, s_oos, +1, r, ctrl[f"{k}_h{h}"]["plus_supported"]) if cells_ok
                   else "invalid (D1: an injected cell fails at this h)")
        v[f"{k}_h{h}"] = {"verdict": verdict, "mean_is": s_is["mean"], "t_is": s_is["t"], "p_is": s_is["p"],
                          "holm_rejected": r, "mean_oos": s_oos["mean"], "t_oos": s_oos["t"], "mde_is": s_is.get("mde")}
    kinds = {x["verdict"] for x in v.values()}
    if "supported" in kinds and "contradicted" in kinds:
        outcome, pair = "D6: a supported and a contradicted cell together", "RS alone against RS-or-VA"
    elif "supported" in kinds:
        outcome, pair = "supported", "RS alone against RS-or-VA"
    elif "contradicted" in kinds:
        outcome, pair = "contradicted", "RS alone against RS-or-VA"
    else:
        outcome, pair = "unanswered", "no price group against RS-or-VA"
    return {"verdicts": v, "outcome": outcome, "l3_arms": f"{pair}, each against RND (card item 9; Niclas decides whether it runs, R055)",
            "criterion_controls": ctrl, "validity": gates, "all_gates_pass": all(gates.values())}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir", type=Path)
    ap.add_argument("--windows", default="IS,OOS")
    ap.add_argument("--battery", type=Path, default=Path("/home/promenix/apex-battery/2026-10-19"))
    a = ap.parse_args()
    a.outdir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    git = lambda *x: subprocess.run(["git", *x], capture_output=True, text=True, check=False).stdout.strip()
    commit = git("rev-parse", "--short", "HEAD")

    def log(msg: str) -> None:
        print(f"[{time.time() - t0:6.0f}s] {msg}", flush=True)

    ef._trading_days_count = functools.lru_cache(maxsize=None)(ef._trading_days_count)   # pure; speed only
    runner = b.Runner(b.Store(a.battery), commit)
    res = {}
    for w in a.windows.split(","):
        pre = runner._window(w)
        P = ld.panels(pre)
        log(f"{w} panels: {len(P['days'])} days, {len(P['tickers'])} tickers")
        res[w] = card_window(P, pre, w, log)
        (a.outdir / f"S24_{w}.json").write_text(json.dumps(res[w], indent=1, default=str))
        log(f"saved S24_{w}")
    if {"IS", "OOS"} <= set(res):
        out = {"producer": {"repo": "apex", "script": "backend/backtest/s24_va_2026_11.py", "commit": commit,
                            "dirty": bool(git("status", "--porcelain", "--", "backend/")),
                            "command": " ".join(sys.argv), "card": CARD},
               **rule(res["IS"], res["OOS"])}
        (a.outdir / "S24_verdict.json").write_text(json.dumps(out, indent=1, default=str))
        log(f"saved S24_verdict: {out['outcome']}; gates {'pass' if out['all_gates_pass'] else 'FAIL'}")
    log("done")


if __name__ == "__main__":
    sys.exit(main())
