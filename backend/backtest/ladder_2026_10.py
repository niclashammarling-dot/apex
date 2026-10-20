"""APEX test ladder, 2026-10-20: cards T1-T6, run as written before any number existed.

Why: the 10-19 battery's edge rule failed everywhere, and the 10-20 probe found the RND null drifted
from its design (turnover, size). Niclas 2026-10-20: "small tests first, note results and then act
on the results"; "the baseline is important in every test"; "make the tests, all options are
valuable". The cards (question, null, baseline, validity gates, defects D1-D6, outcomes) are in
vault raw/notes/2026-10/2026-10-20-apex-test-ladder-and-cards.md, committed (vault 7f89f49f) before
this script ran. Exploratory: no config change follows; a card that answers becomes a v2 candidate.

How: python -u -m backend.backtest.ladder_2026_10 OUTDIR --cards T1,T2,T3,T4,B,T5,T6 [--windows IS,OOS]
     [--seeds 100] [--workers 3] [--battery /home/promenix/apex-battery/2026-10-19]
Precomputes each window once (as the battery), forks workers for T4 and the engine cards.

What: OUTDIR/<card>_<window>.json per card and window, saved as each finishes:
  T1 L0 rank IC by horizon, shuffled-score null, injected positive controls, ATR and momentum ICs.
  T2 L0 score deciles, picks vs same-day same-ATR-quintile non-picks, sector-ETF excess, movers ratio.
  T3 L1 top-k by score held h days with no exits: excess over the pool, the vol-matched expectation,
     B6 (top-k by ATR / by 20-day momentum) and SPY.
  T4 L2 picks and a vol-matched random set through engine_fast._check_exits_fast, one trade at a
     time: TP x SL grid, profit lock on/off, time-only exits, the exit null (random hold from the
     same configuration's hold distribution, paired), MFE/MAE, the close-to-close identity (D3).
  B  baseline block: B0 T-bill, B1 SPY, B3 equal-weight pool, B4 sector-ETF momentum.
  T5 L3 R1 IS start 0 against RND seeds under sizing without the score (S1-S4).
  T6 L3 R1 capacity variants (overflow, sector cap, slots, gate) at five starts IS and start 0 OOS.
"""
from __future__ import annotations

import argparse
import functools
import json
import math
import multiprocessing as mp
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from backend.backtest import battery_2026_10 as b
from backend.backtest import engine_fast as ef
from backend.backtest import profit_metrics as pm

PICK = 0.65                         # live Lock 1 threshold; SECTOR_THRESHOLD_FLOORS is empty
L0_HORIZONS = [1, 5, 10, 25]
HOLDS = [1, 2, 3, 5, 7, 10, 15, 20, 25]
TOPK = [5, 9]
SEED = 20261020
N_SHUFFLE = 200
NONE = 10.0                         # a TP/SL of 1,000 %: the leg never fires
TP_GRID = (0.03, 0.04, 0.06, 0.08, 0.12, NONE)
SL_GRID = (0.03, 0.04, 0.06, 0.08, 0.12, NONE)
LIVE_EXIT = {"tp": 0.06, "sl": 0.06, "pl": [0.04, 0.01], "tdays": 25}

_R: "b.Runner | None" = None
_SHARED: dict = {}


# ── Panels ─────────────────────────────────────────────────────────────────────

def panels(pre: dict) -> dict:
    """Days x tickers frames from the engine's own caches: close (forward-filled, the engine's
    fill price), score and atr_pct (NaN outside the pool), high/low (not filled), sector ETF close."""
    from backend.config import EXCLUDED_SECTORS
    days = [d.isoformat() for d in pre["trading_days"]]
    ts = {t: s for t, s in pre["ticker_sector"].items() if s not in EXCLUDED_SECTORS}
    tick = sorted(ts)
    pc, sc, oc = pre["price_cache"], pre["signal_cache"], pre.get("ohlc_cache") or {}
    close = pd.DataFrame({t: [pc.get((t, d), np.nan) for d in days] for t in tick}, index=days)
    score = pd.DataFrame({t: [sc[(t, d)]["signal_score"] if (t, d) in sc else np.nan for d in days] for t in tick}, index=days)
    atr = pd.DataFrame({t: [sc[(t, d)]["atr_pct"] if (t, d) in sc else np.nan for d in days] for t in tick}, index=days)
    high = pd.DataFrame({t: [getattr(oc.get((t, d)), "high", None) for d in days] for t in tick}, index=days).astype(float)
    low = pd.DataFrame({t: [getattr(oc.get((t, d)), "low", None) for d in days] for t in tick}, index=days).astype(float)
    etf_of = {t: pre["sector_etf"].get(s) for t, s in ts.items()}
    etfs = sorted({e for e in etf_of.values() if e})
    etf_close = pd.DataFrame({e: [pc.get((e, d), np.nan) for d in days] for e in etfs}, index=days)
    spy = pd.Series([pc.get(("SPY", d), np.nan) for d in days], index=days)
    mask = score.notna() & close.notna()
    return {"days": days, "tickers": tick, "sector": ts, "etf_of": etf_of, "close": close, "score": score,
            "atr": atr, "high": high, "low": low, "etf_close": etf_close, "spy": spy, "mask": mask,
            "mom20": close / close.shift(20) - 1}


def fwd(close: pd.DataFrame | pd.Series, h: int):
    return close.shift(-h) / close - 1


def row_rank_corr(x: pd.DataFrame, y: pd.DataFrame, mask: pd.DataFrame) -> pd.Series:
    """Per row: Spearman correlation of x and y over the cells where mask holds and both are finite."""
    ok = mask & x.notna() & y.notna()
    xr = x.where(ok).rank(axis=1)
    yr = y.where(ok).rank(axis=1)
    xc = xr.sub(xr.mean(axis=1), axis=0)
    yc = yr.sub(yr.mean(axis=1), axis=0)
    num = (xc * yc).sum(axis=1)
    den = np.sqrt((xc ** 2).sum(axis=1) * (yc ** 2).sum(axis=1))
    out = num / den
    return out.where(ok.sum(axis=1) >= 5)


def nonoverlap(series: pd.Series, h: int) -> dict:
    """Mean, t and MDE (80 % power, 5 % two-sided) on every h-th observation."""
    s = series.dropna().iloc[::max(h, 1)]
    n = len(s)
    if n < 3:
        return {"mean": None, "t": None, "n": n, "mde": None}
    sd = float(s.std(ddof=1))
    se = sd / math.sqrt(n)
    return {"mean": float(s.mean()), "t": float(s.mean() / se) if se > 0 else None, "n": n,
            "sd": sd, "mde": 2.8 * se}


def quintile(atr: pd.DataFrame, mask: pd.DataFrame) -> pd.DataFrame:
    """ATR quintile 0-4 within each day's pool (NaN outside it)."""
    r = atr.where(mask).rank(axis=1, pct=True)
    return np.ceil(r * 5).clip(1, 5) - 1


# ── T1 / T2 / T3 (L0, L1) ──────────────────────────────────────────────────────

def shuffled(score: pd.DataFrame, mask: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    a = score.where(mask).to_numpy(copy=True)
    for i in range(a.shape[0]):
        idx = np.flatnonzero(~np.isnan(a[i]))
        a[i, idx] = a[i, rng.permutation(idx)]
    return pd.DataFrame(a, index=score.index, columns=score.columns)


def injected(f: pd.DataFrame, mask: pd.DataFrame, target: float, rng: np.random.Generator) -> pd.DataFrame:
    """A synthetic score whose rank IC with f is about `target`: normal scores of f's within-day
    rank, times a = r/sqrt(1-r^2), plus unit noise (Pearson r of the two normals = target)."""
    from statistics import NormalDist
    pr = f.where(mask).rank(axis=1, pct=True)
    n = f.where(mask).notna().sum(axis=1)
    u = (pr.mul(n, axis=0) - 0.5).div(n, axis=0).to_numpy()
    zz = np.full(u.shape, np.nan)
    ok = ~np.isnan(u)
    zz[ok] = np.vectorize(NormalDist().inv_cdf)(u[ok])
    z = pd.DataFrame(zz, index=f.index, columns=f.columns)
    a = target / math.sqrt(1 - target ** 2)
    return a * z + rng.standard_normal(z.shape)


def card_t1(P: dict) -> dict:
    rng = np.random.default_rng(SEED)
    out = {"pool_size": {"median": float(P["mask"].sum(axis=1).median()),
                         "share_days_ge20": float((P["mask"].sum(axis=1) >= 20).mean())}, "h": {}}
    shuf = [shuffled(P["score"], P["mask"], rng) for _ in range(N_SHUFFLE)]
    for h in L0_HORIZONS:
        f = fwd(P["close"], h)
        r = {"score": nonoverlap(row_rank_corr(P["score"], f, P["mask"]), h),
             "atr_B6": nonoverlap(row_rank_corr(P["atr"], f, P["mask"]), h),
             "mom20_B6": nonoverlap(row_rank_corr(P["mom20"], f, P["mask"]), h)}
        null = [nonoverlap(row_rank_corr(s, f, P["mask"]), h) for s in shuf]
        means = np.array([x["mean"] for x in null])
        r["shuffle"] = {"mean_ic_median": float(np.median(means)), "p95": float(np.percentile(means, 95)),
                        "p5": float(np.percentile(means, 5)),
                        "reject_rate_5pct": float(np.mean([abs(x["t"]) > 1.96 for x in null])),
                        "p_score": float(((means >= r["score"]["mean"]).sum() + 1) / (len(means) + 1))}
        r["injected"] = {str(tgt): nonoverlap(row_rank_corr(injected(f, P["mask"], tgt, rng), f, P["mask"]), h)["mean"]
                         for tgt in (0.03, 0.10)}
        out["h"][str(h)] = r
    return out


def card_t2(P: dict) -> dict:
    q = quintile(P["atr"], P["mask"])
    pick = P["mask"] & (P["score"] >= PICK)
    non = P["mask"] & (P["score"] < PICK)
    qs_with_picks = pd.concat([q.where(pick).eq(k).any(axis=1) for k in range(5)], axis=1).sum(axis=1)
    out = {"validity": {"share_days_picks_in_ge3_quintiles": float((qs_with_picks >= 3).mean()),
                        "picks_per_day_median": float(pick.sum(axis=1).median())}, "h": {}}
    rank = P["score"].where(P["mask"]).rank(axis=1, pct=True)
    dec = np.ceil(rank * 10).clip(1, 10)
    daily_r = P["close"].pct_change()
    for h in L0_HORIZONS:
        f = fwd(P["close"], h)
        r: dict = {}
        r["deciles"] = {str(k): nonoverlap(f.where(dec == k).mean(axis=1), h)["mean"] for k in range(1, 11)}
        r["top_minus_bottom"] = nonoverlap(f.where(dec == 10).mean(axis=1) - f.where(dec == 1).mean(axis=1), h)
        # picks minus same-quintile non-picks, weighted by picks in the quintile
        num = pd.Series(0.0, index=f.index)
        wsum = pd.Series(0.0, index=f.index)
        absnum = pd.Series(0.0, index=f.index)
        rvnum = pd.Series(0.0, index=f.index)
        rv = daily_r.rolling(h).std().shift(-h) if h > 1 else daily_r.shift(-1).abs()
        for k in range(5):
            pk, nk = pick & (q == k), non & (q == k)
            npk = pk.sum(axis=1)
            ok = (npk > 0) & (nk.sum(axis=1) > 0)
            d = f.where(pk).mean(axis=1) - f.where(nk).mean(axis=1)
            da = f.abs().where(pk).mean(axis=1) / f.abs().where(nk).mean(axis=1)
            dv = (rv / P["atr"]).where(pk).mean(axis=1) / (rv / P["atr"]).where(nk).mean(axis=1)
            num += (d * npk).where(ok, 0.0)
            absnum += (da * npk).where(ok, 0.0)
            rvnum += (dv * npk).where(ok, 0.0)
            wsum += npk.where(ok, 0)
        r["vol_matched_excess"] = nonoverlap((num / wsum).where(wsum > 0), h)
        r["movers_abs_ratio"] = nonoverlap((absnum / wsum).where(wsum > 0) - 1.0, h)
        r["movers_rv_over_atr_ratio"] = nonoverlap((rvnum / wsum).where(wsum > 0) - 1.0, h)
        ef_ = fwd(P["etf_close"], h)
        etf_f = pd.DataFrame({t: ef_[e] if e in ef_ else np.nan for t, e in P["etf_of"].items()}, index=f.index)[f.columns]
        r["sector_excess_picks"] = nonoverlap((f - etf_f).where(pick).mean(axis=1), h)
        r["sector_excess_nonpicks"] = nonoverlap((f - etf_f).where(non).mean(axis=1), h)
        r["picks_minus_nonpicks_sector_excess"] = nonoverlap((f - etf_f).where(pick).mean(axis=1) - (f - etf_f).where(non).mean(axis=1), h)
        out["h"][str(h)] = r
    return out


def topk_mask(x: pd.DataFrame, mask: pd.DataFrame, k: int) -> pd.DataFrame:
    r = x.where(mask).rank(axis=1, ascending=False, method="first")
    return r <= k


def card_t3(P: dict) -> dict:
    q = quintile(P["atr"], P["mask"])
    pick = P["mask"] & (P["score"] >= PICK)
    out = {"validity": {str(k): float((pick.sum(axis=1) >= k).mean()) for k in TOPK}, "k": {}}
    for k in TOPK:
        sel = topk_mask(P["score"], P["mask"], k)
        selv = topk_mask(P["atr"], P["mask"], k)
        selm = topk_mask(P["mom20"], P["mask"] & P["mom20"].notna(), k)
        res = {}
        for h in HOLDS:
            f = fwd(P["close"], h)
            pool = f.where(P["mask"]).mean(axis=1)
            coh = f.where(sel).mean(axis=1)
            # vol-matched expectation: each selected ticker's same-day same-quintile pool mean
            qmean = pd.DataFrame(index=f.index, columns=f.columns, dtype=float)
            for j in range(5):
                m = f.where(P["mask"] & (q == j)).mean(axis=1)
                qmean = qmean.where(q != j, pd.DataFrame({c: m for c in f.columns}))
            vm = qmean.where(sel).mean(axis=1)
            spy = fwd(P["spy"], h)
            res[str(h)] = {"cohort": nonoverlap(coh, h)["mean"],
                           "excess_pool": nonoverlap(coh - pool, h),
                           "excess_vol_matched": nonoverlap(coh - vm, h),
                           "excess_pool_per_day": (nonoverlap(coh - pool, h)["mean"] or 0) / h,
                           "B6_atr_excess_pool": nonoverlap(f.where(selv).mean(axis=1) - pool, h),
                           "B6_mom_excess_pool": nonoverlap(f.where(selm).mean(axis=1) - pool, h),
                           "B3_pool": nonoverlap(pool, h)["mean"], "B1_spy": nonoverlap(spy, h)["mean"]}
        out["k"][str(k)] = res
    return out


# ── T4 (L2 exits) ──────────────────────────────────────────────────────────────

def entry_sets(P: dict) -> dict:
    """Picks (score >= PICK, every ticker-day with a later day) and the vol-matched random set: per
    pick, one random non-pick of the same day and ATR quintile. A pick with no such non-pick is
    dropped from both sets (counted)."""
    rng = np.random.default_rng(SEED)
    q = quintile(P["atr"], P["mask"]).to_numpy()
    sc = P["score"].where(P["mask"]).to_numpy()
    picks, matched, dropped = [], [], 0
    last = len(P["days"]) - 1
    for i in range(last):
        row_q, row_s = q[i], sc[i]
        for j in np.flatnonzero(row_s >= PICK):
            cand = np.flatnonzero((row_q == row_q[j]) & (row_s < PICK))
            if len(cand) == 0:
                dropped += 1
                continue
            picks.append((i, j))
            matched.append((i, int(rng.choice(cand))))
    return {"picks": picks, "matched": matched, "dropped": dropped}


def _simulate(P: dict, pre: dict, i: int, j: int, cfg: dict) -> dict:
    """One trade through engine_fast._check_exits_fast, day by day from the day after entry."""
    days = P["days"]
    t = P["tickers"][j]
    entry = float(P["close"].iat[i, j])
    trade = {"ticker": t, "sector": P["sector"][t], "entry_date": days[i], "entry_price": entry,
             "peak_price": entry, "shares": 1.0, "amount": 1.0, "signal_score": 0.0,
             "tp": cfg["tp"], "sl": cfg["sl"]}
    pl = cfg.get("pl")
    for k in range(i + 1, len(days)):
        ds = days[k]
        closed = ef._check_exits_fast([trade], pre["price_cache"], date.fromisoformat(ds), ds,
                                      cfg["tp"], cfg["sl"], cfg["tdays"], None,
                                      pl[0] if pl else None, pl[1] if pl else None,
                                      ohlc_cache=pre.get("ohlc_cache"))
        if closed:
            rec = closed[0]["record"]
            return {"pnl": rec["pnl_pct"], "reason": rec["exit_reason"], "hold": rec["days_held"], "exit_idx": k}
    k = len(days) - 1
    return {"pnl": float(P["close"].iat[k, j] / entry - 1), "reason": "END", "hold": k - i, "exit_idx": k}


def configs() -> dict[str, dict]:
    c = {}
    for tp in TP_GRID:
        for sl in SL_GRID:
            c[f"tp{tp:g}_sl{sl:g}_pl"] = {"tp": tp, "sl": sl, "pl": LIVE_EXIT["pl"], "tdays": 25}
            if tp == sl:
                c[f"tp{tp:g}_sl{sl:g}_nopl"] = {"tp": tp, "sl": sl, "pl": None, "tdays": 25}
    for h in (5, 10, 25):
        c[f"time{h}"] = {"tp": NONE, "sl": NONE, "pl": None, "tdays": h}
    return c


def _t4_job(name: str) -> tuple[str, dict]:
    P, pre, E = _SHARED["P"], _SHARED["pre"], _SHARED["E"]
    cfg = configs()[name]
    return name, {s: [_simulate(P, pre, i, j, cfg) for i, j in E[s]] for s in ("picks", "matched")}


def exit_stats(rows: list[dict]) -> dict:
    pnl = np.array([r["pnl"] for r in rows], dtype=float)
    hold = np.array([r["hold"] for r in rows], dtype=float)
    win = pnl > 0
    reasons: dict[str, int] = {}
    for r in rows:
        reasons[r["reason"]] = reasons.get(r["reason"], 0) + 1
    return {"n": len(rows), "win_rate": float(win.mean()), "avg_win": float(pnl[win].mean()) if win.any() else None,
            "avg_loss": float(pnl[~win].mean()) if (~win).any() else None, "exp_pct": float(pnl.mean()),
            "hold_mean": float(hold.mean()), "capital_day": float(pnl.sum() / max(hold.sum(), 1)),
            "reasons": reasons}


def exit_null(P: dict, E: list, live_rows: list[dict], rng: np.random.Generator) -> dict:
    """Each entry exits at the close of a random hold drawn from the live configuration's own hold
    distribution (trading-day offset); paired difference per entry, aggregated by entry month."""
    holds = np.array([max(1, r["hold"]) for r in live_rows])
    close = P["close"].to_numpy()
    last = len(P["days"]) - 1
    diffs = []
    for (i, j), r in zip(E, live_rows):
        h = int(rng.choice(holds))
        k = min(i + h, last)
        diffs.append((P["days"][i][:7], r["pnl"] - (close[k, j] / close[i, j] - 1)))
    s = pd.DataFrame(diffs, columns=["m", "d"]).groupby("m")["d"].mean()
    out = nonoverlap(s, 1)
    out["per_trade_mean"] = float(np.mean([d for _, d in diffs]))
    return out


def mfe_mae(P: dict, E: list, h: int = 25) -> dict:
    hi, lo, cl = P["high"].to_numpy(), P["low"].to_numpy(), P["close"].to_numpy()
    mfe, mae = [], []
    for i, j in E:
        seg_h, seg_l = hi[i + 1:i + 1 + h, j], lo[i + 1:i + 1 + h, j]
        if np.all(np.isnan(seg_h)):
            continue
        mfe.append(np.nanmax(seg_h) / cl[i, j] - 1)
        mae.append(np.nanmin(seg_l) / cl[i, j] - 1)
    mfe, mae = np.array(mfe), np.array(mae)
    qs = (10, 25, 50, 75, 90)
    return {"n": len(mfe), "mfe_q": {str(x): float(np.percentile(mfe, x)) for x in qs},
            "mae_q": {str(x): float(np.percentile(mae, x)) for x in qs},
            "share_mfe_ge_6": float((mfe >= 0.06).mean()), "share_mae_le_-6": float((mae <= -0.06).mean())}


def identity_check(P: dict, E: list, rows: list[dict]) -> dict:
    """D3: with no TP/SL and no profit lock, pnl must equal the close-to-close return of its own
    entry and exit dates (the record rounds pnl to 4 places)."""
    cl = P["close"].to_numpy()
    err = [abs(r["pnl"] - (cl[r["exit_idx"], j] / cl[i, j] - 1)) for (i, j), r in zip(E, rows)]
    return {"n": len(err), "max_abs_err": float(max(err)), "pass": bool(max(err) < 6e-5)}


def card_t4(P: dict, pre: dict, workers: int, log) -> dict:
    global _SHARED
    E = entry_sets(P)
    qa = quintile(P["atr"], P["mask"]).to_numpy()
    mix_p = np.bincount([int(qa[i, j]) for i, j in E["picks"]], minlength=5)
    mix_m = np.bincount([int(qa[i, j]) for i, j in E["matched"]], minlength=5)
    out: dict = {"entries": {"picks": len(E["picks"]), "matched": len(E["matched"]), "dropped": E["dropped"],
                             "quintile_mix_equal": bool((mix_p == mix_m).all())}, "configs": {}}
    _SHARED = {"P": P, "pre": pre, "E": E}
    names = list(configs())
    raw: dict = {}
    t0 = time.time()
    with mp.get_context("fork").Pool(workers) as pool:
        for n, (name, res) in enumerate(pool.imap_unordered(_t4_job, names), 1):
            raw[name] = res
            out["configs"][name] = {s: exit_stats(res[s]) for s in res}
            out["configs"][name]["picks_minus_matched_capital_day"] = (
                out["configs"][name]["picks"]["capital_day"] - out["configs"][name]["matched"]["capital_day"])
            log(f"T4 {n}/{len(names)} {name} {time.time() - t0:.0f}s")
    live = "tp0.06_sl0.06_pl"
    rng = np.random.default_rng(SEED)
    out["exit_null_live"] = {s: exit_null(P, E[s], raw[live][s], rng) for s in ("picks", "matched")}
    out["identity_time25"] = {s: identity_check(P, E[s], raw["time25"][s]) for s in ("picks", "matched")}
    sl_rows = [r for r in raw[live]["picks"] + raw[live]["matched"] if r["reason"] == "SL"]
    out["sanity_live"] = {"worst_sl_pnl": float(min(r["pnl"] for r in sl_rows)) if sl_rows else None,
                          "sl_below_-10pct": int(sum(r["pnl"] < -0.10 for r in sl_rows)),
                          "max_hold": int(max(r["hold"] for r in raw[live]["picks"] + raw[live]["matched"]))}
    out["mfe_mae"] = {s: mfe_mae(P, E[s]) for s in ("picks", "matched")}
    return out


# ── Baseline block ─────────────────────────────────────────────────────────────

def perf(r: pd.Series) -> dict:
    r = r.dropna()
    return {"total_return": float((1 + r).prod() - 1), "cagr": pm.cagr(r), "max_drawdown": pm.max_drawdown(r),
            "calmar": pm.calmar(r)}


def card_baselines(P: dict, rf: pd.Series) -> dict:
    idx = pd.DatetimeIndex(P["days"])
    spy = P["spy"].pct_change()
    pool = P["close"].pct_change().where(P["mask"].shift(1, fill_value=False)).mean(axis=1)
    e = P["etf_close"]
    mom = e / e.shift(20) - 1
    w = pd.DataFrame(0.0, index=e.index, columns=e.columns)
    for i in range(20, len(e), 5):                       # weekly: top 3 ETFs by 20-day return, equal weight
        top = mom.iloc[i].dropna().nlargest(3).index
        w.iloc[i:i + 5] = 0.0
        w.loc[w.index[i:i + 5], top] = 1 / 3
    b4 = (w.shift(1) * e.pct_change()).sum(axis=1).where(w.shift(1).sum(axis=1) > 0)
    rfd = rf.reindex(idx).ffill().fillna(0.0)
    out = {"B0_tbill": perf(pd.Series(rfd.to_numpy(), index=P["days"])), "B1_spy": perf(spy),
           "B3_pool_ew_daily": perf(pool), "B4_sector_etf_mom_top3_weekly": perf(b4)}
    return out


# ── T5 / T6 (L3 engine) ────────────────────────────────────────────────────────

def _engine(w: str, base: dict, start: int, over: dict, random_entries: dict | None = None) -> dict:
    from backend.backtest.engine_fast import run
    u = b.unit(w, base, start)
    pre = _R._window(w)
    days = pre["trading_days"][start:]
    pre_u = dict(pre)
    if u.regime_layer:
        pre_u["regime_timeline"] = _R._timeline(w, u.regime)
    kw = {**_R.incumbent(), **u.kwargs, **over}
    extra = {"random_entries": random_entries} if random_entries else {}
    with b.regime_constants(dict(u.regime)):
        res = run(days[0].isoformat(), days[-1].isoformat(), initial_balance=10_000.0,
                  precomputed=pre_u, **kw, **extra)
    return {"trade_log": res["trade_log"], "equity_curve": res["equity_curve"], "days": [d.isoformat() for d in days]}


def l3_metrics(w: str, res: dict) -> dict:
    mk = _R.market[w]
    closed = [t for t in res["trade_log"] if t.get("pnl") is not None]
    r = pm.daily_returns(res["equity_curve"])
    m = mk["spy"].pct_change().reindex(r.index)
    dep = pm.deployed_fraction(res["trade_log"], res["equity_curve"])
    cap_days = sum(t["days_held"] * t["amount"] for t in closed)
    up, dn = m > 0, m < 0
    em = pm.spy_em_returns(mk["spy"], r.index, float(dep.mean()) if len(dep) else 0.0, mk["rf"])
    return {"trades": len(closed), "exp_pct": pm.expectancy(closed)["pct"]["mean"],
            "pnl_per_capital_day": sum(t["pnl"] for t in closed) / cap_days if cap_days else None,
            "total_return": float((1 + r).prod() - 1), "calmar": pm.calmar(r), "max_drawdown": pm.max_drawdown(r),
            "alpha_tbill": pm.alpha_beta(r, m, mk["rf"])["alpha_annual"], "mean_deployed": float(dep.mean()),
            "upside_capture": float(r[up].mean() / m[up].mean()), "downside_capture": float(r[dn].mean() / m[dn].mean()),
            "amount_mean": float(np.mean([t["amount"] for t in closed])),
            "B2_spy_em": perf(em)}


def ref_fractions(store: "b.Store") -> list[float]:
    """Amount over the previous day's equity, for every entry of the stored 10-19 reference R1 IS start 0."""
    ref = store.get(b.unit("IS", b.R1, 0))
    eq = {p["date"]: p["balance"] for p in ref["equity_curve"]}
    days = ref["trading_days"]
    prev = {days[i]: eq.get(days[i - 1], eq.get(days[i])) for i in range(1, len(days))}
    prev[days[0]] = 10_000.0
    return [t["amount"] / prev[t["entry_date"]] for t in ref["trade_log"] if prev.get(t["entry_date"])]


def _t5_job(job: tuple) -> tuple:
    arm, over, seed, ref_ep, ref_open = job
    o = dict(over)
    if "size_draw" in o:
        o["size_draw"] = {**o["size_draw"], "seed": 1000 + seed}
    res = _engine("IS", b.R1, 0, o, {"seed": seed, "per_day": ref_ep, "open_cap": ref_open})
    m = l3_metrics("IS", res)
    if "size_usd" in over:
        m["share_at_fixed_usd"] = float(np.mean([abs(t["amount"] - over["size_usd"]) < 1e-6 for t in res["trade_log"]]))
    return arm, seed, m


def card_t5(store: "b.Store", seeds: int, workers: int, log) -> dict:
    fr = np.array(ref_fractions(store))
    dec = [float(np.percentile(fr, p)) for p in range(5, 100, 10)]
    med, low = float(np.median(fr)), float(np.percentile(fr, 10))
    arms = {"S1_fixed_usd": {"size_usd": round(med * 10_000, 2)},
            "S2_random_draw": {"size_draw": {"seed": 0, "fractions": dec}},
            "S3_median_fraction": {"size_fixed": med},
            "S4_lowest_fraction": {"size_fixed": low}}
    out: dict = {"fractions": {"median": med, "p10": low, "deciles": dec, "n": len(fr)}, "arms": {}}
    jobs = []
    for arm, over in arms.items():
        res = _engine("IS", b.R1, 0, over)
        m = l3_metrics("IS", res)
        if "size_usd" in over:
            m["share_at_fixed_usd"] = float(np.mean([abs(t["amount"] - over["size_usd"]) < 1e-6 for t in res["trade_log"]]))
        out["arms"][arm] = {"override": {k: (v if not isinstance(v, dict) else {**v}) for k, v in over.items()},
                            "ref": m, "rnd": {}}
        ep = b._entries_per_day(res["trade_log"])
        op = b.open_after_entries(res["trade_log"], res["days"])
        jobs += [(arm, over, s, ep, op) for s in range(1, seeds + 1)]
        log(f"T5 {arm} ref trades {m['trades']}")
    t0 = time.time()
    with mp.get_context("fork").Pool(workers) as pool:
        for n, (arm, seed, m) in enumerate(pool.imap_unordered(_t5_job, jobs), 1):
            out["arms"][arm]["rnd"][str(seed)] = m
            if n % 20 == 0 or n == len(jobs):
                log(f"T5 {n}/{len(jobs)} {time.time() - t0:.0f}s")
    for arm, v in out["arms"].items():
        seeds_ = list(v["rnd"].values())
        v["read"] = {k: {"ref": v["ref"][k], "rnd_median": float(np.median([s[k] for s in seeds_])),
                         "rnd_p95": float(np.percentile([s[k] for s in seeds_], 95)),
                         "p": float((sum(s[k] >= v["ref"][k] for s in seeds_) + 1) / (len(seeds_) + 1))}
                     for k in ("pnl_per_capital_day", "exp_pct", "total_return", "alpha_tbill", "trades", "amount_mean")}
        v["validity"] = {"rnd_mean_size_over_ref": float(np.mean([s["amount_mean"] for s in seeds_]) / v["ref"]["amount_mean"])}
        if arm == "S1_fixed_usd":
            v["validity"]["ref_share_at_fixed_usd"] = v["ref"]["share_at_fixed_usd"]
            v["validity"]["rnd_min_share_at_fixed_usd"] = float(min(s["share_at_fixed_usd"] for s in seeds_))
    return out


T6_VARIANTS = {
    "live_overflow_0.05": {},
    "hardcap": {"overflow_quant_increment": math.inf},
    "hardcap_cap0.40": {"overflow_quant_increment": math.inf, "max_sector_exposure": 0.40},
    "hardcap_cap0.50": {"overflow_quant_increment": math.inf, "max_sector_exposure": 0.50},
    "hardcap_slots12": {"overflow_quant_increment": math.inf, "max_positions": 12},
    "overflow_0.10": {"overflow_quant_increment": 0.10},
    "overflow_0.20": {"overflow_quant_increment": 0.20},
    "threshold_0.70": {"lock1_threshold": 0.70},
}


def _t6_job(job: tuple) -> tuple:
    w, start, name = job
    return w, start, name, l3_metrics(w, _engine(w, b.R1, start, T6_VARIANTS[name]))


def card_t6(workers: int, log) -> dict:
    jobs = [("IS", s, n) for s in b.RND_STARTS for n in T6_VARIANTS] + [("OOS", 0, n) for n in T6_VARIANTS]
    out: dict = {"IS": {}, "OOS": {}}
    with mp.get_context("fork").Pool(workers) as pool:
        for k, (w, s, n, m) in enumerate(pool.imap_unordered(_t6_job, jobs), 1):
            out[w].setdefault(n, {})[str(s)] = m
            log(f"T6 {k}/{len(jobs)}")
    return out


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    global _R
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir", type=Path)
    ap.add_argument("--cards", default="T1,T2,T3,B,T4,T5,T6")
    ap.add_argument("--windows", default="IS,OOS")
    ap.add_argument("--seeds", type=int, default=100)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--battery", type=Path, default=Path("/home/promenix/apex-battery/2026-10-19"))
    a = ap.parse_args()
    a.outdir.mkdir(parents=True, exist_ok=True)
    cards = a.cards.split(",")
    t0 = time.time()

    def log(msg: str) -> None:
        print(f"[{time.time() - t0:6.0f}s] {msg}", flush=True)

    def save(name: str, obj: dict) -> None:
        (a.outdir / f"{name}.json").write_text(json.dumps(obj, indent=1, default=str))
        log(f"saved {name}")

    ef._trading_days_count = functools.lru_cache(maxsize=None)(ef._trading_days_count)   # pure; speed only
    store = b.Store(a.battery)
    _R = b.Runner(store, "ladder")
    for w in a.windows.split(","):
        pre = _R._window(w)
        P = panels(pre)
        log(f"{w} panels: {len(P['days'])} days, {len(P['tickers'])} tickers")
        if "T1" in cards:
            save(f"T1_{w}", card_t1(P))
        if "T2" in cards:
            save(f"T2_{w}", card_t2(P))
        if "T3" in cards:
            save(f"T3_{w}", card_t3(P))
        if "B" in cards:
            save(f"B_{w}", card_baselines(P, _R.market[w]["rf"]))
        if "T4" in cards:
            save(f"T4_{w}", card_t4(P, pre, a.workers, log))
    if "T5" in cards or "T6" in cards:
        for w in ("IS", "OOS"):
            _R._window(w)
        _R.frozen = True
    if "T5" in cards:
        save("T5_IS", card_t5(store, a.seeds, a.workers, log))
    if "T6" in cards:
        save("T6", card_t6(a.workers, log))
    log("done")


if __name__ == "__main__":
    sys.exit(main())
