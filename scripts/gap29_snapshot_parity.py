#!/usr/bin/env python3
"""
gap29_snapshot_parity.py — PRODUCTION_GAP row 29: does the sector snapshot's source change the regime layer? (Q-DAT)

Live's sector_snapshots hold the sector mean of signal_score per intraday poll; the engine builds its snapshot from
closes. Three walks share everything but the snapshot, all on production code (engine_fast.precompute,
RegimeBayes.step, sector_caps.cap_factors over sector_regime._compute_sector_stats), from the engine's own warm-up:
  E  engine snapshot and the engine's own streak history (= build_regime_timeline)
  L  live's snapshot as its EOD run read it, get_latest_sector_scores(as_of=16:15 ET), and streaks over the DB's daily
     averages for the 365 days up to d (get_sector_history's aggregation), read-only
Controls: (c1) E reproduces build_regime_timeline's allocation and cap factors on every window date; (c2) L's snapshot
equals the aggregate_score live logged in data/regime_signal_trace.jsonl on every trace date in the window. A failed
control means no read (exit 3). Reading rule and design sealed first: vault
raw/notes/2026-11/2026-11-23-apex-gap29-sector-snapshot-parity-read.md (vault 2213b20a).

Usage: gap29_snapshot_parity.py --start 2026-09-01 --end 2026-11-20 --out OUT.json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

NY = ZoneInfo("America/New_York")
TRACE = REPO / "data" / "regime_signal_trace.jsonl"
DB = REPO / "data" / "apex.db"
CAP_TOL = 0.01
MATERIAL = 0.05


def eod_cutoff_utc(d: date) -> str:
    """16:15 ET on d in UTC ISO: scheduler._eod_cutoff_utc, copied so the scheduler module is not imported."""
    return datetime.combine(d, time(16, 15), tzinfo=NY).astimezone(timezone.utc).isoformat()


def alloc_set(alloc: dict[str, float]) -> frozenset:
    return frozenset(s for s, v in alloc.items() if v > 0)


def l1(a: dict[str, float], b: dict[str, float]) -> float:
    return sum(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in set(a) | set(b))


def spearman(a: dict[str, float], b: dict[str, float]) -> float | None:
    keys = sorted(set(a) & set(b))
    if len(keys) < 3:
        return None
    def ranks(v):
        order = sorted(keys, key=lambda k: v[k])
        r, i = {}, 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in order[i:j + 1]:
                r[k] = (i + j) / 2 + 1
            i = j + 1
        return r
    ra, rb = ranks(a), ranks(b)
    n = len(keys)
    ma, mb = sum(ra.values()) / n, sum(rb.values()) / n
    cov = sum((ra[k] - ma) * (rb[k] - mb) for k in keys)
    va = sum((ra[k] - ma) ** 2 for k in keys) ** 0.5
    vb = sum((rb[k] - mb) ** 2 for k in keys) ** 0.5
    return cov / (va * vb) if va and vb else None


def _q(xs: list[float], p: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, int(p * (len(s) - 1) + 0.5))]


def summarise(days: list[dict]) -> dict:
    """The sealed report from per-day records (each: snap_e, snap_l, alloc_e, alloc_l, leader_e/l, state_e/l,
    post_e/l, caps_e/l, and optionally alloc_live)."""
    n = len(days)
    deltas = [d["snap_l"][s] - d["snap_e"][s] for d in days for s in d["snap_l"] if s in d["snap_e"]]
    rhos = [r for r in (spearman(d["snap_e"], d["snap_l"]) for d in days) if r is not None]
    top_same = sum(max(d["snap_e"], key=d["snap_e"].get) == max(d["snap_l"], key=d["snap_l"].get) for d in days)
    set_same = sum(alloc_set(d["alloc_e"]) == alloc_set(d["alloc_l"]) for d in days)
    caps_rows = [(d["caps_e"][s], d["caps_l"][s]) for d in days for s in d["caps_l"] if s in d["caps_e"]]
    def differs(i=None):
        idx = range(3) if i is None else [i]
        return sum(any(abs(a[j] - b[j]) > CAP_TOL for j in idx) for a, b in caps_rows)
    out = {
        "sessions": n,
        "input": {
            "sector_days": len(deltas),
            "mean_delta_live_minus_engine": round(sum(deltas) / len(deltas), 4) if deltas else None,
            "median_abs_delta": round(_q([abs(x) for x in deltas], 0.5), 4) if deltas else None,
            "p90_abs_delta": round(_q([abs(x) for x in deltas], 0.9), 4) if deltas else None,
            "spearman_median": round(_q(rhos, 0.5), 3) if rhos else None,
            "spearman_min": round(min(rhos), 3) if rhos else None,
            "same_top_sector": f"{top_same}/{n}",
        },
        "regime": {
            "allocation_set_identical": f"{set_same}/{n}",
            "allocation_set_differs_share": round(1 - set_same / n, 4) if n else None,
            "mean_l1": round(sum(l1(d["alloc_e"], d["alloc_l"]) for d in days) / n, 4) if n else None,
            "same_leader": f"{sum(d['leader_e'] == d['leader_l'] for d in days)}/{n}",
            "same_regime_state": f"{sum(d['state_e'] == d['state_l'] for d in days)}/{n}",
            "max_posterior_diff": round(max((abs(d["post_e"][s] - d["post_l"].get(s, 0.0))
                                             for d in days for s in d["post_e"]), default=0.0), 4),
        },
        "caps": {
            "sector_days": len(caps_rows),
            "any_factor_differs_share": round(differs() / len(caps_rows), 4) if caps_rows else None,
            "score_w_differs_share": round(differs(0) / len(caps_rows), 4) if caps_rows else None,
            "duration_w_differs_share": round(differs(1) / len(caps_rows), 4) if caps_rows else None,
        },
    }
    ref = [d for d in days if d.get("alloc_live") is not None]
    if ref:
        out["reference_live_logged"] = {
            "trace_dates": len(ref),
            "engine_set_equals_live": f"{sum(alloc_set(d['alloc_e']) == alloc_set(d['alloc_live']) for d in ref)}/{len(ref)}",
            "live_input_set_equals_live": f"{sum(alloc_set(d['alloc_l']) == alloc_set(d['alloc_live']) for d in ref)}/{len(ref)}",
        }
    a = out["regime"]["allocation_set_differs_share"]
    c = out["caps"]["any_factor_differs_share"]
    out["reading"] = ("material for allocation" if a is not None and a > MATERIAL
                      else "not material at both levels" if c is not None and c <= MATERIAL
                      else "not material for allocation; caps differ")
    return out


def live_trace(path: Path = TRACE) -> dict[str, dict[str, dict]]:
    """{date: {sector: row}}, the last row per (date, sector): recomputes after a restart append later rows."""
    out: dict[str, dict[str, dict]] = defaultdict(dict)
    for line in path.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            out[r["date"]][r["sector"]] = r
    return out


def live_daily_history(lo: date, hi_cutoff: str) -> list[dict]:
    """get_sector_history's daily aggregation (AVG per UTC calendar day per sector), read-only, lo..cutoff."""
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        rows = conn.execute("""
            SELECT strftime('%Y-%m-%d', timestamp) AS day, sector, ROUND(AVG(avg_score), 4) AS avg_score,
                   MAX(timestamp) AS last_ts
            FROM sector_snapshots WHERE timestamp > ? AND timestamp <= ?
            GROUP BY day, sector ORDER BY day""", (lo.isoformat(), hi_cutoff)).fetchall()
    finally:
        conn.close()
    return [{"day": d, "sector": s, "avg_score": v, "last_ts": t} for d, s, v, t in rows]


def walk(start: str, end: str) -> tuple[list[dict], dict]:
    import pandas as pd
    from backend.backtest import engine_fast as ef
    from backend.db import get_latest_sector_scores
    from backend.regime.regime_bayes import RegimeBayes, RegimeState
    from backend.sector_caps import cap_factors
    from backend.sector_regime import _compute_sector_stats

    pre = ef.precompute(start, end)
    timeline = ef.build_regime_timeline(pre)["days"]
    raw_data, trading_days = pre["raw_data"], pre["trading_days"]
    signal_cache, ticker_sector, sector_etf = pre["signal_cache"], pre["ticker_sector"], pre["sector_etf"]
    first = trading_days[0]
    warm_days = ef._trading_days(date.min, first - timedelta(days=1), raw_data)
    warm_cache = ef._build_signal_cache(raw_data, ticker_sector, sector_etf,
                                        ef._build_spy_cache(raw_data, warm_days),
                                        ef._build_etf_cache(raw_data, list(sector_etf.values()), warm_days), warm_days)
    scored_warm = sorted({date.fromisoformat(d) for (_, d) in warm_cache})
    sectors_cfg = {s: {"tickers": [], "etf": e} for s, e in sector_etf.items()}
    for t, s in ticker_sector.items():
        sectors_cfg[s]["tickers"].append(t)
    etf_map = {s: c["etf"] for s, c in sectors_cfg.items()}
    rb_e = RegimeBayes(sectors_cfg, etf_map, persisted=False)
    rb_l = RegimeBayes(sectors_cfg, etf_map, persisted=False)
    st_e = RegimeState.cold(sectors_cfg)
    st_l = RegimeState.cold(sectors_cfg)
    ipo = {s: ef.REGIME_IPO_NEUTRAL_SHARE for s in sectors_cfg}
    index = raw_data.index
    hist_e: list[dict] = []
    live_hist = live_daily_history(first - timedelta(days=366), eod_cutoff_utc(trading_days[-1]))
    trace = live_trace()
    days, c1_fail, c2_fail, c2_checked = [], [], [], 0

    for d in scored_warm + list(trading_days):
        ds = d.isoformat()
        prev_e, prev_l = st_e.last_result, st_l.last_result
        bucket = (prev_e.regime_state if prev_e else None) or "neutral"
        cache = signal_cache if d >= first else warm_cache
        by_sector: dict[str, list[float]] = defaultdict(list)
        for t, s in ticker_sector.items():
            e = cache.get((t, ds))
            if e is not None:
                by_sector[s].append(ef._regime_score(e["components"], bucket))
        snap_e = {s: round(sum(v) / len(v), 4) for s, v in by_sector.items()}
        hist_e.extend({"sector": s, "day": ds, "avg_score": v} for s, v in snap_e.items())
        horizon = (d - timedelta(days=365)).isoformat()
        while hist_e and hist_e[0]["day"] <= horizon:
            hist_e.pop(0)
        lo = index.searchsorted(pd.Timestamp(d - timedelta(days=90)), side="right")
        hi = index.searchsorted(pd.Timestamp(d), side="right")
        ohlcv = raw_data.iloc[lo:hi]
        if d < first:
            _, st_e, _ = rb_e.step(st_e, d, ohlcv, snap_e, ipo)
            _, st_l, _ = rb_l.step(st_l, d, ohlcv, snap_e, ipo)
            continue
        cutoff = eod_cutoff_utc(d)
        snap_l = get_latest_sector_scores(as_of=cutoff)
        hist_l = [{"sector": r["sector"], "day": r["day"], "avg_score": r["avg_score"]} for r in live_hist
                  if horizon < r["day"] and r["last_ts"] <= cutoff]
        caps_e = cap_factors(snap_e, _compute_sector_stats(hist_e), prev_e)
        caps_l = cap_factors(snap_l, _compute_sector_stats(hist_l), prev_l)
        tl = timeline.get(ds)
        if tl is None or tl["allocation"] != (dict(prev_e.allocation) if prev_e else {}) or tl["cap_factors"] != caps_e:
            c1_fail.append(ds)
        if ds in trace:
            c2_checked += 1
            logged = {s: r["aggregate_score"] for s, r in trace[ds].items()}
            if any(round(snap_l.get(s, 0.0), 4) != round(v, 4) for s, v in logged.items()):
                c2_fail.append({"date": ds, "logged": logged, "read": {s: snap_l.get(s) for s in logged}})
        res_e, st_e, _ = rb_e.step(st_e, d, ohlcv, snap_e, ipo)
        res_l, st_l, _ = rb_l.step(st_l, d, ohlcv, snap_l, ipo)
        days.append({
            "date": ds, "snap_e": snap_e, "snap_l": snap_l,
            "alloc_e": dict(res_e.allocation), "alloc_l": dict(res_l.allocation),
            "alloc_live": ({s: r["allocation"] for s, r in trace[ds].items()} if ds in trace else None),
            "leader_e": res_e.leader, "leader_l": res_l.leader,
            "state_e": res_e.regime_state, "state_l": res_l.regime_state,
            "post_e": dict(st_e.posteriors), "post_l": dict(st_l.posteriors),
            "caps_e": {s: list(v) for s, v in caps_e.items()}, "caps_l": {s: list(v) for s, v in caps_l.items()},
        })
    controls = {"c1_engine_reproduces_timeline": {"checked": len(days), "failed": c1_fail},
                "c2_live_input_equals_logged": {"checked": c2_checked, "failed": c2_fail[:5], "n_failed": len(c2_fail)},
                "warmup_sessions": len(scored_warm)}
    return days, controls


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    days, controls = walk(a.start, a.end)
    ok = not controls["c1_engine_reproduces_timeline"]["failed"] and not controls["c2_live_input_equals_logged"]["n_failed"]
    out = {"window": [a.start, a.end], "controls": controls, "controls_pass": ok,
           "summary": summarise(days) if ok and days else None, "days": days}
    Path(a.out).write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps({k: out[k] for k in ("window", "controls_pass", "summary")}, indent=1, default=str))
    if not ok:
        print("CONTROL FAILED — no read:", json.dumps(controls, default=str)[:2000])
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
