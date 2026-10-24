"""S20b volatility as a state, L0 card, 2026-10-24: does a signal's IC depend on the market's vol state?

Why: S20 (ticker volatility as an unconditional rank) came back unanswered; its thread (template 10b)
is that Niclas's question was volatility as a state ("vix poäng"), and V4 was null IS but negative OOS,
the shape of a state-dependent effect. Niclas 2026-10-24: "Om ingen fråga är besvarad så bör fler
perspektiv utforskas". The card is vault raw/notes/2026-10/2026-10-24-apex-s20b-vol-state-l0-card.md,
committed (vault 188e49c4) before this module. Exploratory: no config change follows.

How: python -u -m backend.backtest.s20b_vol_state_2026_10 OUTDIR [--windows IS,OOS]
     [--battery /home/promenix/apex-battery/2026-10-19]

What: OUTDIR/S20b_<window>.json (effects per predictor and h, per-class effects, controls, counts),
then OUTDIR/S20b_verdict.json (power, Holm over V4 and score at h 1 and 5, the rule, validity).
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
from backend.backtest import s13_macro_l0_2026_10_21 as l0
from backend.backtest import s14_vix_l0_2026_10_21 as s14
from backend.backtest import s20_vol_2026_10 as s20

HORIZONS = [1, 5, 10, 25]
DECIDING_H = ("1", "5")
CONFIRMATORY = ("V4", "score")
PREDICTED = {"V4": +1, "score": 0}            # 0 = two-sided, either sign supports
DESCRIPTIVE = ("V1", "V2", "V3", "V5")
BLOCK = 21
N_PERM = 200
N_LEVEL_DRAWS = 20
TARGET = 0.03
MIN_STRESSED = 20
MIN_OOS_STRESSED = 30
SEED = 20261025


def stressed_of(classes: pd.Series) -> pd.Series:
    return classes.isin(s14.CLASSES)


def effect(ic: pd.Series, classes: pd.Series, h: int) -> dict:
    """NW regression of the daily IC on a STRESSED dummy (and, beside it, on the three classes)."""
    ok = ic.notna() & (classes != "MISSING")
    y, c = ic[ok].to_numpy(float), classes[ok]
    st = stressed_of(c).to_numpy(float)
    if st.sum() < 2 or (1 - st).sum() < 2:
        return {"effect": None, "se": None, "t": None, "p": None, "n_stressed": int(st.sum())}
    beta, se = l0.nw_ols(y, np.column_stack([np.ones(len(y)), st]), h)
    t = float(beta[1] / se[1]) if se[1] > 0 else None
    out = {"neither": float(beta[0]), "effect": float(beta[1]), "se": float(se[1]), "t": t,
           "p": None if t is None else l0.p_two_sided(t), "mde": float(l0.MDE_Z * se[1]),
           "n_stressed": int(st.sum()), "n_neither": int(len(st) - st.sum()), "classes": {}}
    present = [k for k in s14.CLASSES if (c == k).sum() >= 2]
    if present:
        X = np.column_stack([np.ones(len(y))] + [(c == k).to_numpy(float) for k in present])
        bb, ss = l0.nw_ols(y, X, h)
        for n, k in enumerate(present, start=1):
            out["classes"][k] = {"effect": float(bb[n]), "t": float(bb[n] / ss[n]) if ss[n] > 0 else None,
                                 "n": int((c == k).sum())}
    return out


def block_permute(classes: pd.Series, rng: np.random.Generator) -> pd.Series:
    """Class labels with whole BLOCK-day blocks shuffled (keeps episodes together)."""
    v = classes.to_numpy()
    blocks = [v[i:i + BLOCK] for i in range(0, len(v), BLOCK)]
    order = rng.permutation(len(blocks))
    return pd.Series(np.concatenate([blocks[k] for k in order]), index=classes.index)


def conditional_injected(f: pd.DataFrame, mask: pd.DataFrame, stressed: pd.Series, rng) -> pd.DataFrame:
    """Synthetic predictor: IC TARGET on stressed days, 0 elsewhere."""
    on = ld.injected(f, mask, TARGET, rng)
    off = ld.injected(f, mask, 0.0, rng)
    cond = np.broadcast_to(stressed.to_numpy(bool)[:, None], on.shape)
    return on.where(cond, off)


def card_window(P: dict, classes: pd.Series) -> dict:
    rng = np.random.default_rng(SEED)
    mask = P["mask"]
    pr = s20.predictors(P)
    pr["V4"] = s20.partial_rank(pr["V4raw"], pr["V5"], mask)
    pr["score"] = P["score"]
    st = stressed_of(classes)
    out = {"counts": {k: int((classes == k).sum()) for k in ("NEITHER",) + s14.CLASSES + ("MISSING",)},
           "n_stressed": int(st.sum()), "h": {}}
    for h in HORIZONS:
        f = ld.fwd(P["close"], h)
        r = {k: effect(ld.row_rank_corr(pr[k], f, mask), classes, h) for k in CONFIRMATORY + DESCRIPTIVE}
        ic_v4 = ld.row_rank_corr(pr["V4"], f, mask)
        perm = [effect(ic_v4, block_permute(classes, rng), h)["p"] for _ in range(N_PERM)]
        r["perm_reject_rate"] = float(np.mean([p is not None and p <= 0.05 for p in perm]))
        cond = effect(ld.row_rank_corr(conditional_injected(f, mask, st, rng), f, mask), classes, h)
        lvl = [effect(ld.row_rank_corr(ld.injected(f, mask, TARGET, rng), f, mask), classes, h)["p"]
               for _ in range(N_LEVEL_DRAWS)]
        unc = ld.row_rank_corr(ld.injected(f, mask, TARGET, rng), f, mask)
        r["controls"] = {"conditional": cond, "level_reject_rate": float(np.mean([p is not None and p <= 0.05 for p in lvl])),
                         "unconditional_ic": float(unc.mean()),
                         "unconditional_se": float(unc.std(ddof=1) / math.sqrt(max(unc.notna().sum() / h, 1)))}
        out["h"][str(h)] = r
    return out


def verdict(e_is: dict, e_oos: dict, rejected: bool, predicted: int, n_oos_stressed: int) -> str:
    t = e_is.get("t")
    sign_ok = t is not None and (predicted == 0 or np.sign(t) == predicted)
    if rejected and sign_ok:
        if n_oos_stressed < MIN_OOS_STRESSED:
            return "IS-supported, OOS unpowered"
        to = e_oos.get("t")
        return "supported" if to is not None and np.sign(to) == np.sign(t) and abs(to) >= 1 else \
            "unanswered (IS only, OOS sign check fails)"
    if rejected and predicted != 0 and t is not None and np.sign(t) == -predicted:
        return "contradicted"
    return "unanswered"


def validity(IS: dict, OOS: dict) -> dict:
    g = {}
    for w, R in (("IS", IS), ("OOS", OOS)):
        g[f"no_missing_{w}"] = R["counts"]["MISSING"] == 0
        g[f"stressed_ge_{MIN_STRESSED}_{w}"] = R["n_stressed"] >= MIN_STRESSED
        for h, r in R["h"].items():
            c = r["controls"]
            g[f"inject_{w}_h{h}"] = abs(c["unconditional_ic"] - TARGET) <= max(0.01, 2 * c["unconditional_se"])
    g["criterion_positive_IS_h1"] = bool(IS["h"]["1"]["controls"]["conditional"]["p"] is not None
                                         and IS["h"]["1"]["controls"]["conditional"]["p"] <= 0.05 / 4
                                         and IS["h"]["1"]["controls"]["conditional"]["t"] > 0)
    g["criterion_negative"] = all(R["h"][h]["controls"]["level_reject_rate"] <= 0.15 for R in (IS, OOS) for h in R["h"])
    g["block_null"] = all(R["h"][h]["perm_reject_rate"] < 0.10 for R in (IS, OOS) for h in R["h"])
    return g


def rule(IS: dict, OOS: dict) -> dict:
    ps = {(k, h): IS["h"][h][k]["p"] for k in CONFIRMATORY for h in DECIDING_H}
    rej = s20.holm(ps)
    out = {}
    for (k, h), r in rej.items():
        e_is, e_oos = IS["h"][h][k], OOS["h"][h][k]
        out[f"{k}_h{h}"] = {"verdict": verdict(e_is, e_oos, r, PREDICTED[k], OOS["n_stressed"]),
                            "effect_is": e_is["effect"], "t_is": e_is["t"], "p_is": e_is["p"], "mde_is": e_is.get("mde"),
                            "holm_rejected": r, "effect_oos": e_oos["effect"], "t_oos": e_oos["t"]}
    return {"verdicts": out, "validity": validity(IS, OOS)}


def classes_for(pre: dict, days: list[str]) -> pd.Series:
    vix = pre["raw_data"]["^VIX"]["Close"].dropna()
    vix.index = [pd.Timestamp(i).normalize() for i in vix.index]
    c = s14.vix_classes(vix, [pd.Timestamp(d) for d in days])
    c.index = days
    return c


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
        cl = classes_for(pre, P["days"])
        log(f"{w}: {len(P['days'])} days, stressed {int(stressed_of(cl).sum())}, missing {int((cl == 'MISSING').sum())}")
        res[w] = card_window(P, cl)
        (a.outdir / f"S20b_{w}.json").write_text(json.dumps(res[w], indent=1, default=str))
        log(f"saved S20b_{w}")
    if {"IS", "OOS"} <= set(res):
        out = {"producer": {"repo": "apex", "branch": "master", "script": "backend/backtest/s20b_vol_state_2026_10.py",
                            "commit": commit, "run": " ".join(sys.argv),
                            "why": "raw/notes/2026-10/2026-10-24-apex-s20b-vol-state-l0-card.md"},
               **rule(res["IS"], res["OOS"])}
        (a.outdir / "S20b_verdict.json").write_text(json.dumps(out, indent=1, default=str))
        log("saved S20b_verdict")
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
