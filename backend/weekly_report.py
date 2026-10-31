"""
Weekly performance report — emailed Friday at market close.

Covers the Mon–Fri window just ending:
  - GPT-4o analyst commentary (synthesised from all data below)
  - Demo P&L, win rate, gate funnel
  - Live P&L, win rate, gate funnel
  - Each of those against the prior REFERENCE_WEEKS weeks, recomputed from the DB
  - Top sector by avg signal score
  - Threshold drift (demo_config vs calibrated)
  - Best backtest sweep config (if sweep results exist)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from loguru import logger

from backend.backtest.live_shape import describe_varied
from backend.db import LIVE_VERIFIED_EXIT
from backend.gate import lock_codes

_SENT_MARKER = Path(__file__).parent.parent / "data" / "weekly_report_sent.txt"


def _this_week_label() -> str:
    """ISO week string for the current week, e.g. '2026-W14'."""
    now = datetime.now(timezone.utc)
    return now.strftime("%G-W%V")


def _mark_sent() -> None:
    _SENT_MARKER.write_text(_this_week_label())


def was_sent_this_week() -> bool:
    if not _SENT_MARKER.exists():
        return False
    return _SENT_MARKER.read_text().strip() == _this_week_label()

# Below this many trades or candidates the commentary may describe a count but
# not call it a finding. Set by hand (Niclas, 2026-10-07) as a floor until the
# noise-floor machinery can size findings; revisit here, not in the prompt text.
COMMENTARY_MIN_N = 10

_COMMENTARY_SYSTEM = f"""You are the analytics engine for APEX, an automated paper-trading signal system.
Each Friday you receive a week's performance data and write a concise analyst commentary included in the operator report email.

Rules:
- 3–5 sentences maximum
- Synthesise — do not restate numbers verbatim (they appear in the tables below)
- Lead with the most notable finding, positive or negative
- Every count in the data carries its n. Fewer than {COMMENTARY_MIN_N} trades or candidates is a description, not a finding: say what happened, do not infer a trend, edge or risk from it
- Gate funnel: each candidate is counted once, under the lock whose rule stopped it (stops_by_lock, per rule), or as entered or unknown. Name the lock that stopped the most, with its count of the candidates. A lock's count is not a pass rate: until phase 4 the locks do not run in chain order
- `reference` holds the prior weeks recomputed from the DB: pooled lock and win rates with their n, and the median and range of weekly counts and P&L. Call a number high or low only against its reference, and only when this week's n and the reference n are both at least {COMMENTARY_MIN_N}. Config changed inside the reference window, so a difference describes the system as it ran, not a cause
- Thresholds: pass rates are this week's rows against a threshold computed over the sector's full history (window_share = this week's fraction of it). Do not attribute a pass rate to a market move, a selloff or the regime floor, and do not suggest recalibration — what calibration did this week is in `calibration`
- Concentration: only from open_positions_by_sector, never from thresholds or scores
- If live trading had 0 trades this week, skip it entirely
- Tone: direct, data-driven, no filler phrases like "Overall" or "In summary"
"""

_SWEEP_PATH = Path(__file__).parent.parent / "data" / "sweep_results.json"
_OPT_PATH   = Path(__file__).parent.parent / "data" / "optimizer_results.json"
_WF_PATH    = Path(__file__).parent.parent / "data" / "objective_walkforward.json"


# ── Week boundary ─────────────────────────────────────────────────────────────

def _week_start_iso() -> str:
    """Monday 00:00:00 UTC of the current week."""
    now = datetime.now(timezone.utc)
    monday = now - timedelta(days=now.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


# ── Data queries ──────────────────────────────────────────────────────────────

def _fetch_prices(tickers: list[str]) -> dict[str, float]:
    """Return {ticker: last_close} for a batch of tickers. Missing tickers are omitted."""
    if not tickers:
        return {}
    try:
        import yfinance as yf
        data = yf.download(tickers, period="2d", progress=False, auto_adjust=True)
        if data.empty:
            return {}
        close = data["Close"] if "Close" in data.columns else data
        result = {}
        for ticker in tickers:
            try:
                col = close[ticker] if len(tickers) > 1 else close
                price = float(col.dropna().values.flatten()[-1])
                result[ticker] = price
            except Exception:
                pass
        return result
    except Exception as e:
        logger.warning(f"Weekly report: price fetch failed — {e}")
        return {}


BOOKS = ("demo", "live")

# One closed-trade population per book, read by the totals, the sector rows and
# the references, so they always sum to each other. Live excludes the
# RECONCILIATION-tagged batch (exit_confidence='unverified', 2026-08-18; same
# filter as get_live_equity_curve() and /live/compare). Both exclude annotated
# non-samples (db.not_annotated, 2026-10-10); live had no annotations when it
# joined that filter here (2026-10-15). The live exclusions are db.LIVE_VERIFIED_EXIT,
# the canonical sample's (2026-10-20); the window here is on exit, not entry.
_CLOSED_TABLES = {
    "demo": ("trades", ""),
    "live": ("live_trades", "AND " + LIVE_VERIFIED_EXIT),
}


def _closed_summary(book: str, since: str, until: str) -> dict:
    """Closed-trade outcome counts and P&L for one book over [since, until)."""
    from backend.db import get_db, not_annotated
    table, extra = _CLOSED_TABLES[book]
    conn = get_db()
    try:
        r = conn.execute(f"""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN outcome='WIN'  THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN outcome IN ('LOSS','EXPIRED') THEN 1 ELSE 0 END) AS losses,
                COALESCE(SUM(pnl), 0) AS realized_pnl,
                SUM(CASE WHEN exit_reason='REGIME' THEN 1 ELSE 0 END) AS regime_exits,
                COALESCE(SUM(CASE WHEN exit_reason='REGIME' THEN pnl ELSE 0 END), 0) AS regime_pnl
            FROM {table}
            WHERE exited_at >= ? AND exited_at < ? AND outcome IN ('WIN','LOSS','EXPIRED')
              {extra} AND {not_annotated(table)}
        """, (since, until)).fetchone()
    finally:
        conn.close()
    return {
        "closed_total": r["total"]  or 0,
        "wins":         r["wins"]   or 0,
        "losses":       r["losses"] or 0,
        "realized_pnl": round(r["realized_pnl"] or 0, 2),
        "regime_exits": r["regime_exits"] or 0,
        "regime_pnl":   round(r["regime_pnl"] or 0, 2),
    }


def _demo_stats(since: str, until: str) -> dict:
    from backend.config import STARTING_BALANCE
    from backend.db import get_db
    closed = _closed_summary("demo", since, until)
    conn = get_db()
    try:
        # The all-closed total below keeps every trade (annotated ones too) for the book's equity.
        open_rows = conn.execute("""
            SELECT ticker, shares, amount FROM trades WHERE outcome = 'OPEN'
        """).fetchall()

        all_closed = conn.execute("""
            SELECT COALESCE(SUM(pnl), 0) AS total
            FROM trades WHERE outcome IN ('WIN','LOSS','EXPIRED')
        """).fetchone()

        realized_all = all_closed["total"]

        # Fetch current prices to value open positions at market
        open_tickers = [r["ticker"] for r in open_rows]
        prices = _fetch_prices(open_tickers)
        open_cost = sum(r["amount"] for r in open_rows)
        open_market_value = sum(
            prices[r["ticker"]] * r["shares"]
            for r in open_rows
            if r["ticker"] in prices
        )
        # Fall back to cost basis for any ticker where price fetch failed
        open_market_value += sum(
            r["amount"] for r in open_rows if r["ticker"] not in prices
        )
        unrealized_pnl = open_market_value - open_cost

        # Total equity = starting capital + all realised gains/losses + unrealised on open positions
        total_equity = STARTING_BALANCE + realized_all + unrealized_pnl

        return {
            **closed,
            "open_positions":   len(open_rows),
            "open_cost":        round(open_cost, 2),
            "open_market_value": round(open_market_value, 2),
            "unrealized_pnl":   round(unrealized_pnl, 2),
            "balance":          round(total_equity, 2),
            "starting":         STARTING_BALANCE,
        }
    finally:
        conn.close()


def _live_stats(since: str, until: str) -> dict:
    from backend.db import get_db
    closed = _closed_summary("live", since, until)
    conn = get_db()
    try:
        open_row = conn.execute("""
            SELECT COUNT(*) AS cnt FROM live_trades WHERE outcome = 'OPEN'
        """).fetchone()
        return {**closed, "open_positions": open_row["cnt"]}
    finally:
        conn.close()


# The funnel counts each row once, by the rule that stopped it (stop_code; lock labels phase 2, 2026-10-31): per
# lock in chain order L0..L6 with each rule's stops, then the entries, then FILTERED_UNKNOWN. Stages and labels come
# from backend/gate/lock_codes.py (funnel_stages), the same as the dashboard funnel. A row's code names the lock that
# owns its stop, not how far it got: until phase 4 a lock's rules run in several places (L2's earnings before L3, its
# ETF re-test after L5), so no "reached" count is chained here. The skips are L1's rules (blocklist, position_open,
# cooloff, exit_cooloff, affordability). Before phase 2 the funnel chained the module stages by their numbered codes,
# which named the wrong lock for a third of the history (scope note 2026-10-26).
_FUNNEL_TABLES = {"demo": "signals", "live": "live_gate_history"}


def _funnel(book: str, since: str, until: str) -> dict:
    """Gate funnel for one book ('demo' | 'live') over [since, until)."""
    from backend.db import get_db
    table = _FUNNEL_TABLES[book]
    conn = get_db()
    try:
        rows = conn.execute(f"""
            SELECT stop_code, COUNT(*) AS cnt FROM {table}
            WHERE timestamp >= ? AND timestamp < ? AND gate_decision IS NOT NULL
            GROUP BY stop_code
        """, (since, until)).fetchall()
    finally:
        conn.close()
    counts = {r["stop_code"]: r["cnt"] for r in rows}
    stages = lock_codes.funnel_stages(counts)
    locks = [st for st in stages if st["kind"] == "lock"]
    other = {st["code"]: st["count"] for st in stages if st["kind"] == "unregistered"}
    entered = next(st["count"] for st in stages if st["kind"] == "executed")
    unknown = next(st["count"] for st in stages if st["kind"] == "unknown")
    total = sum(counts.values())
    return {
        "total":    total,
        "locks":    locks,
        "entered":  entered,
        "unknown":  unknown,
        "other":    other,
        # Every row is in exactly one line; a code nothing names would break this, and is listed under other.
        "balanced": total == sum(st["count"] for st in locks) + entered + unknown + sum(other.values()),
    }


def _sector_pnl(since: str, until: str) -> dict:
    """Closed trades per sector for the week, and open positions per sector now.

    Same populations as _demo_stats/_live_stats (_CLOSED_TABLES), so the
    sector rows sum to the totals above.
    """
    from backend.db import get_db, not_annotated
    conn = get_db()
    try:
        def closed(book: str) -> list[dict]:
            table, extra = _CLOSED_TABLES[book]
            extra += f" AND {not_annotated(table)}"
            return [dict(r) for r in conn.execute(f"""
                SELECT sector, COUNT(*) AS n,
                       SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) AS wins,
                       ROUND(COALESCE(SUM(pnl), 0), 2) AS pnl
                FROM {table}
                WHERE exited_at >= ? AND exited_at < ? AND outcome IN ('WIN','LOSS','EXPIRED') {extra}
                GROUP BY sector ORDER BY pnl DESC
            """, (since, until)).fetchall()]

        def open_now(table: str) -> dict[str, int]:
            return {r["sector"]: r["n"] for r in conn.execute(
                f"SELECT sector, COUNT(*) AS n FROM {table} WHERE outcome = 'OPEN' GROUP BY sector"
            ).fetchall()}

        return {
            "demo":      closed("demo"),
            "live":      closed("live"),
            "demo_open": open_now("trades"),
            "live_open": open_now("live_trades"),
        }
    finally:
        conn.close()


# ── References: prior weeks, derived not stored ───────────────────────────────
#
# Niclas 2026-10-03: "even if nothing is changed in production every week, we
# still need to use the numbers and learn from them". A number without a
# reference can't be read, so every count the report shows is recomputed for
# the prior weeks from the same tables and the same functions. Nothing is
# stored: the DB already holds every week (raw note 2026-10-03, order (b)).

# 8 = hand-set (Niclas session 2026-10-15): about 50 demo entries pooled at the
# 10-02 week's 6 entered, short enough to stay near the current config.
# Revisit at the 2026-10-29 mechanism review.
REFERENCE_WEEKS = 8
# First Monday after the first eligibility-module row (signals 2026-04-19 22:00 UTC, live_gate_history
# 2026-04-20; measured 2026-10-15). Before it the lock-chain decisions did not exist, and a funnel would read as
# "other"; the phase 2 migration (2026-10-31) re-coded every row since by its owner, so the weeks compare.
REFERENCE_START = "2026-04-20T00:00:00+00:00"


def _reference(since: str) -> dict:
    """The REFERENCE_WEEKS full weeks before `since` (none before REFERENCE_START),
    newest first, with the same counts as this week's report."""
    start = datetime.fromisoformat(since)
    floor = datetime.fromisoformat(REFERENCE_START)
    weeks = []
    for k in range(1, REFERENCE_WEEKS + 1):
        s = start - timedelta(weeks=k)
        if s < floor:
            break
        si, ui = s.isoformat(), (s + timedelta(weeks=1)).isoformat()
        weeks.append({
            "since":   si,
            "partial": len(_partial_sessions(si, ui)),
            "closed":  {b: _closed_summary(b, si, ui) for b in BOOKS},
            "funnel":  {b: _funnel(b, si, ui) for b in BOOKS},
        })
    sectors = _sector_pnl(weeks[-1]["since"], since) if weeks else {"demo": [], "live": []}
    return {"weeks": weeks, "sectors": {b: sectors[b] for b in BOOKS}}


def _spread(xs: list[float]) -> dict | None:
    if not xs:
        return None
    from statistics import median
    return {"median": median(xs), "min": min(xs), "max": max(xs)}


def _ref_book(ref: dict, book: str) -> dict:
    """Pooled rates (with their n) and the spread of weekly counts for one book."""
    ws = ref["weeks"]
    closed = [w["closed"][book] for w in ws]
    funnels = [w["funnel"][book] for w in ws]
    stages = [f["locks"] for f in funnels]
    return {
        "weeks":    len(ws),
        "partial":  sum(1 for w in ws if w["partial"]),
        "n":        sum(c["closed_total"] for c in closed),
        "wins":     sum(c["wins"] for c in closed),
        "closed":   _spread([c["closed_total"] for c in closed]),
        "pnl":      _spread([c["realized_pnl"] for c in closed]),
        "entered":  _spread([f["entered"] for f in funnels]),
        "candidates": sum(f["total"] for f in funnels),
        "locks":    [{"label": locks[0]["label"], "count": sum(lk["count"] for lk in locks)}
                     for locks in zip(*stages)] if stages else [],
    }


def _top_sector(since: str, until: str) -> tuple[str, float] | None:
    from backend.db import get_db
    conn = get_db()
    try:
        row = conn.execute("""
            SELECT sector, ROUND(AVG(avg_score), 4) AS score
            FROM sector_snapshots
            WHERE timestamp >= ? AND timestamp < ?
            GROUP BY sector
            ORDER BY score DESC
            LIMIT 1
        """, (since, until)).fetchone()
        return (row["sector"], row["score"]) if row else None
    finally:
        conn.close()


def _threshold_status(since: str, until: str) -> dict:
    """
    Per-sector Lock 2 (Quant) threshold status — measured facts only.

    For each calibrated sector, computes this week's pass rate (% of
    ticker_history scores >= calibrated threshold). The calibrator sets p80
    over the sector's full history, so the all-time rate is 20%. A week is
    0.2–1.5% of that window, so a week outside the 8–35% band says nothing
    about the threshold's cause or about the regime floor; the report states
    the band, n and the window share, not a cause (2026-10-03 read: the old
    "selloff"/"drifted up" labels fired on sectors that moved the other way).
    `flag` still drives send_weekly_report's calibration trigger, unchanged.
    """
    from backend.db import get_db, get_ticker_thresholds
    from backend.demo_config import get_demo_config
    from backend.ticker_config import get_sectors

    flat = get_demo_config().get("lock1_threshold", 0.45)
    calibrated = get_ticker_thresholds()
    all_sectors = sorted(get_sectors().keys())

    from backend.config import EXCLUDED_SECTORS, SECTOR_THRESHOLD_FLOORS

    # Pull this week's ticker_history scores
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT sector, ticker, signal_score FROM ticker_history
            WHERE day >= DATE(?) AND day < DATE(?)
        """, (since[:10], until[:10])).fetchall()
        # The calibrator reads each sector's full ticker_history
        # (ticker_threshold_calibration.calibrate), so this is the window a
        # threshold is computed from.
        window_rows = {r["sector"]: r["n"] for r in conn.execute(
            "SELECT sector, COUNT(*) AS n FROM ticker_history GROUP BY sector"
        ).fetchall()}
    finally:
        conn.close()

    from collections import defaultdict
    week_scores: dict[str, list[float]] = defaultdict(list)
    week_tickers: dict[str, set] = defaultdict(set)
    for r in rows:
        week_scores[r["sector"]].append(r["signal_score"])
        week_tickers[r["sector"]].add(r["ticker"])

    sectors_out = []
    for sector in all_sectors:
        thresh = calibrated.get(sector)
        scores = week_scores.get(sector, [])
        n = len(scores)

        pass_rate = None
        flag = None
        if thresh is not None and n >= 5:
            above = sum(1 for s in scores if s >= thresh)
            pass_rate = round(above / n, 3)
            if pass_rate > 0.35:
                flag = "high"    # threshold too loose — scores drifted up
            elif pass_rate < 0.08:
                flag = "low"     # threshold too tight — scores compressed
            else:
                flag = "ok"

        floor = SECTOR_THRESHOLD_FLOORS.get(sector)
        sectors_out.append({
            "sector":      sector,
            "threshold":   thresh,
            "floor":       floor,
            # What Lock 2 applies: l3_score._sector_threshold
            "effective":   max(thresh, floor) if thresh is not None and floor is not None else thresh,
            "pass_rate":   pass_rate,
            "n":           n,
            "tickers":     len(week_tickers.get(sector, ())),
            "window_rows": window_rows.get(sector, 0),
            "flag":        flag,
            "fallback":    thresh is None,
            "excluded":    sector in EXCLUDED_SECTORS,
        })

    return {
        "flat":    flat,
        "sectors": sectors_out,
    }


# Three states, three renderings. "No file" and "file I couldn't read" used
# to collapse into one None, so five months of "no sweep" read as normal and
# a corrupt file would have rendered identically. Absence and failure must
# not share a label (2026-09-14, same rule as the audit's SKIPPED row).
_SWEEP_ABSENT = "absent"
_SWEEP_UNREADABLE = "unreadable"
_SWEEP_OK = "ok"


def _shape_lines(path: Path, kind: str) -> list[tuple[str, bool]]:
    """The off-shape / stale label for a results file, plus (sweep) how many distinct
    outcomes the grid produced, as (text, is_warning) lines — shown above the
    numbers, never after them (2026-09-29: the report presented off-shape results
    as describing the system).

    One producer, two renderers (2026-10-03): the label lived only in the HTML
    writer, so the mail's plain-text part showed the same numbers with neither
    the OFF-SHAPE line nor the outcome count (first live run, 10-02).
    """
    from backend.backtest.live_shape import shape_label
    try:
        data = json.loads(path.read_text())
    except Exception:
        return []
    out: list[tuple[str, bool]] = []
    label = shape_label(data, kind)
    if label:
        out.append((label, True))
    if kind == "sweep":
        if data.get("distinct_outcomes") is None:
            # A payload written before 2026-09-29 has no count: unknown, not zero or blank.
            txt = (f"Distinct outcomes: unknown of {data.get('valid_combos', '?')} valid combos "
                   f"(results predate outcome counting — duplicate configs may be listed)")
        else:
            inert = data.get("inert_axes") or []
            txt = (f"Distinct outcomes: {data['distinct_outcomes']} of {data.get('valid_combos')} valid combos"
                   + (f"; never changed a result: {', '.join(inert)}" if inert else ""))
        out.append((txt, False))
    return out


def _shape_html(path: Path, kind: str) -> str:
    return "".join(
        f"<p style='color:#ef4444;font-size:12px;font-weight:600;margin:4px 0;'>{t}</p>" if warn
        else f"<p style='color:#9ca3af;font-size:11px;margin:2px 0;'>{t}</p>"
        for t, warn in _shape_lines(path, kind))


def _shape_text(path: Path, kind: str) -> str:
    return "".join(f"  {'⚠ ' if warn else ''}{t}\n" for t, warn in _shape_lines(path, kind))


def _sweep_best() -> tuple[str, str, list[dict] | None]:
    """Returns (state, detail, entries): the top 3 distinct outcomes from the saved
    top_configs, collapsed by the same helper as the sweep mail. Set only when
    state == ok."""
    if not _SWEEP_PATH.exists():
        return _SWEEP_ABSENT, "no data/sweep_results.json — weekly_research has not written one", None
    try:
        with open(_SWEEP_PATH) as f:
            data = json.load(f)
        from backend.backtest.live_shape import collapse_by_outcome
        from backend.backtest.weekend_sweep import GRID
        saved = data.get("top_configs", [])
        generated = str(data.get("generated_at", "?"))[:16]
        if not saved:
            return _SWEEP_UNREADABLE, f"file parsed but top_configs is empty (generated {generated})", None
        top = collapse_by_outcome(saved, tuple(GRID))
        detail = f"generated {generated}"
        if len(top) < 3:
            detail += f"; the saved top {len(saved)} hold only {len(top)} distinct outcome(s)"
        return _SWEEP_OK, detail, top
    except Exception as e:
        return _SWEEP_UNREADABLE, f"data/sweep_results.json exists but could not be read: {e}", None


def _optimizer_best() -> tuple[str, str, dict | None]:
    """
    Same three states as _sweep_best. The dict carries best_score, best_params,
    experiments_kept/total, final_metrics, generated_at, plus the run-to-run
    noise floor from backend.backtest.optimizer.NOISE_FLOOR and whether it is
    still valid for the current objective. The file had no consumer until
    2026-09-14 — 200 experiments a week written to a file nothing opened, which
    is why nobody noticed its writer was broken.
    """
    if not _OPT_PATH.exists():
        return _SWEEP_ABSENT, "no data/optimizer_results.json — weekly_research has not written one", None
    try:
        with open(_OPT_PATH) as f:
            data = json.load(f)
        if data.get("best_score") is None or not data.get("best_params"):
            return _SWEEP_UNREADABLE, "file parsed but has no best_score/best_params", None
        from backend.backtest.optimizer import NOISE_FLOOR, noise_floor_valid
        data["noise_floor"] = NOISE_FLOOR
        data["noise_floor_valid"] = noise_floor_valid()
        return _SWEEP_OK, f"generated {str(data.get('generated_at', '?'))[:16]}", data
    except Exception as e:
        return _SWEEP_UNREADABLE, f"data/optimizer_results.json exists but could not be read: {e}", None


def _walkforward_last() -> str:
    """
    One line: the last hand-run objective walk-forward, with its date, fold
    count and verdicts. Link, not re-run — folds extend every ~2 months, so a
    weekly re-render would repeat the same result until nobody read it; and
    the harness has a known degenerate case (top-k collapsing to top-1 on
    non-binding params, 2026-09-15) that should be understood before its number
    looks authoritative. Absent file → says so.
    """
    if not _WF_PATH.exists():
        return "no walk-forward run on record (backend/backtest/objective_walkforward.py, hand-run)"
    try:
        from datetime import datetime, timezone
        with open(_WF_PATH) as f:
            d = json.load(f)
        when = d.get("generated_at") or datetime.fromtimestamp(_WF_PATH.stat().st_mtime, timezone.utc).isoformat()
        c2 = d.get("criterion_2") or {}
        c1 = d.get("criterion_1") or {}
        folds = c2.get("folds") or []
        wins  = c2.get("fold_wins") or {}
        return (f"last walk-forward {when[:10]}: {len(folds)} folds, case {c2.get('case', '?')} "
                f"(uncapped {wins.get('uncapped', '?')} / capped {wins.get('capped', '?')} fold wins), "
                f"C1 {c1.get('verdict', 'not run')} — hand-run, re-run at the next fold")
    except Exception as e:
        return f"walk-forward file exists but could not be read: {e}"


# ── Format helpers ────────────────────────────────────────────────────────────

def _sweep_config(r: dict) -> str:
    """Every grid axis, as the sweep mail prints it."""
    return (f"score_thr={r.get('lock1_threshold')} TP={r.get('take_profit_pct', 0)*100:.0f}% "
            f"SL={r.get('stop_loss_pct', 0)*100:.0f}% hold={r.get('time_stop_days')}d "
            f"pos={r.get('max_positions')} vix={r.get('vix_threshold') or 'off'} "
            f"rs={'on' if r.get('use_leading_rs') else 'off'}")


def _pct(v: float | None) -> str:
    return f"{v*100:.1f}%" if v is not None else "—"


def _pnl_color(v: float) -> str:
    return "#22c55e" if v >= 0 else "#ef4444"


def _td(content: str, bold: bool = False, color: str = "") -> str:
    style = "padding:6px 12px;border-bottom:1px solid #374151;"
    if bold:
        style += "font-weight:600;"
    if color:
        style += f"color:{color};"
    return f"<td style='{style}'>{content}</td>"


def _row(*cells) -> str:
    return "<tr>" + "".join(cells) + "</tr>"


# ── GPT-4o commentary ─────────────────────────────────────────────────────────

def _gpt4o_commentary(
    demo: dict, live: dict,
    dfunnel: dict, lfunnel: dict,
    sector_pnl: dict,
    thresh_status: dict,
    sweep_top: list | None,
    recal_changes: dict | None,
    partials: list[str],
    ref: dict | None = None,
) -> str | None:
    """
    Call GPT-4o with the week's stats and return a short analyst commentary string.
    Returns None on any failure so the report still sends without it.
    """
    from backend.config import OPENAI_API_KEY
    if not OPENAI_API_KEY:
        logger.debug("Weekly commentary: OPENAI_API_KEY not set — skipping")
        return None

    try:
        from openai import OpenAI

        from backend.demo_config import get_demo_config
        cfg = get_demo_config()

        demo_wr = demo["wins"] / demo["closed_total"] if demo["closed_total"] else None
        live_wr = live["wins"] / live["closed_total"] if live["closed_total"] else None
        demo_return = (demo["balance"] - demo["starting"]) / demo["starting"]

        def funnel_payload(f: dict) -> dict:
            # Stops per lock and rule (lock labels phase 2): the code names the owner, not how far the row got.
            return {
                "candidates":        f["total"],
                "stops_by_lock":     {st["label"]: {r["rule"]: r["count"] for r in st["rules"]} for st in f["locks"]},
                "entered":           f["entered"],
                "unknown":           f["unknown"],
                "other_decisions":   f["other"],
            }

        payload = {
            "partial_sessions": partials,
            "demo": {
                "closed_trades":  demo["closed_total"],
                "wins":           demo["wins"],
                "losses":         demo["losses"],
                "win_rate":       round(demo_wr, 3) if demo_wr is not None else None,
                "realized_pnl":   demo["realized_pnl"],
                "regime_exits":   demo["regime_exits"],
                "regime_pnl":     demo["regime_pnl"],
                "balance":        demo["balance"],
                "return_pct":     round(demo_return * 100, 2),
                "open_positions": demo["open_positions"],
            },
            "live": {
                "closed_trades":  live["closed_total"],
                "wins":           live["wins"],
                "losses":         live["losses"],
                "win_rate":       round(live_wr, 3) if live_wr is not None else None,
                "realized_pnl":   live["realized_pnl"],
                "regime_exits":   live["regime_exits"],
                "regime_pnl":     live["regime_pnl"],
                "open_positions": live["open_positions"],
            },
            "gate_funnel": {"demo": funnel_payload(dfunnel), "live": funnel_payload(lfunnel)},
            "reference": {
                **{b: _ref_book(ref, b) for b in BOOKS},
                "closed_by_sector": ref["sectors"],
            } if ref else None,
            "closed_by_sector": {"demo": sector_pnl["demo"], "live": sector_pnl["live"]},
            "open_positions_by_sector": {"demo": sector_pnl["demo_open"], "live": sector_pnl["live_open"]},
            "thresholds": [
                {
                    "sector":       t["sector"],
                    "effective":    t["effective"],
                    "pass_rate":    t["pass_rate"],
                    "n_rows":       t["n"],
                    "n_tickers":    t["tickers"],
                    "window_share": round(t["n"] / t["window_rows"], 4) if t["window_rows"] else None,
                }
                for t in thresh_status.get("sectors", [])
                if not t["excluded"] and not t["fallback"]
            ],
            "calibration": _calibration_fact(thresh_status, recal_changes),
            "current_config": {
                "lock1_threshold":    cfg.get("lock1_threshold"),
                "take_profit_pct":    cfg.get("take_profit_pct"),
                "max_positions":      cfg.get("max_positions"),
                "vix_threshold":      cfg.get("vix_threshold"),
            },
            "sweep_best": sweep_top[0]["row"] if sweep_top else None,
        }

        client = OpenAI(api_key=OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model="gpt-4o",
            max_tokens=300,
            messages=[
                {"role": "system", "content": _COMMENTARY_SYSTEM},
                {"role": "user",   "content": json.dumps(payload, indent=2)},
            ],
        )
        commentary = resp.choices[0].message.content.strip()
        logger.info("Weekly commentary generated by GPT-4o")
        return commentary

    except Exception as e:
        logger.warning(f"Weekly commentary failed — skipping: {e}")
        return None


def _calibration_fact(thresh_status: dict, recal_changes: dict | None) -> str:
    """One factual line on what calibration did this week — no cause, no advice."""
    above = [s["sector"] for s in thresh_status["sectors"] if s["flag"] == "high"]
    if recal_changes is None:
        return "Calibration not triggered (no sector above 35% this week)."
    if not recal_changes:
        return (f"Calibration ran (triggered by {len(above)} sector(s) above 35%: {', '.join(above)}); "
                f"no threshold moved by 0.005 or more.")
    moved = ", ".join(f"{s} {a} → {b}" for s, (a, b) in recal_changes.items())
    return f"Calibration ran (triggered by {len(above)} sector(s) above 35%); moved: {moved}."


# ── HTML builder ──────────────────────────────────────────────────────────────

def _partial_sessions(since: str, until: str) -> list[str]:
    """One line per flagged session in the week (session_flags, written by
    scripts/cycle_watch.py; 2026-10-03). Shown before every number: a week with a
    partial session has lower counts that are not a change in the system."""
    from backend.db import get_session_flags
    out = []
    for f in get_session_flags(since[:10], until[:10]):
        ratio = (f"demo {f['cycles']}/{f['expected']}, live {f['live_cycles']}/{f['live_expected']} cycles"
                 if f["status"] == "final" else "ratio not finalized")
        out.append(f"PARTIAL SESSION {f['date']} ({ratio}) — {f['cause']}. Counts and funnel totals "
                   f"this week include it; per-trade results are unaffected.")
    return out


def _rules_text(st: dict) -> str:
    return ", ".join(f"{r['rule']} {r['count']}" for r in st["rules"])


def _plain_funnel(book: str, f: dict) -> str:
    out = f"GATE FUNNEL ({book})\n  Candidates {f['total']} | entered {f['entered']} | unknown {f['unknown']}\n"
    for st in f["locks"]:
        out += f"  {st['label']:18s} {st['count']} stopped" + (f" ({_rules_text(st)})" if st["rules"] else "") + "\n"
    if f["other"]:
        out += "  Other decisions: " + ", ".join(f"{k} {v}" for k, v in f["other"].items()) + "\n"
    if not f["balanced"]:
        out += "  WARNING: the lines do not sum to the candidates\n"
    return out


def _plain_sectors(sp: dict, ref: dict | None = None) -> str:
    out = ""
    for book in BOOKS:
        rows = sp[book]
        out += f"  {book.capitalize()}: " + (", ".join(f"{r['sector']} {r['wins']}/{r['n']} ${r['pnl']:+,.2f}" for r in rows) or "none") + "\n"
        if ref is not None:
            out += f"    prior {len(ref['weeks'])} wk: " + (", ".join(
                f"{r['sector']} {r['wins']}/{r['n']} ${r['pnl']:+,.0f}" for r in ref["sectors"][book]) or "none") + "\n"
    out += "  Open now (demo / live): " + (", ".join(
        f"{sec} {sp['demo_open'].get(sec, 0)}/{sp['live_open'].get(sec, 0)}"
        for sec in sorted(set(sp["demo_open"]) | set(sp["live_open"]))) or "none") + "\n"
    return out


def _spread_text(sp: dict | None, money: bool = False) -> str:
    """Median (min to max) of weekly values."""
    if sp is None:
        return "—"
    fmt = (lambda v: f"${v:+,.2f}") if money else (lambda v: f"{v:g}")
    return f"{fmt(sp['median'])} ({fmt(sp['min'])} to {fmt(sp['max'])})"


def _ratio_text(k: int, n: int) -> str:
    return f"{k}/{n} ({k / n * 100:.0f}%)" if n else "0/0"


def _reference_rows(demo: dict, live: dict, dfunnel: dict, lfunnel: dict,
                    rdemo: dict, rlive: dict) -> list[tuple[str, str, str, str, str]]:
    """(measure, demo this week, demo prior weeks, live this week, live prior weeks)."""
    rows = [
        ("Closed trades", str(demo["closed_total"]), _spread_text(rdemo["closed"]),
         str(live["closed_total"]), _spread_text(rlive["closed"])),
        ("Won / closed", _ratio_text(demo["wins"], demo["closed_total"]), _ratio_text(rdemo["wins"], rdemo["n"]),
         _ratio_text(live["wins"], live["closed_total"]), _ratio_text(rlive["wins"], rlive["n"])),
        ("Realized P&L", f"${demo['realized_pnl']:+,.2f}", _spread_text(rdemo["pnl"], money=True),
         f"${live['realized_pnl']:+,.2f}", _spread_text(rlive["pnl"], money=True)),
        ("Entered", str(dfunnel["entered"]), _spread_text(rdemo["entered"]),
         str(lfunnel["entered"]), _spread_text(rlive["entered"])),
    ]
    for i, (dst, lst) in enumerate(zip(dfunnel["locks"], lfunnel["locks"])):
        rd = rdemo["locks"][i] if rdemo["locks"] else {"count": 0}
        rl = rlive["locks"][i] if rlive["locks"] else {"count": 0}
        rows.append((f"{dst['label']} stopped / candidates", _ratio_text(dst["count"], dfunnel["total"]),
                     _ratio_text(rd["count"], rdemo.get("candidates", 0)),
                     _ratio_text(lst["count"], lfunnel["total"]), _ratio_text(rl["count"], rlive.get("candidates", 0))))
    return rows


def _reference_note(ref: dict, rdemo: dict, since: str) -> str:
    if not ref["weeks"]:
        return f"No reference weeks: none start on or after {REFERENCE_START[:10]}."
    out = (f"Prior {rdemo['weeks']} full weeks ({ref['weeks'][-1]['since'][:10]} up to {since[:10]}), recomputed "
           f"from the DB. Rates are pooled over those weeks with their n; counts and P&L show the weekly median "
           f"(min to max). Config changed inside this window, so a difference describes the system as it ran, "
           f"not a cause.")
    if rdemo["partial"]:
        out += f" {rdemo['partial']} of these weeks had a flagged partial session (flags exist from 2026-10-03)."
    return out


def build_report(recal_changes: dict[str, tuple[float, float]] | None = None,
                 capture: dict | None = None, snapshot: dict | None = None) -> tuple[str, str, str]:
    """Return (subject, html_body, plain_body). A dict passed as `capture` is filled
    with the facts the DB cannot recompute later, for send_weekly_report to store.

    `snapshot` (2026-10-31): re-render a stored week (weekly_report_snapshots via
    db.get_weekly_snapshot). Its window, commentary, optimizer, sweep, thresholds and
    recal changes are used as stored, with no second model call; funnels and P&L come
    from the DB, and unrealized prices are fetched again."""
    if snapshot is not None:
        since, until = snapshot["since"], snapshot["until"]
        recal_changes = snapshot.get("recal_changes")
        # The label and the footer use the time the week was stored (the send), so the render matches the mail.
        now = datetime.fromisoformat(snapshot.get("_written_at") or until)
    else:
        since  = _week_start_iso()
        until  = (datetime.fromisoformat(since) + timedelta(days=7)).isoformat()
        now    = datetime.now(timezone.utc)
    week_label = now.strftime("Week ending %B %d, %Y")
    partials = _partial_sessions(since, until)

    demo  = _demo_stats(since, until)
    live  = _live_stats(since, until)
    dfunnel = _funnel("demo", since, until)
    lfunnel = _funnel("live", since, until)
    sector_pnl = _sector_pnl(since, until)
    ref = _reference(since)
    rdemo, rlive = _ref_book(ref, "demo"), _ref_book(ref, "live")
    top_sector = _top_sector(since, until)
    if snapshot is not None:
        thresh_status = snapshot["thresholds"]
        sweep_state, sweep_detail, sweep_top = (snapshot["sweep"][k] for k in ("state", "detail", "top"))
        opt_state, opt_detail, opt = (snapshot["optimizer"][k] for k in ("state", "detail", "result"))
        commentary = snapshot["commentary"]
    else:
        thresh_status = _threshold_status(since, until)
        sweep_state, sweep_detail, sweep_top = _sweep_best()
        if sweep_state != _SWEEP_OK:
            logger.warning(f"Weekly report: sweep {sweep_state} — {sweep_detail}")
        opt_state, opt_detail, opt = _optimizer_best()
        if opt_state != _SWEEP_OK:
            logger.warning(f"Weekly report: optimizer {opt_state} — {opt_detail}")

        commentary = _gpt4o_commentary(
            demo, live, dfunnel, lfunnel,
            sector_pnl, thresh_status, sweep_top, recal_changes, partials, ref,
        )
    if capture is not None:
        # Order (b) of the 10-03 read, second half: derive what the DB holds, store
        # only what it cannot give back. optimizer_results.json and sweep_results.json
        # are overwritten weekly, ticker_thresholds and the noise floor change in place,
        # and the commentary exists only in the mail.
        from backend.code_identity import read_code_identity
        capture.update({
            "week": _this_week_label(), "since": since, "until": until,
            "code": read_code_identity(),
            "optimizer": {"state": opt_state, "detail": opt_detail, "result": opt},
            "sweep": {"state": sweep_state, "detail": sweep_detail, "top": sweep_top},
            "thresholds": thresh_status,
            "recal_changes": recal_changes,
            "commentary": commentary,
            "commentary_min_n": COMMENTARY_MIN_N, "reference_weeks": REFERENCE_WEEKS,
        })

    subject = f"[APEX] Weekly Report — {week_label}"

    # ── HTML ──
    th_style = (
        "padding:6px 12px;text-align:left;background:#1f2937;"
        "color:#9ca3af;font-size:12px;text-transform:uppercase;letter-spacing:.05em;"
    )
    section_style = (
        "font-size:13px;font-weight:700;color:#6366f1;"
        "padding:16px 0 6px 0;border-bottom:2px solid #374151;margin-bottom:4px;"
    )

    def th(label: str) -> str:
        return f"<th style='{th_style}'>{label}</th>"

    # Demo / Live comparison table
    demo_wr = demo["wins"] / demo["closed_total"] if demo["closed_total"] else None
    live_wr = live["wins"] / live["closed_total"] if live["closed_total"] else None
    demo_return = (demo["balance"] - demo["starting"]) / demo["starting"]

    perf_table = f"""
    <table style='border-collapse:collapse;width:100%;font-size:13px;color:#e5e7eb;'>
      <thead><tr>
        {th('')}{th('Demo')}{th('Live')}
      </tr></thead>
      <tbody>
        {_row(_td('Closed trades (week)'), _td(str(demo['closed_total'])), _td(str(live['closed_total'])))}
        {_row(_td('of which: regime exits'),
              _td(f"{demo['regime_exits']} (${demo['regime_pnl']:+,.2f})" if demo['regime_exits'] else '—', color='#9ca3af'),
              _td(f"{live['regime_exits']} (${live['regime_pnl']:+,.2f})" if live['regime_exits'] else '—', color='#9ca3af'))}
        {_row(_td('Wins / Losses'), _td(f"{demo['wins']}W / {demo['losses']}L"), _td(f"{live['wins']}W / {live['losses']}L"))}
        {_row(_td('Win rate (week)'), _td(_pct(demo_wr)), _td(_pct(live_wr)))}
        {_row(_td('Realized P&amp;L (week)', bold=True),
              _td(f"${demo['realized_pnl']:+,.2f}", bold=True, color=_pnl_color(demo['realized_pnl'])),
              _td(f"${live['realized_pnl']:+,.2f}", bold=True, color=_pnl_color(live['realized_pnl'])))}
        {_row(_td('Open positions'), _td(str(demo['open_positions'])), _td(str(live['open_positions'])))}
        {_row(_td('Unrealized P&amp;L (open)'),
              _td(f"${demo['unrealized_pnl']:+,.2f}", color=_pnl_color(demo['unrealized_pnl'])),
              _td('—'))}
        {_row(_td('Total equity', bold=True), _td(f"${demo['balance']:,.2f} ({_pct(demo_return)})", bold=True, color=_pnl_color(demo_return)), _td('—'))}
      </tbody>
    </table>"""

    # Gate funnel table — each lock's stops per rule, in chain order (lock_codes.funnel_stages)
    def _lock_cell(st: dict) -> str:
        if not st["count"]:
            return _td("0", color="#6b7280")
        return _td(f"{st['count']} ({_rules_text(st)})")

    funnel_rows = [_row(_td('Candidates'), _td(str(dfunnel['total'])), _td(str(lfunnel['total'])))]
    for dst, lst in zip(dfunnel['locks'], lfunnel['locks']):
        funnel_rows.append(_row(_td(dst['label']), _lock_cell(dst), _lock_cell(lst)))
    funnel_rows.append(_row(_td('Entered', bold=True), _td(str(dfunnel['entered']), bold=True),
                            _td(str(lfunnel['entered']), bold=True)))
    funnel_rows.append(_row(_td('Unknown (no rule)', color='#ef4444' if dfunnel['unknown'] or lfunnel['unknown'] else ''),
                            _td(str(dfunnel['unknown'])), _td(str(lfunnel['unknown']))))
    for book, f in (("Demo", dfunnel), ("Live", lfunnel)):
        if f['other']:
            funnel_rows.append(_row(_td(f"{book}: other decisions", color='#f59e0b'),
                                    _td(', '.join(f"{k} {v}" for k, v in f['other'].items()), color='#f59e0b'), _td('')))
        if not f['balanced']:
            funnel_rows.append(_row(_td(f"{book}: the lines do not sum to the candidates", color='#ef4444'), _td(''), _td('')))

    funnel_table = f"""
    <p style='color:#6b7280;font-size:12px;margin:0 0 6px 0;'>
      Locks 1–4 run in order. After Leading, Claude and the overflow check run in either order, so those outcomes are listed side by side.
    </p>
    <table style='border-collapse:collapse;width:100%;font-size:13px;color:#e5e7eb;'>
      <thead><tr>
        {th('Stage')}{th('Demo')}{th('Live')}
      </tr></thead>
      <tbody>{"".join(funnel_rows)}</tbody>
    </table>"""

    ref_rows = _reference_rows(demo, live, dfunnel, lfunnel, rdemo, rlive)
    ref_note = _reference_note(ref, rdemo, since)
    ref_html = f"""
    <p style='color:#6b7280;font-size:12px;margin:0 0 6px 0;'>{ref_note}</p>"""
    if ref["weeks"]:
        ref_html += f"""
    <table style='border-collapse:collapse;width:100%;font-size:13px;color:#e5e7eb;'>
      <thead><tr>{th('')}{th('Demo week')}{th('Demo prior')}{th('Live week')}{th('Live prior')}</tr></thead>
      <tbody>{"".join(_row(_td(m), _td(dw), _td(dp, color='#9ca3af'), _td(lw), _td(lp, color='#9ca3af'))
                      for m, dw, dp, lw, lp in ref_rows)}</tbody>
    </table>"""

    # Closed trades by sector, with n — a description, not a finding at these sizes
    def _sector_cells(rows: list[dict], sector: str) -> str:
        r = next((x for x in rows if x["sector"] == sector), None)
        if not r:
            return _td("—", color="#6b7280")
        return _td(f"{r['wins']}/{r['n']} won, ${r['pnl']:+,.2f}", color=_pnl_color(r["pnl"]))

    def _prior_text(rows: list[dict], sector: str) -> str:
        r = next((x for x in rows if x["sector"] == sector), None)
        return f"{r['wins']}/{r['n']} ${r['pnl']:+,.0f}" if r else "—"

    sp_sectors = sorted({r["sector"] for r in sector_pnl["demo"] + sector_pnl["live"]}
                        | set(sector_pnl["demo_open"]) | set(sector_pnl["live_open"]))
    if sp_sectors:
        sector_pnl_html = f"""
    <table style='border-collapse:collapse;width:100%;font-size:13px;color:#e5e7eb;'>
      <thead><tr>{th('Sector')}{th('Demo closed')}{th('Live closed')}{th('Open now (demo / live)')}{th(f"Prior {len(ref['weeks'])} wk (demo / live)")}</tr></thead>
      <tbody>{"".join(_row(_td(sec), _sector_cells(sector_pnl['demo'], sec), _sector_cells(sector_pnl['live'], sec),
                           _td(f"{sector_pnl['demo_open'].get(sec, 0)} / {sector_pnl['live_open'].get(sec, 0)}"),
                           _td(f"{_prior_text(ref['sectors']['demo'], sec)} / {_prior_text(ref['sectors']['live'], sec)}", color='#9ca3af'))
                      for sec in sp_sectors)}</tbody>
    </table>
    <p style='color:#6b7280;font-size:11px;margin-top:4px;'>Per-sector counts this small describe the week; they are not evidence of an edge.</p>"""
    else:
        sector_pnl_html = "<p style='color:#6b7280;font-size:13px;'>No closed trades and no open positions.</p>"

    # Top sector
    sector_html = ""
    if top_sector:
        sector_html = f"""
        <p style='color:#e5e7eb;font-size:13px;'>
          <strong>{top_sector[0]}</strong> — avg score {top_sector[1]:.3f}
        </p>"""
    else:
        sector_html = "<p style='color:#6b7280;font-size:13px;'>No snapshot data for this week.</p>"

    # Threshold status — measured facts: effective threshold, n, window share.
    # No cause labels: the 10-03 read found "selloff" on sectors that rose and
    # "recalibrate" on a step that changed nothing in four weeks.
    flat = thresh_status["flat"]
    sectors_info = thresh_status["sectors"]
    n_cal = sum(1 for s in sectors_info if not s["fallback"])
    calibration_line = _calibration_fact(thresh_status, recal_changes)

    def _band(s: dict) -> str:
        return {"high": "above 35%", "low": "below 8%", "ok": "8–35%"}.get(s["flag"], "")

    def _thresh_text(s: dict) -> str:
        if s["threshold"] is None:
            return "—"
        if s["effective"] != s["threshold"]:
            return f"{s['effective']} (floor; calibrated {s['threshold']})"
        return str(s["threshold"])

    def _week_text(s: dict) -> str:
        if s["excluded"]:
            return "excluded at Lock 1 Eligibility"
        if s["fallback"]:
            return f"uncalibrated — flat {flat}"
        if s["pass_rate"] is None:
            return f"{s['n']} rows (&lt; 5)"
        share = f"{s['n'] / s['window_rows'] * 100:.1f}%" if s["window_rows"] else "—"
        return (f"{s['pass_rate']*100:.0f}% of {s['n']} rows, {s['tickers']} tickers "
                f"({_band(s)}; {share} of {s['window_rows']:,} history rows)")

    thresh_rows_html = "".join(
        _row(_td(s["sector"]), _td(_thresh_text(s)),
             _td(_week_text(s), color="#6b7280" if (s["excluded"] or s["fallback"] or s["pass_rate"] is None) else ""))
        for s in sectors_info
    )

    thresh_html = f"""
    <p style='color:#e5e7eb;font-size:13px;margin:0 0 4px 0;'>{calibration_line}</p>
    <p style='color:#6b7280;font-size:12px;margin:0 0 6px 0;'>
      Threshold = p80 of the sector's full score history, raised to a floor where one is set (what Lock 2 applies).
      Pass rate = this week's scores at or above the calibrated threshold; the all-time rate is 20% by construction.
      A week is a small share of the history, so a week outside 8–35% is a description, not a cause.
    </p>
    <table style='border-collapse:collapse;width:100%;font-size:13px;color:#e5e7eb;'>
      <thead><tr>{th('Sector')}{th('Threshold')}{th('This week')}</tr></thead>
      <tbody>{thresh_rows_html}</tbody>
    </table>"""

    # Sweep top configs
    sweep_html = ""
    if sweep_top:
        sweep_rows_html = "".join(
            _row(
                _td(f"#{i+1}"),
                _td(f"{_sweep_config(e['row'])}"
                    + (f"<br/><span style='color:#9ca3af;font-size:11px;'>{describe_varied(e)}</span>" if e["combos"] > 1 else "")),
                _td(f"{e['row'].get('sharpe', 0):.2f}"),
                _td(_pct(e["row"].get("total_return_pct")), color="#22c55e" if e["row"].get("total_return_pct", 0) >= 0 else "#ef4444"),
                _td(f"{_pct(e['row'].get('win_rate'))} (n={e['row'].get('total_trades', '?')})"),
            )
            for i, e in enumerate(sweep_top)
        )
        sweep_html = _shape_html(_SWEEP_PATH, "sweep") + f"""
        <table style='border-collapse:collapse;width:100%;font-size:13px;color:#e5e7eb;'>
          <thead><tr>
            {th('#')}{th('Config')}{th('Sharpe')}{th('Return')}{th('Win rate')}
          </tr></thead>
          <tbody>{sweep_rows_html}</tbody>
        </table>
        <p style='color:#6b7280;font-size:11px;margin-top:4px;'>
          Sweep covers last 90 trading days — L1-only backtest ({sweep_detail}). Compare vs current demo config before promoting.
        </p>"""
    elif sweep_state == _SWEEP_ABSENT:
        sweep_html = f"<p style='color:#6b7280;font-size:13px;'>No sweep results: {sweep_detail} (runs Monday 17:00 Stockholm).</p>"
    else:
        sweep_html = f"<p style='color:#ef4444;font-size:13px;'>Sweep results UNREADABLE: {sweep_detail}</p>"

    # Optimizer: score with its noise floor stated inline; best params labelled
    # as one sample from a flat optimum, not a recommendation. Within-run
    # min/median/max is deliberately not shown — it measures the landscape
    # inside a run, not whether a week-over-week move is real.
    if opt_state == _SWEEP_OK:
        nf = opt["noise_floor"]
        fm = opt.get("final_metrics", {})
        bp = opt["best_params"]
        st = opt.get("start") or {}
        warm = st.get("warm_start")
        floor_txt = (
            f"cold-start floor: run-to-run σ {nf['sigma']:.3f}, n={nf['n_runs']} on {nf['measured']} data; "
            f"moves below {nf['threshold']:.2f} between cold-start runs are search noise"
        )
        if warm:
            floor_txt += (" — this run was WARM-STARTED from last week's params, so its best score is a ratchet "
                          "step within the window, not a draw from that distribution; read the incumbent line instead")
        if st.get("start_score") is not None:
            if warm:
                delta = (opt["best_score"] or 0) - st["start_score"]
                incumbent_txt = (f"Incumbent (last week's params re-scored on this window) <b>{st['start_score']:.3f}</b> "
                                 f"→ search {'beat it by ' + format(delta, '.3f') if st.get('displaced') else 'did not beat it'}. "
                                 f"Incumbent trend is the degradation signal; displacement is the ratchet's health.")
            else:
                incumbent_txt = f"Cold start from defaults ({st['start_score']:.3f})."
        else:
            incumbent_txt = "Start score not recorded (results file predates 2026-09-14)."
        if not opt["noise_floor_valid"]:
            floor_txt = ("NOISE FLOOR STALE — objective or window changed since it was measured "
                         f"({nf['measured']}); re-measure before reading any trend")
        floor_color = "#6b7280" if opt["noise_floor_valid"] else "#ef4444"
        opt_html = _shape_html(_OPT_PATH, "optimizer") + f"""
        <p style='color:#e5e7eb;font-size:13px;margin:4px 0;'>
          Best score <b>{opt['best_score']:.3f}</b>
          <span style='color:{floor_color};font-size:11px;'>({floor_txt})</span><br/>
          Kept {opt.get('experiments_kept', '?')}/{opt.get('experiments_total', '?')}
          <span style='color:#6b7280;font-size:11px;'>(cold-start run-to-run {nf['kept_mean']:.1f} ± {nf['kept_sigma']:.1f})</span><br/>
          <span style='font-size:12px;'>{incumbent_txt}</span>
        </p>
        <p style='color:#9ca3af;font-size:12px;margin:4px 0;'>
          One sample from a flat optimum, not a recommendation — Sharpe saturates at 2.0 in the composite and
          six cold-start runs disagreed on max_positions (2–5), L1 (0.64–0.76) and trade count (37–105):
          L1={bp.get('lock1_threshold')} TP={bp.get('take_profit_pct', 0)*100:.0f}% SL={bp.get('stop_loss_pct', 0)*100:.0f}%
          TSL={bp.get('trailing_stop_pct')} hold={bp.get('time_stop_days')}d maxpos={bp.get('max_positions')} VIX={bp.get('vix_threshold')}
          → Sharpe {fm.get('sharpe', 0):.2f}, PF {fm.get('profit_factor', 0):.2f}, WR {_pct(fm.get('win_rate'))}, DD {_pct(fm.get('max_drawdown'))}, n={fm.get('total_trades')}
          <span style='color:#6b7280;'>({opt_detail})</span>
        </p>"""
    elif opt_state == _SWEEP_ABSENT:
        opt_html = f"<p style='color:#6b7280;font-size:13px;'>No optimizer results: {opt_detail} (runs Monday 17:00 Stockholm).</p>"
    else:
        opt_html = f"<p style='color:#ef4444;font-size:13px;'>Optimizer results UNREADABLE: {opt_detail}</p>"
    opt_html += f"<p style='color:#6b7280;font-size:11px;margin:2px 0;'>{_walkforward_last()}</p>"

    commentary_html = ""
    if commentary:
        commentary_html = f"""
    <div style='{section_style}'>AI Analysis</div>
    <p style='color:#e5e7eb;font-size:13px;line-height:1.6;margin:0 0 16px 0;'>{commentary}</p>"""

    html = f"""<!DOCTYPE html>
<html>
<body style='background:#111827;font-family:system-ui,sans-serif;margin:0;padding:24px;'>
  <div style='max-width:680px;margin:0 auto;'>
    <h2 style='color:#6366f1;margin:0 0 4px 0;'>APEX Weekly Report</h2>
    <p style='color:#6b7280;font-size:13px;margin:0 0 24px 0;'>{week_label}</p>
    {"".join(f"<p style='color:#f59e0b;font-size:12px;font-weight:600;margin:0 0 8px 0;'>{p}</p>" for p in partials)}

    {commentary_html}

    <div style='{section_style}'>Performance</div>
    {perf_table}

    <div style='{section_style}'>This Week Against the Prior Weeks</div>
    {ref_html}

    <div style='{section_style}'>Closed Trades by Sector</div>
    {sector_pnl_html}

    <div style='{section_style}'>Gate Funnel</div>
    {funnel_table}

    <div style='{section_style}'>Top Sector This Week</div>
    {sector_html}

    <div style='{section_style}'>Lock 2 Quant Thresholds</div>
    {thresh_html}

    <div style='{section_style}'>Monday Backtest Sweep — Best Configs</div>
    {sweep_html}

    <div style='{section_style}'>Autoresearch Optimizer</div>
    {opt_html}

    <p style='color:#374151;font-size:11px;margin-top:32px;'>
      Generated by APEX at {now.strftime('%Y-%m-%d %H:%M')} UTC
    </p>
  </div>
</body>
</html>"""

    # ── Plain text ──
    def plain_pnl(v: float) -> str:
        return f"${v:+,.2f}"

    plain = f"""APEX Weekly Report — {week_label}
"""
    plain += "".join(f"⚠ {p}\n" for p in partials)
    if commentary:
        plain += f"\nAI ANALYSIS\n  {commentary}\n"

    plain += f"""
PERFORMANCE
  Demo: {demo['closed_total']} closed ({demo['wins']}W/{demo['losses']}L) | Regime exits: {demo['regime_exits']} ({plain_pnl(demo['regime_pnl'])}) | Win rate: {_pct(demo_wr)} | Realized P&L: {plain_pnl(demo['realized_pnl'])} | Unrealized: {plain_pnl(demo['unrealized_pnl'])} | Total equity: ${demo['balance']:,.2f} ({_pct(demo_return)})
  Live: {live['closed_total']} closed ({live['wins']}W/{live['losses']}L) | Regime exits: {live['regime_exits']} ({plain_pnl(live['regime_pnl'])}) | Win rate: {_pct(live_wr)} | P&L: {plain_pnl(live['realized_pnl'])}

{_plain_funnel("Demo", dfunnel)}
{_plain_funnel("Live", lfunnel)}
THIS WEEK AGAINST THE PRIOR WEEKS
  {ref_note}
"""
    if ref["weeks"]:
        plain += f"  {'':28s}{'Demo week':>14s}  {'Demo prior':<30s}{'Live week':>14s}  Live prior\n"
        for m, dw, dp, lw, lp in ref_rows:
            plain += f"  {m:28s}{dw:>14s}  {dp:<30s}{lw:>14s}  {lp}\n"
    plain += f"""
CLOSED TRADES BY SECTOR (n per sector; a description, not evidence of an edge)
{_plain_sectors(sector_pnl, ref)}
TOP SECTOR
  {f"{top_sector[0]} ({top_sector[1]:.3f})" if top_sector else "—"}

LOCK 2 QUANT THRESHOLDS ({n_cal} calibrated, flat fallback: {flat})
  {calibration_line}
"""
    for s in sectors_info:
        plain += f"  {s['sector']:20s}  {_thresh_text(s):34s}  {_week_text(s).replace('&lt;', '<')}\n"

    if sweep_state == _SWEEP_ABSENT:
        plain += f"\nBEST BACKTEST CONFIGS: none — {sweep_detail}\n"
    elif sweep_state == _SWEEP_UNREADABLE:
        plain += f"\nBEST BACKTEST CONFIGS: UNREADABLE — {sweep_detail}\n"
    if sweep_top:
        plain += "\nBEST BACKTEST CONFIGS — top 3 distinct outcomes (last 90d)\n"
        plain += _shape_text(_SWEEP_PATH, "sweep")
        for i, e in enumerate(sweep_top):
            r = e["row"]
            plain += (
                f"  #{i+1}: {_sweep_config(r)} "
                f"Sharpe={r.get('sharpe',0):.2f} "
                f"return={_pct(r.get('total_return_pct'))} n={r.get('total_trades', '?')}\n"
            )
            if e["combos"] > 1:
                plain += f"      {describe_varied(e)}\n"

    if opt_state == _SWEEP_OK:
        nf = opt["noise_floor"]
        fm = opt.get("final_metrics", {})
        bp = opt["best_params"]
        plain += f"\nAUTORESEARCH OPTIMIZER ({opt_detail})\n"
        plain += _shape_text(_OPT_PATH, "optimizer")
        plain += f"  best score {opt['best_score']:.3f}  kept {opt.get('experiments_kept', '?')}/{opt.get('experiments_total', '?')}\n"
        st = opt.get("start") or {}
        if opt["noise_floor_valid"]:
            plain += (f"  cold-start noise floor: run-to-run sigma {nf['sigma']:.3f} (n={nf['n_runs']}, {nf['measured']}); "
                      f"moves < {nf['threshold']:.2f} between cold-start runs are noise; kept {nf['kept_mean']:.1f} +/- {nf['kept_sigma']:.1f}\n")
        else:
            plain += f"  NOISE FLOOR STALE — objective or window changed since {nf['measured']}; re-measure before reading a trend\n"
        if st.get("warm_start"):
            plain += ("  WARM-STARTED from last week's params: best score is a ratchet step within the window, not a draw from the floor's distribution\n")
        if st.get("start_score") is not None:
            if st.get("warm_start"):
                delta = (opt["best_score"] or 0) - st["start_score"]
                plain += (f"  incumbent re-scored on this window {st['start_score']:.3f} -> "
                          f"{'beaten by %.3f' % delta if st.get('displaced') else 'not beaten'} "
                          f"(incumbent trend = degradation signal; displacement = ratchet health)\n")
            else:
                plain += f"  cold start from defaults ({st['start_score']:.3f})\n"
        else:
            plain += "  start score not recorded (results file predates 2026-09-14)\n"
        plain += (f"  one sample from a flat optimum (Sharpe capped at 2.0 in composite), not a recommendation: "
                  f"score_thr={bp.get('lock1_threshold')} TP={bp.get('take_profit_pct', 0)*100:.0f}% SL={bp.get('stop_loss_pct', 0)*100:.0f}% "
                  f"TSL={bp.get('trailing_stop_pct')} hold={bp.get('time_stop_days')}d maxpos={bp.get('max_positions')} VIX={bp.get('vix_threshold')} "
                  f"-> Sharpe {fm.get('sharpe', 0):.2f} PF {fm.get('profit_factor', 0):.2f} WR {_pct(fm.get('win_rate'))} n={fm.get('total_trades')}\n")
    elif opt_state == _SWEEP_ABSENT:
        plain += f"\nAUTORESEARCH OPTIMIZER: none — {opt_detail}\n"
    else:
        plain += f"\nAUTORESEARCH OPTIMIZER: UNREADABLE — {opt_detail}\n"
    plain += f"  {_walkforward_last()}\n"

    plain += f"\n— Generated {now.strftime('%Y-%m-%d %H:%M')} UTC"

    return subject, html, plain


# ── Scheduler entry point ─────────────────────────────────────────────────────

def _store_snapshot(capture: dict) -> None:
    """Before the send, so a failed mail still leaves the week's facts. A failed
    write is logged as an error and the report still goes out; the weekend bundle
    warns on a sent week with no row."""
    try:
        from backend.db import insert_weekly_snapshot
        if insert_weekly_snapshot(capture["week"], capture):
            logger.info(f"Weekly snapshot stored for {capture['week']}")
        else:
            logger.warning(f"Weekly snapshot for {capture['week']} already stored — kept the first")
    except Exception as e:
        logger.error(f"Weekly snapshot not stored: {e}")


def send_weekly_report() -> None:
    # Mark before sending — prevents duplicate sends on rapid server restarts.
    # Trade-off: a crash after marking but before sending skips that week's email.
    # This is preferable to sending 3 emails over a weekend restart loop.
    _mark_sent()
    # Check for drift and recalibrate before building the report,
    # so before/after threshold changes can be included in the email.
    recal_changes: dict[str, tuple[float, float]] = {}
    flagged: list[str] = []
    try:
        since = _week_start_iso()
        until = (datetime.fromisoformat(since) + timedelta(days=7)).isoformat()
        status = _threshold_status(since, until)
        # Only recalibrate upward drift (scores expanded — threshold too loose).
        # Low pass rates during a broad selloff are the regime floor working correctly;
        # recalibrating down would make the system most permissive right after a crash.
        flagged = [s["sector"] for s in status["sectors"] if s["flag"] == "high"]
        if flagged:
            logger.info(f"Threshold upward drift in {len(flagged)} sector(s) — recalibrating: {flagged}")
            from backend.db import get_ticker_thresholds
            from backend.ticker_threshold_calibration import calibrate
            before = get_ticker_thresholds()
            calibrate()
            after = get_ticker_thresholds()
            _MIN_DELTA = 0.005
            recal_changes = {
                s: (before[s], after[s])
                for s in flagged
                if s in before and s in after and abs(after[s] - before[s]) >= _MIN_DELTA
            }
            logger.info(f"Recalibration complete — {len(recal_changes)} threshold(s) changed by >={_MIN_DELTA}")
        else:
            logger.info("Threshold pass rates within range — no recalibration needed")
    except Exception as e:
        logger.error(f"Pre-report recalibration failed: {e}")

    try:
        # Pass dict as-is: None = calibration didn't run; {} = ran but no meaningful change; filled = real changes
        capture: dict = {}
        subject, html, plain = build_report(recal_changes=recal_changes if flagged else None, capture=capture)
        _store_snapshot(capture)
        from backend.alerts import _cfg, _send_email, _send_slack
        cfg = _cfg()
        sent = False
        if cfg["email_to"] and cfg["smtp_user"] and cfg["smtp_pass"]:
            sent |= _send_email(cfg, subject, plain, html_body=html)
        if cfg["slack_url"]:
            sent |= _send_slack(cfg["slack_url"], subject, plain)
        if not sent:
            logger.info(f"Weekly report built (no channel configured):\n{plain}")
        else:
            logger.info("Weekly report sent")
    except Exception as e:
        logger.error(f"Weekly report failed: {e}")


def render_stored(week: str) -> str:
    """The plain report for a stored week, from its snapshot (2026-10-31). The reader of
    db.get_weekly_snapshot: the Saturday weekly read starts here. A week with no snapshot
    raises; it never falls back to a fresh model call."""
    from backend.db import get_weekly_snapshot
    snap = get_weekly_snapshot(week)
    if snap is None:
        raise LookupError(f"no stored snapshot for {week} (weekly_report_snapshots)")
    _, _, plain = build_report(snapshot={**snap["payload"], "_written_at": snap["written_at"]})
    return plain


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "--render":
        try:
            print(render_stored(sys.argv[2]))
        except LookupError as e:
            print(e, file=sys.stderr)
            sys.exit(1)
    else:
        print("usage: python -m backend.weekly_report --render YYYY-Www", file=sys.stderr)
        sys.exit(2)
