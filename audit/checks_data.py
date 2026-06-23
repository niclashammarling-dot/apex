"""
Data-integrity mechanical checks — CHECKs 14, 15, 17, 22, 33, 35, 39, 44, 46, 47, 55, 56.

Covers: EOD regime freshness, calibration freshness, sentiment cache,
Yahoo data pipeline health, Bayesian multiplier health, PCR collection
freshness, live peak_price integrity, regime-conditioned aggregator
weight validation, profit-lock ratchet wiring integrity, IPO sentiment
consecutive-zero detection, and EDGAR S-1 search API health.
"""
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

import requests

from audit._audit_core import REPO, _most_recent_trading_day, flag


# ── CHECK 14 — EOD regime freshness ──────────────────────────────────────────

def check14():
    """
    sector_posteriors.updated_at must be within 3 calendar days.
    3 days covers the widest normal gap: Sunday 1 AM audit, last EOD on Friday.
    Anything older means the catch-up also failed.
    """
    db = REPO / "data/apex.db"
    if not db.exists():
        return
    try:
        conn = sqlite3.connect(db)
        row  = conn.execute("SELECT MAX(updated_at) FROM sector_posteriors").fetchone()
        conn.close()
    except Exception as e:
        flag(14, "EOD regime freshness", "WARNING", "data/apex.db:sector_posteriors",
             f"could not query sector_posteriors: {e}")
        return

    if not row or not row[0]:
        flag(14, "EOD regime freshness", "CRITICAL", "data/apex.db:sector_posteriors",
             "sector_posteriors empty — EOD regime has never run")
        return

    last_dt = datetime.fromisoformat(row[0])
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    age_days = (datetime.now(timezone.utc) - last_dt).total_seconds() / 86400

    if age_days > 3:
        flag(14, "EOD regime freshness", "WARNING", "data/apex.db:sector_posteriors",
             f"EOD regime last updated {age_days:.1f} days ago ({row[0][:10]}) — catch-up may have failed")


# ── CHECK 15 — Calibration freshness ─────────────────────────────────────────

def check15():
    """
    data/calibration_done.txt must contain the current ISO week label.
    Missing or stale marker means Sunday 3 AM cron was missed AND catch-up failed.
    """
    marker       = REPO / "data/calibration_done.txt"
    current_week = datetime.now(timezone.utc).strftime("%G-W%V")

    if not marker.exists():
        flag(15, "Calibration freshness", "WARNING", "data/calibration_done.txt:—",
             f"calibration marker missing — thresholds not calibrated this week ({current_week})")
        return

    stored = marker.read_text().strip()
    if stored != current_week:
        flag(15, "Calibration freshness", "WARNING", "data/calibration_done.txt:—",
             f"calibration last ran {stored}, current week {current_week} — catch-up may have failed")


# ── CHECK 17 — Sentiment cache freshness ─────────────────────────────────────

def check17():
    """
    sentiment_cache must have been populated today (Mon–Fri).
    A stale or missing cache means the 9:35 AM pre-fetch failed or was skipped,
    and Lock 2 is running without Reddit/RSS content for watchlist tickers.
    Only fires on weekdays — cache is intentionally not refreshed on weekends.
    """
    today = date.today()
    if today.weekday() >= 5:
        return

    db = REPO / "data/apex.db"
    if not db.exists():
        return

    try:
        conn = sqlite3.connect(db)
        row  = conn.execute("SELECT MAX(fetched_at) FROM sentiment_cache").fetchone()
        conn.close()
    except sqlite3.OperationalError:
        flag(17, "Sentiment cache freshness", "WARNING", "data/apex.db:sentiment_cache",
             "sentiment_cache table missing — pre-fetch has never run")
        return
    except Exception as e:
        flag(17, "Sentiment cache freshness", "WARNING", "data/apex.db:sentiment_cache",
             f"could not query sentiment_cache: {e}")
        return

    if not row or not row[0]:
        flag(17, "Sentiment cache freshness", "WARNING", "data/apex.db:sentiment_cache",
             "sentiment_cache is empty — pre-fetch has never successfully run")
        return

    last_dt = datetime.fromisoformat(row[0])
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    age_hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600

    if age_hours > 26:
        flag(17, "Sentiment cache freshness", "WARNING", "data/apex.db:sentiment_cache",
             f"sentiment_cache last populated {age_hours:.1f}h ago — pre-fetch may have failed")


# ── CHECK 22 — Yahoo data pipeline health ────────────────────────────────────

def check22():
    """
    On trading days, verify that sector_snapshots are not stale (>26h since last
    write) and that regime_result_cache.json exists and is recent.

    Canary for Yahoo Finance rate-limiting or API outage: stale snapshots on a
    trading day mean the polling pipeline silently failed.
    """
    today      = date.today()
    is_trading = today.weekday() < 5

    if is_trading:
        db_path = REPO / "data/apex.db"
        if db_path.exists():
            try:
                conn = sqlite3.connect(str(db_path))
                row  = conn.execute("SELECT MAX(timestamp) FROM sector_snapshots").fetchone()
                conn.close()
                last_ts = row[0] if row else None
                if not last_ts:
                    flag(22, "Yahoo data pipeline health", "WARNING",
                         "data/apex.db:sector_snapshots",
                         "sector_snapshots is empty — polling has never run")
                else:
                    last_dt = datetime.fromisoformat(last_ts)
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                    age_hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
                    if age_hours > 26:
                        flag(22, "Yahoo data pipeline health", "WARNING",
                             "data/apex.db:sector_snapshots",
                             f"Last sector snapshot {age_hours:.1f}h ago — "
                             "polling may have failed due to Yahoo rate limit or API outage")
            except Exception as e:
                flag(22, "Yahoo data pipeline health", "WARNING",
                     "data/apex.db:sector_snapshots",
                     f"Could not query sector_snapshots: {e}")

    cache = REPO / "data/regime_result_cache.json"
    if not cache.exists():
        flag(22, "Yahoo data pipeline health", "WARNING",
             "data/regime_result_cache.json",
             "regime_result_cache.json missing — regime-bayes will show unavailable after any restart "
             "until next EOD run")
    else:
        try:
            cached     = json.loads(cache.read_text())
            cached_date = cached.get("date", "")
            age_days   = (today - date.fromisoformat(cached_date)).days if cached_date else 999
            if age_days > 5:
                flag(22, "Yahoo data pipeline health", "WARNING",
                     "data/regime_result_cache.json",
                     f"regime_result_cache.json is {age_days} days old (last: {cached_date}) — "
                     "EOD regime may not have run successfully this week")
        except Exception as e:
            flag(22, "Yahoo data pipeline health", "WARNING",
                 "data/regime_result_cache.json",
                 f"Could not parse regime_result_cache.json: {e}")


# ── CHECK 33 — Bayesian multiplier health ────────────────────────────────────

def check33():
    """
    Bayesian multiplier health — silent all-1.0 detection.

    _compute_bayesian_multipliers() can silently return all 1.0 in two
    distinct failure modes:
      A) regime_bayes_result was None (legitimate: no regime data)
      B) ticker_allocations() returned empty/zero (broken: upstream failure)

    Mode A produces no multiplier entries at all (multiplier_count=0).
    Mode B produces entries but every value is 1.0 (all_unity=True).

    Cycles are tagged by runner ("demo"/"live") since gate_runner.py and
    gate_runner_live.py both write to this file; suspicious_cycles is
    counted separately per runner so one runner's failure can't be masked
    by the other's healthy cycles. Live freshness is only required when
    LIVE_ENABLED — when live trading is off, no live cycles are expected.

    CRITICAL: any suspicious cycle detected for either runner in the most
    recent stats file.
    WARNING: stats file missing or predates the most recent trading day for
    a runner that should have run.
    """
    from backend.config import LIVE_ENABLED

    path       = REPO / "data" / "bayesian_multiplier_stats.json"
    today      = date.today()
    is_trading = today.weekday() < 5

    if not path.exists():
        if is_trading:
            flag(33, "Bayesian multiplier health", "WARNING",
                 "data/bayesian_multiplier_stats.json",
                 "Stats file missing — _persist_multiplier_stats() not called; "
                 "gate runner may be down or the call was removed from gate_runner.run()")
        return

    try:
        stats = json.loads(path.read_text())
    except Exception as e:
        flag(33, "Bayesian multiplier health", "WARNING",
             "data/bayesian_multiplier_stats.json",
             f"Stats file unreadable: {e}")
        return

    file_date = stats.get("date", "")
    cycles     = stats.get("cycles", [])
    runners_seen = {c.get("runner", "demo") for c in cycles}
    required_runners = {"demo"} | ({"live"} if LIVE_ENABLED else set())

    if is_trading:
        last_trading_day = _most_recent_trading_day(today - timedelta(days=1))
        file_d = date.fromisoformat(file_date) if file_date else None
        if file_d is None or file_d < last_trading_day:
            flag(33, "Bayesian multiplier health", "WARNING",
                 "data/bayesian_multiplier_stats.json",
                 f"Stats file is from {file_date or '(missing)'}, last trading day was "
                 f"{last_trading_day} — gate runner did not complete a full cycle or "
                 "_persist_multiplier_stats() was removed")
            return

        missing_runners = required_runners - runners_seen
        if missing_runners:
            flag(33, "Bayesian multiplier health", "WARNING",
                 "data/bayesian_multiplier_stats.json",
                 f"No cycles recorded today for runner(s) {sorted(missing_runners)} — "
                 "that gate runner may not be completing cycles")

    suspicious = stats.get("suspicious_cycles", {})
    if not isinstance(suspicious, dict):
        suspicious = {"demo": suspicious}  # back-compat with pre-tagging int format

    for runner, count in suspicious.items():
        if count > 0:
            total = sum(1 for c in cycles if c.get("runner", "demo") == runner)
            flag(33, "Bayesian multiplier health", "CRITICAL",
                 "data/bayesian_multiplier_stats.json",
                 f"[{runner}] {count}/{total} gate cycle(s): regime_result present, ≥3 tickers "
                 f"queued, but all multipliers=1.0 — ticker_allocations() likely returned zeros "
                 f"or sector allocation lookup failed silently; Bayesian sizing had no effect")


# ── CHECK 35 — PCR collection freshness ──────────────────────────────────────

def check35():
    """
    lock4_pcr_history must have observations from the most recent trading day.

    The Lock 4 PCR baseline calibration (per-ticker P25 percentile replacing
    the interim 0.85 fixed threshold) depends on daily collection. A gap in the
    collection means the calibration baseline grows stale.

    Uses the 26h staleness window. Only fires on weekdays.
    """
    today = date.today()
    if today.weekday() >= 5:
        return

    db = REPO / "data/apex.db"
    if not db.exists():
        return

    try:
        conn = sqlite3.connect(db)
        row  = conn.execute("SELECT MAX(date) FROM lock4_pcr_history").fetchone()
        conn.close()
    except sqlite3.OperationalError:
        flag(35, "PCR collection freshness", "WARNING", "data/apex.db:lock4_pcr_history",
             "lock4_pcr_history table missing — PCR collection has never run; "
             "check that scheduler collect_pcr job is wired and backend has been restarted")
        return
    except Exception as e:
        flag(35, "PCR collection freshness", "WARNING", "data/apex.db:lock4_pcr_history",
             f"could not query lock4_pcr_history: {e}")
        return

    if not row or not row[0]:
        flag(35, "PCR collection freshness", "WARNING", "data/apex.db:lock4_pcr_history",
             "lock4_pcr_history is empty — collect_pcr cron job has never completed successfully")
        return

    last_date        = date.fromisoformat(row[0])
    last_trading_day = _most_recent_trading_day(today - timedelta(days=1))
    if last_date < last_trading_day:
        age_days = (today - last_date).days
        flag(35, "PCR collection freshness", "WARNING", "data/apex.db:lock4_pcr_history",
             f"last PCR observation is from {row[0]} ({age_days}d ago, last trading day was "
             f"{last_trading_day}) — collect_pcr cron job may have failed; per-ticker "
             "percentile calibration will be delayed if collection gap grows beyond 1 week")


# ── CHECK 39 — Live peak_price integrity ─────────────────────────────────────

def check39():
    """
    An open live position where peak_price is NULL or equal to entry_price after
    more than 2 trading days means the software trailing stop is silently disabled:
    peak tracking is not running (scheduler issue) or the price feed is failing.
    Either cause leaves the position with no trailing-stop protection.

    Threshold: > 2 trading days open with peak_price IS NULL OR peak_price = entry_price.
    Severity: WARNING (does not prevent entries, but live protection is degraded).
    """
    def _business_days_between(start: date, end: date) -> int:
        """Count Mon–Fri days in (start, end] exclusive of start."""
        n, cur = 0, start + timedelta(days=1)
        while cur <= end:
            if cur.weekday() < 5:
                n += 1
            cur += timedelta(days=1)
        return n

    db = REPO / "data/apex.db"
    if not db.exists():
        return

    try:
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT id, ticker, timestamp, entry_price, peak_price
            FROM live_trades
            WHERE outcome = 'OPEN'
              AND (peak_price IS NULL OR peak_price = entry_price)
        """).fetchall()
        conn.close()
    except Exception as e:
        flag(39, "Live peak_price integrity", "WARNING",
             "data/apex.db:live_trades",
             f"could not query live_trades: {e}")
        return

    stale = []
    for r in rows:
        try:
            entry_date   = datetime.fromisoformat(r["timestamp"]).date()
            trading_days = _business_days_between(entry_date, date.today())
        except Exception:
            trading_days = 0
        if trading_days > 2:
            stale.append((r["ticker"], trading_days, r["peak_price"]))

    if stale:
        detail = "; ".join(
            f"{ticker} (age {days}d, peak={'NULL' if peak is None else 'entry'})"
            for ticker, days, peak in stale
        )
        flag(39, "Live peak_price integrity", "WARNING",
             "data/apex.db:live_trades",
             f"{len(stale)} open position(s) with stale peak_price after >2 trading days — "
             f"trailing stop silently disabled: {detail}")


# ── CHECK 44 — Regime-conditioned aggregator weight validation ────────────────

def check44():
    """
    Sub-check A: sector_posterior_history must have entries for ≥ 7 of the last
    10 trading days — persistence is confirmed accumulating.

    Sub-check B: once 20+ trading days of history exist, verify at least two
    distinct regime buckets have appeared (bull ≥ 0.75, bear < 0.60, neutral
    between). Monotone bucket state means regime-conditioned weights never switch.

    Bucket thresholds from CHECK 9 annotation (2026-05-28, single-snapshot).
    Do not adjust thresholds until sub-check B fails — that is the empirical
    validation signal.
    """
    db = REPO / "data/apex.db"
    if not db.exists():
        return

    try:
        conn = sqlite3.connect(db)
    except Exception as e:
        flag(44, "Regime-conditioned aggregator weight validation", "CRITICAL",
             "data/apex.db:sector_posterior_history",
             f"could not open DB: {e}")
        return

    # Sub-check A — persistence accumulation
    try:
        rows_a = conn.execute("""
            SELECT COUNT(DISTINCT date) AS n
            FROM sector_posterior_history
            WHERE date >= DATE('now', '-14 days')
        """).fetchone()
        distinct_dates = rows_a[0] if rows_a else 0
    except sqlite3.OperationalError:
        conn.close()
        flag(44, "Regime-conditioned aggregator weight validation", "CRITICAL",
             "data/apex.db:sector_posterior_history",
             "sector_posterior_history table missing — insert_sector_posterior_history "
             "never ran; regime-conditioned weights have no validation data accumulating")
        return
    except Exception as e:
        conn.close()
        flag(44, "Regime-conditioned aggregator weight validation", "WARNING",
             "data/apex.db:sector_posterior_history",
             f"could not query posterior history: {e}")
        return

    if distinct_dates < 7:
        flag(44, "Regime-conditioned aggregator weight validation", "CRITICAL",
             "data/apex.db:sector_posterior_history",
             f"only {distinct_dates} distinct date(s) in last 14 calendar days — "
             "persistence not accumulating; validation window cannot be built; "
             "check insert_sector_posterior_history call in EOD regime runner")
        conn.close()
        return

    # Sub-check B — bucket switching (only once 20+ dates exist)
    try:
        all_dates = conn.execute(
            "SELECT DISTINCT date FROM sector_posterior_history ORDER BY date DESC LIMIT 20"
        ).fetchall()
    except Exception as e:
        conn.close()
        flag(44, "Regime-conditioned aggregator weight validation", "WARNING",
             "data/apex.db:sector_posterior_history",
             f"could not query date list for bucket check: {e}")
        return

    conn.close()

    if len(all_dates) < 20:
        return  # not enough history yet; sub-check B deferred

    buckets = set()
    for (d,) in all_dates:
        try:
            conn2 = sqlite3.connect(db)
            top3 = conn2.execute(
                "SELECT posterior FROM sector_posterior_history "
                "WHERE date = ? ORDER BY posterior DESC LIMIT 3", (d,)
            ).fetchall()
            conn2.close()
        except Exception:
            continue
        if not top3:
            continue
        mean_top3 = sum(r[0] for r in top3) / len(top3)
        if mean_top3 >= 0.75:
            buckets.add("bull")
        elif mean_top3 < 0.60:
            buckets.add("bear")
        else:
            buckets.add("neutral")

    if len(buckets) == 1:
        flag(44, "Regime-conditioned aggregator weight validation", "WARNING",
             "data/apex.db:sector_posterior_history",
             f"last 20 trading days all in bucket '{next(iter(buckets))}' — "
             "regime-conditioned weights have never switched; bucket thresholds "
             "(bull≥0.75, bear<0.60) may be misplaced relative to actual posterior "
             "distribution; run retrospective bucket analysis")


# ── CHECK 46 — Profit-lock ratchet wiring integrity ──────────────────────────

def check46():
    """
    Any open live trade where peak gain >= profit_lock_trigger_pct and
    profit_lock_activated = 0 is a wiring failure: the ratchet should have
    fired and set the flag on the cycle that first crossed the trigger.

    Freshness guard: trades entered within the last 10 minutes are excluded.
    The exit-check loop runs every 5 minutes — a brand-new position may not
    have had a cycle run yet and should not trip the check before that window.

    The flag is one-way: once set it is never cleared within a trade's lifetime.
    A CRITICAL here means _maybe_ratchet_bracket_sl() either was not called,
    raised and suppressed its own error, or replace_stop_leg failed every cycle
    since trigger was crossed. Cross-reference gate 4/6 log warnings for the
    trade_id and parent_order_id to determine which path failed.

    Severity: CRITICAL — a position in profit-lock territory without the
    bracket SL ratcheted is exposed to a wide-stop exit on a fast move.
    """
    db = REPO / "data/apex.db"
    if not db.exists():
        return

    try:
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        # profit_lock_trigger_pct from live_config.json; fall back to 0.02
        cfg_path = REPO / "data/live_config.json"
        trigger_pct = 0.02
        if cfg_path.exists():
            try:
                import json as _json
                trigger_pct = _json.loads(cfg_path.read_text()).get(
                    "profit_lock_trigger_pct", 0.02
                )
            except Exception:
                pass

        rows = conn.execute("""
            SELECT id, ticker, alpaca_order_id, entry_price, peak_price,
                   profit_lock_activated, timestamp
            FROM live_trades
            WHERE outcome = 'OPEN'
              AND profit_lock_activated = 0
              AND peak_price IS NOT NULL
              AND (peak_price - entry_price) / entry_price >= :trigger
              AND datetime(timestamp) < datetime('now', '-10 minutes')
        """, {"trigger": trigger_pct}).fetchall()
        conn.close()
    except Exception as e:
        flag(46, "Profit-lock ratchet wiring", "WARNING",
             "data/apex.db:live_trades",
             f"could not query live_trades: {e}")
        return

    for r in rows:
        peak_gain = (r["peak_price"] - r["entry_price"]) / r["entry_price"]
        flag(46, "Profit-lock ratchet wiring", "CRITICAL",
             "data/apex.db:live_trades",
             f"{r['ticker']} trade_id={r['id']} parent={r['alpaca_order_id']}: "
             f"peak_gain={peak_gain:.1%} >= trigger={trigger_pct:.1%} "
             f"but profit_lock_activated=0 — bracket SL not ratcheted; "
             f"check gate-4/6 log warnings for this trade_id")


# ── CHECK 47 — Profit-lock SL trail vs peak ───────────────────────────────────

def check47():
    """
    Continuous ratchet integrity: if profit_lock_activated fired, the SL was
    supposed to trail peak*(1-trail). A closed SL exit where exit_price < entry
    means the ratchet set a floor but stopped following the peak — one-shot bug
    recurrence or replace_stop_leg failures across multiple cycles.

    Sub-check A: open trades with profit_lock_activated=True and peak_price
    materially above the ratchet floor — flagged when peak gain exceeds trigger
    by more than 2× and profit_lock_activated has been set for >30 min. This is
    a proxy for "ratchet should have moved SL further but may not have."

    Sub-check B: closed SL exits where profit_lock_activated=True but
    exit_price < entry_price — the ratcheted SL fired below entry, which means
    the trail was set but didn't protect the gain at all.
    """
    db = REPO / "data/apex.db"
    if not db.exists():
        return

    try:
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        cfg_path = REPO / "data/live_config.json"
        trigger_pct = 0.04
        trail_pct   = 0.01
        if cfg_path.exists():
            try:
                import json as _json
                cfg = _json.loads(cfg_path.read_text())
                trigger_pct = cfg.get("profit_lock_trigger_pct", trigger_pct)
                trail_pct   = cfg.get("profit_lock_trail_pct",   trail_pct)
            except Exception:
                pass

        # Sub-check A: open trades where peak gain >> trigger but flag set >30 min ago
        open_rows = conn.execute("""
            SELECT id, ticker, entry_price, peak_price, profit_lock_activated
            FROM live_trades
            WHERE outcome = 'OPEN'
              AND profit_lock_activated = 1
              AND peak_price IS NOT NULL
              AND (peak_price - entry_price) / entry_price >= :double_trigger
              AND datetime(timestamp) < datetime('now', '-30 minutes')
        """, {"double_trigger": trigger_pct * 2}).fetchall()

        for r in open_rows:
            peak_gain    = (r["peak_price"] - r["entry_price"]) / r["entry_price"]
            ratchet_floor = r["peak_price"] * (1 - trail_pct)
            flag(47, "Profit-lock SL trail vs peak", "WARNING",
                 "data/apex.db:live_trades",
                 f"{r['ticker']} trade_id={r['id']}: peak_gain={peak_gain:.1%} "
                 f"(>2x trigger={trigger_pct:.1%}), ratchet floor should be ~${ratchet_floor:.2f} — "
                 f"verify Alpaca SL leg is tracking peak, not frozen at initial ratchet price")

        # Sub-check B: closed SL exits where ratchet was set but exit below entry
        closed_rows = conn.execute("""
            SELECT id, ticker, entry_price, exit_price, peak_price
            FROM live_trades
            WHERE outcome != 'OPEN'
              AND exit_reason = 'SL'
              AND profit_lock_activated = 1
              AND exit_price IS NOT NULL
              AND exit_price < entry_price
              AND datetime(exited_at) > datetime('now', '-7 days')
        """).fetchall()

        for r in closed_rows:
            peak_gain = (r["peak_price"] - r["entry_price"]) / r["entry_price"] if r["peak_price"] else None
            flag(47, "Profit-lock SL trail vs peak", "CRITICAL",
                 "data/apex.db:live_trades",
                 f"{r['ticker']} trade_id={r['id']}: SL exit at ${r['exit_price']:.2f} "
                 f"< entry ${r['entry_price']:.2f} despite profit_lock_activated=True "
                 f"(peak_gain={peak_gain:.1%} if peak_gain else '?') — "
                 f"ratchet set a floor but trail did not follow peak; check replace_stop_leg log errors")

        conn.close()
    except Exception as e:
        flag(47, "Profit-lock SL trail vs peak", "WARNING",
             "data/apex.db:live_trades",
             f"could not query live_trades: {e}")


# ── CHECK 55 — IPO sentiment consecutive-zero detection ──────────────────────

def check55():
    """
    Flag if ipo_sentiment total_ipos has been 0 for 3+ consecutive logged days.

    A single zero day is not actionable — genuine IPO activity is sparse
    enough that 0 confirmed listings in a 30-day window happens routinely.
    But 3+ consecutive zeros is a pipeline-regression signal: the 2026-06-15
    bug (redundant `q` param → EDGAR 500s, `entity_name`/`display_names`
    field mismatch, filer-CIK-vs-issuer-CIK) produced exactly this pattern
    (total=0, risk_off=True) every single day with no error visible in the
    happy-path log line. See PRE_FIX_CONTAMINATION_DATE in ipo_sentiment.py.
    """
    history_path = REPO / "data/ipo_sentiment_history.json"
    if not history_path.exists():
        return

    try:
        entries = json.loads(history_path.read_text())
    except Exception as e:
        flag(55, "IPO sentiment consecutive-zero", "WARNING",
             "data/ipo_sentiment_history.json",
             f"could not read history: {e}")
        return

    recent = sorted(entries, key=lambda e: e["date"])[-3:]
    if len(recent) == 3 and all(e.get("total_ipos", 0) == 0 for e in recent):
        dates = ", ".join(e["date"] for e in recent)
        flag(55, "IPO sentiment consecutive-zero", "WARNING",
             "data/ipo_sentiment_history.json",
             f"total_ipos=0 for 3 consecutive logged days ({dates}) — "
             f"verify against CHECK 56 (EDGAR S-1 smoke test); if EDGAR "
             f"itself is healthy, this points to a pipeline regression "
             f"in backend/regime/ipo_sentiment.py")


# ── CHECK 56 — EDGAR S-1 search API smoke test ───────────────────────────────

def check56():
    """
    Hit EDGAR full-text search directly for S-1 filings in the last 30 days
    and assert hit count > 0.

    This is independent of ipo_sentiment.py's parsing/dedup/CIK-resolution
    logic — it catches API-level breakage (param rejection, schema changes,
    rate limiting, outage) that CHECK 55 alone can't distinguish from a
    pipeline bug. S-1 filings are continuous and frequent (~200+ in any
    30-day window); 0 hits or a non-200 response means EDGAR itself is
    broken or unreachable, not that IPO activity is genuinely zero.
    """
    end   = date.today()
    start = end - timedelta(days=30)

    try:
        resp = requests.get(
            "https://efts.sec.gov/LATEST/search-index",
            params={
                "forms":     "S-1",
                "dateRange": "custom",
                "startdt":   start.isoformat(),
                "enddt":     end.isoformat(),
            },
            headers={
                "User-Agent": "apex-trading-system contact@apex.local",
                "Accept":     "application/json",
            },
            timeout=15,
        )
        resp.raise_for_status()
        total = resp.json().get("hits", {}).get("total", {}).get("value", 0)
    except Exception as e:
        flag(56, "EDGAR S-1 search API smoke test", "CRITICAL",
             "backend/regime/ipo_sentiment.py",
             f"EDGAR search-index request failed: {e}")
        return

    if total == 0:
        flag(56, "EDGAR S-1 search API smoke test", "CRITICAL",
             "backend/regime/ipo_sentiment.py",
             f"EDGAR returned 0 S-1 filings for {start} to {end} — "
             f"S-1 filings are continuous; 0 indicates EDGAR API "
             f"breakage (param rejection, schema change, rate limit)")


def run() -> None:
    check14()
    check15()
    check17()
    check22()
    check33()
    check35()
    check39()
    check44()
    check46()
    check47()
    check55()
    check56()
