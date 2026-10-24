"""
S13 L0 (ruling Niclas 2026-10-21): does the engine's pool earn more from the close of a macro
blocked or eve session than from other closes? Signal-free. Pre-registration:
vault raw/notes/2026-10/2026-10-21-apex-s13-macro-blackout-l0-preregistration.md (written and
amended before this ran).

Classes come from macro_status, live Lock 1 B2's own rule: FOMC_PRE (hard block, FOMC 1-2
calendar days ahead), FOMC_DAY, RELEASE (CPI/NFP release day), EVE (pre-event penalty day),
CLEAR. The daily series is the pool's equal-weight forward return from close d to close d+h, in
%; it is regressed on the four class dummies with Newey-West (Bartlett, h lags) errors. Primary:
h = 1, IS, Holm over the four classes; OOS checks the sign. Controls: placebo classes drawn from
CLEAR sessions (rejection about 5 %) and the same placebos shifted by the FOMC_PRE MDE (detection
70-90 %).

Usage: python -m backend.backtest.s13_macro_l0_2026_10_21 OUT.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

CLASSES = ("FOMC_PRE", "FOMC_DAY", "RELEASE", "EVE")
HORIZONS = [1, 5, 10]
PRIMARY_H = 1
ALPHA = 0.05
N_PLACEBO = 1000
NEG_RANGE = [0.03, 0.07]        # placebo rejection rate accepted
POS_RANGE = [0.70, 0.90]        # detection rate of an MDE shift accepted
MDE_Z = 1.96 + 0.84             # 80 % power, two-sided 0.05
WINDOWS = {"IS": ("2019-01-02", "2026-10-16"), "OOS": ("2015-01-02", "2018-12-31")}


def day_class(d: date) -> str:
    from backend.macro_calendar import macro_status
    kind, desc = macro_status(d)
    if kind == "hard_block":
        if desc.startswith("FOMC"):
            return "FOMC_DAY" if date.fromisoformat(desc.split()[2]) == d else "FOMC_PRE"
        return "RELEASE"
    return "EVE" if kind == "pre_event" else "CLEAR"


def forward_returns(closes: pd.DataFrame, h: int) -> pd.Series:
    """Equal-weight mean over tickers of close(d+h)/close(d) - 1, in %, for d with d+h in range."""
    fwd = closes.shift(-h) / closes - 1.0
    return (fwd.mean(axis=1, skipna=True) * 100.0).iloc[:-h]


def nw_ols(y: np.ndarray, X: np.ndarray, lags: int) -> tuple[np.ndarray, np.ndarray]:
    """OLS coefficients and Newey-West (Bartlett) standard errors."""
    xtx_inv = np.linalg.inv(X.T @ X)
    beta = xtx_inv @ X.T @ y
    u = y - X @ beta
    xu = X * u[:, None]
    s = xu.T @ xu
    for l in range(1, lags + 1):
        g = xu[l:].T @ xu[:-l]
        s += (1.0 - l / (lags + 1.0)) * (g + g.T)
    v = xtx_inv @ s @ xtx_inv
    return beta, np.sqrt(np.diag(v))


def p_two_sided(t: float) -> float:
    return math.erfc(abs(t) / math.sqrt(2.0))


def holm(ps: dict[str, float]) -> dict[str, float]:
    order, out, run = sorted(ps, key=ps.get), {}, 0.0
    for i, k in enumerate(order):
        run = max(run, min(1.0, (len(order) - i) * ps[k]))
        out[k] = run
    return out


def regress(y: pd.Series, classes: pd.Series, h: int) -> dict:
    X = np.column_stack([np.ones(len(y))] + [(classes == c).to_numpy(float) for c in CLASSES])
    beta, se = nw_ols(y.to_numpy(float), X, h)
    out = {"clear_mean": float(beta[0]), "n": {c: int((classes == c).sum()) for c in ("CLEAR",) + CLASSES}}
    for i, c in enumerate(CLASSES, start=1):
        t = beta[i] / se[i]
        out[c] = {"effect": float(beta[i]), "se": float(se[i]), "t": float(t), "p": p_two_sided(t),
                  "mde": float(MDE_Z * se[i]), "ci95": [float(beta[i] - 1.96 * se[i]), float(beta[i] + 1.96 * se[i])]}
    return out


def placebo_controls(y: pd.Series, classes: pd.Series, n_class: int, shift: float, h: int,
                     n: int = N_PLACEBO, seed: int = 20261021) -> dict:
    """Placebo classes of n_class CLEAR sessions: rejection rate unshifted, detection rate shifted."""
    rng = np.random.default_rng(seed)
    clear = y[classes == "CLEAR"].to_numpy(float)
    rej = det = 0
    for _ in range(n):
        d = np.zeros(len(clear))
        d[rng.choice(len(clear), n_class, replace=False)] = 1.0
        X = np.column_stack([np.ones(len(clear)), d])
        for yy, hit in ((clear, "rej"), (clear + shift * d, "det")):
            b, se = nw_ols(yy, X, h)
            if p_two_sided(b[1] / se[1]) <= ALPHA:
                if hit == "rej":
                    rej += 1
                else:
                    det += 1
    return {"n": n, "class_size": n_class, "shift": shift, "rejection_rate": rej / n, "detection_rate": det / n,
            "negative_ok": NEG_RANGE[0] <= rej / n <= NEG_RANGE[1],
            "positive_ok": POS_RANGE[0] <= det / n <= POS_RANGE[1]}


def window_data(window: str) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    from backend.backtest.engine_fast import precompute
    a, b = WINDOWS[window]
    pre = precompute(a, b)
    days = [d.isoformat() for d in pre["trading_days"]]
    pc = pre["price_cache"]
    pool = sorted(pre["ticker_sector"])
    closes = pd.DataFrame({t: [pc.get((t, d), np.nan) for d in days] for t in pool}, index=days)
    spy = pre["raw_data"]["SPY"]["Close"].dropna()
    spy.index = [pd.Timestamp(i).date().isoformat() for i in spy.index]
    spy = spy.reindex(days).ffill()
    classes = pd.Series([day_class(d) for d in pre["trading_days"]], index=days)
    return closes, spy.to_frame("SPY"), classes


def read(closes: pd.DataFrame, spy: pd.DataFrame, classes: pd.Series) -> dict:
    out = {}
    for h in HORIZONS:
        y = forward_returns(closes, h)
        out[f"h{h}"] = {"pool": regress(y, classes.loc[y.index], h),
                        "spy": regress(forward_returns(spy, h), classes.loc[y.index], h)}
    return out


def verdict(res: dict) -> dict:
    is1, oos1 = res["IS"]["h1"]["pool"], res["OOS"]["h1"]["pool"]
    adj = holm({c: is1[c]["p"] for c in CLASSES})
    out = {}
    for c in CLASSES:
        same_sign = math.copysign(1, is1[c]["effect"]) == math.copysign(1, oos1[c]["effect"])
        if adj[c] > ALPHA:
            v = "no premium detected" if is1[c]["ci95"][1] < 0.10 else "unanswered at this power"
        elif same_sign:
            v = "calendar premium from the entry close" if is1[c]["effect"] > 0 else "calendar discount (supports the blackout)"
        else:
            v = "window-dependent"
        out[c] = {"holm_p": adj[c], "oos_same_sign": same_sign, "verdict": v}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    a = ap.parse_args(argv)
    from backend.backtest.battery_2026_10 import git_state
    head, dirty = git_state()
    if dirty:
        print("refused: tracked files are modified; the record must name a commit")
        return 2
    res, controls = {}, {}
    for w in WINDOWS:
        closes, spy, classes = window_data(w)
        res[w] = read(closes, spy, classes)
        years = (pd.Timestamp(WINDOWS[w][1]) - pd.Timestamp(WINDOWS[w][0])).days / 365.25
        res[w]["class_counts_per_year"] = {c: round(int((classes == c).sum()) / years, 1) for c in ("CLEAR",) + CLASSES}
        if w == "IS":
            y = forward_returns(closes, PRIMARY_H)
            fp = res[w][f"h{PRIMARY_H}"]["pool"]["FOMC_PRE"]
            controls = placebo_controls(y, classes.loc[y.index], res[w][f"h{PRIMARY_H}"]["pool"]["n"]["FOMC_PRE"],
                                        fp["mde"], PRIMARY_H)
    out = {"question": "S13 L0: pool forward return from the close of macro blocked/eve sessions vs CLEAR",
           "commit": head, "windows": WINDOWS, "primary_h": PRIMARY_H, "controls": controls,
           "valid": controls["negative_ok"] and controls["positive_ok"], "results": res}
    out["verdict"] = verdict(res) if out["valid"] else "refused: a control failed; the card stops"
    a.out.write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps({k: out[k] for k in ("commit", "controls", "valid", "verdict")}, indent=1, default=str), flush=True)
    for w in WINDOWS:
        r = res[w]["h1"]["pool"]
        print(w, "counts/yr", res[w]["class_counts_per_year"], "clear", round(r["clear_mean"], 3), flush=True)
        for h in HORIZONS:
            rp = res[w][f"h{h}"]["pool"]
            print(f"  h{h:<2d} " + "  ".join(f"{c} {rp[c]['effect']:+.3f} (se {rp[c]['se']:.3f}, p {rp[c]['p']:.3f})"
                                             for c in CLASSES), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
