"""Profit read of the ported engine (Saturday 2026-10-17, step 3).

Pre-registered in the vault: raw/notes/2026-10/2026-10-16-apex-profit-read-preregistration.md
(amended 10-17 before any run). Runs R0 / R1 / R2 / RND x 200 / SPY-EM on one frozen
precompute, 2019-01-02 -> 2026-10-16; folds are slices of the full-window run.
Each unit is saved as soon as it finishes, so a killed run resumes where it stopped.
Prints progress only; the reading is done from the saved files.

Run from apex/: venv/bin/python -u -m backend.backtest.profit_read_2026_10 <outdir>
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import pandas as pd

WINDOW = ("2019-01-02", "2026-10-16")
FOLDS = {
    "F1":    ("2026-03-02", "2026-04-30"),
    "F2":    ("2026-05-01", "2026-06-30"),
    "F3":    ("2026-07-01", "2026-09-11"),
    "COVID": ("2020-02-03", "2020-06-30"),
    "Y2022": ("2022-01-03", "2022-12-30"),
}
CRISIS_FOLDS = ("COVID", "Y2022")
RND_SEEDS = range(1, 201)


def incumbent_from_live() -> dict:
    """The live config as of the run, read, never copied (pre-registration)."""
    from backend.backtest.live_shape import live_cooloff_days
    from backend.live_config import get_live_config
    c = get_live_config()
    sl, tp = live_cooloff_days()
    return dict(
        lock1_threshold=c["lock1_threshold"], take_profit_pct=c["take_profit_pct"],
        stop_loss_pct=c["stop_loss_pct"], profit_lock_trigger_pct=c["profit_lock_trigger_pct"],
        profit_lock_trail_pct=c["profit_lock_trail_pct"], max_positions=c["max_positions"],
        time_stop_days=c["max_hold_days"], vix_threshold=c["vix_threshold"],
        max_sector_exposure=c["max_sector_exposure"],
        sl_cooldown_days=sl, tp_cooldown_days=tp,
        macro_pre_event_penalty=c["macro_pre_event_penalty"],   # row 10, Niclas 10-17: "ja"
        etf_negative_penalty=c["etf_negative_penalty"], etf_negative_floor=c["etf_negative_floor"],   # row 11, 2026-10-18
    )


RUNS = {
    "R0": dict(sl_cooldown_days=5, tp_cooldown_days=0, regime_layer=False),   # the 09-16 basis on today's code
    "R1": dict(regime_layer=False),
    "R2": dict(regime_layer=True),
}


def entries_per_day(trade_log: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in trade_log:
        out[t["entry_date"]] = out.get(t["entry_date"], 0) + 1
    return out


def fold_metrics(result: dict, spy: pd.Series, rf: pd.Series) -> dict:
    from backend.backtest import profit_metrics as pm
    r = pm.daily_returns(result["equity_curve"])
    m = spy.dropna().pct_change()
    out = {}
    for name, (a, b) in FOLDS.items():
        rr = pm.window(r, a, b)
        trades = [t for t in result["trade_log"] if a <= t["entry_date"] <= b and t.get("pnl") is not None]
        out[name] = {
            "total_return": float((1 + rr).prod() - 1) if len(rr) else None,
            "max_drawdown": pm.max_drawdown(rr), "calmar": pm.calmar(rr),
            "trades": len(trades), "expectancy": pm.expectancy(trades),
            "spy_return": float((1 + pm.window(m, a, b)).prod() - 1),
            "vs_spy_tbill": pm.alpha_beta(rr, pm.window(m, a, b), rf),
        }
    return out


def main(outdir: Path) -> None:
    import yfinance as yf
    from backend.backtest import profit_metrics as pm
    from backend.backtest.engine_fast import precompute, run
    from backend.backtest.gap_bounds import describe, open_rows

    outdir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    print(describe(regime_layer=True), flush=True)            # what the result does not model
    pre = precompute(*WINDOW)
    print(f"precompute done {time.time() - t0:.0f}s, {len(pre['trading_days'])} days", flush=True)
    spy = pre["raw_data"]["SPY"]["Close"].dropna()
    irx = yf.download("^IRX", start=WINDOW[0], end="2026-10-17", progress=False, auto_adjust=False)
    irx_close = irx["Close"].squeeze() if not irx.empty else pd.Series(dtype=float)
    rf = (irx_close / 100 / pm.TRADING_DAYS).rename("rf")        # annual % → daily
    inc = incumbent_from_live()
    (outdir / "incumbent.json").write_text(json.dumps(inc, indent=1))

    results = {}
    for name, kw in RUNS.items():
        f = outdir / f"{name}.json"
        if f.exists():
            results[name] = json.loads(f.read_text())
            print(f"{name} loaded", flush=True)
            continue
        t1 = time.time()
        res = run(*WINDOW, initial_balance=10_000.0, precomputed=pre, **{**inc, **kw})
        rec = {"run": name, "kwargs": {**inc, **kw},
               "gaps": open_rows(regime_layer=kw.get("regime_layer", False)),
               "summary": {**pm.summary(res, spy, 0.0), "vs_spy_tbill": pm.summary(res, spy, rf)["vs_spy"]},
               "folds": fold_metrics(res, spy, rf),
               "equity_curve": res["equity_curve"], "trade_log": res["trade_log"]}
        if "regime_timeline" in pre and name == "R2":
            days = pre["regime_timeline"]["days"]
            by_year: dict[str, dict] = {}
            for d, v in days.items():
                y = by_year.setdefault(d[:4], {"days": 0, "allocated": 0, "none_open": 0, "bull": 0, "neutral": 0, "bear": 0})
                n = sum(1 for a in v["allocation"].values() if a > 0)
                y["days"] += 1; y["allocated"] += n; y["none_open"] += n == 0; y[v["bucket"]] += 1
            rec["regime_by_year"] = {y: {**v, "mean_allocated": round(v["allocated"] / v["days"], 2)} for y, v in by_year.items()}
            rec["warmup_sessions"] = pre["regime_timeline"]["warmup_sessions"]
        f.write_text(json.dumps(rec, default=str))
        results[name] = rec
        print(f"{name} done {time.time() - t1:.0f}s", flush=True)

    per_day = entries_per_day(results["R2"]["trade_log"])
    rnd_dir = outdir / "rnd"; rnd_dir.mkdir(exist_ok=True)
    for seed in RND_SEEDS:
        f = rnd_dir / f"seed_{seed:03d}.json"
        if f.exists():
            continue
        res = run(*WINDOW, initial_balance=10_000.0, precomputed=pre,
                  **{**inc, **RUNS["R2"]}, random_entries={"seed": seed, "per_day": per_day})
        closed = [t for t in res["trade_log"] if t.get("pnl") is not None]
        r = pm.daily_returns(res["equity_curve"])
        f.write_text(json.dumps({"seed": seed, "trades": len(closed), "expectancy": pm.expectancy(closed),
                                 "total_return": float((1 + r).prod() - 1), "max_drawdown": pm.max_drawdown(r),
                                 "calmar": pm.calmar(r)}))
        if seed % 10 == 0:
            print(f"RND {seed}/200 {time.time() - t0:.0f}s", flush=True)

    frac = results["R2"]["summary"]["mean_deployed"]
    days = pm.daily_returns(results["R2"]["equity_curve"]).index
    em = {}
    for leg, cash in (("cash0", 0.0), ("tbill", rf)):
        er = pm.spy_em_returns(spy, days, frac, cash)
        em[leg] = {"fraction": frac, "total_return": float((1 + er).prod() - 1), "cagr": pm.cagr(er),
                   "max_drawdown": pm.max_drawdown(er), "calmar": pm.calmar(er)}
    (outdir / "SPY_EM.json").write_text(json.dumps(em, indent=1))
    print("done", flush=True)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
