"""Rerun battery after PRODUCTION_GAP class 1 (written 2026-10-19; run only on a ruling).

Every result before 2026-10-18 ran on an engine that later turned out to differ from
live (the defect list in the 10-18 audit), so none of it is read as evidence until
it is rerun. This is the rerun, as one script. Only the profit read's runner was in
the repo; the other 10-17 runners lived in a session scratchpad and are gone, so each
cell here is rebuilt from its pre-registration note (path on the cell) and the
results archived in the vault (`raw/notes/2026-10/data/2026-10-17-*.json`) are the
before-numbers, not a reproduction target: the engine has changed since.

Structure:
  Unit   one engine run (window, start shift, overrides on the incumbent, regime
         constants patched for the timeline, RND seed). Its key is a hash of all of
         that, so a unit shared by several cells (R2 at start 0 is in six) runs once.
  Cell   the units a pre-registered test needs and the reader that applies its rule.
         A cell that cannot run says why (`blocked`) and is never silently skipped.
  Store  one JSON per unit, written as soon as the unit finishes: a killed run
         resumes where it stopped.

Start shifts: with a precompute, engine_fast.run ignores start_date/end_date and
loops over precomputed["trading_days"] (engine_fast.py, the day loop). A shifted
start is therefore the precompute with trading_days[k:], sharing the regime
timeline built once on the full window (the regime is a market state; only the
portfolio path moves, as in the 10-17 noise floor).

Run from apex-port-regime/ (prints the plan; nothing runs without --run):
    venv/bin/python -u -m backend.backtest.battery_2026_10 <outdir> [--cells a,b]
    venv/bin/python -u -m backend.backtest.battery_2026_10 <outdir> --run --ruling "<Niclas's words>"
    venv/bin/python -u -m backend.backtest.battery_2026_10 <outdir> --read
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

WINDOWS = {"IS": ("2019-01-02", "2026-10-16"), "OOS": ("2015-01-02", "2018-12-31")}
N_STARTS = 20
RND_SEEDS = range(1, 201)
EVENT_FOLDS = {
    "IS": {"F1": ("2026-03-02", "2026-04-30"), "F2": ("2026-05-01", "2026-06-30"),
           "F3": ("2026-07-01", "2026-09-11"), "COVID": ("2020-02-03", "2020-06-30"),
           "Y2022": ("2022-01-03", "2022-12-30")},
    "OOS": {"CRISIS_2015": ("2015-08-03", "2016-02-11"), "Q4_2018": ("2018-10-01", "2018-12-24")},
}
CAP_FOLDS = ("F1", "F2", "F3", "COVID", "Y2022")      # the 10-17 sweep's five folds
VIX9D_WINDOW = 63                                     # H-V1, locked 10-17

R0 = {"sl_cooldown_days": 5, "tp_cooldown_days": 0, "regime_layer": False}
R1 = {"regime_layer": False}
R2 = {"regime_layer": True}
VAULT_NOTES = "raw/notes/2026-10/"


# ── Units and their store ─────────────────────────────────────────────────────

def _frozen(d: dict | None) -> tuple:
    return tuple(sorted((d or {}).items()))


@dataclass(frozen=True)
class Unit:
    window: str
    start: int = 0
    kw: tuple = ()                 # overrides on the incumbent, _frozen(dict)
    regime: tuple = ()             # regime_bayes constants patched for the timeline
    rnd_ref: "Unit | None" = None  # RND: the unit whose per-day entry counts it copies
    seed: int | None = None
    vix9d_kelly: bool = False      # H-V1 scaler replaces half-Kelly's 0.50

    @property
    def kwargs(self) -> dict:
        return dict(self.kw)

    @property
    def regime_layer(self) -> bool:
        return bool(self.kwargs.get("regime_layer", False))

    def key(self) -> str:
        ref = self.rnd_ref.key() if self.rnd_ref else None
        raw = json.dumps([self.window, self.start, self.kw, self.regime, ref, self.seed, self.vix9d_kelly],
                         default=str)
        return hashlib.sha1(raw.encode()).hexdigest()[:12]


def unit(window: str, base: dict, start: int = 0, regime: dict | None = None,
         vix9d_kelly: bool = False, **over) -> Unit:
    return Unit(window, start, _frozen({**base, **over}), _frozen(regime), vix9d_kelly=vix9d_kelly)


def rnd_units(ref: Unit) -> list[Unit]:
    return [Unit(ref.window, ref.start, ref.kw, ref.regime, rnd_ref=ref, seed=s) for s in RND_SEEDS]


class Store:
    def __init__(self, outdir: Path):
        self.dir = outdir / "units"
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, u: Unit) -> Path:
        return self.dir / f"{u.window}_{u.key()}.json"

    def has(self, u: Unit) -> bool:
        return self.path(u).exists()

    def get(self, u: Unit) -> dict:
        p = self.path(u)
        if not p.exists():
            raise MissingUnit(u)
        return json.loads(p.read_text())

    def put(self, u: Unit, rec: dict) -> None:
        tmp = self.path(u).with_suffix(".tmp")
        tmp.write_text(json.dumps(rec, default=str))
        tmp.replace(self.path(u))


class MissingUnit(Exception):
    """A reader asked for a unit that has not run: the verdict says so, never a pass."""


# ── Cells ─────────────────────────────────────────────────────────────────────

@dataclass
class Cell:
    name: str
    note: str                                  # the pre-registration the rule comes from
    units: list[Unit] = field(default_factory=list)
    read: Callable[["Store"], dict] | None = None
    blocked: str | None = None                 # why it cannot run; reported, never skipped silently


def starts(n: int = N_STARTS) -> range:
    return range(n)


def _profit_read_units(w: str) -> list[Unit]:
    out = [unit(w, b, s) for b in (R0, R1, R2) for s in starts()]
    return out + rnd_units(unit(w, R2)) + rnd_units(unit(w, R1))


CAP_GRID = [0.15, 0.20, 0.25, 0.26, 0.27, 0.28, 0.29, 0.30, 0.35, 0.40, 0.50]   # lists: the registry pins JSON lists
RECOVERY = {"BASE": {}, "F10": {"POSTERIOR_CLAMP_FLOOR": 0.10}, "F15": {"POSTERIOR_CLAMP_FLOOR": 0.15},
            "D80": {"POSTERIOR_DECAY": 0.80}}
HV1_ARMS = {"V1a": {}, "V1b": {"vix_threshold": None}}
ETF_RATES = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30]
ETF_FLOORS = [-0.5, -1.0, -1.5, -2.0]
COOLOFF_00 = {"sl_cooldown_days": 0, "tp_cooldown_days": 0}


def build_cells() -> dict[str, Cell]:
    cells = [
        Cell("profit_read", VAULT_NOTES + "2026-10-16-apex-profit-read-preregistration.md + "
             "2026-10-17-apex-oos-profit-read-2015-2018-preregistration.md",
             _profit_read_units("IS") + _profit_read_units("OOS"), read_profit_read),
        Cell("noise_floor", VAULT_NOTES + "2026-10-17-apex-noise-floor-start-shift-and-vix-without-the-cut.md",
             [unit(w, b, s) for w in WINDOWS for b in (R1, R2) for s in starts()], read_noise_floor),
        Cell("sector_cap", VAULT_NOTES + "2026-10-17-apex-sector-cap-sweep-preregistration.md",
             [unit("IS", b, max_sector_exposure=c) for b in (R2, R1) for c in CAP_GRID]
             + [unit("OOS", b, max_sector_exposure=c) for b in (R2, R1) for c in (0.25, 0.30)], read_sector_cap),
        Cell("regime_weights_3b", VAULT_NOTES + "2026-10-17-apex-regime-weights-hypothesis-3b.md",
             [unit(w, R2, row2_neutral=n) for w in WINDOWS for n in (False, True)], read_3b),
        Cell("regime_recovery", VAULT_NOTES + "2026-10-17-apex-regime-slow-reopening-mechanism-test.md",
             [unit("IS", R2, s, regime=v) for v in RECOVERY.values() for s in starts()]
             + [unit("OOS", R2, regime=v) for v in RECOVERY.values()] + [unit("IS", R1)], read_recovery),
        Cell("vix_scaler_hv1", VAULT_NOTES + "2026-10-17-apex-vix-sizing-and-holdings-hypotheses.md",
             [unit("IS", R2, s, vix9d_kelly=bool(a), **arm) for a, arm in ((0, {}), *((1, x) for x in HV1_ARMS.values()))
              for s in starts()]
             + [unit("OOS", R2, vix9d_kelly=bool(a), **arm) for a, arm in ((0, {}), *((1, x) for x in HV1_ARMS.values()))],
             read_hv1),
        Cell("cooloff_mechanism", VAULT_NOTES + "2026-10-17-apex-cooloff-mechanism-and-exits-baseline-hypotheses.md",
             [unit(w, b, **COOLOFF_00) for w in WINDOWS for b in (R1, R2)]
             + rnd_units(unit("IS", R1)) + rnd_units(unit("OOS", R1)), read_cooloff),
        Cell("etf_penalty", VAULT_NOTES + "2026-10-17-apex-scope-for-sunday-2026-10-18.md (ETF 0.15 cell); "
             "criterion: etf_penalty_sweep._meets_criterion",
             [unit("IS", R2, etf_negative_penalty=None)]
             + [unit("IS", R2, etf_negative_penalty=r, etf_negative_floor=f) for r in ETF_RATES for f in ETF_FLOORS],
             read_etf),
        Cell("check80_counterfactual", VAULT_NOTES + "2026-10-15-apex-check80-first-read-rows-are-not-ticker-days.md",
             blocked="not an engine run: four threshold rules on gate-reached rows with simulated exits; "
                     "its reader is not written yet (battery step 2)"),
        Cell("position_size", VAULT_NOTES + "2026-10-17-apex-open-questions-after-the-saturday-session.md item 33",
             blocked="engine_fast has no sizing mode (only kelly_factor); L5-max / Kelly constant B / Kelly "
                     "realised B / fixed fraction need an engine parameter, and the cell's rule is not "
                     "pre-registered yet"),
        Cell("earnings_on_off", VAULT_NOTES + "2026-10-18-apex-earnings-rules-dead-in-live-since-install.md",
             blocked="historical earnings-date source 2015-2026 (row 9)"),
    ]
    return {c.name: c for c in cells}


# ── Metrics shared by the readers ─────────────────────────────────────────────

def _returns(rec: dict) -> pd.Series:
    from backend.backtest import profit_metrics as pm
    return pm.daily_returns(rec["equity_curve"])


def period(r: pd.Series, a: str, b: str) -> dict:
    from backend.backtest import profit_metrics as pm
    rr = pm.window(r, a, b)
    return {"total_return": float((1 + rr).prod() - 1) if len(rr) else None,
            "max_drawdown": pm.max_drawdown(rr) if len(rr) else None,
            "calmar": pm.calmar(rr) if len(rr) else None}


def years_of(r: pd.Series) -> dict[str, dict]:
    return {str(y): period(r, f"{y}-01-01", f"{y}-12-31") for y in sorted({d.year for d in r.index})}


def folds_of(r: pd.Series, window: str) -> dict[str, dict]:
    return {n: period(r, a, b) for n, (a, b) in EVENT_FOLDS[window].items()}


def _cal(rec: dict) -> float:
    c = rec["summary"]["calmar"]
    return float("nan") if c is None else float(c)


def _gt(a, b) -> bool:
    return a is not None and b is not None and not (math.isnan(a) or math.isnan(b)) and a > b


def years_ahead(a: dict, b: dict, metric: str = "calmar") -> tuple[int, int]:
    """Calendar years in which run a beats run b on `metric`, of the years both have."""
    ys = sorted(set(a["years"]) & set(b["years"]))
    return sum(_gt(a["years"][y][metric], b["years"][y][metric]) for y in ys), len(ys)


def paired(store: "Store", arm: Callable[[int], Unit], base: Callable[[int], Unit],
           metric: Callable[[dict], float] = _cal, n: int = N_STARTS) -> dict:
    d = np.array([metric(store.get(arm(s))) - metric(store.get(base(s))) for s in range(n)])
    se = float(d.std(ddof=1) / math.sqrt(n))
    return {"mean": float(d.mean()), "se": se, "t": float(d.mean() / se) if se > 0 else None,
            "better_in": int((d > 0).sum()), "n": n}


def block_bootstrap_calmar_diff(ra: pd.Series, rb: pd.Series, block: int = 21, draws: int = 2000,
                                seed: int = 0) -> dict:
    """Both series resampled jointly in blocks of `block` sessions (H-V1 rule 1b)."""
    from backend.backtest import profit_metrics as pm
    df = pd.concat({"a": ra, "b": rb}, axis=1, join="inner").dropna()
    n, rng = len(df), np.random.default_rng(seed)
    starts_ = np.arange(0, n - block + 1)
    out = []
    for _ in range(draws):
        idx = np.concatenate([np.arange(s, s + block) for s in rng.choice(starts_, size=math.ceil(n / block))])[:n]
        s = df.iloc[idx].reset_index(drop=True)
        s.index = df.index                                   # calendar only sets the annualisation
        ca, cb = pm.calmar(s["a"]), pm.calmar(s["b"])
        if ca is not None and cb is not None:
            out.append(ca - cb)
    lo, hi = np.percentile(out, [2.5, 97.5])
    return {"draws": len(out), "ci95": [float(lo), float(hi)], "mean": float(np.mean(out))}


def welch(a: list[float], b: list[float]) -> dict:
    a_, b_ = np.asarray(a, float), np.asarray(b, float)
    if len(a_) < 2 or len(b_) < 2:
        return {"n": len(a_), "diff": None, "ci95": None}
    d = a_.mean() - b_.mean()
    se = math.sqrt(a_.var(ddof=1) / len(a_) + b_.var(ddof=1) / len(b_))
    return {"n": len(a_), "diff": float(d), "ci95": [float(d - 1.96 * se), float(d + 1.96 * se)]}


def _rule(name: str, passed: bool | None, **detail) -> dict:
    return {"rule": name, "passed": passed, **detail}


# ── Readers: each applies its note's rule, as written before the run ──────────

def read_profit_read(store: Store) -> dict:
    out: dict = {}
    for w in WINDOWS:
        r0, r1, r2 = (store.get(unit(w, b)) for b in (R0, R1, R2))
        rnd2 = [store.get(u) for u in rnd_units(unit(w, R2))]
        rnd1 = [store.get(u) for u in rnd_units(unit(w, R1))]
        p95 = lambda rs: float(np.percentile([x["expectancy"]["usd"]["mean"] for x in rs], 95))
        e = lambda rec: rec["summary"]["expectancy"]["usd"]["mean"]
        mkt = r2["spy_em"]["tbill"]
        crisis = ("Y2022", "COVID") if w == "IS" else ("CRISIS_2015", "Q4_2018")
        rules = [
            _rule("signal edge R2: expectancy > RND-on-R2 p95", e(r2) > p95(rnd2), r2=e(r2), p95=p95(rnd2)),
            _rule("signal edge R1: expectancy > RND-on-R1 p95", e(r1) > p95(rnd1), r1=e(r1), p95=p95(rnd1)),
            _rule("regime layer: R2 Calmar >= R1 and R2 max DD < R1 in both crisis folds",
                  (_cal(r2) >= _cal(r1)) and all(r2["folds"][f]["max_drawdown"] < r1["folds"][f]["max_drawdown"] for f in crisis),
                  calmar=[_cal(r2), _cal(r1)], dd={f: [r2["folds"][f]["max_drawdown"], r1["folds"][f]["max_drawdown"]] for f in crisis}),
            _rule("against the market: R2 alpha > 0 with t >= 2 (T-bill) and Calmar > SPY-EM (T-bill)",
                  (r2["summary"]["vs_spy_tbill"]["alpha_annual"] or -1) > 0 and (r2["summary"]["vs_spy_tbill"]["t_alpha"] or 0) >= 2
                  and _gt(_cal(r2), mkt["calmar"]),
                  alpha=r2["summary"]["vs_spy_tbill"]["alpha_annual"], t=r2["summary"]["vs_spy_tbill"]["t_alpha"],
                  calmar=_cal(r2), spy_em=mkt["calmar"]),
        ]
        if w == "IS":
            rules.append(_rule("cooloff effect R1 - R0 (reported with its sign, no rule)", None, diff=_cal(r1) - _cal(r0)))
        else:
            ahead, of = years_ahead(r1, r0)
            rules.append(_rule("cooloffs: R1 Calmar > R0 and ahead in >= 3 of 4 years", _gt(_cal(r1), _cal(r0)) and ahead >= 3,
                               diff=_cal(r1) - _cal(r0), years=f"{ahead}/{of}"))
        beside = {"R2_over_R1_starts": sum(_gt(_cal(store.get(unit(w, R2, s))), _cal(store.get(unit(w, R1, s)))) for s in starts()),
                  "R1_over_R0_starts": sum(_gt(_cal(store.get(unit(w, R1, s))), _cal(store.get(unit(w, R0, s)))) for s in starts()),
                  "of": N_STARTS}
        out[w] = {"rules": rules, "across_starts (beside, no rule)": beside}
    out["read"] = "2019-2026 decides; 2015-2018 is the sign check (window weighting ruled 10-17)."
    return out


def read_noise_floor(store: Store) -> dict:
    out = {}
    for w in WINDOWS:
        row = {}
        for name, b in (("R1", R1), ("R2", R2)):
            c = np.array([_cal(store.get(unit(w, b, s))) for s in starts()])
            tr = np.array([store.get(unit(w, b, s))["summary"]["total_return"] for s in starts()])
            row[name] = {"calmar_mean": float(c.mean()), "calmar_sd": float(c.std(ddof=1)),
                         "calmar_range": [float(c.min()), float(c.max())],
                         "total_return_mean": float(tr.mean()), "total_return_sd": float(tr.std(ddof=1))}
        row["R1_over_R2_starts"] = sum(_gt(_cal(store.get(unit(w, R1, s))), _cal(store.get(unit(w, R2, s)))) for s in starts())
        row["readable_single_run_diff_R2"] = 2 * math.sqrt(2) * row["R2"]["calmar_sd"]
        row["readable_single_run_diff_R1"] = 2 * math.sqrt(2) * row["R1"]["calmar_sd"]
        out[w] = row
    out["read"] = "No rule: the floor is the yardstick for every single-run portfolio comparison."
    return out


def read_sector_cap(store: Store) -> dict:
    out: dict = {}
    for name, b in (("R2", R2), ("R1", R1)):
        recs = {c: store.get(unit("IS", b, max_sector_exposure=c)) for c in CAP_GRID}
        base = recs[0.30]
        others = [c for c in CAP_GRID if c != 0.30]
        rows = {}
        for i, c in enumerate(others):
            folds = sum(_gt(recs[c]["folds"][f]["calmar"], base["folds"][f]["calmar"]) for f in CAP_FOLDS)
            nbrs = [others[j] for j in (i - 1, i + 1) if 0 <= j < len(others)]
            rows[str(c)] = {"calmar": _cal(recs[c]), "beats_030": _gt(_cal(recs[c]), _cal(base)), "folds": f"{folds}/5",
                            "neighbours_beat_030": all(_gt(_cal(recs[n]), _cal(base)) for n in nbrs),
                            "passes": _gt(_cal(recs[c]), _cal(base)) and folds >= 4
                            and all(_gt(_cal(recs[n]), _cal(base)) for n in nbrs)}
        out[f"IS_{name}"] = {"calmar_030": _cal(base), "grid": rows, "decides": name == "R2"}
    a, b = store.get(unit("OOS", R2, max_sector_exposure=0.25)), store.get(unit("OOS", R2, max_sector_exposure=0.30))
    ahead, of = years_ahead(a, b)
    out["OOS"] = {"rules": [_rule("0.25 beats 0.30 on R2: window Calmar and >= 3 of 4 years",
                                  _gt(_cal(a), _cal(b)) and ahead >= 3, calmar=[_cal(a), _cal(b)], years=f"{ahead}/{of}")]}
    out["read"] = ("IS rule (10-17): beats 0.30 over the window, in >= 4 of 5 folds, and both grid neighbours "
                   "other than 0.30 beat 0.30 too; on R2. A pass is a bundle proposal, not a config change.")
    return out


def read_3b(store: Store) -> dict:
    out = {}
    for w in WINDOWS:
        a, b = store.get(unit(w, R2, row2_neutral=False)), store.get(unit(w, R2, row2_neutral=True))
        ahead, of = years_ahead(a, b)
        if w == "IS":
            r = _rule("H: R2 Calmar > R2-neutral over the window and in >= 6 of 8 years",
                      _gt(_cal(a), _cal(b)) and ahead >= 6, calmar=[_cal(a), _cal(b)], years=f"{ahead}/{of}")
        else:
            verdict = ("row 2 kept, switch-off withdrawn" if _gt(_cal(a), _cal(b)) and ahead >= 3 else
                       "switch-off stands, weak" if _gt(_cal(a), _cal(b)) else "switch-off stands")
            r = _rule("2015-2018: R2 vs R2-neutral (three outcomes)", None, verdict=verdict,
                      calmar=[_cal(a), _cal(b)], years=f"{ahead}/{of}")
        out[w] = {"rules": [r]}
    return out


def _first_allocation_after(rec: dict, day: str) -> str | None:
    for d in sorted(rec.get("regime_days", {})):
        if d > day and rec["regime_days"][d]["allocated"] > 0:
            return d
    return None


def read_recovery(store: Store) -> dict:
    r1_2022_dd = store.get(unit("IS", R1))["folds"]["Y2022"]["max_drawdown"]
    base = lambda s: unit("IS", R2, s, regime=RECOVERY["BASE"])
    oos_base = store.get(unit("OOS", R2, regime=RECOVERY["BASE"]))
    out = {"R1_2022_max_dd (criterion 2's bar)": r1_2022_dd}
    for name, v in RECOVERY.items():
        if name == "BASE":
            continue
        pr = paired(store, lambda s: unit("IS", R2, s, regime=v), base)
        dd22 = float(np.mean([store.get(unit("IS", R2, s, regime=v))["folds"]["Y2022"]["max_drawdown"] for s in starts()]))
        oos = _cal(store.get(unit("OOS", R2, regime=v))) - _cal(oos_base)
        out[name] = {"rules": [
            _rule("1: mean paired Calmar diff > 2 SE over 20 starts", pr["mean"] > 2 * pr["se"], **pr),
            _rule("2: 2022 max DD (mean over starts) below R1's 2022 DD", dd22 < r1_2022_dd, dd=dd22),
            _rule("3: 2015-2018 Calmar diff not of the opposite sign", not (pr["mean"] > 0 > oos), oos_diff=oos)],
            "beside": {"first_allocation_after_2020-03-23": _first_allocation_after(store.get(unit("IS", R2, regime=v)), "2020-03-23")}}
    out["read"] = "A variant passes on all three. None passes: recovery speed is not the binding problem (HMM deprioritised)."
    return out


def read_hv1(store: Store) -> dict:
    base = lambda s: unit("IS", R2, s)
    out = {}
    for name, arm in HV1_ARMS.items():
        a = lambda s, arm=arm: unit("IS", R2, s, vix9d_kelly=True, **arm)
        pr = paired(store, a, base)
        a0, b0 = store.get(a(0)), store.get(base(0))
        bs = block_bootstrap_calmar_diff(_returns(a0), _returns(b0))
        oos = _cal(store.get(unit("OOS", R2, vix9d_kelly=True, **arm))) - _cal(store.get(unit("OOS", R2)))
        dd_ok = all(a0["folds"][f]["max_drawdown"] <= b0["folds"][f]["max_drawdown"] for f in ("COVID", "Y2022"))
        out[name] = {"rules": [
            _rule("a: mean paired Calmar diff > 2 SE over 20 starts", pr["mean"] > 2 * pr["se"], **pr),
            _rule("b: block bootstrap (21 sessions, 2,000 draws) Calmar diff CI above 0", bs["ci95"][0] > 0, **bs),
            _rule("c: COVID and 2022 max DD not above baseline", dd_ok,
                  dd={f: [a0["folds"][f]["max_drawdown"], b0["folds"][f]["max_drawdown"]] for f in ("COVID", "Y2022")}),
            _rule("d: 2015-2018 Calmar diff not of the opposite sign", not (pr["mean"] > 0 > oos), oos_diff=oos)],
            "vix9d_missing_days": a0.get("vix9d_missing_days")}
    out["read"] = "Each arm needs all four. Rule b reads 'CI excludes 0' on the side the arm claims (above)."
    return out


def _reentry_classes(trades: list[dict], days: list[str]) -> dict[str, list[dict]]:
    """Each entry classed by the last exit in the same ticker (10-17 design): win (TP, or TSL with
    pnl > 0), loss (SL, or TSL with pnl <= 0), TIME; bins in sessions 0 / 1-3 / 4-7 / 8-14 / 15-30;
    no exit in the last 30 sessions = fresh."""
    pos = {d: i for i, d in enumerate(days)}
    bins = ((0, 0, "0"), (1, 3, "1-3"), (4, 7, "4-7"), (8, 14, "8-14"), (15, 30, "15-30"))
    exits: dict[str, list[tuple[int, str, int]]] = {}
    for n, t in enumerate(trades):
        if not t.get("exit_date") or t.get("pnl") is None:
            continue
        r = t["exit_reason"]
        cls = ("win" if r == "TP" or (r == "TSL" and t["pnl"] > 0) else
               "loss" if r in ("SL", "TSL") else "TIME" if r == "TIME" else None)
        if cls and t["exit_date"] in pos:
            exits.setdefault(t["ticker"], []).append((pos[t["exit_date"]], cls, n))
    out: dict[str, list[dict]] = {"fresh": []}
    for n, t in enumerate(trades):
        if t.get("pnl_pct") is None or t["entry_date"] not in pos:
            continue
        i = pos[t["entry_date"]]
        # Exits run before entries in the engine's day, so an exit on the entry day is prior
        # (the 0 bin) unless it is this trade's own same-day exit.
        prior = [(j, c) for j, c, m in exits.get(t["ticker"], []) if j <= i and m != n]
        last = max(prior, default=None)
        gap = i - last[0] if last else None
        if last is None or gap > 30:
            out["fresh"].append(t)
            continue
        label = next(name for lo, hi, name in bins if lo <= gap <= hi)
        out.setdefault(f"{last[1]}_{label}", []).append(t)
    return out


def read_cooloff(store: Store) -> dict:
    from backend.backtest import profit_metrics as pm
    out: dict = {}
    pct = lambda ts: [t["pnl_pct"] for t in ts]
    for w in WINDOWS:
        for name, b in (("R1", R1), ("R2", R2)):
            rec = store.get(unit(w, b, **COOLOFF_00))
            cls = _reentry_classes(rec["trade_log"], rec["trading_days"])
            fresh = pct(cls["fresh"])
            row = {"n_fresh": len(fresh), "fresh_mean": float(np.mean(fresh)) if fresh else None}
            for k in sorted(cls):
                if k != "fresh":
                    row[k] = {**welch(pct(cls[k]), fresh),
                              "stop_share": float(np.mean([t["exit_reason"] in ("SL", "TSL") for t in cls[k]]))}
            row["win_1-7"] = welch(pct(cls.get("win_1-3", []) + cls.get("win_4-7", [])), fresh)
            out[f"{w}_{name}"] = row
    is_, oos = out["IS_R1"], out["OOS_R1"]
    ci = lambda x: x.get("ci95") or [float("nan")] * 2
    rules = [
        _rule("H-TP: win re-entries 1-7 below fresh, CI below 0 on 2019-2026, same sign 2015-2018",
              ci(is_["win_1-7"])[1] < 0 and (oos["win_1-7"]["diff"] or 0) < 0, IS=is_["win_1-7"], OOS=oos["win_1-7"]),
        _rule("H-SL: loss re-entries 1-3: CI includes 0 or above on 2019-2026; not significantly negative 2015-2018",
              ci(is_.get("loss_1-3", {}))[1] >= 0 and not ci(oos.get("loss_1-3", {}))[1] < 0,
              IS=is_.get("loss_1-3"), OOS=oos.get("loss_1-3")),
    ]
    ha = {}
    for w in WINDOWS:
        seeds = [store.get(u) for u in rnd_units(unit(w, R1))]
        ha[w] = {"median_alpha_tbill": float(np.median([s["alpha_tbill"] for s in seeds])),
                 "share_calmar_over_spy_em": float(np.mean([_gt(s["calmar"], s["spy_em_calmar"]) for s in seeds]))}
    rules.append(_rule("H-A: median RND-on-R1 alpha > 0 (T-bill) and >= 80 % of seeds above SPY-EM Calmar; same sign 2015-2018",
                       ha["IS"]["median_alpha_tbill"] > 0 and ha["IS"]["share_calmar_over_spy_em"] >= 0.8
                       and ha["OOS"]["median_alpha_tbill"] > 0, **ha))
    out["rules"] = rules
    out["read"] = ("R1 decides, R2 beside. The 0-session bin is reported separately (declared deviation 10-17); "
                   "the rules read the bins as written. A candidate length goes to a portfolio confirmation run, "
                   "never straight to config.")
    return out


def read_etf(store: Store) -> dict:
    from backend.backtest.etf_penalty_sweep import _meets_criterion
    null = store.get(unit("IS", R2, etf_negative_penalty=None))["engine"]
    grid = {}
    for r in ETF_RATES:
        row = {}
        for f in ETF_FLOORS:
            e = store.get(unit("IS", R2, etf_negative_penalty=r, etf_negative_floor=f))["engine"]
            row[str(f)] = {"profit_factor": e["profit_factor"], "cagr": e["cagr"], "max_drawdown": e["max_drawdown"],
                           "meets": _meets_criterion(e, null)}
        grid[str(r)] = {"floors": row, "floors_passing": sum(v["meets"] for v in row.values())}
    return {"null": null, "grid": grid,
            "read": ("07-17 criterion vs no penalty: PF +2 %, or CAGR +0.5 pp with max DD no worse than +1 pp. "
                     "Prefer a rate that passes across floors over the single best cell (the sweep's own docstring). "
                     "Live 0.15 / -1.0 stays until this is read.")}


# ── Running ───────────────────────────────────────────────────────────────────

@contextlib.contextmanager
def regime_constants(patch: dict):
    from backend.regime import regime_bayes as rb
    old = {k: getattr(rb, k) for k in patch}
    try:
        for k, v in patch.items():
            setattr(rb, k, v)
        yield
    finally:
        for k, v in old.items():
            setattr(rb, k, v)


def vix9d_kelly_factor(close: pd.Series, days: list[str]) -> tuple[dict[str, float], int]:
    """k(t) = 0.75 - 0.50 * pct(t), pct = rank of the day's ^VIX9D close among the last 63
    sessions (0 lowest, 1 highest; ties at their mid-rank). A day without a close keeps the
    engine's 0.50 and is counted."""
    c = close.dropna()

    def _pct(w: np.ndarray) -> float:
        x = w[-1]
        return ((w < x).sum() + 0.5 * ((w == x).sum() - 1)) / (len(w) - 1)
    pct = c.rolling(VIX9D_WINDOW).apply(_pct, raw=True).dropna()
    by_day = {d.date().isoformat(): float(0.75 - 0.50 * p) for d, p in pct.items()}
    k = {d: by_day[d] for d in days if d in by_day}
    return k, len(days) - len(k)


class Runner:
    def __init__(self, store: Store, commit: str):
        self.store, self.commit = store, commit
        self.pre: dict[str, dict] = {}
        self.market: dict[str, dict] = {}
        self.timelines: dict[tuple, dict] = {}
        self.inc: dict | None = None

    def _window(self, w: str) -> dict:
        if w not in self.pre:
            import yfinance as yf
            from backend.backtest.engine_fast import precompute
            a, b = WINDOWS[w]
            t0 = time.time()
            self.pre[w] = precompute(a, b)
            print(f"[{w}] precompute {time.time() - t0:.0f}s, {len(self.pre[w]['trading_days'])} sessions", flush=True)
            end = (pd.Timestamp(b) + pd.Timedelta(days=1)).date().isoformat()
            irx = yf.download("^IRX", start=a, end=end, progress=False, auto_adjust=False)
            v9 = yf.download("^VIX9D", start=(pd.Timestamp(a) - pd.Timedelta(days=130)).date().isoformat(),
                             end=end, progress=False, auto_adjust=False)
            from backend.backtest import profit_metrics as pm
            irx_c = irx["Close"].squeeze() if not irx.empty else pd.Series(dtype=float)
            self.market[w] = {"spy": self.pre[w]["raw_data"]["SPY"]["Close"].dropna(),
                              "rf": (irx_c / 100 / pm.TRADING_DAYS).rename("rf"),
                              "vix9d": v9["Close"].squeeze() if not v9.empty else pd.Series(dtype=float)}
        return self.pre[w]

    def _timeline(self, w: str, regime: tuple) -> dict:
        from backend.backtest.engine_fast import build_regime_timeline
        if (w, regime) not in self.timelines:
            pre = {k: v for k, v in self._window(w).items() if k != "regime_timeline"}
            with regime_constants(dict(regime)):
                self.timelines[(w, regime)] = build_regime_timeline(pre)
        return self.timelines[(w, regime)]

    def incumbent(self) -> dict:
        if self.inc is None:
            from backend.backtest.profit_read_2026_10 import incumbent_from_live
            self.inc = incumbent_from_live()
        return self.inc

    def run(self, u: Unit) -> dict:
        from backend.backtest import profit_metrics as pm
        from backend.backtest.engine_fast import run
        from backend.backtest.gap_bounds import open_rows
        pre = self._window(u.window)
        mk = self.market[u.window]
        days = pre["trading_days"][u.start:]
        pre_u = {**pre, "trading_days": days}
        if u.regime_layer:
            pre_u["regime_timeline"] = self._timeline(u.window, u.regime)
        kw = {**self.incumbent(), **u.kwargs}
        extra, missing = {}, None
        if u.vix9d_kelly:
            extra["kelly_factor"], missing = vix9d_kelly_factor(mk["vix9d"], [d.isoformat() for d in days])
        if u.rnd_ref is not None:
            extra["random_entries"] = {"seed": u.seed, "per_day": self.store.get(u.rnd_ref)["entries_per_day"]}
        with regime_constants(dict(u.regime)):
            res = run(days[0].isoformat(), WINDOWS[u.window][1], initial_balance=10_000.0,
                      precomputed=pre_u, **kw, **extra)
        r = pm.daily_returns(res["equity_curve"])
        closed = [t for t in res["trade_log"] if t.get("pnl") is not None]
        if u.rnd_ref is not None:
            dep = pm.deployed_fraction(res["trade_log"], res["equity_curve"])
            em = pm.spy_em_returns(mk["spy"], r.index, float(dep.mean()) if len(dep) else 0.0, mk["rf"])
            m = mk["spy"].pct_change().reindex(r.index)
            return {"unit": u.key(), "seed": u.seed, "commit": self.commit, "trades": len(closed),
                    "expectancy": pm.expectancy(closed), "total_return": float((1 + r).prod() - 1),
                    "max_drawdown": pm.max_drawdown(r), "calmar": pm.calmar(r),
                    "alpha_tbill": pm.alpha_beta(r, m, mk["rf"])["alpha_annual"], "spy_em_calmar": pm.calmar(em)}
        summ = pm.summary(res, mk["spy"], 0.0)
        summ["vs_spy_tbill"] = pm.summary(res, mk["spy"], mk["rf"])["vs_spy"]
        rec = {"unit": u.key(), "window": u.window, "start": u.start, "first_day": days[0].isoformat(),
               "kwargs": kw, "regime_patch": dict(u.regime), "vix9d_kelly": u.vix9d_kelly,
               "vix9d_missing_days": missing, "commit": self.commit,
               "gaps": open_rows(regime_layer=u.regime_layer),
               "summary": summ, "years": years_of(r), "folds": folds_of(r, u.window),
               "engine": {k: res[k] for k in ("profit_factor", "cagr", "max_drawdown", "total_trades", "win_rate")},
               "entries_per_day": _entries_per_day(res["trade_log"]),
               "trading_days": [d.isoformat() for d in days],
               "equity_curve": res["equity_curve"], "trade_log": res["trade_log"]}
        if u.regime_layer:
            tl = pre_u["regime_timeline"]["days"]
            rec["regime_days"] = {d: {"bucket": v["bucket"], "allocated": sum(1 for a in v["allocation"].values() if a > 0)}
                                  for d, v in tl.items()}
        if u.start == 0 and u.rnd_ref is None:
            frac = summ["mean_deployed"]
            rec["spy_em"] = {}
            for leg, cash in (("cash0", 0.0), ("tbill", mk["rf"])):
                er = pm.spy_em_returns(mk["spy"], r.index, frac, cash)
                rec["spy_em"][leg] = {"fraction": frac, "total_return": float((1 + er).prod() - 1),
                                      "max_drawdown": pm.max_drawdown(er), "calmar": pm.calmar(er)}
        return rec


def _entries_per_day(trade_log: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in trade_log:
        out[t["entry_date"]] = out.get(t["entry_date"], 0) + 1
    return out


def ordered(units: list[Unit]) -> list[Unit]:
    """Unique units, references before the RND units that copy their entry counts, window by window."""
    seen: dict[str, Unit] = {}
    for u in units:
        if u.rnd_ref is not None:
            seen.setdefault(u.rnd_ref.key(), u.rnd_ref)
        seen.setdefault(u.key(), u)
    return sorted(seen.values(), key=lambda u: (u.window, u.rnd_ref is not None, u.key()))


def git_state() -> tuple[str, bool]:
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                                capture_output=True, text=True).stdout.strip())
    return head, dirty


def plan(cells: dict[str, Cell], store: Store) -> dict:
    out = {}
    for c in cells.values():
        u = ordered(c.units)
        out[c.name] = {"units": len(u), "done": sum(store.has(x) for x in u), "blocked": c.blocked, "note": c.note}
    allu = ordered([u for c in cells.values() for u in c.units])
    out["_total"] = {"unique_units": len(allu), "done": sum(store.has(x) for x in allu),
                     "rnd_units": sum(x.rnd_ref is not None for x in allu)}
    return out


def read_all(cells: dict[str, Cell], store: Store, outdir: Path) -> dict:
    verdicts = {}
    for c in cells.values():
        if c.blocked:
            v = {"blocked": c.blocked}
        else:
            try:
                v = c.read(store)
            except MissingUnit as e:
                v = {"incomplete": f"unit {e.args[0].key()} ({e.args[0].window}) has not run"}
        v["note"] = c.note
        verdicts[c.name] = v
        (outdir / "verdicts").mkdir(exist_ok=True)
        (outdir / "verdicts" / f"{c.name}.json").write_text(json.dumps(v, indent=1, default=str))
    return verdicts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir", type=Path)
    ap.add_argument("--cells", default="")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--ruling", default="")
    ap.add_argument("--read", action="store_true")
    a = ap.parse_args(argv)
    cells = build_cells()
    if a.cells:
        cells = {k: cells[k] for k in a.cells.split(",")}
    store = Store(a.outdir)
    print(json.dumps(plan(cells, store), indent=1), flush=True)
    if a.run:
        if not a.ruling.strip():
            print("refused: --run needs --ruling with the words that ruled the run (the battery is frozen until ruled)")
            return 2
        head, dirty = git_state()
        if dirty:
            print("refused: tracked files are modified; a result must name the code it ran on")
            return 2
        from backend.backtest.gap_bounds import describe
        print(describe(regime_layer=True), flush=True)
        runner = Runner(store, head)
        (a.outdir / "manifest.json").write_text(json.dumps(
            {"commit": head, "ruling": a.ruling, "started": pd.Timestamp.now(tz="Europe/Stockholm").isoformat(),
             "incumbent": runner.incumbent(), "windows": WINDOWS, "cells": list(cells)}, indent=1))
        todo = [u for u in ordered([u for c in cells.values() for u in c.units]) if not store.has(u)]
        t0 = time.time()
        for i, u in enumerate(todo, 1):
            store.put(u, runner.run(u))
            if i % 10 == 0 or i == len(todo):
                print(f"{i}/{len(todo)} units, {time.time() - t0:.0f}s", flush=True)
    if a.run or a.read:
        print(json.dumps(read_all(cells, store, a.outdir), indent=1, default=str), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
