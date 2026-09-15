"""
Objective walk-forward — does lifting the Sharpe cap in the optimizer's
composite ship?

Pre-registered 2026-09-15, before any run:
  raw/notes/2026-09/2026-09-15-apex-optimizer-cap-walk-forward-preregistration.md
The criterion below is a transcription of that note; the note is authoritative.

Arms
  capped     _composite_score as shipped (NOISE_FLOOR hash a9a7a936)
  uncapped   same, sharpe_score = max(sharpe, 0) / 2.0 with no ceiling
  incumbent  live config.py tunables (NOT optimizer._default_params — drifted)
  SPY        engine spy_return_pct per test window, reference only

Criterion 1 (diagnostic): six cold-start runs per objective on the full
window, seeds shared; sample sigma of argmax max_positions / lock1_threshold /
total_trades. Pass = shrinks on >= 2, widens on none. Score sigma is not a
statistic (uncapping widens it mechanically).

Criterion 2 (decisive): three expanding folds, ~2-month disjoint tests. Per
fold+objective one cold-start run on train (seed shared per fold), top-3
distinct param sets by that objective from the run's history, each scored on
test; arm value = mean test window return. Fold win = margin >= 1.0 pp.
Pooled test trades >= 20 per arm or the arm cannot win. max_dd > 10% on test
is reported, not enforced.

Decision table: see the note (cases 1-5). This script prints the case.

Usage:  python -m backend.backtest.objective_walkforward [--c1] [--c2] [--out path]
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics as st
import time
from pathlib import Path

from loguru import logger

from backend import config
from backend.backtest import optimizer as opt
from backend.backtest.engine_fast import precompute, run as backtest_run
from backend.json_io import write_json_atomic

FULL_WINDOW = ("2025-12-15", "2026-09-11")
FOLDS = [
    ("F1", ("2025-12-15", "2026-02-27"), ("2026-03-02", "2026-04-30")),
    ("F2", ("2025-12-15", "2026-04-30"), ("2026-05-01", "2026-06-30")),
    ("F3", ("2025-12-15", "2026-06-30"), ("2026-07-01", "2026-09-11")),
]
C1_SEEDS      = [101, 102, 103, 104, 105, 106]
FOLD_SEEDS    = {"F1": 201, "F2": 202, "F3": 203}
TOP_K         = 3
MARGIN_PP     = 1.0     # percentage points of window return
POOLED_FLOOR  = 20      # test-window trades summed over folds
DD_REPORT     = 0.10
OUT_PATH      = Path(__file__).parent.parent.parent / "data" / "objective_walkforward.json"


def score_capped(sharpe, max_dd, win_rate, n_trades):
    return opt._composite_score(sharpe, max_dd, win_rate, n_trades)


def score_uncapped(sharpe, max_dd, win_rate, n_trades):
    """_composite_score with the Sharpe ceiling removed. Floors identical."""
    if n_trades < opt.FLOOR_TRADE_FREQ:
        return None
    if win_rate is not None and win_rate < opt.FLOOR_WIN_RATE:
        return None
    if not math.isfinite(sharpe):
        return None
    dd_penalty   = min(max_dd / 0.50, 1.0)
    wr_score     = min(max((win_rate or 0.0) - 0.30, 0.0) / 0.40, 1.0)
    sharpe_score = max(sharpe, 0.0) / 2.0          # no min(..., 1.0)
    return round(opt.W_SHARPE * sharpe_score - opt.W_DRAWDOWN * dd_penalty + opt.W_WIN_RATE * wr_score, 5)


OBJECTIVES = {"capped": score_capped, "uncapped": score_uncapped}


def incumbent_params() -> dict:
    """Live tunables from config.py — the arm the optimizer has to beat."""
    return dict(
        lock1_threshold=config.LOCK1_THRESHOLD,
        take_profit_pct=config.TAKE_PROFIT_PCT,
        stop_loss_pct=config.STOP_LOSS_PCT,
        trailing_stop_pct=None,
        time_stop_days=config.TIME_STOP_DAYS,
        max_positions=config.MAX_POSITIONS,
        vix_threshold=None,
    )


def _pkey(p: dict) -> tuple:
    return tuple(sorted(p.items()))


def hill_climb(score_fn, start, end, pc, seed: int, n_exp: int = opt.MAX_EXPERIMENTS) -> dict:
    """Cold-start hill-climb, same loop shape as run_optimizer, with an injected objective.
    Returns argmax + full history (every valid evaluation, deduplicated by params)."""
    random.seed(seed)
    cur_p = opt._default_params()
    ev = opt._evaluate(backtest_run, cur_p, start, end, pc, score_fn=score_fn)
    cur_s = ev[0] if ev else None
    history: dict[tuple, dict] = {}
    if ev:
        history[_pkey(cur_p)] = {"params": dict(cur_p), "score": ev[0], "metrics": _slim(ev[1])}
    kept = 0
    for _ in range(n_exp):
        n_mut = 1 if random.random() < 0.75 else random.randint(2, 3)
        cand  = opt._mutate(cur_p, n_mutations=n_mut)
        ev    = opt._evaluate(backtest_run, cand, start, end, pc, score_fn=score_fn)
        if ev is None:
            continue
        history.setdefault(_pkey(cand), {"params": dict(cand), "score": ev[0], "metrics": _slim(ev[1])})
        if cur_s is None or ev[0] > cur_s:
            cur_p, cur_s, kept = cand, ev[0], kept + 1
    return {"argmax": dict(cur_p), "score": cur_s, "kept": kept, "history": list(history.values())}


def _slim(m: dict) -> dict:
    keys = ("sharpe", "max_drawdown", "win_rate", "total_trades", "total_return_pct", "profit_factor", "spy_return_pct")
    return {k: (float(m[k]) if isinstance(m.get(k), (int, float)) else m.get(k)) for k in keys if k in m}


def eval_on(params: dict, start: str, end: str, pc) -> dict | None:
    """Raw engine metrics on a window — no objective applied, no floors."""
    try:
        r = backtest_run(start_date=start, end_date=end, initial_balance=10_000.0,
                         precomputed=pc, **params)
        return _slim(r)
    except Exception as e:
        logger.warning(f"eval_on failed {params}: {e}")
        return None


# ── Criterion 1 ──────────────────────────────────────────────────────────────

def criterion_1() -> dict:
    start, end = FULL_WINDOW
    logger.info(f"C1: precompute {start}→{end}")
    pc = precompute(start, end)
    runs = {name: [] for name in OBJECTIVES}
    for seed in C1_SEEDS:
        for name, fn in OBJECTIVES.items():
            t0 = time.time()
            r = hill_climb(fn, start, end, pc, seed)
            a = r["argmax"]; m = next(h["metrics"] for h in r["history"] if _pkey(h["params"]) == _pkey(a))
            runs[name].append({"seed": seed, "score": r["score"], "kept": r["kept"],
                               "max_positions": a["max_positions"], "lock1_threshold": a["lock1_threshold"],
                               "total_trades": m["total_trades"], "sharpe": m["sharpe"],
                               "win_rate": m["win_rate"], "params": a})
            logger.info(f"C1 {name} seed={seed} score={r['score']:.4f} kept={r['kept']} "
                        f"pos={a['max_positions']} L1={a['lock1_threshold']} n={m['total_trades']} ({time.time()-t0:.0f}s)")
    stats = {}
    for name in OBJECTIVES:
        stats[name] = {k: round(st.stdev([x[k] for x in runs[name]]), 4)
                       for k in ("max_positions", "lock1_threshold", "total_trades")}
        stats[name]["score_sigma_not_a_statistic"] = round(st.stdev([x["score"] for x in runs[name]]), 4)
    shrinks = [k for k in ("max_positions", "lock1_threshold", "total_trades")
               if stats["uncapped"][k] < stats["capped"][k]]
    widens  = [k for k in ("max_positions", "lock1_threshold", "total_trades")
               if stats["uncapped"][k] > stats["capped"][k]]
    verdict = "PASS" if len(shrinks) >= 2 and not widens else "FAIL"
    return {"runs": runs, "sigma": stats, "shrinks": shrinks, "widens": widens, "verdict": verdict}


# ── Criterion 2 ──────────────────────────────────────────────────────────────

def criterion_2() -> dict:
    folds_out = []
    inc = incumbent_params()
    for fid, (tr0, tr1), (te0, te1) in FOLDS:
        logger.info(f"{fid}: precompute train {tr0}→{tr1}, test {te0}→{te1}")
        pc_tr = precompute(tr0, tr1)
        pc_te = precompute(te0, te1)
        fold = {"fold": fid, "train": [tr0, tr1], "test": [te0, te1], "arms": {}}
        for name, fn in OBJECTIVES.items():
            r = hill_climb(fn, tr0, tr1, pc_tr, FOLD_SEEDS[fid])
            top = sorted(r["history"], key=lambda h: -h["score"])[:TOP_K]
            members = []
            for h in top:
                te = eval_on(h["params"], te0, te1, pc_te)
                members.append({"params": h["params"], "train_score": h["score"],
                                "train": h["metrics"], "test": te})
            rets = [m["test"]["total_return_pct"] for m in members if m["test"]]
            fold["arms"][name] = {
                "value": round(st.mean(rets), 4) if rets else None,
                "test_trades": sum(m["test"]["total_trades"] for m in members if m["test"]),
                "dd_breaches": [m["params"] for m in members if m["test"] and m["test"]["max_drawdown"] > DD_REPORT],
                "members": members,
            }
            logger.info(f"{fid} {name}: value={fold['arms'][name]['value']} trades={fold['arms'][name]['test_trades']}")
        te_inc = eval_on(inc, te0, te1, pc_te)
        fold["incumbent"] = te_inc
        fold["spy"] = te_inc.get("spy_return_pct") if te_inc else None
        c, u = fold["arms"]["capped"]["value"], fold["arms"]["uncapped"]["value"]
        if c is None or u is None:
            fold["winner"] = "tie"
        elif (u - c) * 100 >= MARGIN_PP:
            fold["winner"] = "uncapped"
        elif (c - u) * 100 >= MARGIN_PP:
            fold["winner"] = "capped"
        else:
            fold["winner"] = "tie"
        logger.info(f"{fid}: capped={c} uncapped={u} incumbent={te_inc and te_inc['total_return_pct']} "
                    f"spy={fold['spy']} → {fold['winner']}")
        folds_out.append(fold)

    pooled = {n: sum(f["arms"][n]["test_trades"] for f in folds_out) for n in OBJECTIVES}
    wins   = {n: sum(1 for f in folds_out if f["winner"] == n) for n in OBJECTIVES}
    eligible = {n: pooled[n] >= POOLED_FLOOR for n in OBJECTIVES}
    mean_of = {n: round(st.mean([f["arms"][n]["value"] for f in folds_out if f["arms"][n]["value"] is not None]), 4)
               for n in OBJECTIVES}
    inc_rets = [f["incumbent"]["total_return_pct"] for f in folds_out if f["incumbent"]]
    mean_of["incumbent"] = round(st.mean(inc_rets), 4) if inc_rets else None
    inc_beats_both = sum(1 for f in folds_out if f["incumbent"] and all(
        f["arms"][n]["value"] is not None and f["incumbent"]["total_return_pct"] > f["arms"][n]["value"]
        for n in OBJECTIVES)) >= 2

    if wins["uncapped"] >= 2 and eligible["uncapped"]:
        case = 1 if (mean_of["incumbent"] is None or mean_of["uncapped"] >= mean_of["incumbent"]) else 2
    elif wins["capped"] >= 2 and eligible["capped"]:
        case = 3
    else:
        case = 4
    return {"folds": folds_out, "pooled_trades": pooled, "eligible": eligible, "fold_wins": wins,
            "mean_of_folds": mean_of, "case": case, "case_5_incumbent_beats_both": inc_beats_both}


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--c1", action="store_true")
    ap.add_argument("--c2", action="store_true")
    ap.add_argument("--out", default=str(OUT_PATH))
    a = ap.parse_args()
    if not (a.c1 or a.c2):
        a.c1 = a.c2 = True
    from datetime import datetime, timezone
    out = {"generated_at": datetime.now(timezone.utc).isoformat(), "engine_commit": "6c8c16d", "preregistration": "raw/notes/2026-09/2026-09-15-apex-optimizer-cap-walk-forward-preregistration.md"}
    if a.c2:
        out["criterion_2"] = criterion_2()
        write_json_atomic(Path(a.out), out)
    if a.c1:
        out["criterion_1"] = criterion_1()
        write_json_atomic(Path(a.out), out)
    logger.info(f"written {a.out}")
    if "criterion_2" in out:
        c2 = out["criterion_2"]
        print(f"\nC2 case {c2['case']} | fold wins {c2['fold_wins']} | pooled {c2['pooled_trades']} | "
              f"mean-of-folds {c2['mean_of_folds']} | case5 {c2['case_5_incumbent_beats_both']}")
        for f in c2["folds"]:
            print(f"  {f['fold']}: capped={f['arms']['capped']['value']} uncapped={f['arms']['uncapped']['value']} "
                  f"incumbent={f['incumbent'] and f['incumbent']['total_return_pct']} spy={f['spy']} → {f['winner']}")
    if "criterion_1" in out:
        c1 = out["criterion_1"]
        print(f"\nC1 {c1['verdict']} | sigma {json.dumps(c1['sigma'])} | shrinks {c1['shrinks']} widens {c1['widens']}")


if __name__ == "__main__":
    main()
