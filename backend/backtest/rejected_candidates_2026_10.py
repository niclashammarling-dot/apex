"""
T7 rejected-candidate card (vault raw/notes/2026-10/2026-10-20-apex-rejected-candidate-card.md,
pre-registered de58ebf1 before this ran). Does each live judgment layer reject candidates that do
worse than what it lets through, on the same days with the same exits?

live_gate_history logged every judgment in real time, so the layers the backtest cannot replay
(sentiment, Claude/L5: model look-ahead) are measured here without look-ahead. One label per
ticker-day: entered if it had any TRADE_EXECUTED that day, else the furthest layer it reached.
Every ticker-day, entered or rejected, is measured the same way: entered at that day's close
through the L2 simulator (ladder_2026_10._simulate, the engine's own exit function), live exits.

Usage: python -m backend.backtest.rejected_candidates_2026_10 OUT.json [--perms 2000]
Reads the production DB read-only and the battery's IS window (price and signal caches).
"""
from __future__ import annotations

import argparse
import functools
import json
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

import backend.backtest.battery_2026_10 as b
import backend.backtest.engine_fast as ef
import backend.backtest.ladder_2026_10 as ld

LIVE_DB     = Path("/home/promenix/apex/data/apex.db")
LIVE_CONFIG = Path("/home/promenix/apex/data/live_config.json")
UNTIL       = "2026-10-10"          # exclusive: ≥ 5 sessions of prices before the cache ends 10-16
WINDOW_START = "2019-01-02"         # a dated window (--end) starts where the battery's IS window does
MIN_FORWARD = 5                     # sessions of prices a decision day needs after it
SEED        = 20261020
N_B5        = 50                    # vol-matched draws per ticker-day (10 for eligibility, report-only)
SD_ASSUMED  = 0.045                 # the card's pre-run power assumption
EXIT        = {**ld.LIVE_EXIT, "pl": list(ld.LIVE_EXIT["pl"])}

# code -> (stage rank, layer): the card's frozen layers, by the place in the run where a row stopped. The codes
# stored before lock labels phase 2 (2026-10-31) are numbered one behind the locks; after the migration they are
# read from gate_code_migration.old_code, so a re-run reproduces the archived result. Rows written after it carry
# a stop_code, mapped here to the same layers (STOP_LAYERS); skips, refusals after the review, broker errors and
# unknown rows carry no judgment on the candidate and are dropped, as SKIPPED_*, TRADE_REJECTED and TRADE_FAILED
# were. Every registered stop_code is in STOP_LAYERS or DROPPED_STOPS (test).
STAGES = {
    "FILTERED_ELIGIBILITY":    (1, "eligibility"),
    "FILTERED_L1":             (2, "quant"),
    "FILTERED_L2":             (3, "sentiment"),
    "FILTERED_LEADING":        (4, "leading"),
    "FILTERED_ETF_PENALTY":    (4, "leading"),
    "FILTERED_OVERFLOW_QUANT": (5, "capacity"),
    "FILTERED_L3":             (6, "l5"),
    "TRADE_EXECUTED":          (9, "entered"),
}
_LAYER_OF_RULE = {
    (1, "regime"): "FILTERED_ELIGIBILITY", (1, "vix"): "FILTERED_ELIGIBILITY",
    (1, "macro_event"): "FILTERED_ELIGIBILITY", (2, "earnings"): "FILTERED_ELIGIBILITY",
    (1, "macro_pre_event"): "FILTERED_L1", (2, "earnings_near"): "FILTERED_L1", (3, "threshold"): "FILTERED_L1",
    (4, "grok"): "FILTERED_L2", (4, "unavailable"): "FILTERED_L2", (4, "unavailable_inferred"): "FILTERED_L2",
    (5, "leading"): "FILTERED_LEADING", (5, "unavailable"): "FILTERED_LEADING", (2, "etf"): "FILTERED_ETF_PENALTY",
    (2, "overflow"): "FILTERED_OVERFLOW_QUANT",
    (6, "claude"): "FILTERED_L3", (6, "unavailable"): "FILTERED_L3", (6, "unavailable_inferred"): "FILTERED_L3",
    (0, "drawdown_guard"): "FILTERED_L3",
}
DROPPED_RULES = {(0, "halt_m4_drawdown"), (0, "loss_cap"), (0, "halt_m4_expectancy"), (1, "blocklist"),
                 (1, "position_open"), (1, "cooloff"), (1, "exit_cooloff"), (1, "exit_cooloff_win"),
                 (1, "notional_too_small"), (1, "price_missing"), (2, "max_positions"), (2, "sector_cap"),
                 (6, "broker_error")}


def _stop_layers() -> dict[str, tuple[int, str]]:
    from backend.gate.lock_codes import TRADE_EXECUTED, stop_code
    out = {stop_code(o, r): STAGES[old] for (o, r), old in _LAYER_OF_RULE.items()}
    out[TRADE_EXECUTED] = STAGES["TRADE_EXECUTED"]
    return out


STOP_LAYERS = _stop_layers()
TESTED   = ("l5", "sentiment", "leading")       # Holm family
REPORTED = ("eligibility", "quant", "capacity")


def gate_rows(con, since: str, until: str) -> list[tuple[str, str, str]]:
    """(ticker, timestamp, code) for live_gate_history in [since, until): the code stored before phase 2 (from the
    migration record where the row was migrated, else the stored gate_decision on a database not yet migrated),
    or the stop_code of a row written after it."""
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    cols = {r[1] for r in con.execute("PRAGMA table_info(live_gate_history)")}
    code = "g.gate_decision"
    join = ""
    if "gate_code_migration" in tables:
        join = "LEFT JOIN gate_code_migration m ON m.tbl = 'live_gate_history' AND m.row_id = g.id"
        code = "COALESCE(m.old_code, g.stop_code, g.gate_decision)" if "stop_code" in cols else "COALESCE(m.old_code, g.gate_decision)"
    elif "stop_code" in cols:
        code = "COALESCE(g.stop_code, g.gate_decision)"
    return con.execute(f"SELECT g.ticker, g.timestamp, {code} FROM live_gate_history g {join} "
                       "WHERE g.timestamp >= ? AND g.timestamp < ?", (since, until)).fetchall()


def label_ticker_days(rows: list[tuple[str, str, str]]) -> pd.DataFrame:
    """rows: (ticker, utc timestamp, code). One row per ticker-day: the furthest stage reached that day; codes
    with no judgment on the candidate (skips, refusals after the review, broker errors, unknown) are dropped."""
    best: dict[tuple[str, str], tuple[int, str]] = {}
    for ticker, ts, dec in rows:
        stage = STAGES.get(dec) or STOP_LAYERS.get(dec)
        if stage is None:
            continue
        key = (ticker, ts[:10])
        if key not in best or stage[0] > best[key][0]:
            best[key] = stage
    return pd.DataFrame([{"ticker": t, "d": d, "layer": s[1]} for (t, d), s in sorted(best.items())],
                        columns=["ticker", "d", "layer"])


def until_for(days: list[str], min_forward: int = MIN_FORWARD) -> str:
    """Exclusive entry bound for a window ending at days[-1]: every decision day keeps
    min_forward sessions of prices after it."""
    return days[-min_forward]


def dated_window(end: str) -> dict:
    """The monthly shadow book's window (T7 v2 section): the battery IS window's start to `end`, built
    with the engine's own precompute. 'latest' = the last weekday before today; the caches key on
    the dates, so a new end builds new caches once."""
    if end == "latest":
        end = (pd.Timestamp.now(tz="America/New_York").normalize().tz_localize(None) - pd.offsets.BDay(1)).date().isoformat()
    t0 = time.time()
    pre = ef.precompute(WINDOW_START, end)
    print(f"[{WINDOW_START}..{end}] precompute {time.time() - t0:.0f}s, {len(pre['trading_days'])} sessions", flush=True)
    return pre


def to_events(td: pd.DataFrame, sessions: list[str]) -> pd.DataFrame:
    """v2 unit (T7 card, v2 section): consecutive sessions of the same ticker with the same label are
    one event, measured from its first day. v1 counted MRNA's 19 sentiment rejections as 19 units."""
    pos = {d: k for k, d in enumerate(sessions)}
    x = td[td.d.isin(pos)].assign(k=lambda f: f.d.map(pos)).sort_values(["ticker", "k"])
    new = (x.ticker != x.ticker.shift()) | (x.layer != x.layer.shift()) | (x.k != x.k.shift() + 1)
    x = x.assign(event=new.cumsum())
    first = x.groupby("event").head(1).set_index("event")
    first["event_days"] = x.groupby("event").size()
    return first.drop(columns="k").reset_index(drop=True)


def within_day_perm(df: pd.DataFrame, a: str, g: str, col: str, perms: int,
                    rng: np.random.Generator) -> dict:
    """mean(a) - mean(g) over ticker-days labelled a or g; one-sided p from shuffling the labels
    among the ticker-days of the same date. Days holding one label only cannot move."""
    x = df[df.layer.isin([a, g])].dropna(subset=[col])
    vals, is_a = x[col].to_numpy(), (x.layer == a).to_numpy()
    groups = [np.flatnonzero((x.d == d).to_numpy()) for d in x.d.unique()]
    obs = vals[is_a].mean() - vals[~is_a].mean()
    hits = 0
    for _ in range(perms):
        lab = is_a.copy()
        for ix in groups:
            lab[ix] = rng.permutation(lab[ix])
        hits += (vals[lab].mean() - vals[~lab].mean()) >= obs
    mixed = sum(1 for ix in groups if 0 < is_a[ix].sum() < len(ix))
    return {"diff": float(obs), "p": (hits + 1) / (perms + 1), "n_a": int(is_a.sum()),
            "n_g": int((~is_a).sum()), "mixed_days": mixed}


def mde(sd: float, n1: int, n2: int) -> float:
    """One-sided 5 %, 80 % power: (1.645 + 0.842) x SE of a difference of means."""
    return float(2.487 * sd * np.sqrt(1 / n1 + 1 / n2)) if n1 and n2 else float("nan")


def holm(ps: dict[str, float]) -> dict[str, float]:
    order = sorted(ps, key=ps.get)
    out, run = {}, 0.0
    for k, name in enumerate(order):
        run = max(run, min(1.0, (len(order) - k) * ps[name]))
        out[name] = run
    return out


def measure(P: dict, pre: dict, td: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    days, tick = P["days"], P["tickers"]
    q = ld.quintile(P["atr"], P["mask"]).to_numpy()
    m = P["mask"].to_numpy()
    close = P["close"].to_numpy()
    out = []
    for r in td.itertuples():
        rec = {"ticker": r.ticker, "d": r.d, "layer": r.layer, "covered": False}
        if r.d in days and r.ticker in tick:
            i, j = days.index(r.d), tick.index(r.ticker)
            if np.isfinite(close[i, j]):
                s = ld._simulate(P, pre, i, j, EXIT)
                last = len(days) - 1
                rec.update(covered=True, pnl=s["pnl"], end=s["reason"] == "END",
                           fwd5=close[min(i + 5, last), j] / close[i, j] - 1 if i + 5 <= last else np.nan,
                           fwd10=close[min(i + 10, last), j] / close[i, j] - 1 if i + 10 <= last else np.nan,
                           sector=P["sector"][r.ticker])
                cand = np.flatnonzero(m[i] & (q[i] == q[i, j])) if m[i, j] else np.flatnonzero(m[i])
                cand = cand[cand != j]
                if len(cand):
                    n = 10 if r.layer == "eligibility" else N_B5
                    rec["b5"] = float(np.mean([ld._simulate(P, pre, i, int(k), EXIT)["pnl"]
                                               for k in rng.choice(cand, n)]))
                rec["b3"] = float(np.nanmean(close[min(i + 5, last), m[i]] / close[i, m[i]] - 1))
        out.append(rec)
    return pd.DataFrame(out)


def simulator_gate(M: pd.DataFrame, live: list[dict]) -> dict:
    """D2: the simulator's outcome sign against live's on entered ticker-days (canonical sample)."""
    lv = {(t["ticker"], t["timestamp"][:10]): t["pnl"] / t["notional"] for t in live if t["notional"]}
    e = M[(M.layer == "entered") & M.covered]
    pairs = [(row.pnl, lv[(row.ticker, row.d)]) for row in e.itertuples() if (row.ticker, row.d) in lv]
    if not pairs:
        return {"n": 0, "sign_agreement": None, "valid": False}
    a = np.array(pairs)
    agree = float(((a[:, 0] > 0) == (a[:, 1] > 0)).mean())
    return {"n": len(pairs), "sign_agreement": agree, "corr": float(np.corrcoef(a.T)[0, 1]), "valid": agree >= 0.80}


def controls(M: pd.DataFrame, rng: np.random.Generator, perms: int) -> dict:
    """Negative: entered vs entered, randomly halved within day (reject rate <= 10 %).
    Positive: the realised MDE added to entered, bootstrap-resampled, against leading
    (rejection rate >= 60 %; the card's stated power is 80 %)."""
    e = M[(M.layer == "entered") & M.covered].copy()
    neg = 0
    for _ in range(200):
        x = e.copy()
        x["layer"] = np.where(rng.random(len(x)) < 0.5, "entered", "half")
        neg += within_day_perm(x, "entered", "half", "pnl", 200, rng)["p"] < 0.05
    g = M[(M.layer == "leading") & M.covered]
    eff = mde(float(pd.concat([e.pnl, g.pnl]).std()), len(e), len(g))
    pos = 0
    for _ in range(200):
        x = pd.concat([e.sample(len(e), replace=True, random_state=int(rng.integers(1 << 31))),
                       g.sample(len(g), replace=True, random_state=int(rng.integers(1 << 31)))])
        x.loc[x.layer == "entered", "pnl"] += eff
        pos += within_day_perm(x, "entered", "leading", "pnl", 200, rng)["p"] < 0.05
    return {"negative_reject_rate": neg / 200, "negative_valid": neg / 200 <= 0.10,
            "positive_effect": eff, "positive_reject_rate": pos / 200, "positive_valid": pos / 200 >= 0.60}


def read_card(M: pd.DataFrame, rng: np.random.Generator, perms: int) -> dict:
    cov = M[M.covered]
    groups = {}
    for layer in ("entered",) + TESTED + REPORTED:
        x = cov[cov.layer == layer]
        n_all = int((M.layer == layer).sum())
        groups[layer] = {
            "n": int(len(x)), "coverage": len(x) / n_all if n_all else None,
            "mean": float(x.pnl.mean()) if len(x) else None,
            "win_rate": float((x.pnl > 0).mean()) if len(x) else None,
            "end_share": float(x.end.mean()) if len(x) else None,
            "excess_b5": float((x.pnl - x.b5).mean()) if len(x) else None,
            "fwd5": float(x.fwd5.mean()) if len(x) else None, "fwd10": float(x.fwd10.mean()) if len(x) else None,
            "top_sector_share": float(x.sector.value_counts(normalize=True).iloc[0]) if len(x) else None,
            "top_sector": x.sector.value_counts().index[0] if len(x) else None,
        }
    sd = float(cov[cov.layer.isin(("entered",) + TESTED)].pnl.std())
    tests = {}
    for layer in TESTED + REPORTED:
        if groups[layer]["n"] == 0:
            continue
        t = within_day_perm(cov, "entered", layer, "pnl", perms, rng)
        t["fwd5"] = within_day_perm(cov, "entered", layer, "fwd5", perms, rng)
        t["fwd10"] = within_day_perm(cov, "entered", layer, "fwd10", perms, rng)
        t["mde_realised"] = mde(sd, t["n_a"], t["n_g"])
        t["mde_assumed"] = mde(SD_ASSUMED, t["n_a"], t["n_g"])
        tests[layer] = t
    adj = holm({k: tests[k]["p"] for k in TESTED if k in tests})
    for k, v in adj.items():
        tests[k]["p_holm"] = v
    if "leading" in tests:   # v2: leading's fixed holds are pre-registered secondaries, Holm over the two
        sec = holm({h: tests["leading"][h]["p"] for h in ("fwd5", "fwd10")})
        tests["leading"]["secondary_holm"] = sec
    pooled = cov.assign(layer=np.where(cov.layer == "entered", "entered", "rejected"))
    return {"sd_pnl": sd, "groups": groups, "tests": tests,
            "chain_pooled_context": within_day_perm(pooled, "entered", "rejected", "pnl", perms, rng)}


# ── T7 line (INDEX T7 sub-bullet, ruled 2026-10-21 "Yes adopt the t7 line"; scope vault
#    raw/notes/2026-11/2026-11-10-apex-t7-line-build-scope.md, built 2026-11-14) ──────────────────────────

T7_ARCHIVE_1109 = Path("/mnt/c/Users/Nix/Niclas-KB/raw/notes/2026-11/data/2026-11-09-apex-t7-live-vs-same-exit-random.json")
N_FLIPS = 10_000


def t7_line(P: dict, pre: dict, live: list[dict], until: str | None, ids: list[int] | None = None) -> dict:
    """Live's canonical closed entries before `until` against same-day, volatility-matched random entries under the
    same exits (live_vs_engine_2026_10_20.compare, one function, no copy), under R9 and pre-R9 with a fresh generator
    per peak model as that module's main does. `ids` restricts to given rows in live's order (the known-present control)."""
    import backend.backtest.live_vs_engine_2026_10_20 as lve
    keep = set(ids) if ids is not None else None
    trades = [t for t in live if (until is None or t["timestamp"][:10] < until) and (keep is None or t["id"] in keep)]
    out: dict = {"n_rows": len(trades), "until_exclusive": until, "n_rnd": lve.N_RND,
                 "method": "live_vs_engine_2026_10_20.compare (ladder._simulate, the trade's own TP/SL)"}
    rows_r9: list[dict] = []
    for name, flag in (("r9", True), ("pre_r9", False)):
        res = lve.compare(P, pre, trades, flag, np.random.default_rng(lve.SEED))
        R = pd.DataFrame(res["rows"])
        by = {}
        for k, v in res["by"].items():
            if not v.get("n"):
                by[k] = v
                continue
            sub = R if k == "all" else R[R.cls == k]
            # p_live_mean is per trade (the share of random draws whose mean reaches live's); the day-clustered p flips
            # each day's summed excess together (as M9's 11-13 reading), since trades on one day are not independent.
            by[k] = {**v, "excess": v["live_mean"] - v["rnd_mean"], "p_unit": "per trade (random-draw share)",
                     "p_day_cluster_excess": day_cluster_p(sub.d, sub.live - sub.rnd_mean), "n_days": int(sub.d.nunique())}
        out[name] = {"n": res["n"], "by": by}
        if flag:
            rows_r9 = res["rows"]
            out["rows_r9"] = rows_r9
    sector_of = {t["id"]: t.get("sector") for t in trades}
    out["group3"] = group3(rows_r9, sector_of)
    out["sector_baseline"] = sector_baseline(P, pre, trades, rows_r9)
    return out


def group3(rows: list[dict], sector_of: dict) -> dict:
    """Go-criterion group 3 beside the line (Niclas 2026-11-13 "3) ja"; descriptive, post hoc, no decision use): live
    P&L per trade and excess over the random baseline, for all trades, without the best sector, without the best 3
    trades, and without both; best chosen per quantity (largest summed value, largest single values)."""
    D = pd.DataFrame(rows)
    if D.empty:
        return {"n": 0}
    D["sector"] = D.id.map(sector_of)
    D["excess"] = D.live - D.rnd_mean
    out: dict = {}
    for q in ("live", "excess"):
        by_sector = D.groupby("sector")[q].sum()
        best_sector = by_sector.idxmax()
        best3 = set(D.nlargest(3, q).id)

        def read(mask) -> dict:
            sub = D[mask]
            return {"n": int(len(sub)), "mean": float(sub[q].mean()) if len(sub) else None}
        out[q] = {"all": read(D.id.notna()), "best_sector": best_sector,
                  "without_best_sector": read(D.sector != best_sector),
                  "without_best_3": read(~D.id.isin(best3)),
                  "without_both": read((D.sector != best_sector) & ~D.id.isin(best3)),
                  "sum_by_sector": {k: float(v) for k, v in by_sector.items()}}
    return out


def day_cluster_p(days: pd.Series, d: pd.Series, flips: int = N_FLIPS, seed: int = SEED) -> float | None:
    """One-sided sign-flip p of the summed difference with each day's sum flipped together (live_class_day_cluster)."""
    s = pd.Series(np.asarray(d, float), index=np.asarray(days)).groupby(level=0).sum().to_numpy()
    if len(s) < 2:
        return None
    signs = np.random.default_rng(seed).choice([-1.0, 1.0], size=(flips, len(s)))
    null = (signs * s).sum(1)
    return float((np.sum(null >= s.sum()) + 1) / (flips + 1))


def sector_baseline(P: dict, pre: dict, trades: list[dict], rows: list[dict]) -> dict:
    """Same-sector baseline beside the line (Niclas 2026-11-13 "1) ja den är bra bredvid"; descriptive): each trade's
    comparator is the engine-pool name that day in its sector nearest in atr_pct, its own ticker and the day's other
    live entries excluded, simulated with the trade's own TP/SL under R9. A trade with no candidate is counted, not
    dropped; one with at most one candidate is flagged and the reading is repeated without them."""
    import backend.backtest.live_vs_engine_2026_10_20 as lve
    days, tick = P["days"], P["tickers"]
    mask, atr = P["mask"].to_numpy(), P["atr"].to_numpy()
    sector = np.array([P["sector"][x] for x in tick])
    by_id = {t["id"]: t for t in trades}
    live_on: dict[str, set] = {}
    for t in trades:
        live_on.setdefault(t["timestamp"][:10], set()).add(t["ticker"])
    before = ld.PEAK
    lve._with_peak(True)
    recs = []
    try:
        for r in rows:
            t, i, j = by_id[r["id"]], days.index(r["d"]), tick.index(r["ticker"])
            pool = np.flatnonzero(mask[i] & (sector == sector[j]))
            pool = pool[pool != j]
            others = {tick.index(x) for x in live_on.get(r["d"], set()) if x in tick} - {j}
            kept = np.array([k for k in pool if k not in others], dtype=int)
            rec = {"id": r["id"], "d": r["d"], "cls": r["cls"], "live": r["live"], "rnd_mean": r["rnd_mean"],
                   "cands_before": int(len(pool)), "cands": int(len(kept)), "flag_le1": len(kept) <= 1}
            finite = kept[np.isfinite(atr[i, kept])] if len(kept) else kept
            if len(finite) and np.isfinite(atr[i, j]):
                k = int(finite[np.argmin(np.abs(atr[i, finite] - atr[i, j]))])
                rec["base"] = ld._simulate(P, pre, i, k, lve._cfg(t))["pnl"]
                rec["atr_ratio"] = float(atr[i, k] / atr[i, j]) if atr[i, j] else None
            recs.append(rec)
    finally:
        ld.PEAK = before
    S = pd.DataFrame(recs)
    if S.empty:
        return {"n": 0}

    def read(sub: pd.DataFrame) -> dict:
        c = sub[sub["base"].notna()] if "base" in sub else sub.iloc[0:0]
        if c.empty:
            return {"n": int(len(sub)), "with_comparator": 0}
        sel, part = c.live - c.base, c.base - c.rnd_mean
        return {"n": int(len(sub)), "with_comparator": int(len(c)), "no_comparator": int(len(sub) - len(c)),
                "live_minus_sector": float(sel.mean()), "p_day_cluster_live_minus_sector": day_cluster_p(c.d, sel),
                "sector_minus_random": float(part.mean()), "p_day_cluster_sector_minus_random": day_cluster_p(c.d, part),
                "atr_ratio_median": float(c.atr_ratio.median()) if "atr_ratio" in c else None}
    out = {"all": read(S), "by_class": {k: read(g) for k, g in S.groupby("cls")},
           "candidates": {"before_exclusion": S.cands_before.value_counts().sort_index().to_dict(),
                          "after_exclusion": S.cands.value_counts().sort_index().to_dict()},
           "flagged_le1": [int(x) for x in S[S.flag_le1].id], "all_without_flagged": read(S[~S.flag_le1])}
    return out


def t7_control(P: dict, pre: dict, live: list[dict], archive: Path = T7_ARCHIVE_1109) -> dict:
    """Scope item 2, the known-present control: on the battery IS window and the 11-09 archive's 62 ids, t7_line must
    reproduce the archive's R9 numbers, and group 3 the 11-13 hand reading. A mismatch stops the build."""
    arch = json.loads(archive.read_text())["sets"]["canonical"]["r9"]
    ids = [r["id"] for r in arch["rows"]]
    got = t7_line(P, pre, live, None, ids)
    want, have = arch["by"]["all"], got["r9"]["by"]["all"]
    keys = ("n", "live_mean", "rnd_mean", "p_live_mean", "live_wr", "rnd_wr", "p_live_wr")
    match = {k: abs(float(have[k]) - float(want[k])) < 1e-9 for k in keys}
    g3 = got["group3"]
    hand = {("live", "all"): 0.0097, ("excess", "all"): 0.0121, ("live", "without_best_sector"): 0.0024,
            ("excess", "without_best_sector"): 0.0035, ("live", "without_best_3"): 0.0070,
            ("excess", "without_best_3"): 0.0084, ("live", "without_both"): 0.0012, ("excess", "without_both"): 0.0020}
    g3_match = {f"{q}|{k}": abs(g3[q][k]["mean"] - v) < 0.00006 for (q, k), v in hand.items()}
    return {"archive": str(archive), "n_ids": len(ids), "r9_all": {k: (have[k], want[k]) for k in keys},
            "r9_all_day_cluster": {"p": have.get("p_day_cluster_excess"), "n_days": have.get("n_days"),
                                   "m9_1113_reading": {"p": 0.051, "n_days": 29}},
            "match": match, "group3_match": g3_match, "ok": all(match.values()) and all(g3_match.values())}


def producer(argv: list[str], why: str) -> dict:
    import subprocess
    from datetime import datetime, timezone
    root = Path(__file__).resolve().parents[2]
    git = lambda *a: subprocess.run(["git", *a], cwd=root, capture_output=True, text=True).stdout.strip()
    return {"repo": "apex", "commit": git("rev-parse", "--short", "HEAD"),
            "script": "backend/backtest/rejected_candidates_2026_10.py",
            "run": " ".join([".venv/bin/python", "-u", "-m", "backend.backtest.rejected_candidates_2026_10", *argv]),
            "why": why, "run_at": datetime.now(timezone.utc).isoformat(),
            "dirty": bool(git("status", "--porcelain", "--", "backend/", "scripts/")),
            "pushed": subprocess.run(["git", "merge-base", "--is-ancestor", "HEAD", "origin/master"], cwd=root).returncode == 0}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--perms", type=int, default=2000)
    ap.add_argument("--battery", type=Path, default=Path("/home/promenix/apex-battery/2026-10-19"))
    ap.add_argument("--unit", choices=("event", "day"), default="event", help="v2 event (default); v1 ticker-day")
    ap.add_argument("--end", default="", help="dated window end (YYYY-MM-DD or 'latest'); default: the battery IS window")
    ap.add_argument("--t7-control", action="store_true",
                    help="only the T7 line's known-present control (battery IS window, the 11-09 archive's 62 ids)")
    ap.add_argument("--why", default="T7 monthly read (rejected-candidate card v2 and the T7 line)")
    a = ap.parse_args(argv)
    argv_used = list(argv if argv is not None else sys.argv[1:])
    t0 = time.time()
    since = json.loads(LIVE_CONFIG.read_text()).get("live_account_since")   # the canonical sample's start
    ef._trading_days_count = functools.lru_cache(maxsize=None)(ef._trading_days_count)   # pure; speed only
    pre = dated_window(a.end) if a.end else b.Runner(b.Store(a.battery), "t7")._window("IS")
    P = ld.panels(pre)
    until = until_for(P["days"]) if a.end else UNTIL
    con = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
    import backend.db as db                          # canonical live sample, same DB, read only
    db.DB_PATH = LIVE_DB
    live = db.live_sample_rows(since=since)
    if a.t7_control:
        ctl = t7_control(P, pre, live)
        a.out.write_text(json.dumps({"producer": producer(argv_used, a.why), "t7_control": ctl}, indent=1, default=str))
        print(f"[{time.time() - t0:5.0f}s] T7 control {'OK' if ctl['ok'] else 'MISMATCH'}: {ctl['match']} {ctl['group3_match']}", flush=True)
        return 0 if ctl["ok"] else 1
    rows = gate_rows(con, since, until)
    td = label_ticker_days(rows)
    print(f"[{time.time() - t0:5.0f}s] {len(rows)} gate rows -> {len(td)} ticker-days "
          f"{td.layer.value_counts().to_dict()}", flush=True)
    if a.unit == "event":
        td = to_events(td, P["days"])
        print(f"[{time.time() - t0:5.0f}s] -> {len(td)} events {td.layer.value_counts().to_dict()}", flush=True)
    rng = np.random.default_rng(SEED)
    M = measure(P, pre, td, rng)
    print(f"[{time.time() - t0:5.0f}s] measured", flush=True)
    out = {"producer": producer(argv_used, a.why),
           "card": "raw/notes/2026-10/2026-10-20-apex-rejected-candidate-card.md", "since": since,
           "until_exclusive": until, "window_end": P["days"][-1], "unit": a.unit, "live_sample": db.LIVE_SAMPLE, "exit": EXIT,
           "simulator_gate": simulator_gate(M, live), **read_card(M, rng, a.perms)}
    out["controls"] = controls(M, rng, a.perms)
    out["t7_line"] = t7_line(P, pre, live, until)
    print(f"[{time.time() - t0:5.0f}s] T7 line: {out['t7_line']['r9']['by'].get('all')}", flush=True)
    a.out.write_text(json.dumps(out, indent=1, default=str))
    M.to_csv(a.out.with_suffix(".rows.csv"), index=False)
    print(f"[{time.time() - t0:5.0f}s] saved {a.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
