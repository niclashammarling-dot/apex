"""APEX exits card T8, 2026-10-22: entry population x exit arm, at L2 and L3, as written before any code.

Why: ladder T4 found live's bracket losing to a random exit time of the same length, and fixed-time
exits best per capital-day (IS 0.103 against 0.055 %/day, polled). T4 was L2 only and its OOS
advantage shrank to 0.039 against 0.030. The card (question, arms, populations, nulls, gates,
decision rule a-f, amendments A-J) is vault raw/notes/2026-10/2026-10-20-apex-exits-card.md,
committed (vault 3e560e5e) before this module. Niclas 2026-10-21: "make the t8 amendment, and make
it count"; "exit is important, catching as much of the peak as possible ... It's the combination
of them that determines the strategy". Exploratory for config: a passing arm proposes a shadow book,
never a live change.

How: python -u -m backend.backtest.exits_t8_2026_10 OUTDIR [--levels L2,L3] [--windows IS,OOS]
     [--seeds 100] [--workers 3] [--battery /home/promenix/apex-battery/2026-10-19] [--precheck-only]
Order (card § Order): the SL pre-check first, and the run stops (exit 4) on an unexplained D3; then
L2 per window, then L3. Each output is saved as it finishes.

What: OUTDIR/precheck.json; L2_<window>.json (stats, paired tests, power, controls) and
L2_<window>_rows.npz (per-trade rows, T4's open gap); L3.json. The reader prints power before any
arm-against-LIVE number (amendment J).
"""
from __future__ import annotations

import argparse
import functools
import io
import json
import math
import multiprocessing as mp
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from backend.backtest import battery_2026_10 as b
from backend.backtest import engine_fast as ef
from backend.backtest import ladder_2026_10 as ld
from backend.backtest import profit_metrics as pm
from backend.backtest.archive_guard import IdenticalArms
from backend.backtest.exit_rules import POLLED_PEAK_GAP, STOP_SLIP_GAP_PCT

NONE = ld.NONE
HOLD_MAX = 25
SEED = 20261022
N_LIVE_RND = 1000                     # archived p-values: >= 1,000 draws (ruling 2026-10-20)
N_CONTROL = 200
N_BOOT = 1000
COSTS_BP = [0, 5, 10, 20]
COST_GATE_BP = 10                     # amendment E
RETENTION_BAR = 0.5                   # amendment J, chosen, not derived
POSITIVE_SHIFT = 0.0002               # item 9: +0.02 %/day
YEAR_2022 = ("2022-01-03", "2022-12-30")
PL = [0.04, 0.01]

# Card item 2 and amendment C. "atr": (tp, sl) multiples of the entry close's atr_pct; "k" is ATRSYM's.
ARMS: dict[str, dict] = {
    "LIVE":   {"tp": 0.06, "sl": 0.06, "pl": PL, "tdays": 25},
    "TIME5":  {"tp": NONE, "sl": NONE, "pl": None, "tdays": 5},
    "TIME10": {"tp": NONE, "sl": NONE, "pl": None, "tdays": 10},
    "TIME25": {"tp": NONE, "sl": NONE, "pl": None, "tdays": 25},
    "NOSTOP": {"tp": 0.06, "sl": NONE, "pl": None, "tdays": 25},
    "ATR":    {"atr": (2.0, 1.0), "pl": PL, "tdays": 25},
    "ATRSYM": {"atr": ("k", "k"), "pl": PL, "tdays": 25},
    "TRAIL2": {"tp": NONE, "sl": 0.06, "pl": [0.04, 0.02], "tdays": 25},
    "TRAIL3": {"tp": NONE, "sl": 0.06, "pl": [0.04, 0.03], "tdays": 25},
    "TP4NS":  {"tp": 0.04, "sl": NONE, "pl": None, "tdays": 25},
}
NON_LIVE = [a for a in ARMS if a != "LIVE"]
# Amendment B's symmetric no-lock rate at the arm's width: reference rows, not arms, never judged.
REFERENCE = {"SYM4": {"tp": 0.04, "sl": 0.04, "pl": None, "tdays": 25},
             "SYM6": {"tp": 0.06, "sl": 0.06, "pl": None, "tdays": 25}}
SYM_OF = {"LIVE": "SYM6", "NOSTOP": "SYM6", "TRAIL2": "SYM6", "TRAIL3": "SYM6", "TP4NS": "SYM4"}
LABELS = {"TP4NS": "selected from T4's grid after reading"}
PEAKS = ("close", "high", "polled")
DECIDING_PEAK = "polled"
POPS = ("picks", "matched", "b5", "live57")
REASONS = ("TP", "SL", "TSL", "TIME", "END")

_SHARED: dict = {}


def has_lock(cfg: dict) -> bool:
    return bool(cfg.get("pl"))


def peaks_for(cfg: dict) -> tuple[str, ...]:
    """The peak model reaches an exit only through the lock (exit_rules.resting_stop reads the peak
    only when the ratchet is active), so a lock-free arm runs once and is the same under all three
    (tests/test_exits_t8_2026_10.py asserts it)."""
    return PEAKS if has_lock(cfg) else (DECIDING_PEAK,)


def atrsym_k(P_is: dict) -> float:
    """Card item 2: k = 0.06 / median atr_pct of the IS picks, fixed before any OOS read (D5)."""
    E = ld.entry_sets(P_is)
    atr = P_is["atr"].to_numpy()
    return 0.06 / float(np.median([atr[i, j] for i, j in E["picks"]]))


def entry_cfg(arm: str, atr_pct: float, k: float) -> dict:
    spec = ARMS.get(arm) or REFERENCE[arm]
    if "atr" not in spec:
        return spec
    m = [k if x == "k" else x for x in spec["atr"]]
    if not atr_pct or not np.isfinite(atr_pct) or atr_pct <= 0:     # the engine's rule: no ATR, no ATR exit
        return {"tp": 0.06, "sl": 0.06, "pl": spec["pl"], "tdays": spec["tdays"]}
    return {"tp": m[0] * atr_pct, "sl": m[1] * atr_pct, "pl": spec["pl"], "tdays": spec["tdays"]}


# ── Populations ────────────────────────────────────────────────────────────────

def purge(E: list, n_days: int) -> tuple[list, int]:
    """Amendment F: an entry whose 25-day maximum hold reaches past the window's last day is dropped.
    OOS (2015-2018) precedes IS, so these are OOS's tail into IS and IS's tail into the present."""
    keep = [(i, j) for i, j in E if i + HOLD_MAX <= n_days - 1]
    return keep, len(E) - len(keep)


def b5_random(P: dict, picks: list, rng: np.random.Generator) -> list:
    """B5: per pick, one uniformly random other pool ticker on the same day (no ATR matching)."""
    m = P["mask"].to_numpy()
    out = []
    for i, j in picks:
        cand = np.flatnonzero(m[i])
        cand = cand[cand != j]
        out.append((i, int(rng.choice(cand))))
    return out


def live57(P: dict, rows: list[dict], rng: np.random.Generator) -> dict:
    """Amendment A4: the canonical live sample re-entered at its decision close, each with N_LIVE_RND
    same-day, same-ATR-quintile random draws (live_vs_engine_2026_10_20's method), drawn once and
    shared by every arm (common random numbers)."""
    days, tick = P["days"], P["tickers"]
    q = ld.quintile(P["atr"], P["mask"]).to_numpy()
    m = P["mask"].to_numpy()
    E, draws, ids, skipped, truncated = [], [], [], [], []
    for t in rows:
        d = t["timestamp"][:10]
        if d not in days or t["ticker"] not in tick:
            skipped.append({"id": t["id"], "why": "outside the window or pool"})
            continue
        i, j = days.index(d), tick.index(t["ticker"])
        if i + 1 > len(days) - 1:
            skipped.append({"id": t["id"], "why": "no later day"})
            continue
        cand = np.flatnonzero(m[i] & (q[i] == q[i, j])) if m[i, j] else np.array([], dtype=int)
        cand = cand[cand != j]
        if len(cand) == 0:
            cand = np.flatnonzero(m[i])
            cand = cand[cand != j]
        if i + HOLD_MAX > len(days) - 1:
            truncated.append(t["id"])
        E.append((i, j))
        draws.append(rng.choice(cand, N_LIVE_RND))
        ids.append(t["id"])
    return {"E": E, "draws": draws, "ids": ids, "skipped": skipped, "truncated": truncated}


# ── Simulation ─────────────────────────────────────────────────────────────────

def simulate(P: dict, pre: dict, i: int, j: int, arm: str, peak: str, k: float) -> dict:
    cfg = entry_cfg(arm, float(P["atr"].iat[i, j]), k)
    before = ld.PEAK
    ld.PEAK = ld.PEAK_MODELS[peak]
    try:
        return ld._simulate(P, pre, i, j, cfg)
    finally:
        ld.PEAK = before


def _job(job: tuple) -> tuple:
    arm, peak, pop = job
    P, pre, k, E = _SHARED["P"], _SHARED["pre"], _SHARED["k"], _SHARED["E"]
    if pop == "live57_rnd":
        L = _SHARED["L"]
        out = []
        for (i, _), dr in zip(L["E"], L["draws"]):
            cache: dict[int, float] = {}
            for c in np.unique(dr):
                cache[int(c)] = simulate(P, pre, i, int(c), arm, peak, k)["pnl"]
            out.append(np.array([cache[int(c)] for c in dr]))
        return arm, peak, pop, out
    return arm, peak, pop, [simulate(P, pre, i, j, arm, peak, k) for i, j in E[pop]]


# ── Statistics ─────────────────────────────────────────────────────────────────

def p_one_sided(t: float | None) -> float | None:
    """P(T >= t) under the normal approximation (months >= 40 in both windows)."""
    return None if t is None else 0.5 * math.erfc(t / math.sqrt(2))


def holm(ps: dict[str, float | None], alpha: float = 0.05) -> dict[str, bool]:
    items = sorted(((p, a) for a, p in ps.items() if p is not None))
    out, m, stop = {a: False for a in ps}, len(items), False
    for r, (p, a) in enumerate(items):
        if stop or p > alpha / (m - r):
            stop = True
            continue
        out[a] = True
    return out


def month_of(P: dict, E: list) -> np.ndarray:
    return np.array([P["days"][i][:7] for i, _ in E])


def capital_day_by(keys: np.ndarray, pnl: np.ndarray, hold: np.ndarray) -> pd.Series:
    df = pd.DataFrame({"k": keys, "p": pnl, "h": np.maximum(hold, 1)})
    g = df.groupby("k")
    return g["p"].sum() / g["h"].sum()


def paired_monthly(P: dict, E: list, a: list[dict], ref: list[dict]) -> dict:
    """Card item 10's paired monthly test on the goal statistic: per entry month, the arm's return
    per capital-day minus the reference's on the same entries; t over months, one-sided p (arm > ref)."""
    mo = month_of(P, E)
    d = (capital_day_by(mo, _pnl(a), _hold(a)) - capital_day_by(mo, _pnl(ref), _hold(ref))).dropna()
    out = ld.nonoverlap(d, 1)
    out["p_one_sided"] = p_one_sided(out["t"])
    out["series"] = {str(k): float(v) for k, v in d.items()}
    return out


def _pnl(rows: list[dict]) -> np.ndarray:
    return np.array([r["pnl"] for r in rows], dtype=float)


def _hold(rows: list[dict]) -> np.ndarray:
    return np.array([r["hold"] for r in rows], dtype=float)


def mfe_panel(P: dict, E: list) -> dict:
    """Per entry: the 25-day MFE from the highs (amendment B, the entry's peak), and the arrays the
    capture statistics need."""
    hi, cl = P["high"].to_numpy(), P["close"].to_numpy()
    mfe25 = np.full(len(E), np.nan)
    for n, (i, j) in enumerate(E):
        seg = hi[i + 1:i + 1 + HOLD_MAX, j]
        if not np.all(np.isnan(seg)):
            mfe25[n] = np.nanmax(seg) / cl[i, j] - 1
    return {"mfe25": mfe25}


def peak_stats(mfe25: np.ndarray) -> dict:
    x = mfe25[np.isfinite(mfe25)]
    return {"n": int(len(x)), "share_ge_4": float((x >= 0.04).mean()), "share_ge_6": float((x >= 0.06).mean()),
            "median": float(np.median(x))}


def capture(P: dict, E: list, rows: list[dict], mfe25: np.ndarray) -> dict:
    """Amendment B: realised / MFE over the trade's own hold (MFE > 0), and realised / 25-day MFE."""
    hi, cl = P["high"].to_numpy(), P["close"].to_numpy()
    own, full = [], []
    for n, ((i, j), r) in enumerate(zip(E, rows)):
        seg = hi[i + 1:r["exit_idx"] + 1, j]
        if len(seg) and not np.all(np.isnan(seg)):
            m = np.nanmax(seg) / cl[i, j] - 1
            if m > 0:
                own.append(r["pnl"] / m)
        if np.isfinite(mfe25[n]) and mfe25[n] > 0:
            full.append(r["pnl"] / mfe25[n])
    f = lambda a: {"n": len(a), "median": float(np.median(a)) if a else None, "mean": float(np.mean(a)) if a else None}
    return {"own_hold": f(own), "mfe25": f(full)}


def costs(rows: list[dict]) -> dict:
    """Amendment E: round trips per capital-day and the net goal statistic per cost."""
    pnl, hold = _pnl(rows), np.maximum(_hold(rows), 1)
    H = hold.sum()
    return {"round_trips_per_capital_day": float(len(rows) / H),
            "net_capital_day": {str(c): float((pnl.sum() - len(rows) * c / 1e4) / H) for c in COSTS_BP}}


def breakeven_bp(arm: list[dict], ref: list[dict]) -> float | None:
    """The cost per round trip (bp) at which the arm's capital-day advantage over the reference vanishes;
    None when the arm trades no more often (its advantage then never vanishes) or has none."""
    sa, ha, na = _pnl(arm).sum(), np.maximum(_hold(arm), 1).sum(), len(arm)
    sr, hr, nr = _pnl(ref).sum(), np.maximum(_hold(ref), 1).sum(), len(ref)
    adv, dturn = sa / ha - sr / hr, na / ha - nr / hr
    if adv <= 0 or dturn <= 0:
        return None
    return float(adv / dturn * 1e4)


def post_stop(P: dict, E: list, rows: list[dict], time25: list[dict]) -> dict:
    """Card item 4 (Kaminski & Lo): for each SL/TSL exit, the return from the exit fill to the close
    on the day TIME25 from the same entry exits. Negative: the stop saved money; positive: it cut a
    recovery."""
    cl = P["close"].to_numpy()
    out = {}
    for reason in ("SL", "TSL"):
        x = [cl[t["exit_idx"], j] / (cl[i, j] * (1 + r["pnl"])) - 1
             for (i, j), r, t in zip(E, rows, time25) if r["reason"] == reason and t["exit_idx"] > r["exit_idx"]]
        out[reason] = {"n": len(x), "mean": float(np.mean(x)) if x else None,
                       "t": float(np.mean(x) / (np.std(x, ddof=1) / math.sqrt(len(x)))) if len(x) > 2 and np.std(x) > 0 else None}
    return out


def tail(rows: list[dict], P: dict, E: list) -> dict:
    """Amendment C's loss tail, read for every arm: worst 1 % / 5 % of trades and the MAE 10th pct."""
    pnl = _pnl(rows)
    lo, cl = P["low"].to_numpy(), P["close"].to_numpy()
    mae = [np.nanmin(lo[i + 1:r["exit_idx"] + 1, j]) / cl[i, j] - 1 for (i, j), r in zip(E, rows)
           if r["exit_idx"] > i and not np.all(np.isnan(lo[i + 1:r["exit_idx"] + 1, j]))]
    return {"p1": float(np.percentile(pnl, 1)), "p5": float(np.percentile(pnl, 5)),
            "mae_p10": float(np.percentile(mae, 10)) if mae else None}


def year_table(P: dict, E: list, a: list[dict], ref: list[dict]) -> dict:
    """Amendment J, beside: the capital-day advantage per calendar year of entry."""
    yr = np.array([P["days"][i][:4] for i, _ in E])
    d = (capital_day_by(yr, _pnl(a), _hold(a)) - capital_day_by(yr, _pnl(ref), _hold(ref))).dropna()
    return {"by_year": {str(k): float(v) for k, v in d.items()}, "years_positive": int((d > 0).sum()), "years": len(d)}


def subset(P: dict, E: list, rows: list[dict], lo: str, hi: str) -> tuple[list, list]:
    keep = [n for n, (i, _) in enumerate(E) if lo <= P["days"][i] <= hi]
    return [E[n] for n in keep], [rows[n] for n in keep]


# ── Controls and power (items 9, 10; amendments H, J) ─────────────────────────

def negative_control(P: dict, E: list, live: list[dict], rng: np.random.Generator) -> dict:
    """Item 9: LIVE against itself. Entries split at random into two labels within each month; the
    paired monthly test of label A against label B should reject at about 5 %."""
    mo = month_of(P, E)
    pnl, hold = _pnl(live), _hold(live)
    rej = 0
    for _ in range(N_CONTROL):
        lab = rng.random(len(E)) < 0.5
        d = (capital_day_by(mo[lab], pnl[lab], hold[lab]) - capital_day_by(mo[~lab], pnl[~lab], hold[~lab])).dropna()
        t = ld.nonoverlap(d, 1)["t"]
        rej += t is not None and abs(t) > 1.96
    return {"n": N_CONTROL, "reject_rate_5pct": rej / N_CONTROL}


def power(series: dict[str, float], rng: np.random.Generator) -> dict:
    """Items 9/10 and amendment J: the paired monthly series centred on zero, a shift injected, months
    resampled; detection = one-sided p <= 0.05. Reads the series' spread, not its mean."""
    d = np.array(list(series.values()), dtype=float)
    if len(d) < 3:
        return {"months": len(d), "mde_80": None}
    c = d - d.mean()
    idx = rng.integers(0, len(c), size=(N_BOOT, len(c)))
    boot = c[idx]

    def rate(delta: float) -> float:
        x = boot + delta
        t = x.mean(1) / (x.std(1, ddof=1) / math.sqrt(len(c)))
        return float((t > 1.645).mean())

    grid = [g / 1e5 for g in range(1, 201)]               # 0.001 .. 0.2 %/day
    mde = next((g for g in grid if rate(g) >= 0.8), None)
    return {"months": len(d), "sd": float(d.std(ddof=1)), "mde_80": mde,
            "detect_rate_at_0.02pct_day": rate(POSITIVE_SHIFT), "mde_formula_2.8se": float(2.8 * d.std(ddof=1) / math.sqrt(len(d)))}


def oos_class(is_adv: float, oos: dict) -> dict:
    """Amendment J's classes and rule (f)."""
    adv, p = oos["mean"], oos["p_one_sided"]
    r = adv / is_adv if is_adv and is_adv > 0 and adv is not None else None
    if adv is None:
        cls = "unanswered"
    elif adv <= 0:
        cls = "reverses"
    elif p is not None and p <= 0.05 and r is not None and r >= RETENTION_BAR:
        cls = "holds"
    else:
        cls = "period-dependent"
    return {"is_advantage": is_adv, "oos_advantage": adv, "retention": r, "oos_p": p, "class": cls}


# ── SL pre-check (item 7) ──────────────────────────────────────────────────────

def sl_precheck(P: dict, pre: dict, E: list, rows: list[dict]) -> dict:
    """Every LIVE SL exit below -10 %: the bar's open must be at or below the stop (a gap) and the fill
    the open less R7's gap slip. Anything else is D3 and stops the card."""
    oc = pre.get("ohlc_cache") or {}
    tick, days = P["tickers"], P["days"]
    n, gap_ok, d3 = 0, 0, []
    for (i, j), r in zip(E, rows):
        if r["reason"] != "SL" or r["pnl"] >= -0.10:
            continue
        n += 1
        entry = float(P["close"].iat[i, j])
        stop = entry * (1 - ARMS["LIVE"]["sl"])
        bar = oc.get((tick[j], days[r["exit_idx"]]))
        fill = entry * (1 + r["pnl"])
        if bar is None or bar.open is None:
            d3.append({"ticker": tick[j], "entry": days[i], "exit": days[r["exit_idx"]], "why": "no bar: a close-path SL"})
        elif bar.open > stop:
            d3.append({"ticker": tick[j], "entry": days[i], "exit": days[r["exit_idx"]], "why": "fill below the stop without a gap"})
        elif abs(fill - bar.open * (1 - STOP_SLIP_GAP_PCT)) > 1e-4 * entry:
            d3.append({"ticker": tick[j], "entry": days[i], "exit": days[r["exit_idx"]], "why": "gap fill is not the open less R7's slip"})
        else:
            gap_ok += 1
    return {"n_sl_below_-10pct": n, "gap_filled_at_open": gap_ok, "d3": d3, "pass": not d3}


# ── L2 card ────────────────────────────────────────────────────────────────────

def card_l2(w: str, P: dict, pre: dict, k: float, live_rows: list[dict] | None, workers: int, log) -> tuple[dict, dict]:
    global _SHARED
    rng = np.random.default_rng(SEED)
    E0 = ld.entry_sets(P)
    picks, n_pp = purge(E0["picks"], len(P["days"]))
    matched, n_pm = purge(E0["matched"], len(P["days"]))
    E = {"picks": picks, "matched": matched, "b5": b5_random(P, picks, rng)}
    L = live57(P, live_rows, rng) if live_rows else None
    if L:
        E["live57"] = L["E"]
    out: dict = {"window": w, "k_atrsym": k, "labels": LABELS,
                 "entries": {**{p: len(v) for p, v in E.items()}, "purged_picks": n_pp, "purged_matched": n_pm,
                             "dropped_no_match": E0["dropped"]}}
    atr = P["atr"].to_numpy()
    tp_atr = np.array([ARMS["ATR"]["atr"][0] * atr[i, j] for i, j in picks])
    # The ATR arm's lock can arm only where its 2 x ATR target sits above the 4 % trigger: the
    # precondition. Whether it did arm is pairs[...]["lock_armed_share"].
    out["entries"]["atr_tp_above_lock_trigger_share"] = float((tp_atr > PL[0]).mean()) if len(tp_atr) else None
    if L:
        out["entries"]["live57_skipped"] = L["skipped"]
        # Not purged: every arm and every draw ends at the window's last day alike (END), so the
        # comparison stays paired; the count is reported.
        out["entries"]["live57_truncated_by_window_end"] = len(L["truncated"])
    _SHARED = {"P": P, "pre": pre, "k": k, "E": E, "L": L}
    jobs = [(a, pk, pop) for a, cfg in {**ARMS, **REFERENCE}.items() for pk in peaks_for(cfg) for pop in E]
    if L:
        jobs += [(a, pk, "live57_rnd") for a, cfg in ARMS.items() for pk in peaks_for(cfg)]
    R: dict = {}
    t0 = time.time()
    with mp.get_context("fork").Pool(workers) as pool:
        for n, (a, pk, pop, res) in enumerate(pool.imap_unordered(_job, jobs), 1):
            R[(a, pk, pop)] = res
            if n % 10 == 0 or n == len(jobs):
                log(f"L2 {w} {n}/{len(jobs)} {time.time() - t0:.0f}s")

    def rows(a: str, pk: str, pop: str) -> list[dict]:
        return R[(a, pk if has_lock(ARMS.get(a) or REFERENCE[a]) else DECIDING_PEAK, pop)]

    # Validity: simulator identity (D3) and the identical-arms guard per peak and population.
    out["identity_time25"] = {p: ld.identity_check(P, E[p], rows("TIME25", DECIDING_PEAK, p)) for p in E}
    from backend.backtest.archive_guard import identical_pairs, refuse_identical
    out["guard_explained"] = []
    for pk in PEAKS:
        arms_of = lambda pop: {a: ({**(ARMS.get(a) or REFERENCE[a])}, [r["pnl"] for r in rows(a, pk, pop)])
                               for a in {**ARMS, **REFERENCE}}
        on_picks = {frozenset(x["arms"]) for x in identical_pairs(arms_of("picks"))}
        for pop in E:
            # A pair identical on LIVE57 alone (57 trades) is explained when it differs on the picks
            # under the same peak: the setting is wired, and nothing among the 57 reached it.
            expl = {} if pop != "live57" else {
                frozenset(x["arms"]): f"differs on picks under peak {pk}: wired; no LIVE57 trade reached it"
                for x in identical_pairs(arms_of(pop)) if frozenset(x["arms"]) not in on_picks}
            out["guard_explained"] += [{**x, "peak": pk, "pop": pop} for x in refuse_identical(arms_of(pop), expl)]
    for a, cfg in ARMS.items():
        if has_lock(cfg):
            refuse_identical({f"{a}@{pk}": ({"peak": pk}, [r["pnl"] for r in R[(a, pk, "picks")]]) for pk in PEAKS})

    # Entry peaks per population (arm-free).
    mfe = {p: mfe_panel(P, E[p])["mfe25"] for p in E}
    out["peak"] = {p: peak_stats(mfe[p]) for p in E}
    mo = month_of(P, E["picks"])
    for thr in (0.04, 0.06):
        dm = (pd.Series((mfe["picks"] >= thr).astype(float)).groupby(mo).mean()
              - pd.Series((mfe["matched"] >= thr).astype(float)).groupby(mo).mean()).dropna()
        out["peak"][f"picks_minus_matched_ge_{thr:g}"] = ld.nonoverlap(dm, 1)

    out["pairs"] = {}
    for a, cfg in {**ARMS, **REFERENCE}.items():
        for pk in PEAKS:
            for pop in E:
                rs = rows(a, pk, pop)
                s = ld.exit_stats(rs)
                s.update(costs=costs(rs), capture=capture(P, E[pop], rs, mfe[pop]), tail=tail(rs, P, E[pop]),
                         post_stop=post_stop(P, E[pop], rs, rows("TIME25", DECIDING_PEAK, pop)))
                if has_lock(cfg):
                    # Share of trades whose lock armed. An arm whose lock rarely arms is read as partly
                    # unarmed, not as a verdict on its exit rule (ruling 2026-10-21, the ATR arm).
                    s["lock_armed_share"] = float(np.mean([r["peak_gain"] >= cfg["pl"][0] for r in rs])) if rs else None
                if a in ARMS:
                    lv = rows("LIVE", pk, pop)
                    s["breakeven_bp_vs_live"] = breakeven_bp(rs, lv)
                    s["win_rate_same_exit_b5"] = ld.exit_stats(rows(a, pk, "b5"))["win_rate"]
                    s["win_rate_excess_over_b5"] = s["win_rate"] - s["win_rate_same_exit_b5"]
                    if a in SYM_OF:
                        s["win_rate_sym_ref"] = {SYM_OF[a]: ld.exit_stats(rows(SYM_OF[a], pk, pop))["win_rate"]}
                    if pop != "live57":
                        s2022 = subset(P, E[pop], rs, *YEAR_2022)
                        if s2022[0]:
                            l2022 = subset(P, E[pop], lv, *YEAR_2022)[1]
                            s["year_2022"] = {**ld.exit_stats(s2022[1]),
                                              "capital_day_minus_live": ld.exit_stats(s2022[1])["capital_day"] - ld.exit_stats(l2022)["capital_day"]}
                out["pairs"][f"{a}|{pk}|{pop}"] = s

    # Arm against LIVE (same peak, same entries), picks minus matched, exit null, per-year table.
    out["vs_live"], out["power"] = {}, {}
    prng = np.random.default_rng(SEED + 1)
    for pk in PEAKS:
        for a in NON_LIVE:
            for pop in ("picks", "matched", "b5"):
                pm_ = paired_monthly(P, E[pop], rows(a, pk, pop), rows("LIVE", pk, pop))
                out["power"][f"{a}|{pk}|{pop}"] = power(pm_["series"], prng)
                out["vs_live"][f"{a}|{pk}|{pop}"] = pm_
            out["vs_live"][f"{a}|{pk}|picks"]["by_year"] = year_table(P, E["picks"], rows(a, pk, "picks"), rows("LIVE", pk, "picks"))
    out["holm_picks"] = {pk: holm({a: out["vs_live"][f"{a}|{pk}|picks"]["p_one_sided"] for a in NON_LIVE}) for pk in PEAKS}
    out["picks_minus_matched"] = {f"{a}|{pk}": paired_monthly(P, E["picks"], rows(a, pk, "picks"), rows(a, pk, "matched"))
                                  for a in ARMS for pk in PEAKS}
    out["exit_null"] = {f"{a}|{pk}": {p: ld.exit_null(P, E[p], rows(a, pk, p), np.random.default_rng(SEED))
                                      for p in ("picks", "matched")} for a in ARMS for pk in PEAKS}
    out["negative_control"] = negative_control(P, E["picks"], rows("LIVE", DECIDING_PEAK, "picks"), np.random.default_rng(SEED + 2))

    if L:
        out["live57"] = live57_read(R, L)
    return out, R


def live57_read(R: dict, L: dict) -> dict:
    """Amendment A4/H: per arm and peak, the 57 against their 1,000 same-day draws under the same arm,
    and rule (e)'s paired difference against LIVE. Power is computed from LIVE's own SD first."""
    lv = _pnl(R[("LIVE", DECIDING_PEAK, "live57")])
    out = {"n": len(lv), "power": {"live_sd": float(lv.std(ddof=1)), "mde_per_trade": float(2.8 * lv.std(ddof=1) / math.sqrt(len(lv)))},
           "arms": {}}
    for a, cfg in ARMS.items():
        for pk in peaks_for(cfg):
            x = _pnl(R[(a, pk, "live57")])
            rnd = np.array(R[(a, pk, "live57_rnd")])            # trades x draws
            rm, rw = rnd.mean(0), (rnd > 0).mean(0)
            lref = _pnl(R[("LIVE", pk if has_lock(cfg) else DECIDING_PEAK, "live57")])
            d = x - lref
            sd = d.std(ddof=1)
            out["arms"][f"{a}|{pk}"] = {
                "mean": float(x.mean()), "win_rate": float((x > 0).mean()),
                "rnd_mean": float(rm.mean()), "rnd_win_rate": float(rw.mean()),
                "p_mean": float((rm >= x.mean()).mean()), "p_win": float((rw >= (x > 0).mean()).mean()),
                "minus_live_mean": float(d.mean()), "minus_live_t": float(d.mean() / (sd / math.sqrt(len(d)))) if sd > 0 else None}
    return out


def save_rows(path: Path, R: dict) -> None:
    arr = {}
    for (a, pk, pop), res in R.items():
        key = f"{a}|{pk}|{pop}"
        if pop == "live57_rnd":
            arr[key] = np.array(res, dtype=np.float32)
            continue
        arr[key + "|pnl"] = np.array([r["pnl"] for r in res], dtype=np.float32)
        arr[key + "|hold"] = np.array([r["hold"] for r in res], dtype=np.int16)
        arr[key + "|exit_idx"] = np.array([r["exit_idx"] for r in res], dtype=np.int32)
        arr[key + "|reason"] = np.array([REASONS.index(r["reason"]) for r in res], dtype=np.int8)
        arr[key + "|peak_gain"] = np.array([r["peak_gain"] for r in res], dtype=np.float32)
    np.savez_compressed(path, **arr)


def decide(l2: dict[str, dict], l3: dict | None) -> dict:
    """Card item 11 (a)-(f) per non-LIVE arm. (b) needs L3; (e) needs LIVE57 (IS only)."""
    IS, OOS = l2.get("IS"), l2.get("OOS")
    out = {}
    for a in NON_LIVE:
        r: dict = {}
        if IS and OOS:
            r["a"] = bool(all(IS["pairs"][f"{a}|{pk}|picks"]["capital_day"] > IS["pairs"][f"LIVE|{pk}|picks"]["capital_day"]
                         and OOS["pairs"][f"{a}|{pk}|picks"]["capital_day"] > OOS["pairs"][f"LIVE|{pk}|picks"]["capital_day"]
                         for pk in PEAKS))
            is_adv = IS["vs_live"][f"{a}|{DECIDING_PEAK}|picks"]["mean"]
            r["f"] = oos_class(is_adv, OOS["vs_live"][f"{a}|{DECIDING_PEAK}|picks"])
        if IS:
            y = IS["pairs"][f"{a}|{DECIDING_PEAK}|picks"].get("year_2022", {})
            full = IS["vs_live"][f"{a}|{DECIDING_PEAK}|picks"]["mean"]
            r["c"] = None if not y or full is None else bool(np.sign(y["capital_day_minus_live"]) == np.sign(full) or full <= 0)
            r["d"] = bool(IS["pairs"][f"{a}|{DECIDING_PEAK}|picks"]["costs"]["net_capital_day"][str(COST_GATE_BP)]
                      > IS["pairs"][f"LIVE|{DECIDING_PEAK}|picks"]["costs"]["net_capital_day"][str(COST_GATE_BP)])
            if "live57" in IS:
                t = IS["live57"]["arms"][f"{a}|{DECIDING_PEAK}"]["minus_live_t"]
                r["e_entry_dependent"] = bool(t is not None and t <= -2 and (full or 0) > 0)
            r["holm_is_polled"] = bool(IS["holm_picks"][DECIDING_PEAK][a])
            # LIVE carries the lock, so every arm's verdict against it is read under all three peaks.
            r["lock_model_dependent"] = len({IS["vs_live"][f"{a}|{pk}|picks"]["mean"] > 0 for pk in PEAKS}) > 1
            if has_lock(ARMS[a]):
                r["lock_armed_share"] = {x: IS["pairs"][f"{x}|{DECIDING_PEAK}|picks"]["lock_armed_share"] for x in (a, "LIVE")}
        if l3 and a in l3.get("arms", {}):
            x = l3["arms"][a]
            r["b"] = bool(x["p"]["pnl_per_capital_day"] <= 0.05 and x["starts_above_live"] >= 4)
        known = [r.get(k) for k in ("a", "b", "c", "d")] + [r.get("f", {}).get("class") == "holds" if "f" in r else None]
        r["beats_live"] = None if None in known else bool(all(known) and r["holm_is_polled"] and not r.get("e_entry_dependent")
                                                          and not r["lock_model_dependent"])
        out[a] = r
    return out


# ── L3 card ────────────────────────────────────────────────────────────────────

def l3_over(arm: str, k: float) -> dict:
    """Engine overrides for one arm, peak pinned to polled (card item 3)."""
    spec = ARMS[arm]
    o = {"peak_from_high": True, "peak_haircut": POLLED_PEAK_GAP, "time_stop_days": spec["tdays"],
         "profit_lock_trigger_pct": spec["pl"][0] if spec["pl"] else None,
         "profit_lock_trail_pct": spec["pl"][1] if spec["pl"] else None}
    if "atr" in spec:
        o.update(atr_exits=True, atr_exit_mult=tuple(k if x == "k" else x for x in spec["atr"]))
    else:
        o.update(take_profit_pct=spec["tp"], stop_loss_pct=spec["sl"])
    return o


def parse_french(text: str) -> pd.DataFrame:
    """A Kenneth French daily CSV: a header row that starts with a comma, then YYYYMMDD rows in
    percent; notes above and below are skipped. Returned in fractions."""
    lines = text.splitlines()
    cols = [c.strip() for c in next(l for l in lines if l.startswith(",")).split(",")[1:]]
    rows = [l.split(",") for l in lines if len(l.split(",")[0].strip()) == 8 and l.split(",")[0].strip().isdigit()]
    df = pd.DataFrame([[float(x) for x in r[1:1 + len(cols)]] for r in rows], columns=cols,
                      index=pd.to_datetime([r[0].strip() for r in rows], format="%Y%m%d"))
    return df / 100.0


def carhart() -> pd.DataFrame | None:
    """Kenneth French's daily FF3 and momentum files, downloaded and parsed here (amendment D). None
    when they cannot be fetched: the row then reads "not run"."""
    base = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"

    def read(name: str) -> pd.DataFrame:
        with urllib.request.urlopen(base + name, timeout=60) as r:
            z = zipfile.ZipFile(io.BytesIO(r.read()))
        return parse_french(z.read(z.namelist()[0]).decode("latin-1"))
    try:
        f3 = read("F-F_Research_Data_Factors_daily_CSV.zip")
        mom = read("F-F_Momentum_Factor_daily_CSV.zip")
    except Exception:
        return None
    return f3.join(mom, how="inner")


def carhart_alpha(r: pd.Series, F: pd.DataFrame | None) -> dict:
    if F is None:
        return {"status": "not run: factor file not fetched"}
    idx = pd.to_datetime(r.index)
    df = pd.concat({"r": pd.Series(r.to_numpy(), index=idx)}, axis=1).join(F, how="inner").dropna()
    if len(df) < 30:
        return {"status": "not run: too few overlapping days", "n": len(df)}
    mom = [c for c in F.columns if c.lower().startswith("mom")][0]
    y = (df["r"] - df["RF"]).to_numpy()
    X = np.column_stack([np.ones(len(df)), df[["Mkt-RF", "SMB", "HML", mom]].to_numpy()])
    XtX = np.linalg.inv(X.T @ X)
    coef = XtX @ X.T @ y
    u = y - X @ coef
    L = pm.newey_west_lag(len(y))
    Xu = X * u[:, None]
    S = Xu.T @ Xu
    for lag in range(1, L + 1):
        G = Xu[lag:].T @ Xu[:-lag]
        S += (1 - lag / (L + 1)) * (G + G.T)
    se = np.sqrt(np.diag(XtX @ S @ XtX))
    return {"status": "run", "n": len(df), "alpha_annual": float(coef[0] * 252), "t_alpha": float(coef[0] / se[0]),
            "loadings": dict(zip(["mkt", "smb", "hml", "mom"], map(float, coef[1:]))), "factor_last_date": str(F.index.max().date())}


def l3_read(res: dict, F: pd.DataFrame | None) -> dict:
    m = ld.l3_metrics("IS", res)
    r = pm.daily_returns(res["equity_curve"])
    m["cagr"] = pm.cagr(r)
    y = r[(r.index >= pd.Timestamp(YEAR_2022[0])) & (r.index <= pd.Timestamp(YEAR_2022[1]))]
    m["year_2022"] = ld.perf(y)
    m["carhart"] = carhart_alpha(r, F)
    m["entries"] = len(res["trade_log"])
    return m


def _l3_job(job: tuple) -> tuple:
    arm, start, seed, k, ep, op = job
    rnd = {"seed": seed, "per_day": ep, "open_cap": op} if seed else None
    res = ld._engine("IS", b.R1, start, l3_over(arm, k), rnd)
    return arm, start, seed, l3_read(res, _SHARED.get("F")), b._entries_per_day(res["trade_log"]), b.open_after_entries(res["trade_log"], res["days"])


def _v(x: float | None) -> float:
    return -math.inf if x is None else x


def card_l3(k: float, seeds: int, workers: int, log) -> dict:
    global _SHARED
    F = carhart()
    _SHARED = {"F": F}
    out: dict = {"k_atrsym": k, "peak": DECIDING_PEAK, "carhart": "fetched" if F is not None else "not fetched", "arms": {}}
    refs = [(a, s, 0, k, None, None) for a in ARMS for s in b.RND_STARTS] + [("LIVE", 0, 0, k, None, None)]
    ref: dict = {}
    with mp.get_context("fork").Pool(workers) as pool:
        for n, (a, s, _, m, ep, op) in enumerate(pool.imap_unordered(_l3_job, refs), 1):
            ref.setdefault((a, s), []).append((m, ep, op))
            log(f"L3 ref {n}/{len(refs)}")
    from backend.backtest.archive_guard import fingerprint
    twice = ref[("LIVE", 0)]
    out["control_live_twice_identical"] = len(twice) == 2 and fingerprint(twice[0][0]) == fingerprint(twice[1][0])
    jobs = [(a, 0, sd, k, ref[(a, 0)][0][1], ref[(a, 0)][0][2]) for a in ARMS for sd in range(1, seeds + 1)]
    rnd: dict = {}
    t0 = time.time()
    with mp.get_context("fork").Pool(workers) as pool:
        for n, (a, _, sd, m, _, _) in enumerate(pool.imap_unordered(_l3_job, jobs), 1):
            rnd.setdefault(a, {})[str(sd)] = m
            if n % 50 == 0 or n == len(jobs):
                log(f"L3 RND {n}/{len(jobs)} {time.time() - t0:.0f}s")
    live = {s: ref[("LIVE", s)][0][0] for s in b.RND_STARTS}
    for a in ARMS:
        r0 = ref[(a, 0)][0][0]
        sds = list(rnd[a].values())
        stat = ("pnl_per_capital_day", "total_return", "alpha_tbill", "calmar")
        out["arms"][a] = {
            "override": {k_: (list(v) if isinstance(v, tuple) else v) for k_, v in l3_over(a, k).items()},
            "starts": {str(s): ref[(a, s)][0][0] for s in b.RND_STARTS},
            "rnd_summary": {st: float(np.median([x[st] for x in sds if x[st] is not None])) for st in stat},
            "p": {st: float((sum(_v(x[st]) >= _v(r0[st]) for x in sds) + 1) / (len(sds) + 1)) for st in stat},
            "turnover_gate": {"ref_entries": r0["entries"], "rnd_entries_median": float(np.median([x["entries"] for x in sds])),
                              "within_1pct": all(abs(x["entries"] - r0["entries"]) <= 0.01 * r0["entries"] for x in sds)},
            "starts_above_live": int(sum(_v(ref[(a, s)][0][0]["pnl_per_capital_day"]) > _v(live[s]["pnl_per_capital_day"])
                                         for s in b.RND_STARTS)),
            "rnd": rnd[a]}
    out["holm_rnd_p"] = holm({a: out["arms"][a]["p"]["pnl_per_capital_day"] for a in NON_LIVE})
    return out


# ── Main ───────────────────────────────────────────────────────────────────────

def trial_count(windows: list[str], live57: bool, l3: bool) -> dict:
    """Amendment G: every arm x peak x level x window x population counts, reported or not, plus
    T4's 45 grid configs (TP4NS was picked from them)."""
    arm_peaks = sum(len(peaks_for(c)) for c in ARMS.values())
    l2 = arm_peaks * len(windows) * 3 + (arm_peaks if live57 and "IS" in windows else 0)
    l3n = len(ARMS) if l3 else 0
    return {"l2": l2, "l3": l3n, "t4_grid": 45, "total": l2 + l3n + 45,
            "family": "nine non-LIVE arms on the goal statistic, Holm 5 %"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir", type=Path)
    ap.add_argument("--levels", default="L2,L3")
    ap.add_argument("--windows", default="IS,OOS")
    ap.add_argument("--seeds", type=int, default=100)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--battery", type=Path, default=Path("/home/promenix/apex-battery/2026-10-19"))
    ap.add_argument("--precheck-only", action="store_true", help="item 7 alone: its counts go into the card before any arm runs")
    a = ap.parse_args(argv)
    a.outdir.mkdir(parents=True, exist_ok=True)
    levels, windows = a.levels.split(","), a.windows.split(",")
    t0 = time.time()

    def log(msg: str) -> None:
        print(f"[{time.time() - t0:6.0f}s] {msg}", flush=True)

    def save(name: str, obj: dict) -> None:
        (a.outdir / f"{name}.json").write_text(json.dumps(obj, indent=1, default=str))
        log(f"saved {name}")

    ef._trading_days_count = functools.lru_cache(maxsize=None)(ef._trading_days_count)   # pure; speed only
    ld._R = b.Runner(b.Store(a.battery), "exits-t8")
    pre_is = ld._R._window("IS")
    P_is = ld.panels(pre_is)
    k = atrsym_k(P_is)
    log(f"ATRSYM k = {k:.4f} (IS picks)")

    # Item 7 first: LIVE under the close peak (T4's model) on IS and OOS picks + matched.
    pc: dict = {}
    for w in windows:
        pre = pre_is if w == "IS" else ld._R._window(w)
        P = P_is if w == "IS" else ld.panels(pre)
        E = ld.entry_sets(P)
        ent = E["picks"] + E["matched"]
        rows = [simulate(P, pre, i, j, "LIVE", "close", k) for i, j in ent]
        pc[w] = sl_precheck(P, pre, ent, rows)
        log(f"pre-check {w}: {pc[w]['n_sl_below_-10pct']} SL exits below -10 %, D3 {len(pc[w]['d3'])}")
    save("precheck", pc)
    if not all(v["pass"] for v in pc.values()):
        log("D3 in the SL pre-check: the card stops until it is explained (item 7)")
        return 4
    if a.precheck_only:
        return 0

    l2: dict = {}
    if "L2" in levels:
        import backend.db as db
        live_db = Path("/home/promenix/apex/data/apex.db")
        db.DB_PATH = live_db
        since = json.loads((live_db.parent / "live_config.json").read_text()).get("live_account_since")
        canonical = db.live_sample_rows(since=since)
        for w in windows:
            pre = pre_is if w == "IS" else ld._R._window(w)
            P = P_is if w == "IS" else ld.panels(pre)
            try:
                res, R = card_l2(w, P, pre, k, canonical if w == "IS" else None, a.workers, log)
            except IdenticalArms as e:       # ruling 2026-10-20: refused, kept for diagnosis only
                save(f"L2_{w}.refused", {"refused": str(e)})
                return 3
            if w == "IS":
                res["live_sample"] = {"definition": db.LIVE_SAMPLE, "since": since, "n_rows": len(canonical)}
            l2[w] = res
            save(f"L2_{w}", res)
            save_rows(a.outdir / f"L2_{w}_rows.npz", R)
    l3 = None
    if "L3" in levels:
        ld._R._window("IS")
        ld._R.frozen = True
        l3 = card_l3(k, a.seeds, a.workers, log)
        save("L3", l3)
    save("decision", {"rule": decide(l2, l3), "trials": trial_count(windows, "L2" in levels, l3 is not None)})
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
