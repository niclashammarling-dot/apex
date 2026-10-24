"""S21 short-term reversal, L0 card, 2026-10-24: total vs residual reversal, and the score without it.

Why: S20/S20b found the score 0.60 rank-correlated with the 5-day return, whose IC is negative and more
so after stressed VIX closes. Niclas 2026-10-24: "Vad kan vi ta reda på om reversalen?". The card is
vault raw/notes/2026-10/2026-10-24-apex-s21-reversal-l0-card.md, committed (vault c5a3c881) before
this module. Exploratory: no config change follows.

How: python -u -m backend.backtest.s21_reversal_2026_10 OUTDIR [--windows IS,OOS]
     [--battery /home/promenix/apex-battery/2026-10-19]

What: OUTDIR/S21_<window>.json, then OUTDIR/S21_verdict.json (Holm over R1-R3 at h 1 and 5, rule,
per-criterion controls, validity).
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

import numpy as np
import pandas as pd

from backend.backtest import battery_2026_10 as b
from backend.backtest import engine_fast as ef
from backend.backtest import ladder_2026_10 as ld
from backend.backtest import s20_vol_2026_10 as s20
from backend.backtest import s20b_vol_state_2026_10 as sb

HORIZONS = [1, 5, 10, 25]
DECIDING_H = ("1", "5")
CONFIRMATORY = ("R1", "R2", "R3")
PREDICTED = {"R1": -1, "R2": -1, "R3": 0}
LOOKBACKS = [1, 3, 10, 20]
TARGET = 0.03
N_SHUFFLE = 200
N_ZERO = 20
SEED = 20261027


def etf_close_by_ticker(P: dict) -> pd.DataFrame:
    cols = {t: (P["etf_close"][e] if e in P["etf_close"].columns else pd.Series(np.nan, index=P["close"].index))
            for t, e in ((t, P["etf_of"].get(t)) for t in P["tickers"])}
    return pd.DataFrame(cols, index=P["close"].index)


def past_return(close: pd.DataFrame, n: int) -> pd.DataFrame:
    return close / close.shift(n) - 1


def predictors(P: dict) -> dict[str, pd.DataFrame]:
    close, etf = P["close"], etf_close_by_ticker(P)
    out = {}
    for n in LOOKBACKS + [5]:
        out[f"tot{n}"] = past_return(close, n)
        out[f"etf{n}"] = past_return(etf, n)
        out[f"res{n}"] = out[f"tot{n}"] - out[f"etf{n}"]
    out["R1"], out["R2"] = out["tot5"], out["res5"]
    out["R3"] = s20.partial_rank(P["score"], out["R1"], P["mask"])
    return out


def card_window(P: dict, classes: pd.Series) -> dict:
    rng = np.random.default_rng(SEED)
    mask = P["mask"]
    pr = predictors(P)
    desc = [f"{k}{n}" for n in LOOKBACKS for k in ("tot", "res")] + ["etf5", "score"]
    pr["score"] = P["score"]
    q = ld.quintile(P["atr"], mask)
    out = {"pool_size": {"share_days_ge20": float((mask.sum(axis=1) >= 20).mean())},
           "pred_corr": s20.pred_corr({k: pr[k] for k in ("R1", "R2", "R3", "etf5", "score")}, mask), "h": {}}
    for h in HORIZONS:
        f = ld.fwd(P["close"], h)
        r = {k: s20.ic_stats(pr[k], f, mask, h) for k in CONFIRMATORY + tuple(desc)}
        r["state"] = {k: sb.effect(ld.row_rank_corr(pr[k], f, mask), classes, h) for k in ("R1", "R2")}
        r["atr_quintile"] = {str(k): s20.ic_stats(pr["R1"], f, mask & (q == k), h)["mean"] for k in range(5)}
        r["shuffle"] = {}
        for k in CONFIRMATORY:
            null = [s20.ic_stats(ld.shuffled(pr[k], mask, rng), f, mask, h) for _ in range(N_SHUFFLE)]
            r["shuffle"][k] = float(np.mean([x["t"] is not None and abs(x["t"]) > 1.96 for x in null]))
        inj = ld.injected(f, mask, TARGET, rng)
        r["injected"] = {"ic": s20.ic_stats(inj, f, mask, h)["mean"],
                         "se": s20.ic_stats(inj, f, mask, h)["sd"] / math.sqrt(s20.ic_stats(inj, f, mask, h)["n"])}
        r["criterion"] = {"plus": s20.ic_stats(ld.injected(f, mask, TARGET, rng), f, mask, h),
                          "minus": s20.ic_stats(ld.injected(f, mask, -TARGET, rng), f, mask, h),
                          "zero": [s20.ic_stats(ld.injected(f, mask, 0.0, rng), f, mask, h)["p"] for _ in range(N_ZERO)]}
        out["h"][str(h)] = r
    return out


def verdict(s_is: dict, s_oos: dict, rejected: bool, predicted: int, control_ok: bool) -> str:
    t, to = s_is.get("t"), s_oos.get("t")
    if not control_ok:
        return "unanswered (D4: control fails at this h)"
    if rejected and t is not None and (predicted == 0 or np.sign(t) == predicted):
        if to is not None and np.sign(to) == np.sign(t) and abs(to) >= 1:
            return "supported"
        return "unanswered (IS only, OOS sign check fails)"
    if rejected and predicted != 0 and t is not None and np.sign(t) == -predicted:
        return "contradicted"
    return "unanswered"


def controls(IS: dict, OOS: dict) -> dict:
    bound = 0.05 / (len(CONFIRMATORY) * len(DECIDING_H))
    out = {}
    for h in DECIDING_H:
        ci, co = IS["h"][h]["criterion"], OOS["h"][h]["criterion"]

        def passes(si, so, sign):
            return bool(si["p"] is not None and si["p"] <= bound and np.sign(si["t"]) == sign
                        and so["t"] is not None and np.sign(so["t"]) == sign and abs(so["t"]) >= 1)

        out[h] = {"minus_supported_negative": passes(ci["minus"], co["minus"], -1),
                  "plus_supported_positive": passes(ci["plus"], co["plus"], +1),
                  "zero_reject_rate": float(np.mean([p is not None and p <= bound for p in ci["zero"]]))}
    return out


def validity(IS: dict, OOS: dict) -> dict:
    g = {}
    for w, R in (("IS", IS), ("OOS", OOS)):
        g[f"pool_{w}"] = R["pool_size"]["share_days_ge20"] >= 0.95
        for h, r in R["h"].items():
            g[f"inject_{w}_h{h}"] = abs(r["injected"]["ic"] - TARGET) <= max(0.01, 2 * r["injected"]["se"])
    for k in CONFIRMATORY:
        g[f"shuffle_{k}"] = float(np.mean([R["h"][h]["shuffle"][k] for R in (IS, OOS) for h in R["h"]])) < 0.10
    return g


def rule(IS: dict, OOS: dict) -> dict:
    ctl = controls(IS, OOS)
    rej = s20.holm({(k, h): IS["h"][h][k]["p"] for k in CONFIRMATORY for h in DECIDING_H})
    v = {}
    for (k, h), r in rej.items():
        ok = ctl[h]["minus_supported_negative"] and (PREDICTED[k] != 0 or ctl[h]["plus_supported_positive"])
        s_is, s_oos = IS["h"][h][k], OOS["h"][h][k]
        v[f"{k}_h{h}"] = {"verdict": verdict(s_is, s_oos, r, PREDICTED[k], ok), "ic_is": s_is["mean"],
                          "t_is": s_is["t"], "p_is": s_is["p"], "holm": r, "mde_is": s_is.get("mde"),
                          "ic_oos": s_oos["mean"], "t_oos": s_oos["t"],
                          "label": "bid-ask bounce not removed" if h == "1" else None}
    return {"verdicts": v, "controls": ctl, "validity": validity(IS, OOS)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir", type=Path)
    ap.add_argument("--windows", default="IS,OOS")
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
        res[w] = card_window(P, sb.classes_for(pre, P["days"]))
        (a.outdir / f"S21_{w}.json").write_text(json.dumps(res[w], indent=1, default=str))
        log(f"saved S21_{w}")
    if {"IS", "OOS"} <= set(res):
        out = {"producer": {"repo": "apex", "branch": "master", "script": "backend/backtest/s21_reversal_2026_10.py",
                            "commit": commit, "run": " ".join(sys.argv),
                            "why": "raw/notes/2026-10/2026-10-24-apex-s21-reversal-l0-card.md"},
               **rule(res["IS"], res["OOS"])}
        (a.outdir / "S21_verdict.json").write_text(json.dumps(out, indent=1, default=str))
        log("saved S21_verdict")
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
