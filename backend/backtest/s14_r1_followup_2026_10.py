"""APEX S14 R1 follow-up, 2026-10-27: the L2 instrument's weakness and its fix, the VIX classes by level and
year, and in the full engine the displacement behind the L3 reading, time stops and M4 crossings.

Why: the S14 R1 run (vault card raw/notes/2026-10/2026-10-24-apex-s14-r1-vix-gate-card.md § 9) ended
"unanswered (L2 control)": delta = 0 passed L2 in 3 of 20 draws. Niclas 2026-10-27 asked what sets those
draws apart and how to fix the instrument; whether VIX >= 30 (or >= 40) guarantees a better result, also in
2023-2026; why M4 fires at every start; and whether a shorter time stop separates displacement from path
("går det att testa fler tester där time stop är kortare?"). Exploratory: no rule was written before
this run, so nothing here is a verdict; the cards it proposes carry the rules.

How: python -u -m backend.backtest.s14_r1_followup_2026_10 OUTDIR [--workers 3] [--skip-engine]
     [--ledger /home/promenix/apex-battery/ledger-2026-10-24/ledger_IS.csv]
     [--battery /home/promenix/apex-battery/2026-10-19]

What: OUTDIR/S14R1_followup_L2.json (the 20 card draws replayed, the 400-draw characterisation, the
null-distribution test with its calibration and power, the VIX tables), and unless --skip-engine
OUTDIR/S14R1_followup_L3.json (per time stop, arm and start: P&L, per capital-day, M4 crossings; the
displacement split) and OUTDIR/S14R1_followup_tradelogs.json (the trade logs it was read from).
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
import pandas as pd

from backend.backtest import battery_2026_10 as b
from backend.backtest import engine_fast as ef
from backend.backtest import ladder_2026_10 as ld
from backend.backtest import s14_r1_vix_gate_2026_10 as s
from backend.backtest.candidate_ledger_2026_10 import VIX_LEVEL
from backend.backtest.exits_l3_m4_2026_10 import START_BALANCE
from backend.config import M4_DRAWDOWN_PCT as M4_DD

N_PROBE = 400                    # draws in the characterisation and in the null reference
ALPHA = 0.025                    # one-sided, the bar the card's 95 % CI lower bound stands for
POWER_DELTAS = [0.005, 0.01, 0.0117]   # 0.0117: the real VIX >= 30 net mean of the 10-27 run
VIX_BINS = [0, 15, 20, 25, 30, 40, 1000]
HIGH = 40.0                      # Niclas 10-27: "varför handlar vi inte bara i det intervallet?"
TIME_STOPS = [25, 15, 10]        # 25 is live's max_hold_days (get_live_config, 2026-10-27)
NEAR = [[0, 25], [26, 60], [61, 1_000_000]]   # trading days since the last VIX >= 30 day; 25 = the time stop
PROBE_SEED = 7                   # the 10-27 zero probe's seed, so the characterisation is the same 400 draws
REF_SEED = 20261028

_SHARED: dict = {}


# ── L2: the instrument ─────────────────────────────────────────────────────────

def card_draws(calm, mu, ep_days) -> list[dict]:
    """The card's 20 delta = 0 draws, replayed in the runner's rng order (one draw for the half-width first)."""
    rng = np.random.default_rng(s.SEED)
    s.boot_ci(*s.null_draw(calm, mu, ep_days, 0.0, rng), rng)
    out = []
    for i in range(s.N_ZERO):
        x, g = s.null_draw(calm, mu, ep_days, 0.0, rng)
        c = s.boot_ci(x, g, rng)
        out.append({"draw": i, "passed": bool(c["lo"] > 0), "mean": c["mean"], "lo": c["lo"], "hi": c["hi"],
                    "half_width": c["half_width"], "n_rows": c["n_rows"]})
    return out


def characterise(calm, mu, ep_days, n: int, rng) -> dict:
    """n delta = 0 draws: CI pass rate by half-width tercile, and the CI's width against the draws' own spread."""
    r = []
    for _ in range(n):
        c = s.boot_ci(*s.null_draw(calm, mu, ep_days, 0.0, rng), rng)
        r.append((c["mean"], c["half_width"], c["lo"] > 0))
    d = pd.DataFrame(r, columns=["mean", "half", "passed"])
    t = pd.qcut(d["half"], 3, labels=["narrow", "mid", "wide"])
    return {"draws": n, "pass_rate": float(d["passed"].mean()),
            "pass_rate_by_half_width": {k: float(v) for k, v in d.groupby(t, observed=True)["passed"].mean().items()},
            "sd_of_draw_means": float(d["mean"].std()), "mean_half_width_over_1_96": float((d["half"] / 1.96).mean())}


def null_means(calm, mu, ep_days, n: int, rng, delta: float = 0.0) -> np.ndarray:
    return np.array([s.null_draw(calm, mu, ep_days, delta, rng)[0].mean() for _ in range(n)])


def null_p(value: float, ref: np.ndarray) -> float:
    """One-sided: the share of reference null means at or above value."""
    return float((ref >= value).mean())


def null_test(calm, mu, ep_days, real: float, n: int, rng) -> dict:
    """The fix: compare the mean with the null's own distribution of means, not with a CI estimated from the
    same draw. Calibration: n fresh delta = 0 draws tested against the reference must pass at about ALPHA.
    Power: n draws at each POWER_DELTAS."""
    ref = null_means(calm, mu, ep_days, n, rng)
    cal = null_means(calm, mu, ep_days, n, rng)
    power = {str(d): float(np.mean([null_p(v, ref) < ALPHA for v in null_means(calm, mu, ep_days, n, rng, d)]))
             for d in POWER_DELTAS}
    return {"reference_draws": n, "alpha": ALPHA, "p_real": null_p(real, ref), "real_mean": real,
            "ref_quantiles": {str(q): float(np.quantile(ref, q)) for q in (0.9, 0.95, 0.975, 0.99)},
            "calibration_pass_rate": float(np.mean([null_p(v, ref) < ALPHA for v in cal])), "power": power}


# ── L2: the VIX classes ───────────────────────────────────────────────────────

def summary(x: pd.Series, exits: pd.Series) -> dict:
    return {"n": int(len(x)), "win": float((x > 0).mean()), "mean": float(x.mean()), "sd": float(x.std()),
            "p01": float(x.quantile(0.01)), "p99": float(x.quantile(0.99)),
            "tp_share": float((exits == "TP").mean()), "sl_share": float((exits == "SL").mean())}


def vix_tables(df: pd.DataFrame, cl: dict, n_eps: int) -> dict:
    """By VIX bin; VIX >= 30 and >= 40 by year; each episode with its rows at >= 40; calm runs' signs."""
    bins = pd.cut(df["vix"], list(VIX_BINS), right=False)
    by_bin = {str(k): summary(g["net"], g["exit_live"]) for k, g in df.groupby(bins, observed=True)}
    yr = df["date"].str[:4]
    by_year = {f"ge_{lvl:g}": {y: summary(g["net"], g["exit_live"]) for y, g in df[df["vix"] >= lvl].groupby(yr)}
               for lvl in (VIX_LEVEL, HIGH)}
    c = df["date"].map(cl)
    st = df["vix"] >= VIX_LEVEL
    eps = []
    for e, g in df[st].groupby(c[st]):
        h = g[g["vix"] >= HIGH]
        eps.append({"episode": int(e), "first": g["date"].min(), "last": g["date"].max(), "vix_max": float(g["vix"].max()),
                    "n": int(len(g)), "mean": float(g["net"].mean()), "win": float((g["net"] > 0).mean()),
                    "n_ge_40": int(len(h)), "mean_ge_40": float(h["net"].mean()) if len(h) else None})
    calm = df[~st].groupby(c[~st])["net"].mean()
    calm = calm[calm.index >= n_eps]
    return {"by_bin": by_bin, "by_year": by_year, "episodes": eps,
            "episodes_positive": sum(e["mean"] > 0 for e in eps), "calm_runs": int(len(calm)),
            "calm_runs_positive": int((calm > 0).sum())}


# ── L3: displacement, time stops, M4 ──────────────────────────────────────────

def m4_crossings(trade_log: list[dict], start: float = START_BALANCE, halt: float = M4_DD) -> dict:
    """Realised equity in exit order: separate passages of the halt (a new one after the drawdown fell below
    half the halt), the first date, the deepest drawdown. The engine keeps trading: this is measured, not enforced."""
    closed = sorted((t for t in trade_log if t.get("pnl") is not None and t.get("exit_date")), key=lambda t: t["exit_date"])
    eq = peak = start
    n, inside, first, mdd = 0, False, None, 0.0
    for t in closed:
        eq += t["pnl"]
        peak = max(peak, eq)
        dd = (peak - eq) / peak
        mdd = max(mdd, dd)
        if dd >= halt and not inside:
            n, inside, first = n + 1, True, first or t["exit_date"]
        elif dd < halt / 2:
            inside = False
    return {"crossings": n, "first": first, "max_drawdown": mdd}


def since_high(days: list[str], vix: dict[str, float]) -> dict[str, int]:
    """Trading days since the last VIX >= 30 day (0 on one); NEAR[-1][1] before the first."""
    out, last = {}, None
    for i, d in enumerate(days):
        if vix.get(d, 0.0) >= VIX_LEVEL:
            last = i
        out[d] = i - last if last is not None else NEAR[-1][1]
    return out


def displacement(on: list[dict], off: list[dict], vix: dict[str, float], since: dict[str, int]) -> dict:
    """Entries in one arm only (same ticker and day absent in the other), VIX >= 30 entries left out, by
    trading days since the last VIX >= 30 day: displacement sits inside the time stop, path differences anywhere."""
    key = lambda t: (t["ticker"], t["entry_date"])
    ko, kf = {key(t): t for t in on}, {key(t): t for t in off}
    out = {f"{a}-{z}": {"on_only": 0, "on_only_pnl": 0.0, "off_only": 0, "off_only_pnl": 0.0} for a, z in NEAR}
    for mine, other, tag in ((ko, kf, "on_only"), (kf, ko, "off_only")):
        for k, t in mine.items():
            if k in other or vix.get(t["entry_date"], 0.0) >= VIX_LEVEL:
                continue
            d = since.get(t["entry_date"], NEAR[-1][1])
            a, z = next(x for x in NEAR if x[0] <= d <= x[1])
            out[f"{a}-{z}"][tag] += 1
            out[f"{a}-{z}"][tag + "_pnl"] += t["pnl"] or 0.0
    out["shared"] = len(set(ko) & set(kf))
    return out


def run_row(tl: list[dict]) -> dict:
    closed = [t for t in tl if t.get("pnl") is not None]
    cap = sum(t["days_held"] * t["amount"] for t in closed)
    pnl = float(sum(t["pnl"] for t in closed))
    return {"entries": len(tl), "pnl": pnl, "pnl_per_capital_day": pnl / cap if cap else None, "m4": m4_crossings(tl)}


def _job(job: tuple) -> tuple:
    arm, start, ts = job
    o = {**s.over(arm), "time_stop_days": ts}
    res = ld._engine("IS", b.R1, start, o)
    return job, [{k: t.get(k) for k in ("ticker", "entry_date", "exit_date", "pnl", "amount", "days_held")}
                 for t in res["trade_log"]]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir", type=Path)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--skip-engine", action="store_true")
    ap.add_argument("--ledger", type=Path, default=Path("/home/promenix/apex-battery/ledger-2026-10-24/ledger_IS.csv"))
    ap.add_argument("--battery", type=Path, default=Path("/home/promenix/apex-battery/2026-10-19"))
    a = ap.parse_args(argv)
    a.outdir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False).stdout.strip()
    producer = {"repo": "apex", "branch": "master", "script": "backend/backtest/s14_r1_followup_2026_10.py",
                "commit": commit, "run": " ".join(sys.argv), "ledger": str(a.ledger),
                "why": "raw/notes/2026-10/2026-10-24-apex-s14-r1-vix-gate-card.md § 10", "label": "exploratory"}

    def log(msg: str) -> None:
        print(f"[{time.time() - t0:6.0f}s] {msg}", flush=True)

    df = s.net_rows(pd.read_csv(a.ledger))
    calm, mu, ep_days, eps, cl = s.calm_null(df)
    real = float(df.loc[df["vix"] >= VIX_LEVEL, "net"].mean())
    L2 = {"card_draws": card_draws(calm, mu, ep_days),
          "characterise": characterise(calm, mu, ep_days, N_PROBE, np.random.default_rng(PROBE_SEED)),
          "null_test": null_test(calm, mu, ep_days, real, N_PROBE, np.random.default_rng(REF_SEED)),
          "vix": vix_tables(df, cl, len(eps))}
    (a.outdir / "S14R1_followup_L2.json").write_text(json.dumps({"producer": producer, **L2}, indent=1, default=str))
    nt = L2["null_test"]
    log(f"saved L2: null test p {nt['p_real']:.4f}, calibration {nt['calibration_pass_rate']:.3f}, power {nt['power']}")
    if a.skip_engine:
        return 0

    vix = df.groupby("date")["vix"].first().to_dict()
    ef._trading_days_count = functools.lru_cache(maxsize=None)(ef._trading_days_count)   # pure; speed only
    ld._R = b.Runner(b.Store(a.battery), commit[:7])
    ld._R._window("IS")
    ld._R.frozen = True
    jobs = [(arm, st, ts) for ts in TIME_STOPS for arm in s.ARMS for st in b.RND_STARTS]
    T: dict = {}
    with mp.get_context("fork").Pool(a.workers) as pool:
        for n, (job, tl) in enumerate(pool.imap_unordered(_job, jobs), 1):
            T[job] = tl
            log(f"{n}/{len(jobs)} {job}")
    (a.outdir / "S14R1_followup_tradelogs.json").write_text(json.dumps(
        {"producer": producer, "logs": {f"{k[0]}|{k[1]}|{k[2]}": v for k, v in T.items()}}))
    since = since_high(sorted(vix), vix)
    L3 = {"time_stops": {str(ts): {arm: {str(st): run_row(T[(arm, st, ts)]) for st in b.RND_STARTS} for arm in s.ARMS}
                         for ts in TIME_STOPS},
          "displacement_ts25": {str(st): displacement(T[("ON", st, 25)], T[("OFF", st, 25)], vix, since)
                                for st in b.RND_STARTS}}
    (a.outdir / "S14R1_followup_L3.json").write_text(json.dumps({"producer": producer, **L3}, indent=1, default=str))
    log("saved L3")
    return 0


if __name__ == "__main__":
    sys.exit(main())
