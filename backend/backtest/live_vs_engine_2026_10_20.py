"""
Live against the engine on the same entries, and against same-day volatility-matched random
entries with the same exits (2026-10-20; vault raw/notes/2026-10/2026-10-20-apex-live-vs-engine-
canonical-sample.md).

Two trade sets, named in the output, because the same question on two sets gave opposite answers
that day:
  canonical  db.live_sample_rows() — closed since live_account_since (07-07 reset), unverified
             exits and annotated rows out. The set every live analysis uses (ruling 2026-10-20).
  pooled     every closed live row since 04-06, the pre-reset $15-30k positions included. Kept
             only to reproduce the corrected first reading; never pooled again.
Each live trade is re-entered at the decision day's close through the engine's own exit function
(ladder_2026_10._simulate) with the trade's own TP/SL, under the pre-R9 peak (close) and R9 (high).

Usage: python -m backend.backtest.live_vs_engine_2026_10_20 OUT.json
Reads the production DB read-only and the battery's IS window.
"""
from __future__ import annotations

import argparse
import functools
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import backend.backtest.battery_2026_10 as b
import backend.backtest.engine_fast as ef
import backend.backtest.ladder_2026_10 as ld

LIVE_DB = Path("/home/promenix/apex/data/apex.db")
SEED    = 20261020
N_RND   = 200
CLASSES = ("in_session", "same_day_pre_open", "earlier_day")


def _cfg(t: dict) -> dict:
    return {"tp": round(t["tp_price"] / t["entry_price"] - 1, 3), "sl": round(1 - t["sl_price"] / t["entry_price"], 3),
            "pl": [0.04, 0.01], "tdays": 25}


def _with_peak(flag: bool):
    """_simulate calls ef._check_exits_fast by name; bind its peak model for one pass."""
    ef._check_exits_fast = functools.partial(_ORIG, peak_from_high=flag)


_ORIG = ef._check_exits_fast


def compare(P: dict, pre: dict, trades: list[dict], peak_from_high: bool, rng: np.random.Generator) -> dict:
    _with_peak(peak_from_high)
    try:
        days, tick = P["days"], P["tickers"]
        q = ld.quintile(P["atr"], P["mask"]).to_numpy()
        m = P["mask"].to_numpy()
        rows, rnd = [], []
        for t in trades:
            d = t["timestamp"][:10]
            if d not in days or t["ticker"] not in tick or not t["notional"]:
                continue
            i, j = days.index(d), tick.index(t["ticker"])
            cfg = _cfg(t)
            e = ld._simulate(P, pre, i, j, cfg)
            cand = np.flatnonzero(m[i] & (q[i] == q[i, j])) if m[i, j] else np.array([], dtype=int)
            cand = cand[cand != j]
            if len(cand) == 0:                                   # outside the engine pool that day
                cand = np.flatnonzero(m[i]); cand = cand[cand != j]
            rnd.append([ld._simulate(P, pre, i, int(k), cfg)["pnl"] for k in rng.choice(cand, N_RND)])
            rows.append({"id": t["id"], "ticker": t["ticker"], "d": d, "cls": t["signal_class"],
                         "live": t["pnl"] / t["notional"], "eng": e["pnl"], "eng_reason": e["reason"]})
    finally:
        ef._check_exits_fast = _ORIG
    D, R = pd.DataFrame(rows), np.array(rnd)
    return {"n": len(D), "by": {k: _read(D[mask], R[mask.to_numpy()])
                                 for k, mask in [("all", D.cls.notna() | D.cls.isna())] +
                                 [(c, D.cls == c) for c in CLASSES]},
            "rows": D.assign(rnd_mean=R.mean(1), rnd_wr=(R > 0).mean(1)).to_dict("records")}


def _read(D: pd.DataFrame, R: np.ndarray) -> dict:
    if len(D) == 0:
        return {"n": 0}
    lw, ew = D.live > 0, D.eng > 0
    rw, rm = (R > 0).mean(0), R.mean(0)                          # per-draw win rate and mean across trades
    return {"n": len(D), "live_wr": float(lw.mean()), "live_mean": float(D.live.mean()),
            "eng_wr": float(ew.mean()), "eng_mean": float(D.eng.mean()),
            "rnd_wr": float(rw.mean()), "rnd_mean": float(rm.mean()),
            "p_live_wr": float((rw >= lw.mean()).mean()), "p_live_mean": float((rm >= D.live.mean()).mean()),
            "p_eng_wr": float((rw >= ew.mean()).mean()), "p_eng_mean": float((rm >= D.eng.mean()).mean()),
            "live_win_eng_loss": int((lw & ~ew).sum()), "live_loss_eng_win": int((~lw & ew).sum()),
            "sign_agreement": float((lw == ew).mean()), "corr": float(D.live.corr(D.eng))}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--battery", type=Path, default=Path("/home/promenix/apex-battery/2026-10-19"))
    a = ap.parse_args(argv)
    import backend.db as db
    db.DB_PATH = LIVE_DB
    since = json.loads((LIVE_DB.parent / "live_config.json").read_text()).get("live_account_since")
    canonical = db.live_sample_rows(since=since)
    con = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    pooled = [dict(r) for r in con.execute("SELECT * FROM live_trades WHERE outcome IN ('WIN', 'LOSS') ORDER BY timestamp")]
    ef._trading_days_count = functools.lru_cache(maxsize=None)(ef._trading_days_count)   # pure; speed only
    pre = b.Runner(b.Store(a.battery), "live-vs-engine")._window("IS")
    P = ld.panels(pre)
    out = {"since": since, "sets": {
        "canonical": {"definition": db.LIVE_SAMPLE, "n_rows": len(canonical),
                      "pre_r9": compare(P, pre, canonical, False, np.random.default_rng(SEED)),
                      "r9": compare(P, pre, canonical, True, np.random.default_rng(SEED))},
        "pooled": {"definition": "every closed live row (WIN/LOSS) since 2026-04-06, pre-reset included; "
                                 "the corrected first reading, never pooled again", "n_rows": len(pooled),
                   "pre_r9": compare(P, pre, pooled, False, np.random.default_rng(SEED))}}}
    a.out.write_text(json.dumps(out, indent=1, default=str))
    for s, v in out["sets"].items():
        for k in ("pre_r9", "r9"):
            if k in v:
                print(s, k, json.dumps(v[k]["by"]["all"]), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
