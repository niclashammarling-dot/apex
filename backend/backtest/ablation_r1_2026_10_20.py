"""
7C ablation, targeted (ruling Niclas 2026-10-20, item 10): which engine fix since the 10-17 archive
moved IS R1's signal edge from rank 198/200 (archived, experiment b) to p 0.076 at start 0
(battery 2026-10-19)?

The battery's `--explain profit_read` reverts each fix across the whole cell: 6,120 units x 7
fixes, about 40 h. The archived rule read one quantity, R1's RND rank at start 0, so this reads
that quantity alone: IS R1 at start 0 plus its 300 RND seeds, under each revert in FIXES and
under no revert (8 arms, 2,408 units). R9 (e753565) postdates the battery, so every arm runs with
peak_from_high=False; the no-revert arm must then reproduce the stored battery units exactly (the
control). 1C's null-design changes (RND inherits R2's constraints, the overflow leak closed) are not
engine fixes and have no revert here: a flip that no single revert restores may be theirs.

Usage: python -m backend.backtest.ablation_r1_2026_10_20 OUTDIR [--workers 3]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

import backend.backtest.battery_2026_10 as b

BATTERY = Path("/home/promenix/apex-battery/2026-10-19")
PIN     = {"peak_from_high": False}              # the 10-19 battery ran before R9
ARCHIVED_P = 3 / 201                             # rank 198/200 (experiment b) as a rank p: (2 + 1) / (200 + 1)
ARMS = {"fixed": {}, **{name: kw for name, (_commit, kw) in b.FIXES.items()}}


def arm_units(revert: dict) -> list[b.Unit]:
    ref = b._ablate(b.unit("IS", b.R1, 0), {**revert, **PIN})
    return [ref] + b.rnd_units(ref)


def read_arm(store: b.Store, revert: dict) -> dict:
    ref, *rnd = arm_units(revert)
    e = b._expectancy(store.get(ref))
    seeds = [b._expectancy(store.get(u)) for u in rnd]
    p = (sum(x >= e for x in seeds) + 1) / (len(seeds) + 1)
    return {"expectancy": e, "rnd_p95": float(np.percentile(seeds, 95)), "p": p,
            "rank": int(sum(x < e for x in seeds)) + 1, "of": len(seeds) + 1, "trades": len(store.get(ref)["trade_log"])}


def reproduction(store: b.Store, battery: b.Store) -> dict:
    """The no-revert arm against the stored battery: the reference and every seed, expectancy equal."""
    ours = arm_units({})
    theirs = [b.unit("IS", b.R1, 0)] + b.rnd_units(b.unit("IS", b.R1, 0))
    diff = [abs(b._expectancy(store.get(a)) - b._expectancy(battery.get(t))) for a, t in zip(ours, theirs)]
    return {"units": len(diff), "max_abs_diff": max(diff), "reproduces": max(diff) < 1e-9}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir", type=Path)
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args(argv)
    head, dirty = b.git_state()
    if dirty:
        print("refused: tracked files are modified; the record must name a commit")
        return 2
    store, battery = b.Store(a.outdir), b.Store(BATTERY)
    todo = b.ordered([u for kw in ARMS.values() for u in arm_units(kw) if not store.has(u)])
    t0 = time.time()
    print(f"[{0:6.0f}s] {len(todo)} units to run, head {head[:7]}", flush=True)
    b.run_units(b.Runner(store, head), todo, a.workers, log=lambda m: print(f"[{time.time() - t0:6.0f}s] {m}", flush=True))
    out = {"question": "which fix moved IS R1 start-0 RND rank from 198/200 (archived) to the battery's p",
           "archived_p": ARCHIVED_P, "pin": PIN, "commit": head,
           "control": reproduction(store, battery),
           "arms": {name: read_arm(store, kw) for name, kw in ARMS.items()}}
    fixed = out["arms"]["fixed"]
    for name, r in out["arms"].items():
        r["restores_archived_side"] = r["p"] <= 0.05 and fixed["p"] > 0.05
        if name != "fixed":
            r["trade_log_diff"] = b.trade_log_diff(store.get(arm_units({})[0])["trade_log"],
                                                   store.get(arm_units(ARMS[name])[0])["trade_log"])
    restores = [n for n, r in out["arms"].items() if n != "fixed" and r["restores_archived_side"]]
    out["status"] = ("explained by " + restores[0] if len(restores) == 1 else
                     "unexplained: no single fix restores p <= 0.05 (interaction, 1C null design, or a bug)"
                     if not restores else "unexplained: several fixes restore it (" + ", ".join(restores) + ")")
    (a.outdir / "ablation_r1.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps({k: v for k, v in out.items() if k != "arms"}, indent=1, default=str), flush=True)
    for n, r in out["arms"].items():
        print(f"{n:32s} p {r['p']:.4f} rank {r['rank']}/{r['of']} exp {r['expectancy']:+.2f} trades {r['trades']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
