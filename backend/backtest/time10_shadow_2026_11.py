"""
TIME10 shadow exit card (vault raw/notes/2026-10/2026-10-24-apex-time10-shadow-exit-card.md, pre-registered
2026-10-24 before any TIME10 number on live entries; Niclas's exception to T8 outcome 12). On APEX's live
entries, does exiting at the 10th session's close (no TP, no SL, no lock) earn more per capital-day than the
live bracket (TP 6 %, SL 6 %, lock 4 %/1 %, 25 days)? The selection is given, so only the exit is tested.

Every canonical live entry (db.live_sample_rows) is entered at its entry day's close through T7's simulator
(ladder_2026_10._simulate, the engine's own exit function, T7's peak model) under LIVE and TIME10. Retro: entries
before 2026-10-24; forward: from it (out of sample by construction). An entry whose TIME10 or LIVE exit lies past
the window's last session waits; it is not dropped. ALLCAP8 (capped-stop card) and TP4NS-ATR (ATR card) are
simulated on every entry beside them, report-only (INDEX addition 2026-10-24). The 2022 row is read from T8's
saved L2 rows (L2_IS_rows.npz), its entries rebuilt from the battery IS window and verified against T8's stored
2022 capital-days before use.

Usage: python -u -m backend.backtest.time10_shadow_2026_11 OUT.json [--end YYYY-MM-DD|latest] [--flips 10000]
       [--previous PREV.json]
Reads the production DB read-only. Writes OUT.json and OUT.rows.csv. Read monthly with T7 (first read 2026-11-20).
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

CARD = "raw/notes/2026-10/2026-10-24-apex-time10-shadow-exit-card.md"
FORWARD_FROM = "2026-10-24"
SEED = 20261114
FLIPS = 10_000                    # § 4
N_NEGATIVE = 500                  # § 7 negative control
N_BOOT = 2000                     # § 7 positive control, resampled per-trade differences
N_RND = 20                        # same-day, same-ATR-quintile draws per entry for the win-rate baseline (§ 3)
POSITIVE_SHIFT = 0.0002           # § 7: +0.02 %/day
MATTERS = 0.003                   # § 8: about 0.3 pt per trade, the effect that matters (T4 on R9, estimate)
MDE_MAX = 0.005                   # § 9 (c): realised MDE <= 0.5 pt per trade
FIDELITY_SIGN = 0.90              # § 6
FIDELITY_MEAN = 0.005             # § 6: mean per-trade difference within +/-0.5 pt
TAIL_PT = 0.02                    # § 9 (d), § 10: p95 loss not worse by more than 2 pt
YEAR_2022 = ("2022-01-03", "2022-12-30")
T8_DIR = Path("/home/promenix/apex-battery/t8-2026-10-22")
BATTERY = Path("/home/promenix/apex-battery/2026-10-19")


# ── Statistics (pure; tests/test_time10_shadow_2026_11.py) ────────────────────

def capital_day(pnl: np.ndarray, hold: np.ndarray) -> float | None:
    """Σ pnl / Σ days held (§ 3)."""
    days = float(np.sum(hold))
    return float(np.sum(pnl)) / days if days > 0 else None


def swap_permutation(pt, ht, pl, hl, flips: int, rng: np.random.Generator) -> dict:
    """§ 4: paired sign-flip, each trade's two arms swapped at random; the statistic is the capital-day
    difference TIME10 minus LIVE recomputed under each flip."""
    pt, ht, pl, hl = (np.asarray(x, dtype=float) for x in (pt, ht, pl, hl))
    obs = capital_day(pt, ht) - capital_day(pl, hl)
    s = rng.random((flips, len(pt))) < 0.5
    a_p, a_h = np.where(s, pl, pt).sum(1), np.where(s, hl, ht).sum(1)
    b_p, b_h = np.where(s, pt, pl).sum(1), np.where(s, ht, hl).sum(1)
    null = a_p / a_h - b_p / b_h
    return {"diff": float(obs), "p_one_sided": float((np.sum(null >= obs) + 1) / (flips + 1)),
            "p_two_sided": float((np.sum(np.abs(null) >= abs(obs)) + 1) / (flips + 1))}


def per_trade(pt, ht, pl, hl) -> dict:
    """Paired per-trade capital-day differences: mean, sd, t, and the MDE (2.8 se, § 8)."""
    d = np.asarray(pt, float) / np.maximum(np.asarray(ht, float), 1) - np.asarray(pl, float) / np.maximum(np.asarray(hl, float), 1)
    n = len(d)
    if n < 2:
        return {"n": n, "mean": None, "sd": None, "t": None, "mde_per_day": None}
    sd = float(d.std(ddof=1))
    se = sd / math.sqrt(n)
    return {"n": n, "mean": float(d.mean()), "sd": sd, "t": float(d.mean() / se) if se > 0 else None,
            "mde_per_day": 2.8 * se}


def mde_per_trade(pt, pl) -> float | None:
    """§ 8's MDE in points per trade: 2.8 x sd(per-trade pnl difference) / sqrt(n)."""
    d = np.asarray(pt, float) - np.asarray(pl, float)
    return float(2.8 * d.std(ddof=1) / math.sqrt(len(d))) if len(d) > 1 else None


def tail(pnl: np.ndarray) -> dict:
    """Per-trade loss tail: p95 loss (the 5th percentile of pnl) and the max loss."""
    pnl = np.asarray(pnl, float)
    return {"p95_loss": float(np.percentile(pnl, 5)), "max_loss": float(pnl.min())} if len(pnl) else {}


def fidelity(sim: np.ndarray, real: np.ndarray) -> dict:
    """§ 6: simulated LIVE against realised live on the same entries."""
    sim, real = np.asarray(sim, float), np.asarray(real, float)
    if not len(sim):
        return {"n": 0, "valid": False}
    agree = float(((sim > 0) == (real > 0)).mean())
    mean_diff = float((sim - real).mean())
    return {"n": len(sim), "sign_agreement": agree, "mean_diff": mean_diff,
            "valid": agree >= FIDELITY_SIGN and abs(mean_diff) <= FIDELITY_MEAN}


def negative_control(pnl, hold, rng: np.random.Generator, n: int = N_NEGATIVE) -> dict:
    """§ 7: LIVE against itself, entries split at random into two labels; Welch's t on per-trade
    capital-day returns should reject at about 5 %."""
    r = np.asarray(pnl, float) / np.maximum(np.asarray(hold, float), 1)
    rej = 0
    for _ in range(n):
        lab = rng.random(len(r)) < 0.5
        a, b = r[lab], r[~lab]
        if len(a) < 2 or len(b) < 2:
            continue
        se = math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
        rej += se > 0 and abs(a.mean() - b.mean()) / se > 1.96
    return {"n": n, "reject_rate_5pct": rej / n}


def positive_control(d: np.ndarray, rng: np.random.Generator, shift: float = POSITIVE_SHIFT) -> dict:
    """§ 7: the per-trade capital-day differences centred, the shift added, trades resampled;
    detection = one-sided t > 1.645. Reports the detection rate at the stated power."""
    d = np.asarray(d, float)
    if len(d) < 3:
        return {"n": len(d), "detect_rate": None}
    c = d - d.mean()
    boot = c[rng.integers(0, len(c), size=(N_BOOT, len(c)))] + shift
    t = boot.mean(1) / (boot.std(1, ddof=1) / math.sqrt(len(c)))
    return {"n": len(d), "shift_per_day": shift, "detect_rate": float((t > 1.645).mean())}


def decide(perm: dict, pt_stats: dict, mde_pt: float | None, tails: dict, fid: dict) -> dict:
    """§ 9 rules (a)-(d) on one population."""
    a = bool(fid.get("valid"))
    t = pt_stats.get("t")
    b = t is not None and t <= -2
    c = (perm["diff"] > 0 and perm["p_one_sided"] <= 0.05 and mde_pt is not None and mde_pt <= MDE_MAX)
    d = tails["TIME10"]["p95_loss"] >= tails["LIVE"]["p95_loss"] - TAIL_PT
    if not a:
        outcome = "invalid: simulator fidelity failed, no reading"
    elif b:
        outcome = "sign veto (b): TIME10 not proposed for live"
    elif c and d:
        outcome = "support (c) and tail (d) hold: propose with the 2022 row"
    else:
        outcome = "recorded, no action" + (" (unanswered at this power, D4)" if mde_pt is not None and mde_pt > MDE_MAX else "")
    return {"a_fidelity": a, "b_sign_veto": b, "c_support": bool(c), "d_tail": bool(d), "outcome": outcome}


def criterion_controls(pt, pl, hl, rng: np.random.Generator, flips: int) -> dict:
    """§ 7 per criterion: a synthetic arm 'LIVE + the effect that matters' must pass each rule and the arm 'LIVE'
    must fail it; a rule the synthetic winner cannot pass at this n is reported (D4 when it is power, D8 if not).
    The synthetic arms carry the observed spread of the per-trade differences (TIME10 minus LIVE, centred), so a
    rule is judged at the variance the real read meets, not on a noiseless shift."""
    pt, pl, hl = np.asarray(pt, float), np.asarray(pl, float), np.asarray(hl, float)
    noise = (pt - pl) - (pt - pl).mean()
    fid = {"valid": True}
    out = {}
    for name, arm in (("winner", pl + MATTERS + noise), ("live", pl.copy())):
        perm = swap_permutation(arm, hl, pl, hl, flips, rng)
        rule = decide(perm, per_trade(arm, hl, pl, hl), mde_per_trade(arm, pl),
                      {"TIME10": tail(arm), "LIVE": tail(pl)}, fid)
        out[name] = {k: rule[k] for k in ("b_sign_veto", "c_support", "d_tail")}
    loser = pl - 10 * MATTERS + noise                 # (b) must fire on a clearly worse arm
    out["veto_fires_on_a_worse_arm"] = decide(swap_permutation(loser, hl, pl, hl, flips, rng),
                                              per_trade(loser, hl, pl, hl), None,
                                              {"TIME10": tail(loser), "LIVE": tail(pl)}, fid)["b_sign_veto"]
    out["c_passable_at_this_n"] = out["winner"]["c_support"]
    if not out["c_passable_at_this_n"]:
        out["c_note"] = "the synthetic winner cannot pass (c) at this n: power (D4, card § 8), not a criterion defect"
    return out


# ── Data ──────────────────────────────────────────────────────────────────────

def arm_cfgs(atr_pct: float) -> dict[str, dict]:
    from backend.backtest import exits_atr_2026_10 as xa
    from backend.backtest import exits_capped_stop_2026_10 as cs
    from backend.backtest import exits_t8_2026_10 as t8
    return {"LIVE": dict(t8.ARMS["LIVE"]), "TIME10": dict(t8.ARMS["TIME10"]),
            "ALLCAP8": cs.leg_cfg("ALLCAP8", atr_pct), "TP4NS_ATR": xa.twin_cfg("P3", atr_pct)}


def simulate_entries(P: dict, pre: dict, live: list[dict], rng: np.random.Generator) -> pd.DataFrame:
    from backend.backtest import ladder_2026_10 as ld
    days, tick = P["days"], P["tickers"]
    close, atr, m = P["close"].to_numpy(), P["atr"].to_numpy(), P["mask"].to_numpy()
    q = ld.quintile(P["atr"], P["mask"]).to_numpy()
    last = len(days) - 1
    out = []
    for t in live:
        d, tk = t["timestamp"][:10], t["ticker"]
        rec = {"id": t["id"], "ticker": tk, "d": d, "forward": d >= FORWARD_FROM,
               "realised": t["pnl"] / t["notional"] if t.get("notional") else np.nan, "status": "waiting"}
        if d not in days or tk not in tick or not np.isfinite(close[days.index(d), tick.index(tk)]):
            rec["status"] = "not in window"
            out.append(rec)
            continue
        i, j = days.index(d), tick.index(tk)
        cfgs = arm_cfgs(float(atr[i, j]))
        sims = {a: ld._simulate(P, pre, i, j, c) for a, c in cfgs.items()}
        if i + 10 <= last and sims["LIVE"]["reason"] != "END" and sims["TIME10"]["reason"] != "END":
            rec["status"] = "complete"
            cand = np.flatnonzero(m[i] & (q[i] == q[i, j])) if m[i, j] else np.flatnonzero(m[i])
            cand = cand[cand != j]
            for a in ("LIVE", "TIME10"):
                if len(cand):
                    draws = [ld._simulate(P, pre, i, int(k), cfgs[a])["pnl"] for k in rng.choice(cand, N_RND)]
                    rec[f"{a}_rnd_win"] = float(np.mean(np.array(draws) > 0))
        for a, s in sims.items():
            rec[f"{a}_pnl"], rec[f"{a}_hold"], rec[f"{a}_reason"] = s["pnl"], s["hold"], s["reason"]
        out.append(rec)
    return pd.DataFrame(out)


def read_population(M: pd.DataFrame, rng: np.random.Generator, flips: int, window_end: str | None = None) -> dict:
    c = M[M.status == "complete"]
    if len(c) < 2:
        return {"n": len(c), "note": "fewer than two complete entries"}
    pt, ht, pl, hl = (c[k].to_numpy(float) for k in ("TIME10_pnl", "TIME10_hold", "LIVE_pnl", "LIVE_hold"))
    fid = fidelity(c.LIVE_pnl.to_numpy(float), c.realised.to_numpy(float))
    perm = swap_permutation(pt, ht, pl, hl, flips, rng)
    stats = per_trade(pt, ht, pl, hl)
    b0 = tbill_per_day(c.d.min(), window_end)
    mde_pt = mde_per_trade(pt, pl)
    tails = {a: tail(c[f"{a}_pnl"].to_numpy(float)) for a in ("LIVE", "TIME10", "ALLCAP8", "TP4NS_ATR")}
    arms = {a: {"capital_day": capital_day(c[f"{a}_pnl"].to_numpy(float), c[f"{a}_hold"].to_numpy(float)),
                "win_rate": float((c[f"{a}_pnl"] > 0).mean()),
                **({"win_rate_same_exit_random": float(c[f"{a}_rnd_win"].mean())} if f"{a}_rnd_win" in c else {})}
            for a in ("LIVE", "TIME10", "ALLCAP8", "TP4NS_ATR")}
    return {"n": len(c), "fidelity": fid, "arms": arms, "permutation": perm, "per_trade": stats,
            "mde_per_trade": mde_pt, "tails": tails,
            "baseline": {"b0_tbill_per_day": b0, "realised_mean_pnl": float(c.realised.mean()),
                         "simulated_live_mean_pnl": float(c.LIVE_pnl.mean())},
            "rules": decide(perm, stats, mde_pt, tails, fid),
            "controls": {"negative": negative_control(pl, hl, rng),
                         "positive": positive_control(pt / np.maximum(ht, 1) - pl / np.maximum(hl, 1), rng),
                         "per_criterion": criterion_controls(pt, pl, hl, rng, min(flips, 2000))}}


def tbill_per_day(start: str | None, end: str | None) -> dict:
    """§ 5 B0: the 13-week T-bill (^IRX, annual %) as a daily rate, averaged over the read's days. A failed
    download is reported, not skipped."""
    if not start or not end:
        return {"value": None, "reason": "no complete entries"}
    try:
        import yfinance as yf
        irx = yf.download("^IRX", start=start, end=(pd.Timestamp(end) + pd.Timedelta(days=1)).date().isoformat(),
                          progress=False, auto_adjust=False)["Close"]
        rate = float(np.asarray(irx, float).ravel().mean()) / 100 / 252
        return {"value": rate, "source": "^IRX close mean / 100 / 252", "days": int(len(irx))}
    except Exception as e:
        return {"value": None, "reason": f"^IRX download failed: {e}"}


def row_2022(log=print) -> dict:
    """§ 10 from T8's saved L2 IS rows (polled peak, T8's deciding model). The picks are rebuilt from the battery
    IS window as T8 built them; the rebuild counts as T8's only if it reproduces T8's stored 2022 capital-days."""
    from backend.backtest import battery_2026_10 as b
    from backend.backtest import exits_t8_2026_10 as t8
    from backend.backtest import ladder_2026_10 as ld
    z = np.load(T8_DIR / "L2_IS_rows.npz")
    stored = json.loads((T8_DIR / "L2_IS.json").read_text())["pairs"]
    P = ld.panels(b.Runner(b.Store(BATTERY), "time10")._window("IS"))
    picks, _ = t8.purge(ld.entry_sets(P)["picks"], len(P["days"]))
    n = len(z["LIVE|polled|picks|pnl"])
    if len(picks) != n:
        return {"valid": False, "reason": f"rebuilt picks {len(picks)} != T8's {n}"}
    keep = np.array([YEAR_2022[0] <= P["days"][i] <= YEAR_2022[1] for i, _ in picks])
    res = {}
    for a in ("TIME10", "LIVE"):
        pnl, hold = z[f"{a}|polled|picks|pnl"][keep].astype(float), z[f"{a}|polled|picks|hold"][keep].astype(float)
        res[a] = {"n": int(keep.sum()), "capital_day": capital_day(pnl, hold), **tail(pnl),
                  "t8_stored_capital_day": stored[f"{a}|polled|picks"]["year_2022"]["capital_day"]}
    ok = all(abs(res[a]["capital_day"] - res[a]["t8_stored_capital_day"]) < 1e-6 for a in res)
    diff = res["TIME10"]["capital_day"] - res["LIVE"]["capital_day"]
    holds = diff >= 0 and res["TIME10"]["p95_loss"] >= res["LIVE"]["p95_loss"] - TAIL_PT
    log(f"2022 row: rebuild {'matches' if ok else 'DOES NOT match'} T8's stored capital-days; diff {diff:+.6f}/day")
    return {"valid": ok, "arms": res, "capital_day_diff": diff, "holds": bool(holds) if ok else None,
            "verdict": ("holds" if holds else "regime-conditional") if ok else "invalid: rebuild does not reproduce T8"}


def instability(cur: dict, prev_path: Path | None) -> dict:
    """§ 9: a point estimate that changes sign in two consecutive monthly reads is instability."""
    if not prev_path or not prev_path.exists():
        return {"previous": None}
    prev = json.loads(prev_path.read_text()).get("retro", {}).get("permutation", {}).get("diff")
    now = cur.get("permutation", {}).get("diff")
    flip = prev is not None and now is not None and (prev > 0) != (now > 0)
    return {"previous": str(prev_path), "previous_diff": prev, "diff": now, "sign_changed": flip}


def main(argv: list[str] | None = None) -> int:
    from backend.backtest import ladder_2026_10 as ld
    from backend.backtest import rejected_candidates_2026_10 as rc
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--end", default="latest")
    ap.add_argument("--flips", type=int, default=FLIPS)
    ap.add_argument("--previous", type=Path, default=None)
    a = ap.parse_args(argv)
    t0 = time.time()
    log = lambda s: print(f"[{time.time() - t0:5.0f}s] {s}", flush=True)
    since = json.loads(rc.LIVE_CONFIG.read_text()).get("live_account_since")
    import backend.db as db
    db.DB_PATH = rc.LIVE_DB                           # read-only use: live_sample_rows only
    sqlite3.connect(f"file:{rc.LIVE_DB}?mode=ro", uri=True).close()
    live = db.live_sample_rows(since=since)
    pre = rc.dated_window(a.end)
    P = ld.panels(pre)
    log(f"{len(live)} canonical live entries since {since}; window ends {P['days'][-1]}")
    rng = np.random.default_rng(SEED)
    M = simulate_entries(P, pre, live, rng)
    log(f"simulated: {M.status.value_counts().to_dict()}")
    out = {"card": CARD, "since": since, "window_end": P["days"][-1], "live_sample": db.LIVE_SAMPLE,
           "simulator": "ladder_2026_10._simulate, T7's peak model", "forward_from": FORWARD_FROM,
           "status": M.status.value_counts().to_dict(),
           "retro": read_population(M[~M.forward], rng, a.flips, P["days"][-1]),
           "forward": read_population(M[M.forward], rng, a.flips, P["days"][-1])}
    out["instability"] = instability(out["retro"], a.previous)
    out["row_2022"] = row_2022(log)
    a.out.write_text(json.dumps(out, indent=1, default=str))
    M.to_csv(a.out.with_suffix(".rows.csv"), index=False)
    log(f"saved {a.out}: retro {out['retro'].get('rules', {}).get('outcome')}; 2022 {out['row_2022'].get('verdict')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
