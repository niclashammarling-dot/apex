"""APEX S14 R1 card, 2026-10-27: does the VIX >= 30 gate cost APEX's own candidates money, at L2 (the
candidate ledger) and at L3 (R1 in the full engine, with M4's realised drawdown)?

Why: INDEX "APEX S14 R1 card: build the runner and run it" (Niclas 2026-10-24: "Vi sparar det till måndag
eller tisdag"). The card is vault raw/notes/2026-10/2026-10-24-apex-s14-r1-vix-gate-card.md, committed
before this module. IS only: OOS has 2 VIX >= 30 candidate rows and cannot check a sign (card item 2).
Episodes are the S14 probe's (a run of level days; one non-level day inside joins it), over the ledger's
days: 19 in IS, the card's count. Exploratory.

How: python -u -m backend.backtest.s14_r1_vix_gate_2026_10 OUTDIR [--workers 3] [--skip-l3]
     [--ledger /home/promenix/apex-battery/ledger-2026-10-24/ledger_IS.csv]
     [--battery /home/promenix/apex-battery/2026-10-19]

     [--zero-probe N [--probe-seed 7]]   (exploratory: the delta = 0 pass rate over N draws)

What: OUTDIR/S14R1_L2.json (controls first, then the classes and the 2020 / 2022 / rest rows),
OUTDIR/S14R1_L3.json (rewritten after every engine run: per arm and start the metrics, M4's drawdown and
the period rows; controls), OUTDIR/S14R1_decision.json (the card's rule), and with --zero-probe only
OUTDIR/S14R1_zero_probe.json.
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
from backend.backtest import exits_l3_m4_2026_10 as l3
from backend.backtest import ladder_2026_10 as ld
from backend.backtest.archive_guard import fingerprint
from backend.backtest.candidate_ledger_2026_10 import VIX_LEVEL   # live's vix_threshold, one source
from backend.backtest.exit_rules import POLLED_PEAK_GAP
from backend.backtest.s14_episodes_probe_2026_10_21 import episodes

N_BOOT = 2000                    # card item 3: episode-bootstrap draws
N_ZERO = 20                      # card item 6: delta = 0 draws
ZERO_MAX = 0.10                  # card item 6: delta = 0 may pass L2 in at most 10 % of draws
COST_BP = 10                     # card item 3: net of 10 bp per round trip
NEED = 4                         # card items 4-5: at >= 4 of 5 starts
SEED = 20261027
PERIODS = {"2020": ("2020-02-01", "2020-06-30"), "2022": ("2022-01-01", "2022-12-31")}   # card item 3; the rest is "rest"
ARMS = ("ON", "OFF")

_SHARED: dict = {}


# ── L2: the candidate ledger ───────────────────────────────────────────────────

def day_clusters(days: list[str], level: list[bool]) -> tuple[list[tuple[str, str, int]], dict[str, int]]:
    """The episodes, and a cluster id per day: episode e is id e (its joined non-level day included), each
    calm run between episodes gets the next id. Every CI resamples these whole (card item 2b)."""
    eps = episodes(level, days)
    out, e, nxt, cur = {}, 0, len(eps), None
    for d in days:
        while e < len(eps) and d > eps[e][1]:
            e += 1
        if e < len(eps) and eps[e][0] <= d:
            out[d], cur = e, None
        else:
            if cur is None:
                cur, nxt = nxt, nxt + 1
            out[d] = cur
    return eps, out


def boot_ci(x: np.ndarray, g: np.ndarray, rng: np.random.Generator) -> dict:
    """Mean per row with a 95 % CI from resampling whole clusters; n is the cluster count."""
    ids = np.unique(g)
    if len(ids) == 0:
        return {"mean": None, "lo": None, "hi": None, "half_width": None, "n_rows": 0, "n_episodes": 0}
    sums = np.array([x[g == i].sum() for i in ids])
    cnts = np.array([(g == i).sum() for i in ids])
    pick = rng.integers(0, len(ids), (N_BOOT, len(ids)))
    boot = sums[pick].sum(1) / cnts[pick].sum(1)
    lo, hi = float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))
    return {"mean": float(x.mean()), "lo": lo, "hi": hi, "half_width": (hi - lo) / 2,
            "n_rows": int(len(x)), "n_episodes": int(len(ids))}


def null_draw(calm: list[np.ndarray], mu: float, ep_days: list[int], delta: float,
              rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """Card item 6: each episode's rows replaced by the rows of a random run of as many consecutive calm
    days (`calm`: one array of net outcomes per calm day, in date order), centred on the calm mean `mu` so
    the null is 0 net, plus delta. A run may straddle an episode; the shape of calm days is what is kept."""
    xs, gs = [], []
    for e, k in enumerate(ep_days):
        s = int(rng.integers(0, len(calm) - k + 1))
        v = np.concatenate(calm[s:s + k]) - mu + delta
        xs.append(v)
        gs.append(np.full(len(v), e))
    return np.concatenate(xs), np.concatenate(gs)


def null_pass_rates(calm: list[np.ndarray], mu: float, ep_days: list[int], n: int,
                    rng: np.random.Generator) -> dict:
    """Share of n delta = 0 draws that pass L2 (lower bound > 0), and two-sided (either bound past 0)."""
    lo = two = 0
    for _ in range(n):
        c = boot_ci(*null_draw(calm, mu, ep_days, 0.0, rng), rng)
        lo += c["lo"] > 0
        two += c["lo"] > 0 or c["hi"] < 0
    return {"draws": n, "pass_rate": lo / n, "two_sided_rate": two / n}


def calm_null(df: pd.DataFrame) -> tuple[list[np.ndarray], float, list[int], list, dict]:
    """The null's inputs: net outcomes per calm day (date order), their mean, episode day counts, episodes,
    the cluster map. `df` carries `net`."""
    day_vix = df.groupby("date")["vix"].first().sort_index()
    days, level = list(day_vix.index), list(day_vix >= VIX_LEVEL)
    eps, cl = day_clusters(days, level)
    st = df["vix"] >= VIX_LEVEL
    by_day = {d: g.to_numpy() for d, g in df.loc[~st, ["date", "net"]].groupby("date")["net"]}
    calm = [by_day.get(d, np.empty(0)) for d in days if cl[d] >= len(eps)]
    return calm, float(np.concatenate(calm).mean()), [n for _, _, n in eps], eps, cl


def net_rows(df: pd.DataFrame) -> pd.DataFrame:
    df = df[df["pnl_live"].notna()].copy()
    df["net"] = df["pnl_live"] - COST_BP / 1e4
    return df


def in_period(dates: pd.Series, name: str) -> pd.Series:
    if name == "rest":
        return ~functools.reduce(lambda x, y: x | y, (in_period(dates, p) for p in PERIODS))
    a, z = PERIODS[name]
    return (dates >= a) & (dates <= z)


def l2(df: pd.DataFrame, rng: np.random.Generator, log=print) -> dict:
    df = net_rows(df)
    calm, mu, ep_days, eps, cl = calm_null(df)
    df["cluster"] = df["date"].map(cl)
    st = df["vix"] >= VIX_LEVEL

    # Controls first (card items 6-7): delta = 0 sets the half-width, delta+ = 2 x it must pass.
    zero = boot_ci(*null_draw(calm, mu, ep_days, 0.0, rng), rng)
    half = zero["half_width"]
    zero_pass = null_pass_rates(calm, mu, ep_days, N_ZERO, rng)["pass_rate"]
    plus = boot_ci(*null_draw(calm, mu, ep_days, 2 * half, rng), rng)
    ctl = {"zero": zero, "half_width": half, "zero_pass_rate": float(zero_pass),
           "zero_ok": bool(zero_pass <= ZERO_MAX), "plus": plus, "plus_supported": bool(plus["lo"] > 0),
           "calm_mean_net": mu}
    log(f"L2 controls: half-width {half:.5f}, +delta supported {ctl['plus_supported']}, "
        f"zero pass rate {ctl['zero_pass_rate']:.2f}")

    def cls(mask: pd.Series) -> dict:
        r = boot_ci(df.loc[mask, "net"].to_numpy(), df.loc[mask, "cluster"].to_numpy(), rng)
        x = df.loc[mask, "net"]
        r.update(worst_1pct=float(x.quantile(0.01)) if len(x) else None,
                 hold_mean=float(df.loc[mask, "hold_live"].mean()) if len(x) else None)
        return r

    periods = {}
    for p in (*PERIODS, "rest"):
        m = in_period(df["date"], p)
        periods[p] = {"vix_ge_30": cls(st & m), "vix_lt_30": cls(~st & m)}
    return {"controls": ctl, "n_episodes": len(eps), "episodes": eps, "cost_bp": COST_BP,
            "vix_ge_30": cls(st), "vix_lt_30": cls(~st), "periods": periods}


# ── L3: R1 in the full engine, gate on against off ─────────────────────────────

def over(arm: str) -> dict:
    """Live's exits with the polled peak (the L3+M4 card's STATIC); the gate at live's level or off. The
    R1UNIT control is the battery's R1 with only the peak model, so it must equal ON."""
    if arm == "R1UNIT":
        return {"peak_from_high": True, "peak_haircut": POLLED_PEAK_GAP}
    return {**l3.over("STATIC"), "vix_threshold": VIX_LEVEL if arm == "ON" else None}


def period_rows(trade_log: list[dict], vix: dict[str, float]) -> dict:
    """Per period: closed trades entered in it, their P&L, P&L per capital-day; and entries on VIX >= 30 days."""
    closed = [t for t in trade_log if t.get("pnl") is not None]
    out = {}
    for p in (*PERIODS, "rest"):
        d = pd.Series([t["entry_date"] for t in closed], dtype=object)
        m = in_period(d, p).to_numpy() if len(d) else np.zeros(0, bool)
        ts = [t for t, k in zip(closed, m) if k]
        cap = sum(t["days_held"] * t["amount"] for t in ts)
        out[p] = {"trades": len(ts), "pnl": float(sum(t["pnl"] for t in ts)),
                  "pnl_per_capital_day": float(sum(t["pnl"] for t in ts) / cap) if cap else None}
    out["entries_on_vix_ge_30"] = sum(vix.get(t["entry_date"], 0.0) >= VIX_LEVEL for t in trade_log)
    return out


def l3_rule(on: dict[int, dict], off: dict[int, dict]) -> dict:
    """Card items 4-5 at L3: off higher per capital-day at >= NEED starts with its realised drawdown at most
    DD_TOL deeper (costs), or off deeper by more than DD_TOL at >= NEED starts (protects)."""
    starts = sorted(set(on) & set(off))
    need = NEED if len(starts) >= 5 else len(starts)
    v = lambda m: m["pnl_per_capital_day"] if m["pnl_per_capital_day"] is not None else -np.inf
    up = sum(v(off[s]) > v(on[s]) for s in starts)
    dd_ok = sum(off[s]["m4"]["max_drawdown"] <= on[s]["m4"]["max_drawdown"] + l3.DD_TOL for s in starts)
    return {"starts": len(starts), "off_higher": up, "dd_ok": dd_ok, "dd_deeper": len(starts) - dd_ok,
            "costs": up >= need and dd_ok >= need, "protects": len(starts) - dd_ok >= need}


def decide(L2: dict, L3: dict | None) -> dict:
    """Card item 5. Controls gate the reading; 2020 or 2022 alone negative at L2 blocks 'costs money'."""
    c = L2["controls"]
    r = l3_rule(L3["IS"]["ON"], L3["IS"]["OFF"]) if L3 is not None else None   # reported beside any verdict
    if not (c["plus_supported"] and c["zero_ok"]):
        return {"verdict": "unanswered (L2 control)", "controls": c, "l3": r}
    if L3 is not None and not all(L3["controls"].values()):
        return {"verdict": "unanswered (L3 control)", "controls": L3["controls"], "l3": r}
    g = L2["vix_ge_30"]
    neg = [p for p in PERIODS if (L2["periods"][p]["vix_ge_30"]["mean"] or 0) < 0]
    costs = g["lo"] > 0 and r is not None and r["costs"] and not neg
    protects = g["hi"] < 0 or (r is not None and r["protects"])
    v = "costs money" if costs else "protects" if protects else "trade-off"
    if L3 is None and v == "trade-off":
        v = "L2 only: no verdict without L3"
    return {"verdict": v, "l2_lo_above_0": g["lo"] > 0, "l2_hi_below_0": g["hi"] < 0,
            "negative_periods_at_l2": neg, "l3": r, "is_only": "OOS has 2 VIX >= 30 rows (card item 2)"}


def _job(job: tuple) -> tuple:
    arm, start, tag = job
    res = ld._engine("IS", b.R1, start, over(arm))
    m = l3.metrics("IS", res)
    m["periods"] = period_rows(res["trade_log"], _SHARED["vix"])
    return arm, start, tag, {"m": m, "fp": fingerprint(res["trade_log"])}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir", type=Path)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--skip-l3", action="store_true")
    ap.add_argument("--zero-probe", type=int, default=0, help="exploratory: delta = 0 pass rate over N draws, then exit")
    ap.add_argument("--probe-seed", type=int, default=7)
    ap.add_argument("--ledger", type=Path, default=Path("/home/promenix/apex-battery/ledger-2026-10-24/ledger_IS.csv"))
    ap.add_argument("--battery", type=Path, default=Path("/home/promenix/apex-battery/2026-10-19"))
    a = ap.parse_args(argv)
    a.outdir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False).stdout.strip()
    producer = {"repo": "apex", "branch": "master", "script": "backend/backtest/s14_r1_vix_gate_2026_10.py",
                "commit": commit, "run": " ".join(sys.argv), "ledger": str(a.ledger),
                "why": "raw/notes/2026-10/2026-10-24-apex-s14-r1-vix-gate-card.md"}

    def log(msg: str) -> None:
        print(f"[{time.time() - t0:6.0f}s] {msg}", flush=True)

    df = pd.read_csv(a.ledger)
    if a.zero_probe:   # 10b of the 10-27 run: can 20 draws tell the instrument's rate from the 10 % bar?
        calm, mu, ep_days, _, _ = calm_null(net_rows(df))
        r = null_pass_rates(calm, mu, ep_days, a.zero_probe, np.random.default_rng(a.probe_seed))
        r.update(seed=a.probe_seed, label="exploratory, not the card's verdict")
        (a.outdir / "S14R1_zero_probe.json").write_text(json.dumps({"producer": producer, **r}, indent=1))
        log(f"zero probe: {r}")
        return 0
    rng = np.random.default_rng(SEED)
    L2 = l2(df, rng, log)
    (a.outdir / "S14R1_L2.json").write_text(json.dumps({"producer": producer, **L2}, indent=1, default=str))
    g = L2["vix_ge_30"]
    log(f"saved L2: VIX >= 30 net {g['mean']:+.4f} [{g['lo']:+.4f}, {g['hi']:+.4f}] over {g['n_episodes']} episodes")

    L3 = None
    if not a.skip_l3:
        _SHARED["vix"] = df.groupby("date")["vix"].first().to_dict()
        ef._trading_days_count = functools.lru_cache(maxsize=None)(ef._trading_days_count)   # pure; speed only
        ld._R = b.Runner(b.Store(a.battery), commit[:7])
        ld._R._window("IS")
        ld._R.frozen = True
        log(f"incumbent vix_threshold {ld._R.incumbent().get('vix_threshold')} (the card's 30)")
        jobs = [(arm, s, "") for arm in ARMS for s in b.RND_STARTS] + [("ON", 0, "twice"), ("R1UNIT", 0, "")]
        R: dict = {}
        with mp.get_context("fork").Pool(a.workers) as pool:
            for n, (arm, s, tag, out) in enumerate(pool.imap_unordered(_job, jobs), 1):
                R[(arm, s, tag)] = out
                log(f"{n}/{len(jobs)} {arm} start {s} {tag}")
                (a.outdir / "S14R1_L3.json").write_text(json.dumps(
                    {"producer": producer, "partial": {f"{k[0]}|{k[1]}|{k[2]}": v for k, v in R.items()}},
                    indent=1, default=str))
        IS = {arm: {s: R[(arm, s, "")]["m"] for s in b.RND_STARTS} for arm in ARMS}
        controls = {"on_twice_identical": R[("ON", 0, "")]["fp"] == R[("ON", 0, "twice")]["fp"],
                    "on_equals_r1_unit": R[("ON", 0, "")]["fp"] == R[("R1UNIT", 0, "")]["fp"],
                    "on_has_no_vix_entries": all(IS["ON"][s]["periods"]["entries_on_vix_ge_30"] == 0 for s in b.RND_STARTS),
                    "off_has_vix_entries": all(IS["OFF"][s]["periods"]["entries_on_vix_ge_30"] > 0 for s in b.RND_STARTS)}
        log(f"L3 controls {controls}")
        L3 = {"controls": controls, "overrides": {arm: over(arm) for arm in (*ARMS, "R1UNIT")},
              "IS": IS, "rule": l3_rule(IS["ON"], IS["OFF"])}
        (a.outdir / "S14R1_L3.json").write_text(json.dumps(
            {"producer": producer, **{**L3, "IS": {arm: {str(s): m for s, m in d.items()} for arm, d in IS.items()}}},
            indent=1, default=str))
        log("saved L3")
    D = decide(L2, L3)
    (a.outdir / "S14R1_decision.json").write_text(json.dumps({"producer": producer, **D}, indent=1, default=str))
    log(f"verdict: {D['verdict']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
