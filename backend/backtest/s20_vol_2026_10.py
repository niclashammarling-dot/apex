"""S20 volatility as a signal, L0 card, 2026-10-24: run as written before any number existed.

Why: Niclas 2026-10-24 asked whether volatility can be a signal ("vix poäng eller liknande"), then
"fortsätt testa tills alla frågor är besvarade". The card (question, predictors, predicted signs,
Holm rule, per-criterion controls, outcomes) is vault
raw/notes/2026-10/2026-10-24-apex-s20-volatility-l0-card.md, committed (vault 0123140a) before this
script was written. T1's ATR IC (B6) was already seen, so level predictors are descriptive only.
Exploratory: no config change follows.

How: python -u -m backend.backtest.s20_vol_2026_10 OUTDIR [--windows IS,OOS]
     [--battery /home/promenix/apex-battery/2026-10-19]

What: OUTDIR/S20_<window>.json per window (statistics, controls), then OUTDIR/S20_verdict.json
(the item-9 rule over both windows, the per-criterion controls, the validity gates).
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

HORIZONS = ld.L0_HORIZONS                     # 1, 5, 10, 25
CONFIRMATORY = ("V3", "V4")
PREDICTED = {"V3": -1, "V4": +1}              # card item 3
DESCRIPTIVE = ("V1", "V2", "V4raw", "V5")
ALPHA = 0.05
IC_ECON = 0.030                               # v2 card, conservative end
N_SHUFFLE = 200
N_ZERO_DRAWS = 20
SEED = 20261024


# ── Predictors (closes up to and including day d only) ─────────────────────────

def rv(logret: pd.DataFrame, n: int) -> pd.DataFrame:
    return logret.rolling(n, min_periods=n).std()


def etf_returns_by_ticker(P: dict) -> pd.DataFrame:
    """Each ticker's sector-ETF simple daily return, aligned to the ticker columns (NaN without an ETF)."""
    er = P["etf_close"].pct_change(fill_method=None)
    cols = {t: (er[e] if e in er.columns else pd.Series(np.nan, index=er.index))
            for t, e in ((t, P["etf_of"].get(t)) for t in P["tickers"])}
    return pd.DataFrame(cols, index=P["close"].index)


def predictors(P: dict) -> dict[str, pd.DataFrame]:
    close = P["close"]
    lr = np.log(close / close.shift(1))
    sr = close.pct_change(fill_method=None)
    rv5, rv20, rv60 = rv(lr, 5), rv(lr, 20), rv(lr, 60)
    with np.errstate(divide="ignore", invalid="ignore"):
        shock = np.log(rv5 / rv60)
    shock = shock.replace([np.inf, -np.inf], np.nan)
    rel = (sr - etf_returns_by_ticker(P)).rolling(20, min_periods=20).std()
    return {"V1": P["atr"], "V2": rv20, "V3": rel, "V4raw": shock,
            "V5": close / close.shift(5) - 1}


def partial_rank(x: pd.DataFrame, z: pd.DataFrame, mask: pd.DataFrame) -> pd.DataFrame:
    """Per day: rank of x residualised (OLS) on rank of z, over cells where mask, x and z hold."""
    ok = mask & x.notna() & z.notna()
    xr = x.where(ok).rank(axis=1)
    zr = z.where(ok).rank(axis=1)
    xc = xr.sub(xr.mean(axis=1), axis=0)
    zc = zr.sub(zr.mean(axis=1), axis=0)
    beta = (xc * zc).sum(axis=1) / (zc ** 2).sum(axis=1)
    return xc.sub(zc.mul(beta, axis=0))


def quintile_ic(x: pd.DataFrame, f: pd.DataFrame, mask: pd.DataFrame, atr: pd.DataFrame) -> pd.Series:
    """Mean over ATR quintiles of the within-quintile daily rank IC (V4q)."""
    q = ld.quintile(atr, mask)
    per = [ld.row_rank_corr(x, f, mask & (q == k)) for k in range(5)]
    return pd.concat(per, axis=1).mean(axis=1)


# ── Statistics and the item-9 rule ─────────────────────────────────────────────

def p_two_sided(t: float | None) -> float | None:
    if t is None or not math.isfinite(t):
        return None
    return 2 * (1 - NormalDist().cdf(abs(t)))


def holm(pvals: dict) -> dict:
    """Holm step-down at ALPHA: key -> rejected (bool). None p-values are never rejected."""
    items = sorted(((p, k) for k, p in pvals.items() if p is not None))
    m = len(items)
    out = {k: False for k in pvals}
    for i, (p, k) in enumerate(items):
        if p <= ALPHA / (m - i):
            out[k] = True
        else:
            break
    return out


def verdict(is_stat: dict, oos_stat: dict, predicted: int, rejected: bool, control_ok: bool) -> str:
    """Card item 9 for one predictor and horizon."""
    t_is, t_oos = is_stat.get("t"), oos_stat.get("t")
    if not control_ok:
        return "unanswered (D4: +0.03 control fails at this h)"
    if rejected and t_is is not None and np.sign(t_is) == predicted:
        if t_oos is not None and np.sign(t_oos) == predicted and abs(t_oos) >= 1:
            return "supported"
        return "unanswered (IS only, OOS sign check fails)"
    if rejected and t_is is not None and np.sign(t_is) == -predicted:
        return "contradicted"
    return "unanswered"


def ic_stats(x: pd.DataFrame, f: pd.DataFrame, mask: pd.DataFrame, h: int) -> dict:
    s = ld.nonoverlap(ld.row_rank_corr(x, f, mask), h)
    s["p"] = p_two_sided(s.get("t"))
    return s


def pred_corr(preds: dict, mask: pd.DataFrame) -> dict:
    """Day-average cross-sectional rank correlation between every pair of predictors."""
    keys = list(preds)
    out = {}
    for i, a in enumerate(keys):
        for c in keys[i + 1:]:
            out[f"{a}~{c}"] = float(ld.row_rank_corr(preds[a], preds[c], mask).mean())
    return out


def card_window(P: dict) -> dict:
    rng = np.random.default_rng(SEED)
    mask = P["mask"]
    pr = predictors(P)
    pr["V4"] = partial_rank(pr["V4raw"], pr["V5"], mask)
    out = {"pool_size": {"median": float(mask.sum(axis=1).median()),
                         "share_days_ge20": float((mask.sum(axis=1) >= 20).mean())},
           "coverage": {k: float((v.notna() & mask).sum().sum() / mask.sum().sum()) for k, v in pr.items()},
           "pred_corr": pred_corr({**pr, "score": P["score"]}, mask), "h": {}}
    for h in HORIZONS:
        f = ld.fwd(P["close"], h)
        r = {k: ic_stats(pr[k], f, mask, h) for k in CONFIRMATORY + DESCRIPTIVE}
        r["V4q"] = ld.nonoverlap(quintile_ic(pr["V4raw"], f, mask, P["atr"]), h)
        r["shuffle"] = {}
        for k in CONFIRMATORY:
            null = [ic_stats(ld.shuffled(pr[k], mask, rng), f, mask, h) for _ in range(N_SHUFFLE)]
            means = np.array([x["mean"] for x in null])
            r["shuffle"][k] = {"p5": float(np.percentile(means, 5)), "p95": float(np.percentile(means, 95)),
                               "reject_rate_5pct": float(np.mean([abs(x["t"]) > 1.96 for x in null]))}
        r["injected"] = {str(tgt): ic_stats(ld.injected(f, mask, tgt, rng), f, mask, h)["mean"]
                         for tgt in (0.03, 0.10)}
        r["criterion"] = {
            "plus": ic_stats(ld.injected(f, mask, IC_ECON, rng), f, mask, h),
            "minus": ic_stats(ld.injected(f, mask, -IC_ECON, rng), f, mask, h),
            "zero": [ic_stats(ld.injected(f, mask, 0.0, rng), f, mask, h) for _ in range(N_ZERO_DRAWS)],
        }
        out["h"][str(h)] = r
    return out


def criterion_controls(IS: dict, OOS: dict) -> dict:
    """Item 7 per criterion. A synthetic arm passes Holm whatever its rank in an 8-test family iff
    p <= ALPHA/8 (Holm's first step), so that bound is used for the synthetic arms."""
    bound = ALPHA / (len(CONFIRMATORY) * len(HORIZONS))
    out = {}
    for h in map(str, HORIZONS):
        ci, co = IS["h"][h]["criterion"], OOS["h"][h]["criterion"]

        def passes(si, so, sign):
            return (si["p"] is not None and si["p"] <= bound and np.sign(si["t"]) == sign
                    and so["t"] is not None and np.sign(so["t"]) == sign and abs(so["t"]) >= 1)

        zero_rate = float(np.mean([z["p"] is not None and z["p"] <= bound for z in ci["zero"]]))
        out[h] = {"plus_supported": bool(passes(ci["plus"], co["plus"], +1)),
                  "minus_contradicted": bool(ci["minus"]["p"] is not None and ci["minus"]["p"] <= bound
                                             and ci["minus"]["t"] < 0),
                  "zero_reject_rate": zero_rate}
    return out


def validity(IS: dict, OOS: dict) -> dict:
    gates = {}
    for w, R in (("IS", IS), ("OOS", OOS)):
        gates[f"pool_{w}"] = R["pool_size"]["share_days_ge20"] >= 0.95
        for h, r in R["h"].items():
            for tgt, got in r["injected"].items():
                gates[f"inject_{tgt}_{w}_h{h}"] = abs(got - float(tgt)) <= 0.01
    for k in CONFIRMATORY:
        rates = [R["h"][h]["shuffle"][k]["reject_rate_5pct"] for R in (IS, OOS) for h in R["h"]]
        gates[f"shuffle_{k}"] = float(np.mean(rates)) < 0.10
    return gates


def rule(IS: dict, OOS: dict) -> dict:
    ctrl = criterion_controls(IS, OOS)
    pvals = {(k, h): IS["h"][h][k]["p"] for k in CONFIRMATORY for h in map(str, HORIZONS)}
    rej = holm(pvals)
    v = {}
    for (k, h), r in rej.items():
        s_is, s_oos = IS["h"][h][k], OOS["h"][h][k]
        v[f"{k}_h{h}"] = {"verdict": verdict(s_is, s_oos, PREDICTED[k], r, ctrl[h]["plus_supported"]),
                          "ic_is": s_is["mean"], "t_is": s_is["t"], "p_is": s_is["p"], "holm_rejected": r,
                          "ic_oos": s_oos["mean"], "t_oos": s_oos["t"], "mde_is": s_is.get("mde"),
                          "mde_vs_ic_econ": None if s_is.get("mde") is None else s_is["mde"] <= IC_ECON}
    return {"verdicts": v, "criterion_controls": ctrl, "validity": validity(IS, OOS)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir", type=Path)
    ap.add_argument("--windows", default="IS,OOS")
    ap.add_argument("--battery", type=Path, default=Path("/home/promenix/apex-battery/2026-10-19"))
    a = ap.parse_args()
    a.outdir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False).stdout.strip()

    def log(msg: str) -> None:
        print(f"[{time.time() - t0:6.0f}s] {msg}", flush=True)

    ef._trading_days_count = functools.lru_cache(maxsize=None)(ef._trading_days_count)   # pure; speed only
    runner = b.Runner(b.Store(a.battery), commit)
    res = {}
    for w in a.windows.split(","):
        P = ld.panels(runner._window(w))
        log(f"{w} panels: {len(P['days'])} days, {len(P['tickers'])} tickers")
        res[w] = card_window(P)
        (a.outdir / f"S20_{w}.json").write_text(json.dumps(res[w], indent=1, default=str))
        log(f"saved S20_{w}")
    if {"IS", "OOS"} <= set(res):
        out = {"producer": {"script": "backend/backtest/s20_vol_2026_10.py", "commit": commit,
                            "command": " ".join(sys.argv), "card": "vault 0123140a"},
               **rule(res["IS"], res["OOS"])}
        (a.outdir / "S20_verdict.json").write_text(json.dumps(out, indent=1, default=str))
        log("saved S20_verdict")
    log("done")


if __name__ == "__main__":
    sys.exit(main())
