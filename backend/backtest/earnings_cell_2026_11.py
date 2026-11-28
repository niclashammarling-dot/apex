"""Earnings cell (GAP-8/9), L0 card, 2026-11-28: run as sealed.

Why: live's earnings blackout and near-term penalty never fired until the 11-14 shadow build, and nothing measures
whether a close entry that holds a report carries more tail than other closes. The card (question, classes, series,
statistic, one primary test, power, controls, validity, outcomes) is vault
raw/notes/2026-11/2026-11-23-apex-earnings-cell-preregistration-draft.md, sealed 2026-11-23 after Niclas's read,
before this script was written. L0 decides first; L2 (the blackout's own candidates, items 12-15) runs after L0 reads.

Readings of the sealed text, stated before the run. The first and the last were ruled by Niclas on 2026-11-28 and
written into the card as Amendment 1 (vault 95a372d1) before the run; the others follow the card's words:
- item 7's left tail is read on the raw close-to-close return: item 7 says "returns below -6 % (the stop distance)",
  and the stop acts on the raw price; item 5's day-demeaned "series" is the regression's input. The demeaned share is
  reported beside it, not judged.
- a report's timing comes from the source's New York timestamp: before 09:30 is before the open, from 16:00 after the
  close; 00:00 (no time given) and 09:30-15:59 are untimed: their HOLD candidates, the two sessions before and the five
  after are excluded from every class and counted (measured 2026-11-28: 30 of 3,942 rows since 2015).
- a hole (earnings_source.EarningsSet.in_hole) overrides every class (item 3: excluded from every class).
- a report whose surprise is missing keeps its HOLD and PRE2 days; its POST days are counted, not classed (item 4).
- the placebo regressions drop the real HOLD days and move the drawn days from CLEAR to HOLD (item 10).
- item 10's positive control adds each placebo regression's own MDE (2.8 x its HOLD SE): a 70-90 % band around the
  nominal 80 % tests the SE's calibration only at the tested regression's own MDE. Taken literally ("the HOLD MDE",
  item 9's from the real run) the shift is sized on reaction days, whose variance is higher than the CLEAR days the
  placebos are drawn from, so detection nears 100 % and the card stops for a reason that says nothing about the test
  (seen on the synthetic planted-tail fixture 2026-11-28: 1.00). The literal rate is reported beside, not judged.

How: python -u -m backend.backtest.earnings_cell_2026_11 OUTDIR [--windows IS,OOS] [--placebos 1000]
     [--battery /home/promenix/apex-battery/2026-10-19]

What: OUTDIR/earnings_reports.json (the fetched reports, the set's digest: the source changes over time, so the run
reads only this file once written), OUTDIR/EARN_L0_<window>.json per window, OUTDIR/EARN_L0_verdict.json (item 8's
rule, the controls, the validity counts, the producer block).
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from backend.backtest.earnings_source import EARNINGS_LIMIT, EarningsSet

CARD = ("vault raw/notes/2026-11/2026-11-23-apex-earnings-cell-preregistration-draft.md (sealed 2026-11-23; "
        "Amendment 1 2026-11-28, vault 95a372d1, before the run)")
HORIZONS = [1, 5, 10, 25]
PRIMARY_H = 1                          # Niclas 2026-11-23: L0 tests the reaction day
TAIL = -0.06                           # the live stop distance (live_config sl 6 %), item 7
CLASSES = ("HOLD", "PRE2", "NEAR", "POST+", "POST-")   # CLEAR is the intercept
EXCLUDED = ("HOLE", "UNTIMED", "POST?")
NEAR_DAYS = [3, 14]                    # l2_risk_earnings_days + 1 .. l2_risk_earnings_near_days
N_PLACEBO = 1000
SEED = 20261128
ALPHA = 0.05
NEG_BAND, POS_BAND = (0.03, 0.07), (0.70, 0.90)
MDE_K = 2.8                            # 80 % power, 5 % two-sided


# ── Reports ─────────────────────────────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Report:
    day: str                           # New York date of the announcement, ISO
    when: str                          # "bmo", "amc" or "untimed"
    surprise: float | None             # Surprise(%) from the same row
    reported: bool                     # Reported EPS present (D5: a past row without it may be a stale schedule)


def timing(ts: pd.Timestamp) -> str:
    ny = ts.tz_convert("America/New_York") if ts.tzinfo is not None else ts
    m = ny.hour * 60 + ny.minute
    if m == 0:
        return "untimed"
    if m < 9 * 60 + 30:
        return "bmo"
    return "amc" if m >= 16 * 60 else "untimed"


def reports_from_frame(df: pd.DataFrame | None, before: pd.Timestamp) -> list[Report]:
    if df is None or df.empty:
        return []
    out = []
    for ts, row in df[df.index < before].iterrows():
        s = row.get("Surprise(%)")
        out.append(Report(ts.tz_convert("America/New_York").date().isoformat() if ts.tzinfo is not None else ts.date().isoformat(),
                          timing(ts), None if pd.isna(s) else float(s), not pd.isna(row.get("Reported EPS"))))
    return sorted(out, key=lambda r: r.day)


def fetch_reports(tickers: list[str], before: str) -> dict:
    import yfinance as yf
    cut = pd.Timestamp(before, tz="America/New_York")
    reps, failed = {}, {}
    for t in tickers:
        try:
            reps[t] = [asdict(r) for r in reports_from_frame(yf.Ticker(t).get_earnings_dates(limit=EARNINGS_LIMIT), cut)]
        except Exception as e:
            failed[t] = f"{type(e).__name__}: {e}"[:200]
    return {"limit": EARNINGS_LIMIT, "before": before, "yfinance": yf.__version__, "reports": reps, "failed": failed}


def earnings_set(archive: dict) -> EarningsSet:
    dates = {t: tuple(sorted({date.fromisoformat(r["day"]) for r in rs})) for t, rs in archive["reports"].items() if rs}
    failed = {**archive.get("failed", {}), **{t: "no rows" for t, rs in archive["reports"].items() if not rs}}
    return EarningsSet(dates, failed)


# ── Classes ─────────────────────────────────────────────────────────────────────────────────────────────────────

def classify(days: list[str], tickers: list[str], reports: dict[str, list[dict]], es: EarningsSet) -> pd.DataFrame:
    """Days x tickers labels (item 4), lowest precedence first: CLEAR < NEAR < POST < PRE2 < HOLD < UNTIMED < HOLE."""
    sess = [date.fromisoformat(d) for d in days]
    lab = pd.DataFrame("CLEAR", index=days, columns=tickers, dtype=object)
    for t in tickers:
        col = np.array(["CLEAR"] * len(sess), dtype=object)
        rs = [r for r in reports.get(t, [])]
        rdays = sorted(date.fromisoformat(r["day"]) for r in rs)
        j = 0
        for i, d in enumerate(sess):                                  # NEAR: next report 3-14 calendar days ahead
            while j < len(rdays) and rdays[j] < d:
                j += 1
            if j < len(rdays) and NEAR_DAYS[0] <= (rdays[j] - d).days <= NEAR_DAYS[1]:
                col[i] = "NEAR"
        holds, untimed = [], []
        lo, hi = sess[0] - timedelta(days=7), sess[-1]
        for r in rs:
            rd = date.fromisoformat(r["day"])
            if not lo <= rd <= hi:                                     # outside the window: no day of it to label
                continue
            before = int(np.searchsorted(sess, rd, side="left")) - 1   # last session strictly before the report day
            upto = int(np.searchsorted(sess, rd, side="right")) - 1    # last session on or before it
            if r["when"] == "bmo":
                holds.append((before, r))
            elif r["when"] == "amc":
                holds.append((upto, r))
            else:
                untimed.append((before, upto))
        for h, r in holds:                                             # POST, then PRE2, then HOLD
            sign = None if r["surprise"] is None else ("POST+" if r["surprise"] > 0 else "POST-" if r["surprise"] < 0 else None)
            for k in range(h + 1, h + 6):
                if 0 <= k < len(sess):
                    col[k] = sign or "POST?"
        for h, _ in holds:
            for k in (h - 2, h - 1):
                if 0 <= k < len(sess):
                    col[k] = "PRE2"
        for h, _ in holds:
            if 0 <= h < len(sess):
                col[h] = "HOLD"
        for a, b in untimed:
            for k in range(a - 2, b + 6):
                if 0 <= k < len(sess):
                    col[k] = "UNTIMED"
        for i, d in enumerate(sess):
            if es.in_hole(t, d):
                col[i] = "HOLE"
        lab[t] = col
    return lab


# ── Statistics ──────────────────────────────────────────────────────────────────────────────────────────────────

def norm_p(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2))


def ols_dk(y: np.ndarray, codes: np.ndarray, day: np.ndarray, k: int, lags: int) -> tuple[np.ndarray, np.ndarray]:
    """OLS of y on an intercept and k-1 class dummies (code 0 = the intercept class); standard errors from day sums
    of the scores with a Bartlett window of `lags` days (clustered by day at 0; Newey-West across days above it)."""
    X = np.zeros((len(y), k))
    X[:, 0] = 1.0
    nz = codes > 0
    X[np.flatnonzero(nz), codes[nz]] = 1.0
    xtx_inv = np.linalg.inv(X.T @ X)
    beta = xtx_inv @ (X.T @ y)
    e = y - X @ beta
    T = int(day.max()) + 1
    G = np.column_stack([np.bincount(day, weights=X[:, j] * e, minlength=T) for j in range(k)])
    S = G.T @ G
    for lag in range(1, lags + 1):
        w = 1 - lag / (lags + 1)
        C = G[lag:].T @ G[:-lag]
        S += w * (C + C.T)
    V = xtx_inv @ S @ xtx_inv
    return beta, np.sqrt(np.maximum(np.diag(V), 0.0))


def two_prop(x1: int, n1: int, x2: int, n2: int) -> dict:
    if min(n1, n2) == 0:
        return {"diff": None, "z": None, "p": None}
    p1, p2, p = x1 / n1, x2 / n2, (x1 + x2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    z = (p1 - p2) / se if se > 0 else 0.0
    return {"hold_share": p1, "clear_share": p2, "diff": p1 - p2, "z": z, "p": norm_p(z), "n_hold": n1, "n_clear": n2}


def long_frame(lab: pd.DataFrame, close: pd.DataFrame, h: int) -> pd.DataFrame:
    """One row per classed ticker-day with a forward return: raw, day-demeaned (item 5), the class and the day index.
    The pool mean is over every ticker with a finite return that day; nothing crosses the window end (D3)."""
    f = close.shift(-h) / close - 1
    dm = f.sub(f.mean(axis=1), axis=0)
    L = lab.reindex(index=f.index, columns=f.columns).to_numpy()
    F, D = f.to_numpy(), dm.to_numpy()
    ii, jj = np.nonzero(np.isfinite(D) & ~np.isin(L, EXCLUDED))
    df = pd.DataFrame({"raw": F[ii, jj], "y": D[ii, jj], "cls": L[ii, jj], "day": ii},
                      index=pd.MultiIndex.from_arrays([f.index[ii], f.columns[jj]]))
    return df


def regression(df: pd.DataFrame, h: int) -> dict:
    order = ("CLEAR",) + CLASSES
    codes = df["cls"].map({c: i for i, c in enumerate(order)}).to_numpy()
    beta, se = ols_dk(df["y"].to_numpy(), codes, df["day"].to_numpy(), len(order), 0 if h == 1 else h)
    out = {"CLEAR_mean": float(beta[0]), "n": {c: int((df["cls"] == c).sum()) for c in order}}
    for i, c in enumerate(order[1:], start=1):
        z = beta[i] / se[i] if se[i] > 0 else 0.0
        out[c] = {"coef": float(beta[i]), "se": float(se[i]), "p": norm_p(z),
                  "ci95": [float(beta[i] - 1.96 * se[i]), float(beta[i] + 1.96 * se[i])], "mde": float(MDE_K * se[i])}
    return out


def tail(df: pd.DataFrame, col: str) -> dict:
    hold, clear = df[df["cls"] == "HOLD"][col], df[df["cls"] == "CLEAR"][col]
    return two_prop(int((hold < TAIL).sum()), len(hold), int((clear < TAIL).sum()), len(clear))


def placebo_draws(lab: pd.DataFrame, rng: np.random.Generator, n: int) -> list[list[tuple[str, str]]]:
    """n placebo HOLD sets: per real HOLD ticker-day, one CLEAR day of the same ticker in the same quarter."""
    q = pd.PeriodIndex(pd.to_datetime(lab.index), freq="Q")
    pools: dict[tuple[str, object], np.ndarray] = {}
    real: list[tuple[str, object]] = []
    for t in lab.columns:
        col = lab[t].to_numpy()
        for i in np.flatnonzero(col == "HOLD"):
            real.append((t, q[i]))
        for per in set(q):
            idx = np.flatnonzero((col == "CLEAR") & (q == per))
            if len(idx):
                pools[(t, per)] = idx
    days = lab.index.to_numpy()
    draws = []
    for _ in range(n):
        s = []
        for t, per in real:
            idx = pools.get((t, per))
            if idx is not None:
                s.append((days[rng.choice(idx)], t))
        draws.append(s)
    return draws


def placebo_controls(df: pd.DataFrame, draws: list, mde: float) -> dict:
    """Item 10: the mean test on each placebo HOLD (real HOLD rows dropped), plain and with the MDE added."""
    base = df[df["cls"] != "HOLD"]
    order = ("CLEAR",) + CLASSES
    codes0 = base["cls"].map({c: i for i, c in enumerate(order)}).to_numpy()
    y0, day = base["y"].to_numpy(), base["day"].to_numpy()
    pos = pd.Series(np.arange(len(base)), index=base.index)
    rej = det = det_lit = 0
    for s in draws:
        rows = pos.reindex(pd.MultiIndex.from_tuples(s)).dropna().astype(int).to_numpy()
        rows = rows[codes0[rows] == 0]                                 # drawn from CLEAR days only
        codes = codes0.copy()
        codes[rows] = 1                                                # HOLD
        b, se = ols_dk(y0, codes, day, len(order), 0)
        rej += norm_p(b[1] / se[1]) <= ALPHA
        for shift, hit in ((MDE_K * se[1], "own"), (mde, "literal")):
            y = y0.copy()
            y[rows] += shift
            b2, se2 = ols_dk(y, codes, day, len(order), 0)
            if norm_p(b2[1] / se2[1]) <= ALPHA:
                det += hit == "own"
                det_lit += hit == "literal"
    n = len(draws)
    return {"n": n, "negative_reject_rate": rej / n, "negative_band": NEG_BAND,
            "negative_pass": NEG_BAND[0] <= rej / n <= NEG_BAND[1],
            "positive_shift": "each placebo's own MDE", "positive_detect_rate": det / n, "positive_band": POS_BAND,
            "positive_pass": POS_BAND[0] <= det / n <= POS_BAND[1],
            "literal_shift_hold_mde": mde, "literal_detect_rate": det_lit / n}


def d1(lab: pd.DataFrame) -> dict:
    """HOLD counts per ticker-year against about 4 a year; partial years at the window's edges are not judged."""
    years = pd.to_datetime(lab.index).year
    full = [y for y in sorted(set(years)) if (years == y).sum() >= 240]
    counts = {t: {int(y): int((lab[t].to_numpy()[years == y] == "HOLD").sum()) for y in full} for t in lab.columns}
    flat = [c for per in counts.values() for c in per.values()]
    off = sorted((t, y, c) for t, per in counts.items() for y, c in per.items() if c not in (3, 4, 5)
                 and not (lab[t].to_numpy()[years == y] == "HOLE").any())
    return {"full_years": full, "ticker_years": len(flat), "distribution": {str(k): flat.count(k) for k in sorted(set(flat))},
            "off_3_to_5_without_a_hole": off[:40], "off_count": len(off)}


def card_window(lab: pd.DataFrame, close: pd.DataFrame, n_placebo: int, log=print) -> dict:
    out: dict = {"days": len(close.index), "tickers": len(close.columns),
                 "excluded_ticker_days": {c: int((lab == c).sum().sum()) for c in EXCLUDED},
                 "class_ticker_days": {c: int((lab == c).sum().sum()) for c in ("CLEAR",) + CLASSES},
                 "horizons": {}}
    for h in HORIZONS:
        df = long_frame(lab, close, h)
        out["horizons"][str(h)] = {"regression": regression(df, h)}
        if h == PRIMARY_H:
            out["horizons"][str(h)]["tail_raw"] = tail(df, "raw")
            out["horizons"][str(h)]["tail_demeaned"] = tail(df, "y")
            mde = out["horizons"][str(h)]["regression"]["HOLD"]["mde"]
            log(f"h={h}: HOLD n={out['horizons'][str(h)]['regression']['n']['HOLD']}, placebos {n_placebo}")
            draws = placebo_draws(lab, np.random.default_rng(SEED), n_placebo)
            out["controls"] = placebo_controls(df, draws, mde)
            log(f"controls: negative {out['controls']['negative_reject_rate']:.3f}, "
                f"positive {out['controls']['positive_detect_rate']:.3f}")
    out["d1"] = d1(lab)
    return out


def rule(IS: dict, OOS: dict | None) -> dict:
    """Item 8: the one primary test, IS h=1, HOLD's tail share minus CLEAR's, two-sided at 0.05; the OOS sign checks."""
    t = IS["horizons"][str(PRIMARY_H)]["tail_raw"]
    ctrl = IS["controls"]
    oos = OOS["horizons"][str(PRIMARY_H)]["tail_raw"]["diff"] if OOS else None
    if not (ctrl["negative_pass"] and ctrl["positive_pass"]):
        outcome = "card stopped: a control failed (item 10)"
    elif t["p"] is None or t["p"] > ALPHA:
        outcome = "no excess tail detected in IS (unanswered at this power is read from the MDE beside it)"
    elif oos is None or (oos > 0) != (t["diff"] > 0):
        outcome = "window-dependent: significant in IS, OOS sign disagrees; reported, not acted on"
    elif t["diff"] > 0:
        outcome = "HOLD carries tail risk (IS p <= 0.05, OOS sign agrees)"
    else:
        outcome = "less tail than CLEAR (IS p <= 0.05, OOS sign agrees)"
    return {"primary": {"window": "IS", "h": PRIMARY_H, **t}, "oos_diff": oos, "outcome": outcome,
            "controls_pass": bool(ctrl["negative_pass"] and ctrl["positive_pass"])}


def panels(pre: dict, window: tuple[str, str]) -> pd.DataFrame:
    from backend.config import EXCLUDED_SECTORS
    a, b = window
    days = [d.isoformat() for d in pre["trading_days"] if a <= d.isoformat() <= b]
    tick = sorted(t for t, s in pre["ticker_sector"].items() if s not in EXCLUDED_SECTORS)
    pc = pre["price_cache"]
    return pd.DataFrame({t: [pc.get((t, d), np.nan) for d in days] for t in tick}, index=days)


def main() -> int:
    from backend.backtest import battery_2026_10 as b
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir", type=Path)
    ap.add_argument("--windows", default="IS,OOS")
    ap.add_argument("--placebos", type=int, default=N_PLACEBO)
    ap.add_argument("--battery", type=Path, default=Path("/home/promenix/apex-battery/2026-10-19"))
    a = ap.parse_args()
    a.outdir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    git = lambda *x: subprocess.run(["git", *x], capture_output=True, text=True, check=False).stdout.strip()
    commit = git("rev-parse", "--short", "HEAD")
    dirty = bool(git("status", "--porcelain", "--", "backend/"))
    if dirty:
        print("refused: backend/ has uncommitted changes; the producer block must name the code that ran", flush=True)
        return 2

    def log(msg: str) -> None:
        print(f"[{time.time() - t0:6.0f}s] {msg}", flush=True)

    runner = b.Runner(b.Store(a.battery), commit)
    arch_path = a.outdir / "earnings_reports.json"
    res: dict = {}
    for w in a.windows.split(","):
        pre = runner._window(w)
        close = panels(pre, b.WINDOWS[w])
        if not arch_path.exists():                                     # fetched once, then read from the archive
            arch = fetch_reports(sorted(close.columns), "2026-11-28")
            arch["digest"] = earnings_set(arch).digest()
            arch_path.write_text(json.dumps(arch, indent=1))
            log(f"fetched reports for {len(arch['reports'])} tickers, {len(arch['failed'])} failed, digest {arch['digest']}")
        arch = json.loads(arch_path.read_text())
        es = earnings_set(arch)
        lab = classify(list(close.index), list(close.columns), arch["reports"], es)
        log(f"{w}: {len(close.index)} days x {len(close.columns)} tickers classed")
        res[w] = card_window(lab, close, a.placebos, log)
        res[w]["reports_without_reported_eps"] = sum(1 for rs in arch["reports"].values() for r in rs
                                                     if not r["reported"] and b.WINDOWS[w][0] <= r["day"] <= b.WINDOWS[w][1])
        (a.outdir / f"EARN_L0_{w}.json").write_text(json.dumps(res[w], indent=1, default=str))
        log(f"saved EARN_L0_{w}")
    if "IS" in res:
        out = {"producer": {"repo": "apex", "script": "backend/backtest/earnings_cell_2026_11.py", "commit": commit,
                            "dirty": dirty, "command": " ".join(sys.argv), "card": CARD,
                            "earnings_digest": json.loads(arch_path.read_text())["digest"]},
               **rule(res["IS"], res.get("OOS"))}
        (a.outdir / "EARN_L0_verdict.json").write_text(json.dumps(out, indent=1, default=str))
        log(f"saved EARN_L0_verdict: {out['outcome']}")
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
