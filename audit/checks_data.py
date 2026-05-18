"""
Data-integrity mechanical checks — CHECKs 14, 15, 17, 22, 33, 35, 39.

Covers: EOD regime freshness, calibration freshness, sentiment cache,
Yahoo data pipeline health, Bayesian multiplier health, PCR collection
freshness, and live peak_price integrity.
"""
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

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

    The suspicious_cycles counter in the stats file tracks mode B specifically.

    CRITICAL: any suspicious cycle detected in the most recent stats file.
    WARNING: stats file missing or predates the most recent trading day.
    """
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

    suspicious = stats.get("suspicious_cycles", 0)
    if suspicious > 0:
        total = len(stats.get("cycles", []))
        flag(33, "Bayesian multiplier health", "CRITICAL",
             "data/bayesian_multiplier_stats.json",
             f"{suspicious}/{total} gate cycle(s): regime_result present, ≥3 tickers queued, "
             f"but all multipliers=1.0 — ticker_allocations() likely returned zeros or "
             f"sector allocation lookup failed silently; Bayesian sizing had no effect")


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
    import pandas as pd

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
            entry_date    = datetime.fromisoformat(r["timestamp"]).date()
            trading_days  = len(pd.bdate_range(entry_date, date.today())) - 1
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


def run() -> None:
    check14()
    check15()
    check17()
    check22()
    check33()
    check35()
    check39()
