"""Edge-test probe, 2026-10-20: why does the RND null make fewer trades than its reference?

Why: the 10-19 full battery's signal-edge rule (per-trade USD expectancy vs 300 RND seeds) failed
in every arm. Read back from the stored units, the RND arms made ~18 % fewer trades than their
reference at every start, in both windows, though RND is capped at the reference's entries per
day and given its open slots. Niclas 2026-10-20: "See if you can settle it" (item 7), plus
"how about not using any overflow?" (item 5) and the sizing alternatives (item 8). Exploratory:
no rule is read here; the defects found feed a pre-registered v2 edge test.
Vault: raw/notes/2026-10/2026-10-20-apex-edge-test-probe-trade-count-and-sizing.md

How: python -u -m backend.backtest.edge_probe_2026_10_20 /home/promenix/apex-battery/2026-10-19 OUT.json [--seeds N] [--workers W]
     then: ... /home/promenix/apex-battery/2026-10-19 OUT.json --summary SUMMARY.json (no engine runs)
Run 2026-10-20 08:37-09:03 Stockholm with --seeds 100 --workers 3 (310 runs, 1,553 s).
Reads the 10-19 battery store (reference IS R1/R2 start 0 with trade logs), re-runs RND seeds with
full trade logs (control: seed 1..5 must reproduce the stored seed records exactly), then runs
three variants of R1 IS start 0 with their own reference and N RND seeds each: hard cap (no
overflow, row 14 math.inf), fixed size 10 % for both arms, and both.

What: OUT.json, one entry per arm: per-run trade count, expectancy USD/% per trade, total return,
alpha (rf T-bill), mean hold days, mean amount, P&L per capital-day, exit-reason mix, and the
entry-slot audit (per day: reference entries, RND entries, and whether a short day was
slot-bound — RND's open count at its cap — or not).
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import multiprocessing as mp
import sys
import time
from pathlib import Path

import numpy as np

from backend.backtest import battery_2026_10 as b
from backend.backtest import profit_metrics as pm

_R: "b.Runner | None" = None
_REFS: dict[str, dict] = {}


def _engine(arm: str, base: dict, over: dict, seed: int | None) -> dict:
    """One engine run as Runner.run does it, keeping the full trade log."""
    from backend.backtest.engine_fast import run
    u = b.unit("IS", base, 0)
    pre = _R._window("IS")
    days = pre["trading_days"]
    pre_u = dict(pre)
    if u.regime_layer:
        pre_u["regime_timeline"] = _R._timeline("IS", u.regime)
    kw = {**_R.incumbent(), **u.kwargs, **over}
    extra = {}
    if seed is not None:
        ref = _REFS[arm]
        extra["random_entries"] = {"seed": seed, "per_day": ref["entries_per_day"],
                                   "open_cap": ref["open_after_entries"]}
    with b.regime_constants(dict(u.regime)):
        res = run(days[0].isoformat(), days[-1].isoformat(), initial_balance=10_000.0,
                  precomputed=pre_u, **kw, **extra)
    return {"trade_log": res["trade_log"], "equity_curve": res["equity_curve"],
            "max_pos": kw["max_positions"], "days": [d.isoformat() for d in days]}


def metrics(res: dict, ref: dict | None) -> dict:
    mk = _R.market["IS"]
    log = res["trade_log"]
    closed = [t for t in log if t.get("pnl") is not None]
    r = pm.daily_returns(res["equity_curve"])
    m = mk["spy"].pct_change().reindex(r.index)
    cap_days = sum(t["days_held"] * t["amount"] for t in closed)
    out = {
        "trades": len(closed),
        "expectancy": pm.expectancy(closed),
        "total_return": float((1 + r).prod() - 1),
        "alpha_tbill": pm.alpha_beta(r, m, mk["rf"])["alpha_annual"],
        "hold_days_mean": float(np.mean([t["days_held"] for t in closed])),
        "amount_mean": float(np.mean([t["amount"] for t in closed])),
        "score_mean": float(np.mean([t["signal_score"] for t in closed])),
        "pnl_per_capital_day": sum(t["pnl"] for t in closed) / cap_days if cap_days else None,
        "exit_mix": dict(collections.Counter(t["exit_reason"] for t in closed)),
        "pct_by_exit": {k: float(np.mean([t["pnl_pct"] for t in closed if t["exit_reason"] == k]))
                        for k in {t["exit_reason"] for t in closed}},
        "hold_by_exit": {k: float(np.mean([t["days_held"] for t in closed if t["exit_reason"] == k]))
                         for k in {t["exit_reason"] for t in closed}},
    }
    if ref is not None:
        out["slots"] = slot_audit(ref["entries_per_day"], ref["open_after_entries"], b._entries_per_day(log),
                                  b.open_after_entries(log, res["days"]), res["max_pos"])
    return out


def slot_audit(ref_entries: dict[str, int], ref_open: dict[str, int], mine: dict[str, int],
               open_after: dict[str, int], max_pos: int) -> dict:
    """On each day the reference entered k, did RND enter k? If short, was RND at its open-position
    cap after its own entries (slot-bound) or below it (pool-bound: held / cooloff / sector cap /
    ETF / loss cap / price rejected the random draws)?"""
    short = slot_bound = pool_bound = missing = 0
    for d, k in ref_entries.items():
        got = mine.get(d, 0)
        if got >= k:
            continue
        short += 1
        missing += k - got
        if open_after.get(d, 0) >= max(max_pos, ref_open.get(d, max_pos)):
            slot_bound += 1
        else:
            pool_bound += 1
    return {"ref_entries": sum(ref_entries.values()), "rnd_entries": sum(mine.values()),
            "short_days": short, "missing_entries": missing,
            "short_slot_bound": slot_bound, "short_pool_bound": pool_bound}


def rank_p(ref: float, xs) -> float:
    """One-sided rank p, as the battery's edge rule: (seeds >= ref + 1) / (n + 1)."""
    xs = np.asarray(list(xs), dtype=float)
    return float(((xs >= ref).sum() + 1) / (len(xs) + 1))


def _dist(ref: float, xs: list[float]) -> dict:
    a = np.asarray(xs, dtype=float)
    return {"ref": ref, "rnd_median": float(np.median(a)), "rnd_p5": float(np.percentile(a, 5)),
            "rnd_p95": float(np.percentile(a, 95)), "p_ge": rank_p(ref, a), "p_le": rank_p(-ref, -a)}


_STATS = {
    "trades": lambda x: x["trades"],
    "expectancy_usd": lambda x: x["expectancy"]["usd"]["mean"],
    "expectancy_pct": lambda x: x["expectancy"]["pct"]["mean"],
    "pnl_per_capital_day": lambda x: x["pnl_per_capital_day"],
    "total_return": lambda x: x["total_return"],
    "alpha_tbill": lambda x: x["alpha_tbill"],
    "hold_days": lambda x: x["hold_days_mean"],
    "amount": lambda x: x["amount_mean"],
    "score": lambda x: x["score_mean"],
    "tp_share_of_tp_sl": lambda x: x["exit_mix"].get("TP", 0) / (x["exit_mix"].get("TP", 0) + x["exit_mix"].get("SL", 0)),
    "hold_tp": lambda x: x["hold_by_exit"]["TP"],
    "hold_sl": lambda x: x["hold_by_exit"]["SL"],
}


def summarize(probe: dict, store: "b.Store") -> dict:
    """The numbers the vault note quotes: per probe arm, each statistic's reference value against its
    RND distribution; and, from the 10-19 battery store, the same edge test read on other statistics
    at all five RND starts (300 seeds each)."""
    out: dict = {"control_reproduces_battery": probe["_control_reproduces_battery"], "arms": {}, "battery": {}}
    for arm, v in probe.items():
        if arm.startswith("_"):
            continue
        seeds = list(v["rnd"].values())
        out["arms"][arm] = {"override": v["override"], "seeds": len(seeds),
                            "stats": {k: _dist(f(v["ref"]), [f(s) for s in seeds]) for k, f in _STATS.items()},
                            "slots_median": {k: float(np.median([s["slots"][k] for s in seeds])) for k in seeds[0]["slots"]}}

    def bat(rec: dict, k: str) -> float:
        if "seed" in rec:
            return {"usd": rec["expectancy"]["usd"]["mean"], "pct": rec["expectancy"]["pct"]["mean"],
                    "total_return": rec["total_return"], "alpha_tbill": rec["alpha_tbill"],
                    "calmar": rec["calmar"], "trades": rec["trades"]}[k]
        s = rec["summary"]
        return {"usd": s["expectancy"]["usd"]["mean"], "pct": s["expectancy"]["pct"]["mean"],
                "total_return": s["total_return"], "alpha_tbill": s["vs_spy_tbill"]["alpha_annual"],
                "calmar": s["calmar"], "trades": s["trades"]}[k]

    for w in b.WINDOWS:
        for name, base in (("R1", b.R1), ("R2", b.R2)):
            for s in b.RND_STARTS:
                u = b.unit(w, base, s)
                ref, seeds = store.get(u), [store.get(x) for x in b.rnd_units(u)]
                out["battery"][f"{w}.{name}.s{s}"] = {k: _dist(bat(ref, k), [bat(x, k) for x in seeds])
                                                      for k in ("usd", "pct", "total_return", "alpha_tbill", "calmar", "trades")}
    return out


def _seed_job(job: tuple[str, dict, dict, int]) -> tuple[str, int, dict]:
    arm, base, over, seed = job
    res = _engine(arm, base, over, seed)
    return arm, seed, metrics(res, _REFS[arm])


def main() -> None:
    global _R
    ap = argparse.ArgumentParser()
    ap.add_argument("battery_dir", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--seeds", type=int, default=100)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--summary", type=Path, default=None,
                    help="read OUT (an earlier probe run) and the battery store, write this summary JSON; no engine runs")
    a = ap.parse_args()
    store = b.Store(a.battery_dir)
    if a.summary is not None:
        a.summary.write_text(json.dumps(summarize(json.loads(a.out.read_text()), store), indent=1))
        print(f"summary written: {a.summary}", flush=True)
        return
    _R = b.Runner(store, "probe")
    t0 = time.time()
    _R._window("IS")
    print(f"precompute {time.time() - t0:.0f}s", flush=True)

    arms = {
        "base_R1": (b.R1, {}),
        "base_R2": (b.R2, {}),
        "hardcap_R1": (b.R1, {"overflow_quant_increment": math.inf}),
        "fixed10_R1": (b.R1, {"size_fixed": 0.10}),
        "hardcap_fixed10_R1": (b.R1, {"overflow_quant_increment": math.inf, "size_fixed": 0.10}),
    }
    out: dict = {}
    for arm, (base, over) in arms.items():
        if not over:
            ref = store.get(b.unit("IS", base, 0))
            _REFS[arm] = ref
            res = {"trade_log": ref["trade_log"], "equity_curve": ref["equity_curve"],
                   "max_pos": ref["kwargs"]["max_positions"], "days": ref["trading_days"]}
        else:
            res = _engine(arm, base, over, None)
            _REFS[arm] = {"entries_per_day": b._entries_per_day(res["trade_log"]),
                          "open_after_entries": b.open_after_entries(res["trade_log"], res["days"])}
        out[arm] = {"override": {k: str(v) for k, v in over.items()}, "ref": metrics(res, None), "rnd": {}}
        print(f"{arm} ref: trades {out[arm]['ref']['trades']}", flush=True)

    jobs = [(arm, base, over, s) for arm, (base, over) in arms.items() if not over for s in range(1, 6)]
    jobs += [(arm, base, over, s) for arm, (base, over) in arms.items() if over for s in range(1, a.seeds + 1)]
    with mp.get_context("fork").Pool(a.workers) as pool:
        for i, (arm, seed, m) in enumerate(pool.imap_unordered(_seed_job, jobs), 1):
            out[arm]["rnd"][seed] = m
            if i % 10 == 0 or i == len(jobs):
                print(f"{i}/{len(jobs)} {time.time() - t0:.0f}s", flush=True)
                a.out.write_text(json.dumps(out, indent=1, default=str))

    # Control: base seeds must reproduce the battery's stored seed records.
    ctrl = []
    for arm, base in (("base_R1", b.R1), ("base_R2", b.R2)):
        for u in b.rnd_units(b.unit("IS", base, 0))[:5]:
            st = store.get(u)
            mine = out[arm]["rnd"][u.seed]
            ctrl.append(st["trades"] == mine["trades"] and abs(st["expectancy"]["usd"]["mean"] - mine["expectancy"]["usd"]["mean"]) < 1e-9)
    out["_control_reproduces_battery"] = all(ctrl)
    a.out.write_text(json.dumps(out, indent=1, default=str))
    print(f"control reproduces battery: {all(ctrl)}  done {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    sys.exit(main())
