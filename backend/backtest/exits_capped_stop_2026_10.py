"""APEX capped ATR stop, 2026-10-24: live's exit with a volatility-scaled stop that stops growing at a ceiling.

Why: the ATR leg ablation found about half of the scaled live rule's gain in the wider stop, and the
tail with it; its ATR deciles put the point where the tail grows near a stop of 8 %. Niclas 2026-10-24:
"det bör finnas ett tak. Vad är rimligt?", then "a sedan b". The card is vault
raw/notes/2026-10/2026-10-24-apex-capped-stop-card.md, committed (vault 88cab634) before this module.
It runs the ablation's pipeline (exits_atr_ablation_2026_10) with this card's arms bound in.
Exploratory: no live change rests on it.

How: python -u -m backend.backtest.exits_capped_stop_2026_10 OUTDIR [--windows IS,OOS] [--workers 3]
     [--battery /home/promenix/apex-battery/2026-10-19]

What: OUTDIR/CAP_<window>.json, OUTDIR/CAP_decision.json.
"""
from __future__ import annotations

import argparse
import functools
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from backend.backtest import battery_2026_10 as b
from backend.backtest import engine_fast as ef
from backend.backtest import exits_atr_2026_10 as xa
from backend.backtest import exits_atr_ablation_2026_10 as ab
from backend.backtest import exits_t8_2026_10 as t8
from backend.backtest import ladder_2026_10 as ld

# arm -> (legs scaled to ATR, stop ceiling); card item 3
CAPPED: dict[str, tuple[tuple[str, ...], float]] = {
    "ALLCAP8": (("tp", "sl", "pl"), 0.08), "ALLCAP10": (("tp", "sl", "pl"), 0.10),
    "SLCAP8": (("sl",), 0.08), "SLCAP10": (("sl",), 0.10),
}


def leg_cfg(arm: str, atr_pct: float) -> dict:
    if arm == "STATIC":
        return dict(ab.LIVE)
    legs, ceiling = CAPPED[arm]
    tw = xa.twin_cfg("P1", atr_pct)
    live = ab.LIVE
    return {"tp": tw["tp"] if "tp" in legs else live["tp"],
            "sl": min(tw["sl"], ceiling) if "sl" in legs else live["sl"],
            "pl": tw["pl"] if "pl" in legs else live["pl"], "tdays": live["tdays"]}


def bind() -> None:
    """Point the ablation pipeline at this card's arms (module state, inherited by forked workers)."""
    ab.leg_cfg = leg_cfg
    ab.ABLATION = list(CAPPED)
    ab.DECILE_ARMS = ("STATIC", *CAPPED)
    ab.all_arms = lambda: ["STATIC", *CAPPED]


def ceiling_share(P: dict, E: list) -> dict:
    atr = P["atr"].to_numpy()
    sl = np.array([xa.twin_cfg("P1", float(atr[i, j]))["sl"] for i, j in E])
    return {str(c): float((sl > c).mean()) for c in (0.08, 0.10)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir", type=Path)
    ap.add_argument("--windows", default="IS,OOS")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--battery", type=Path, default=Path("/home/promenix/apex-battery/2026-10-19"))
    a = ap.parse_args(argv)
    a.outdir.mkdir(parents=True, exist_ok=True)
    bind()
    t0 = time.time()
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False).stdout.strip()

    def log(msg: str) -> None:
        print(f"[{time.time() - t0:6.0f}s] {msg}", flush=True)

    ef._trading_days_count = functools.lru_cache(maxsize=None)(ef._trading_days_count)   # pure; speed only
    ld._R = b.Runner(b.Store(a.battery), commit[:7])
    from backend import db
    live_db = Path("/home/promenix/apex/data/apex.db")
    db.DB_PATH = live_db
    since = json.loads((live_db.parent / "live_config.json").read_text()).get("live_account_since")
    canonical = db.live_sample_rows(since=since)
    res, R, P, E = {}, {}, {}, {}
    for w in a.windows.split(","):
        pre = ld._R._window(w)
        P[w] = ld.panels(pre)
        res[w], R[w] = ab.card_window(w, P[w], pre, canonical if w == "IS" else None, a.workers, log)
        E[w] = t8.purge(ld.entry_sets(P[w])["picks"], len(P[w]["days"]))[0]
        res[w]["stop_above_ceiling_share"] = ceiling_share(P[w], E[w])
        (a.outdir / f"CAP_{w}.json").write_text(json.dumps(res[w], indent=1, default=str))
        log(f"saved CAP_{w}")
    if {"IS", "OOS"} <= set(res):
        out = {"producer": {"repo": "apex", "branch": "master", "script": "backend/backtest/exits_capped_stop_2026_10.py",
                            "commit": commit, "run": " ".join(sys.argv),
                            "why": "raw/notes/2026-10/2026-10-24-apex-capped-stop-card.md"},
               **ab.decide(res, R, P, E)}
        (a.outdir / "CAP_decision.json").write_text(json.dumps(out, indent=1, default=str))
        log("saved CAP_decision")
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
