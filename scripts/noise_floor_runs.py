"""NOISE_FLOOR re-measure: N cold-start optimizer runs on identical data, then the block's numbers.

Vault: INDEX "APEX NOISE_FLOOR re-measure on the corrected engine + lifted cap" (filed 2026-09-16; must land
before the fourth fold, ~2026-11-14). Method as measured 2026-09-14 (optimizer.py NOISE_FLOOR comment):
run_optimizer(warm_start=False, notify=False, results_path=...) per run, 200 experiments each, one window, one
precompute cache. Runs are seeded 0..N-1 so each is reproducible.

Resumable: run i writes OUTDIR/run_<i>.json when it completes; a rerun skips every run whose file is there.
Writes only under OUTDIR plus the engine's own backtest cache (data/backtest_cache, as the weekly run does).

Each run also records why each discarded experiment returned None (Niclas 2026-11-08: a silent None from
_evaluate is the class the gate runner had; if the Nones cluster in one part of the space the search is
censored and σ partly measures where the failures fall): an exception (logged at DEBUG only by _evaluate),
or which _composite_score floor failed. And the roster the runs used (count + hash of get_sectors() tickers),
since _objective_hash() does not include it (apex-moc {OT-apex-041}).

Known-present controls (exit 2 if any misses):
  - every run starts from the same score (defaults on identical data; 2026-09-14: 0.29495 in all six);
  - _objective_hash() is the same before the first run and after the last (nothing changed mid-measurement);
  - per run, the Nones traced equal the experiments run minus the experiments recorded.

Usage: venv/bin/python -u scripts/noise_floor_runs.py OUTDIR [--runs 6]
"""
import json
import math
import statistics
import hashlib
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))           # backend.* imports when run as scripts/noise_floor_runs.py
WHY = "raw/notes/2026-11/2026-11-08-apex-noise-floor-remeasured.md"   # vault note the numbers go to


def _r(x: float, places: int) -> float:
    """Half-up, as the 2026-09-14 block was written (mean 0.6655 -> 0.666; round() gives 0.665)."""
    return float(Decimal(str(x)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def summarize(scores: list[float], kept: list[int]) -> dict:
    """The NOISE_FLOOR block's numbers. σ is the sample σ (ddof 1), as in 2026-09-14 (0.032 from six runs).
    Threshold, two routes as in the block comment: the observed range, and a 2σ band on the difference of
    two independent runs (2·σ·√2); the larger, to two decimals."""
    sigma = statistics.stdev(scores)
    rng = max(scores) - min(scores)
    band = 2 * math.sqrt(2) * sigma
    return {
        "n_runs": len(scores),
        "mean": _r(statistics.mean(scores), 3),
        "sigma": _r(sigma, 3),
        "range": _r(rng, 3),
        "threshold": _r(max(rng, band), 2),
        "threshold_routes": {"range": _r(rng, 4), "two_sigma_diff": _r(band, 4)},
        "kept_mean": _r(statistics.mean(kept), 1),
        "kept_sigma": _r(statistics.stdev(kept), 1),
    }


def none_cause(exc: BaseException | None, r: dict | None, floor_trades: int, floor_win_rate: float) -> str:
    """Why _evaluate returned None, in _composite_score's order of checks."""
    if exc is not None:
        return f"exception:{type(exc).__name__}"
    if r is None:
        return "no_result"
    if r["total_trades"] < floor_trades:
        return "trades_below_floor"
    if r["win_rate"] is not None and r["win_rate"] < floor_win_rate:
        return "win_rate_below_floor"
    if not math.isfinite(r["sharpe"]):
        return "sharpe_not_finite"
    return "unclassified"


def roster() -> dict:
    from backend.ticker_config import get_sectors
    tickers = sorted({t for cfg in get_sectors().values() for t in cfg.get("tickers", [])})
    return {"n_tickers": len(tickers), "hash": hashlib.sha256(",".join(tickers).encode()).hexdigest()[:8]}


def main(argv: list[str]) -> int:
    import backend.backtest.optimizer as opt
    from backend.backtest.optimizer import _date_range, _objective_hash, run_optimizer

    nones: list[dict] = []
    original = opt._evaluate

    def traced(backtest_run, params, *a, **k):
        seen: dict = {}

        def run(**kw):
            try:
                seen["r"] = backtest_run(**kw)
                return seen["r"]
            except Exception as e:
                seen["exc"] = e
                raise

        res = original(run, params, *a, **k)
        if res is None:
            r = seen.get("r")
            nones.append({"cause": none_cause(seen.get("exc"), r, opt.FLOOR_TRADE_FREQ, opt.FLOOR_WIN_RATE),
                          "trades": r and r.get("total_trades"), "win_rate": r and r.get("win_rate"),
                          "params": dict(params)})
        return res

    opt._evaluate = traced            # run_optimizer resolves _evaluate as a module global

    out = Path(argv[0])
    n = int(argv[argv.index("--runs") + 1]) if "--runs" in argv else 6
    out.mkdir(parents=True, exist_ok=True)
    hash_before = _objective_hash()
    start, end = _date_range()
    print(f"window {start} -> {end} | objective_hash {hash_before} | {n} runs -> {out}", flush=True)

    runs = []
    for i in range(n):
        f = out / f"run_{i}.json"
        if f.exists():
            runs.append(json.loads(f.read_text()))
            print(f"run {i}: on disk, skipped (best {runs[-1]['best_score']:.4f})", flush=True)
            continue
        t0 = datetime.now(timezone.utc)
        nones.clear()
        res = run_optimizer(seed=i, warm_start=False, notify=False, results_path=out / f"optimizer_{i}.json")
        row = {"seed": i, "best_score": res["best_score"], "start_score": res["start_score"], "kept": res["kept"],
               "total": res["total"], "best_params": res["best_params"], "started": t0.isoformat(),
               "none": len(nones), "none_causes": dict(Counter(x["cause"] for x in nones)), "none_rows": list(nones),
               "finished": datetime.now(timezone.utc).isoformat()}
        f.write_text(json.dumps(row, indent=1, default=str))
        runs.append(row)
        print(f"run {i}: best {row['best_score']:.4f} start {row['start_score']:.5f} kept {row['kept']}/{row['total']} "
              f"none {row['none']} {row['none_causes']}",
              flush=True)

    hash_after = _objective_hash()
    starts = {round(r["start_score"], 6) for r in runs}
    # experiments run = MAX_EXPERIMENTS unless the time budget stopped the loop (never near it: ~1 min a run)
    nones_ok = all(r["none"] == opt.MAX_EXPERIMENTS - r["total"] for r in runs)
    controls = {"same_start_score": len(starts) == 1, "start_scores": sorted(starts),
                "hash_unchanged": hash_before == hash_after, "hash_before": hash_before, "hash_after": hash_after,
                "nones_traced_equal_discarded": nones_ok}
    ok = controls["same_start_score"] and controls["hash_unchanged"] and nones_ok
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                            text=True).stdout.strip()
    result = {"producer": {"repo": "apex", "commit": commit, "script": "scripts/noise_floor_runs.py",
                           "run": " ".join(["venv/bin/python", "-u", "scripts/noise_floor_runs.py", *argv]),
                           "why": WHY, "run_at": datetime.now(timezone.utc).isoformat(),
                           "window": [start, end], "objective_hash": hash_after, "roster": roster()},
              "controls": controls,
              "summary": summarize([r["best_score"] for r in runs], [r["kept"] for r in runs]),
              "runs": [{k: r[k] for k in ("seed", "best_score", "start_score", "kept", "total", "none", "none_causes")}
                       for r in runs]}
    (out / "summary.json").write_text(json.dumps(result, indent=1, default=str))
    print(json.dumps({k: result[k] for k in ("controls", "summary", "runs")}, indent=1, default=str))
    print("CONTROL", "OK" if ok else "FAIL")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
