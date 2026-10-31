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
                 (1, "position_open"), (1, "cooloff"), (1, "exit_cooloff"), (1, "notional_too_small"),
                 (1, "price_missing"), (2, "max_positions"), (2, "sector_cap"), (6, "broker_error")}


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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--perms", type=int, default=2000)
    ap.add_argument("--battery", type=Path, default=Path("/home/promenix/apex-battery/2026-10-19"))
    ap.add_argument("--unit", choices=("event", "day"), default="event", help="v2 event (default); v1 ticker-day")
    ap.add_argument("--end", default="", help="dated window end (YYYY-MM-DD or 'latest'); default: the battery IS window")
    a = ap.parse_args(argv)
    t0 = time.time()
    since = json.loads(LIVE_CONFIG.read_text()).get("live_account_since")   # the canonical sample's start
    ef._trading_days_count = functools.lru_cache(maxsize=None)(ef._trading_days_count)   # pure; speed only
    pre = dated_window(a.end) if a.end else b.Runner(b.Store(a.battery), "t7")._window("IS")
    P = ld.panels(pre)
    until = until_for(P["days"]) if a.end else UNTIL
    con = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
    rows = gate_rows(con, since, until)
    import backend.db as db                          # canonical live sample, same DB, read only
    db.DB_PATH = LIVE_DB
    live = db.live_sample_rows(since=since)
    td = label_ticker_days(rows)
    print(f"[{time.time() - t0:5.0f}s] {len(rows)} gate rows -> {len(td)} ticker-days "
          f"{td.layer.value_counts().to_dict()}", flush=True)
    if a.unit == "event":
        td = to_events(td, P["days"])
        print(f"[{time.time() - t0:5.0f}s] -> {len(td)} events {td.layer.value_counts().to_dict()}", flush=True)
    rng = np.random.default_rng(SEED)
    M = measure(P, pre, td, rng)
    print(f"[{time.time() - t0:5.0f}s] measured", flush=True)
    out = {"card": "raw/notes/2026-10/2026-10-20-apex-rejected-candidate-card.md", "since": since,
           "until_exclusive": until, "window_end": P["days"][-1], "unit": a.unit, "live_sample": db.LIVE_SAMPLE, "exit": EXIT,
           "simulator_gate": simulator_gate(M, live), **read_card(M, rng, a.perms)}
    out["controls"] = controls(M, rng, a.perms)
    a.out.write_text(json.dumps(out, indent=1, default=str))
    M.to_csv(a.out.with_suffix(".rows.csv"), index=False)
    print(f"[{time.time() - t0:5.0f}s] saved {a.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
