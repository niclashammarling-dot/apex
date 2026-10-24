"""
S14 L0 (ruling Niclas 2026-10-21): does the engine's pool earn more from the close of a session
after a VIX jump, or from a close the VIX >= 30 gate blocks, than from other closes? Signal-free.
Pre-registration: vault raw/notes/2026-10/2026-10-21-apex-s14-heightened-vix-l0-preregistration.md
(written before this code).

Classes on the VIX close of d, the close the engine's gate reads (Hu, Pan, Wang & Zhu 2019):
jump = VIX(d) - Vbar(d-1) >= 3.0 with Vbar(d-1) = 0.3 Vbar(d-2) + 0.7 VIX(d-1), seeded on the
first close of a 60-session warm-up; level = VIX(d) >= 30. JUMP_ONLY, LEVEL_ONLY, BOTH, NEITHER
(reference). h = 1 is the literature's quantity; h = 5 and 10 are APEX's hold and contain the
turbulence after the spike. Per h: Newey-West regression on the three dummies (S13's nw_ols),
Holm over the three classes; beside the mean, per class: SD, 5th percentile, and the share of
windows whose path low (the basket's worst close d+k against close d, k <= h) is below -5 %.
Controls: S13's placebo machinery on NEITHER closes, JUMP_ONLY-sized, shifted by its h = 1 MDE.

Usage: python -m backend.backtest.s14_vix_l0_2026_10_21 OUT.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from backend.backtest import s13_macro_l0_2026_10_21 as l0

CLASSES = ("JUMP_ONLY", "LEVEL_ONLY", "BOTH")
HORIZONS = [1, 5, 10]
JUMP_CUTOFF = 3.0
LAMBDA = 0.3
LEVEL = 30.0
WARMUP = 60
DD_CUT = -5.0                  # % path low that counts as a drawdown
LIT_SIZE = [0.48, 0.5, 0.5]   # % per HORIZONS entry: Hu et al. at h 1; the card's APEX threshold at h 5/10
EXPECTED = {"IS": {"jump": 88, "level": 149, "both": 36}, "OOS": {"jump": 34, "level": 9, "both": 5}}
COUNT_TOL = 2


def vix_classes(vix: pd.Series, days: list, warmup: int = WARMUP) -> pd.Series:
    """Class of each day in `days` from the full VIX close series (index sorted, includes warm-up)."""
    first = vix.index.get_loc(days[0])
    v = vix.iloc[max(0, first - warmup):]
    vbar = v.iloc[0]
    out = {}
    for i in range(1, len(v)):
        jump = v.iloc[i] - vbar >= JUMP_CUTOFF            # vbar is Vbar(d-1) here
        level = v.iloc[i] >= LEVEL
        out[v.index[i]] = ("BOTH" if jump and level else "JUMP_ONLY" if jump
                           else "LEVEL_ONLY" if level else "NEITHER")
        vbar = LAMBDA * vbar + (1.0 - LAMBDA) * v.iloc[i]   # becomes Vbar(d) for the next day
    return pd.Series([out.get(d, "MISSING") for d in days], index=days)   # MISSING fails the validity gate


def path_low(closes: pd.DataFrame, h: int) -> pd.Series:
    """Worst equal-weight basket return (%) from close d to any close d+k, k = 1..h."""
    lows = pd.concat([l0.forward_returns(closes, k) for k in range(1, h + 1)], axis=1)
    return lows.iloc[: len(closes) - h].min(axis=1)


def regress(y: pd.Series, classes: pd.Series, h: int) -> dict:
    X = np.column_stack([np.ones(len(y))] + [(classes == c).to_numpy(float) for c in CLASSES])
    beta, se = l0.nw_ols(y.to_numpy(float), X, h)
    out = {"neither_mean": float(beta[0]), "n": {c: int((classes == c).sum()) for c in ("NEITHER",) + CLASSES}}
    for i, c in enumerate(CLASSES, start=1):
        t = beta[i] / se[i]
        out[c] = {"effect": float(beta[i]), "se": float(se[i]), "t": float(t), "p": l0.p_two_sided(t),
                  "mde": float(l0.MDE_Z * se[i]),
                  "ci95": [float(beta[i] - 1.96 * se[i]), float(beta[i] + 1.96 * se[i])]}
    return out


def risk(y: pd.Series, low: pd.Series, classes: pd.Series) -> dict:
    out = {}
    for c in ("NEITHER",) + CLASSES:
        m = (classes == c).to_numpy()
        if not m.any():
            out[c] = None
            continue
        out[c] = {"sd": float(y[m].std(ddof=1)) if m.sum() > 1 else None,
                  "p5": float(np.percentile(y[m], 5)),
                  "dd_share": float((low[m] < DD_CUT).mean())}
    return out


def verdict(res: dict) -> dict:
    out = {}
    oos_jump = {h: res["OOS"][f"h{h}"]["pool"]["JUMP_ONLY"]["effect"] for h in HORIZONS}
    for h in HORIZONS:
        r = res["IS"][f"h{h}"]["pool"]
        adj = l0.holm({c: r[c]["p"] for c in CLASSES})
        for c in CLASSES:
            e, (lo, hi) = r[c]["effect"], r[c]["ci95"]
            if adj[c] <= l0.ALPHA and e > 0:
                if h == 1:
                    v = ("replicates the one-day premium" if oos_jump[1] > 0
                         else "one-day premium, OOS sign differs")
                else:
                    v = "premium survives an APEX-length hold"
            elif adj[c] <= l0.ALPHA and e < 0:
                v = ("the gate avoids a loss" if h > 1 and c in ("LEVEL_ONLY", "BOTH")
                     else "significant negative: no mapped outcome (D6)")
            else:
                v = "unanswered at this power" if hi >= LIT_SIZE[HORIZONS.index(h)] else "no premium detected"
            out[f"h{h} {c}"] = {"holm_p": adj[c], "verdict": v}
    return out


def window(w: str):
    from backend.backtest.engine_fast import precompute
    closes, spy, _ = l0.window_data(w)
    a, b = l0.WINDOWS[w]
    pre = precompute(a, b)
    vix = pre["raw_data"]["^VIX"]["Close"].dropna()
    vix.index = [pd.Timestamp(i).normalize() for i in vix.index]
    days = [pd.Timestamp(d) for d in closes.index]
    missing = [d for d in days if d not in vix.index]
    classes = vix_classes(vix, days)
    classes.index = closes.index
    macro = pd.Series([l0.day_class(d.date()) for d in days], index=closes.index)
    return closes, spy, classes, macro, missing


def read(closes, spy, classes, macro) -> dict:
    out = {}
    for h in HORIZONS:
        y = l0.forward_returns(closes, h)
        c = classes.loc[y.index]
        out[f"h{h}"] = {"pool": regress(y, c, h), "spy": regress(l0.forward_returns(spy, h), c, h),
                        "risk": risk(y, path_low(closes, h), c)}
    out["macro_share"] = {k: float((macro[classes == k] != "CLEAR").mean()) if (classes == k).any() else None
                          for k in ("NEITHER",) + CLASSES}
    return out


def counts(classes: pd.Series) -> dict:
    return {"jump": int(classes.isin(["JUMP_ONLY", "BOTH"]).sum()),
            "level": int(classes.isin(["LEVEL_ONLY", "BOTH"]).sum()),
            "both": int((classes == "BOTH").sum())}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    a = ap.parse_args(argv)
    from backend.backtest.battery_2026_10 import git_state
    head, dirty = git_state()
    if dirty:
        print("refused: tracked files are modified; the record must name a commit")
        return 2
    res, gates, controls = {}, {}, {}
    for w in l0.WINDOWS:
        closes, spy, classes, macro, missing = window(w)
        res[w] = read(closes, spy, classes, macro)
        cnt = counts(classes)
        res[w]["counts"] = cnt
        res[w]["by_year"] = {k: classes[classes == k].groupby(lambda d: d[:4]).size().to_dict() for k in CLASSES}
        gates[w] = {"vix_on_every_session": not missing, "missing": [str(d.date()) for d in missing][:5],
                    "counts_match": all(abs(cnt[k] - EXPECTED[w][k]) <= COUNT_TOL for k in cnt)}
        if w == "IS":
            y = l0.forward_returns(closes, 1)
            c = classes.loc[y.index].replace("NEITHER", "CLEAR")
            r1 = res[w]["h1"]["pool"]
            controls = l0.placebo_controls(y, c, r1["n"]["JUMP_ONLY"], r1["JUMP_ONLY"]["mde"], 1)
    valid = all(g["vix_on_every_session"] and g["counts_match"] for g in gates.values()) \
        and controls["negative_ok"] and controls["positive_ok"]
    out = {"question": "S14 L0: pool forward return from heightened-VIX / VIX-gate closes vs NEITHER",
           "commit": head, "windows": l0.WINDOWS, "gates": gates, "controls": controls, "valid": valid,
           "results": res, "verdict": verdict(res) if valid else "refused: a gate or control failed; the card stops"}
    a.out.write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps({k: out[k] for k in ("commit", "gates", "controls", "valid", "verdict")}, indent=1, default=str),
          flush=True)
    for w in l0.WINDOWS:
        print(w, "counts", res[w]["counts"], "macro share", res[w]["macro_share"], flush=True)
        for h in HORIZONS:
            r, rk = res[w][f"h{h}"]["pool"], res[w][f"h{h}"]["risk"]
            print(f"  h{h:<2d} neither {r['neither_mean']:+.3f} " + "  ".join(
                f"{c} {r[c]['effect']:+.3f} [{r[c]['ci95'][0]:+.2f},{r[c]['ci95'][1]:+.2f}] n{r['n'][c]}"
                for c in CLASSES), flush=True)
            print("       risk " + "  ".join(
                f"{c} sd {v['sd']:.2f} p5 {v['p5']:+.2f} dd {v['dd_share']:.2f}" if v and v["sd"] is not None
                else f"{c} -" for c, v in rk.items()), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
