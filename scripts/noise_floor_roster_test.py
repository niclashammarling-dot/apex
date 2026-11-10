"""NOISE_FLOOR roster test: the floor WITH and WITHOUT the delisted names (BLD, TMHC), same window, same seeds.

Vault: raw/notes/2026-11/2026-11-10-apex-noise-floor-roster-test.md (pre-registered before this script was
written; the rule below is copied from it). Input to the roster policy ruling (apex-moc {OT-apex-041}, R016).

BLD and TMHC survive only in data/backtest_cache/84485b3bd4de.parquet (2025-02-04..2026-06-12), whose columns
are the roster of 5d7cf69 (data/tickers.json as of 2026-06-14, merged with config.SECTORS as get_sectors() does)
plus SPY, minus ^VIX. ^VIX is fetched once into OUTDIR/vix.parquet (reused on a rerun) and added to both arms.
Per arm, scripts/noise_floor_runs.main runs six cold-start optimizer runs (seeds 0-5) with get_sectors,
_date_range and engine_fast._download_all patched to the arm; nothing is downloaded for any roster ticker.

Rule (pre-registered): level material when |mean WITH - mean WITHOUT| >= WITH's threshold; spread material when
sigma WITHOUT / sigma WITH is outside 0.445-2.25 (90 % interval, F(5,5)). Either -> shape 3 dead.

Writes OUTDIR/with/, OUTDIR/without/ (noise_floor_runs output, resumable per run) and OUTDIR/roster_test.json.
Usage: venv/bin/python -u scripts/noise_floor_roster_test.py OUTDIR [--runs 6]
"""
import json
import statistics
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
WHY = "raw/notes/2026-11/2026-11-10-apex-noise-floor-roster-test.md"
ROSTER_COMMIT = "5d7cf69"
CACHE = ROOT / "data" / "backtest_cache" / "84485b3bd4de.parquet"
REMOVED = ("BLD", "TMHC")
WINDOW = ("2025-09-15", "2026-06-12")     # LOOKBACK_DAYS 270 ending Friday 2026-06-12
SIGMA_RATIO = (0.445, 2.25)


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def roster_at_commit() -> dict:
    from backend.config import SECTORS as defaults
    merged = dict(defaults)
    merged.update(json.loads(_git("show", f"{ROSTER_COMMIT}:data/tickers.json")))
    return merged


def without(sectors: dict) -> dict:
    return {s: {**c, "tickers": [t for t in c["tickers"] if t not in REMOVED]} for s, c in sectors.items()}


def requested(sectors: dict) -> set[str]:
    """The tickers precompute asks _download_all for (engine_fast.precompute)."""
    return {"SPY", "^VIX"} | {c["etf"] for c in sectors.values()} | {t for c in sectors.values() for t in c["tickers"]}


def vix_frame(out: Path, index: pd.DatetimeIndex) -> pd.DataFrame:
    f = out / "vix.parquet"
    if not f.exists():
        import yfinance as yf
        v = yf.download("^VIX", start=index.min().date().isoformat(),
                        end=(index.max() + pd.Timedelta(days=1)).date().isoformat(),
                        auto_adjust=True, progress=False, group_by="ticker")
        if v.empty:
            raise RuntimeError("^VIX download returned no rows")
        if not isinstance(v.columns, pd.MultiIndex):
            v.columns = pd.MultiIndex.from_product([["^VIX"], v.columns])
        v.to_parquet(f)
    return pd.read_parquet(f)


def verdict(w: dict, wo: dict) -> dict:
    level = round(abs(w["mean"] - wo["mean"]), 4)    # means are 3-decimal, the threshold 2: no float residue
    ratio = wo["sigma"] / w["sigma"] if w["sigma"] else float("inf")
    level_material = level >= w["threshold"]
    spread_material = not (SIGMA_RATIO[0] <= ratio <= SIGMA_RATIO[1])
    return {"level_diff": level, "with_threshold": w["threshold"], "level_material": level_material,
            "sigma_ratio": round(ratio, 3), "sigma_ratio_interval": list(SIGMA_RATIO),
            "spread_material": spread_material, "shape3_dead": level_material or spread_material}


def main(argv: list[str]) -> int:
    import backend.backtest.engine_fast as ef
    import backend.backtest.optimizer as opt
    import backend.ticker_config as tc
    import noise_floor_runs as nfr

    out = Path(argv[0])
    runs = argv[argv.index("--runs") + 1] if "--runs" in argv else "6"
    out.mkdir(parents=True, exist_ok=True)
    cache = pd.read_parquet(CACHE)
    cache = cache.join(vix_frame(out, cache.index), how="left")
    cols = set(cache.columns.get_level_values(0))

    arms = {"with": roster_at_commit()}
    arms["without"] = without(arms["with"])
    controls: dict = {}
    for name, sectors in arms.items():
        missing = requested(sectors) - cols
        if missing:
            raise RuntimeError(f"{name}: tickers not in the cache: {sorted(missing)}")
    controls["with_reads"] = len(requested(arms["with"]))
    controls["without_reads"] = len(requested(arms["without"]))
    controls["with_equals_cache_plus_vix"] = requested(arms["with"]) == cols
    controls["without_drops_exactly_removed"] = requested(arms["with"]) - requested(arms["without"]) == set(REMOVED)
    s, e = date.fromisoformat(WINDOW[0]), date.fromisoformat(WINDOW[1])
    keys = {n: ef._signal_cache_key(sorted(requested(a)), s, e) for n, a in arms.items()}
    controls["signal_cache_keys_differ"] = keys["with"] != keys["without"]

    def loader(arm_set: set[str]):
        def _download_all(tickers, start, end):
            if set(tickers) != arm_set:
                raise RuntimeError(f"precompute asked for {sorted(set(tickers) ^ arm_set)} outside the arm")
            df = cache[sorted(set(tickers))]
            return df.loc[pd.Timestamp(start):pd.Timestamp(end)]
        return _download_all

    summaries, exits = {}, {}
    orig = (tc.get_sectors, opt._date_range, ef._download_all, opt._evaluate)
    try:
        for name, sectors in arms.items():
            tc.get_sectors = lambda sectors=sectors: {k: dict(v) for k, v in sectors.items()}
            opt._date_range = lambda: WINDOW
            ef._download_all = loader(requested(sectors))
            opt._evaluate = orig[3]
            print(f"== arm {name}: {len(requested(sectors))} tickers incl. SPY/^VIX/ETFs", flush=True)
            exits[name] = nfr.main([str(out / name), "--runs", runs])
            summaries[name] = json.loads((out / name / "summary.json").read_text())
    finally:
        tc.get_sectors, opt._date_range, ef._download_all, opt._evaluate = orig

    w, wo = summaries["with"], summaries["without"]
    paired = [round(a["best_score"] - b["best_score"], 4) for a, b in zip(w["runs"], wo["runs"])]
    controls["runner_controls_ok"] = all(c == 0 for c in exits.values())
    controls["seeds_paired"] = [r["seed"] for r in w["runs"]] == [r["seed"] for r in wo["runs"]]
    ok = all(v for k, v in controls.items() if isinstance(v, bool))
    head = _git("rev-parse", "--short", "HEAD").strip()
    dirty = bool(_git("status", "--porcelain", "--", "scripts/", "backend/").strip())
    pushed = subprocess.run(["git", "merge-base", "--is-ancestor", "HEAD", "origin/master"], cwd=ROOT).returncode == 0
    result = {"producer": {"repo": "apex", "commit": head, "script": "scripts/noise_floor_roster_test.py",
                           "run": " ".join(["venv/bin/python", "-u", "scripts/noise_floor_roster_test.py", *argv]),
                           "why": WHY, "run_at": datetime.now(timezone.utc).isoformat(), "dirty": dirty,
                           "pushed": pushed, "cache": CACHE.name, "roster_commit": ROSTER_COMMIT,
                           "removed": list(REMOVED), "window": list(WINDOW)},
              "controls": controls,
              "with": w["summary"], "without": wo["summary"],
              "with_runs": w["runs"], "without_runs": wo["runs"],
              "paired_diff_with_minus_without": paired,
              "verdict": verdict(w["summary"], wo["summary"])}
    (out / "roster_test.json").write_text(json.dumps(result, indent=1, default=str))
    print(json.dumps({k: result[k] for k in ("controls", "with", "without", "paired_diff_with_minus_without",
                                             "verdict")}, indent=1, default=str))
    print("CONTROL", "OK" if ok else "FAIL")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
