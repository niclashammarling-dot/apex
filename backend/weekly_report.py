"""
Weekly performance report — emailed Friday at market close.

Covers the Mon–Fri window just ending:
  - GPT-4o analyst commentary (synthesised from all data below)
  - Demo P&L, win rate, gate funnel
  - Live P&L, win rate, gate funnel
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
- Gate funnel: each lock has the number that reached it and the number it filtered. Name the lock that filtered the most, with its counts. Do not call a filter rate high or low without a reference — none is supplied
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


def _demo_stats(since: str, until: str) -> dict:
    from backend.config import STARTING_BALANCE
    from backend.db import get_db
    conn = get_db()
    try:
        closed = conn.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN outcome='WIN'  THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN outcome IN ('LOSS','EXPIRED') THEN 1 ELSE 0 END) AS losses,
                COALESCE(SUM(pnl), 0) AS realized_pnl,
                SUM(CASE WHEN exit_reason='REGIME' THEN 1 ELSE 0 END) AS regime_exits,
                COALESCE(SUM(CASE WHEN exit_reason='REGIME' THEN pnl ELSE 0 END), 0) AS regime_pnl
            FROM trades
            WHERE exited_at >= ? AND exited_at < ? AND outcome IN ('WIN','LOSS','EXPIRED')
        """, (since, until)).fetchone()

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
            "closed_total":     closed["total"]  or 0,
            "wins":             closed["wins"]   or 0,
            "losses":           closed["losses"] or 0,
            "realized_pnl":     round(closed["realized_pnl"] or 0, 2),
            "regime_exits":     closed["regime_exits"] or 0,
            "regime_pnl":       round(closed["regime_pnl"] or 0, 2),
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
    conn = get_db()
    try:
        # exit_confidence != 'unverified' excludes the RECONCILIATION-tagged
        # batch (2026-08-18) — same filter as get_live_equity_curve() and
        # /live/compare; without it this report silently disagreed with both.
        closed = conn.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN outcome='WIN'  THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN outcome IN ('LOSS','EXPIRED') THEN 1 ELSE 0 END) AS losses,
                COALESCE(SUM(pnl), 0) AS realized_pnl,
                SUM(CASE WHEN exit_reason='REGIME' THEN 1 ELSE 0 END) AS regime_exits,
                COALESCE(SUM(CASE WHEN exit_reason='REGIME' THEN pnl ELSE 0 END), 0) AS regime_pnl
            FROM live_trades
            WHERE exited_at >= ? AND exited_at < ? AND outcome IN ('WIN','LOSS','EXPIRED')
              AND exit_confidence != 'unverified'
        """, (since, until)).fetchone()

        open_row = conn.execute("""
            SELECT COUNT(*) AS cnt FROM live_trades WHERE outcome = 'OPEN'
        """).fetchone()

        return {
            "closed_total":   closed["total"]  or 0,
            "wins":           closed["wins"]   or 0,
            "losses":         closed["losses"] or 0,
            "realized_pnl":   round(closed["realized_pnl"] or 0, 2),
            "regime_exits":   closed["regime_exits"] or 0,
            "regime_pnl":     round(closed["regime_pnl"] or 0, 2),
            "open_positions": open_row["cnt"],
        }
    finally:
        conn.close()


# The funnel reads gate_decision only. The lock1/2/3_pass columns changed meaning
# when the lock chain came in (gate_runner._chain_to_gate_result: lock1_pass =
# Quant, lock2_pass = Sentiment, lock3_pass = Claude), and the old query built on
# them counted open/cooloff skips as "L1 pass" and gated eligibility on
# lock1_pass=1, which has never occurred (report for 10-02: L1 83% shown, 41%
# true; eligibility 0 shown, 180 true). Locks 1–4 run in sequence (gate/chain.py
# exit_lock 1–4), so each has a true "reached" count. After Leading the order is
# not fixed — on the deferred-L5 path overflow is checked before Claude
# (gate_runner.py, TRADE_QUEUED_PENDING_L5) — so those outcomes are split, not
# chained.
_FUNNEL_SKIPS = ("SKIPPED_OPEN", "SKIPPED_COOLOFF", "SKIPPED_BLOCKLIST")
_FUNNEL_LOCKS = (
    ("Lock 1 Eligibility", ("FILTERED_ELIGIBILITY",)),
    ("Lock 2 Quant",       ("FILTERED_L1",)),
    ("Lock 3 Sentiment",   ("FILTERED_L2",)),
    ("Lock 4 Leading",     ("FILTERED_LEADING", "FILTERED_ETF_PENALTY")),
)
_FUNNEL_AFTER_LEADING = (
    ("Lock 5 Claude filtered",  ("FILTERED_L3",)),
    ("Overflow filtered",       ("FILTERED_OVERFLOW_QUANT",)),
    ("Not filled (rejected/failed)", ("TRADE_REJECTED", "TRADE_FAILED")),
    ("Entered",                 ("TRADE_EXECUTED",)),
)
_FUNNEL_TABLES = {"demo": "signals", "live": "live_gate_history"}


def _funnel(book: str, since: str, until: str) -> dict:
    """Gate funnel for one book ('demo' | 'live') over [since, until)."""
    from backend.db import get_db
    table = _FUNNEL_TABLES[book]
    conn = get_db()
    try:
        rows = conn.execute(f"""
            SELECT gate_decision, COUNT(*) AS cnt FROM {table}
            WHERE timestamp >= ? AND timestamp < ? AND gate_decision IS NOT NULL
            GROUP BY gate_decision
        """, (since, until)).fetchall()
    finally:
        conn.close()
    counts = {r["gate_decision"]: r["cnt"] for r in rows}
    known = set(_FUNNEL_SKIPS)
    for _, decs in _FUNNEL_LOCKS + _FUNNEL_AFTER_LEADING:
        known.update(decs)

    skipped = {d: counts.get(d, 0) for d in _FUNNEL_SKIPS}
    total = sum(counts.values())
    reached_gate = total - sum(skipped.values())
    locks = []
    n = reached_gate
    for label, decs in _FUNNEL_LOCKS:
        failed = sum(counts.get(d, 0) for d in decs)
        locks.append({"stage": label, "reached": n, "failed": failed, "passed": n - failed})
        n -= failed
    after = [{"outcome": label, "n": sum(counts.get(d, 0) for d in decs)}
             for label, decs in _FUNNEL_AFTER_LEADING]
    # Anything the stages above don't name is shown, never dropped: a new
    # decision string must not vanish from the totals.
    other = {d: c for d, c in counts.items() if d not in known}
    return {
        "total":          total,
        "skipped":        skipped,
        "reached_gate":   reached_gate,
        "locks":          locks,
        "passed_leading": n,
        "after_leading":  after,
        "entered":        counts.get("TRADE_EXECUTED", 0),
        "other":          other,
        "balanced":       n == sum(a["n"] for a in after) + sum(other.values()),
    }


def _sector_pnl(since: str, until: str) -> dict:
    """Closed trades per sector for the week, and open positions per sector now.

    Same populations as _demo_stats/_live_stats (live excludes
    exit_confidence='unverified'), so the sector rows sum to the totals above.
    """
    from backend.db import get_db
    conn = get_db()
    try:
        def closed(table: str, extra: str) -> list[dict]:
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
            "demo":      closed("trades", ""),
            "live":      closed("live_trades", "AND exit_confidence != 'unverified'"),
            "demo_open": open_now("trades"),
            "live_open": open_now("live_trades"),
        }
    finally:
        conn.close()


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
            # What Lock 2 applies: lock2_quant._sector_threshold
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
    return (f"L1={r.get('lock1_threshold')} TP={r.get('take_profit_pct', 0)*100:.0f}% "
            f"SL={r.get('stop_loss_pct', 0)*100:.0f}% hold={r.get('time_stop_days')}d "
            f"pos={r.get('max_positions')} vix={r.get('vix_threshold') or 'off'} "
            f"rs={'on' if r.get('use_leading_rs') else 'off'}")


def _pct(v: float | None) -> str:
    return f"{v*100:.1f}%" if v is not None else "—"


def _pnl_color(v: float) -> str:
    return "#22c55e" if v >= 0 else "#ef4444"


def _funnel_rate(numer: int | None, denom: int | None) -> str:
    if not denom:
        return "—"
    return f"{(numer or 0) / denom * 100:.0f}%"


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
            return {
                "candidates":        f["total"],
                "skipped":           f["skipped"],
                "reached_gate":      f["reached_gate"],
                "locks":             f["locks"],
                "passed_leading":    f["passed_leading"],
                "after_leading":     {a["outcome"]: a["n"] for a in f["after_leading"]},
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


def _plain_funnel(book: str, f: dict) -> str:
    out = (f"GATE FUNNEL ({book})\n  Candidates {f['total']} | skipped open {f['skipped']['SKIPPED_OPEN']}, "
           f"cooloff {f['skipped']['SKIPPED_COOLOFF']}")
    if f["skipped"]["SKIPPED_BLOCKLIST"]:
        out += f", blocklist {f['skipped']['SKIPPED_BLOCKLIST']}"
    out += f" | reached the gate {f['reached_gate']}\n"
    for st in f["locks"]:
        out += f"  {st['stage']:20s} {st['failed']} of {st['reached']} filtered\n"
    out += f"  Passed Leading {f['passed_leading']}: " + ", ".join(f"{a['outcome']} {a['n']}" for a in f["after_leading"]) + "\n"
    if f["other"]:
        out += "  Other decisions: " + ", ".join(f"{k} {v}" for k, v in f["other"].items()) + "\n"
    if not f["balanced"]:
        out += "  WARNING: counts after Leading do not sum to Passed Leading\n"
    return out


def _plain_sectors(sp: dict) -> str:
    out = ""
    for book in ("demo", "live"):
        rows = sp[book]
        out += f"  {book.capitalize()}: " + (", ".join(f"{r['sector']} {r['wins']}/{r['n']} ${r['pnl']:+,.2f}" for r in rows) or "none") + "\n"
    out += "  Open now (demo / live): " + (", ".join(
        f"{sec} {sp['demo_open'].get(sec, 0)}/{sp['live_open'].get(sec, 0)}"
        for sec in sorted(set(sp["demo_open"]) | set(sp["live_open"]))) or "none") + "\n"
    return out


def build_report(recal_changes: dict[str, tuple[float, float]] | None = None) -> tuple[str, str, str]:
    """Return (subject, html_body, plain_body)."""
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
    top_sector = _top_sector(since, until)
    thresh_status = _threshold_status(since, until)
    sweep_state, sweep_detail, sweep_top = _sweep_best()
    if sweep_state != _SWEEP_OK:
        logger.warning(f"Weekly report: sweep {sweep_state} — {sweep_detail}")
    opt_state, opt_detail, opt = _optimizer_best()
    if opt_state != _SWEEP_OK:
        logger.warning(f"Weekly report: optimizer {opt_state} — {opt_detail}")

    commentary = _gpt4o_commentary(
        demo, live, dfunnel, lfunnel,
        sector_pnl, thresh_status, sweep_top, recal_changes, partials,
    )

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

    # Gate funnel table — every rate shown with the count it is a rate of
    def _lock_cell(st: dict) -> str:
        if not st["reached"]:
            return _td("0 reached", color="#6b7280")
        return _td(f"{st['failed']} of {st['reached']} filtered ({_funnel_rate(st['passed'], st['reached'])} pass)")

    funnel_rows = [
        _row(_td('Candidates'), _td(str(dfunnel['total'])), _td(str(lfunnel['total']))),
        _row(_td('Skipped — open position'), _td(str(dfunnel['skipped']['SKIPPED_OPEN'])), _td(str(lfunnel['skipped']['SKIPPED_OPEN']))),
        _row(_td('Skipped — cooloff'), _td(str(dfunnel['skipped']['SKIPPED_COOLOFF'])), _td(str(lfunnel['skipped']['SKIPPED_COOLOFF']))),
    ]
    if dfunnel['skipped']['SKIPPED_BLOCKLIST'] or lfunnel['skipped']['SKIPPED_BLOCKLIST']:
        funnel_rows.append(_row(_td('Skipped — live blocklist'), _td(str(dfunnel['skipped']['SKIPPED_BLOCKLIST'])), _td(str(lfunnel['skipped']['SKIPPED_BLOCKLIST']))))
    funnel_rows.append(_row(_td('Reached the gate', bold=True), _td(str(dfunnel['reached_gate']), bold=True), _td(str(lfunnel['reached_gate']), bold=True)))
    for dst, lst in zip(dfunnel['locks'], lfunnel['locks']):
        funnel_rows.append(_row(_td(dst['stage']), _lock_cell(dst), _lock_cell(lst)))
    funnel_rows.append(_row(_td('Passed Leading', bold=True), _td(str(dfunnel['passed_leading']), bold=True), _td(str(lfunnel['passed_leading']), bold=True)))
    for da, la in zip(dfunnel['after_leading'], lfunnel['after_leading']):
        funnel_rows.append(_row(_td(f"&nbsp;&nbsp;{da['outcome']}", bold=da['outcome'] == 'Entered'),
                                _td(str(da['n']), bold=da['outcome'] == 'Entered'),
                                _td(str(la['n']), bold=la['outcome'] == 'Entered')))
    for book, f in (("Demo", dfunnel), ("Live", lfunnel)):
        if f['other']:
            funnel_rows.append(_row(_td(f"{book}: other decisions", color='#f59e0b'),
                                    _td(', '.join(f"{k} {v}" for k, v in f['other'].items()), color='#f59e0b'), _td('')))
        if not f['balanced']:
            funnel_rows.append(_row(_td(f"{book}: counts after Leading do not sum to Passed Leading", color='#ef4444'), _td(''), _td('')))

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

    # Closed trades by sector, with n — a description, not a finding at these sizes
    def _sector_cells(rows: list[dict], sector: str) -> str:
        r = next((x for x in rows if x["sector"] == sector), None)
        if not r:
            return _td("—", color="#6b7280")
        return _td(f"{r['wins']}/{r['n']} won, ${r['pnl']:+,.2f}", color=_pnl_color(r["pnl"]))

    sp_sectors = sorted({r["sector"] for r in sector_pnl["demo"] + sector_pnl["live"]}
                        | set(sector_pnl["demo_open"]) | set(sector_pnl["live_open"]))
    if sp_sectors:
        sector_pnl_html = f"""
    <table style='border-collapse:collapse;width:100%;font-size:13px;color:#e5e7eb;'>
      <thead><tr>{th('Sector')}{th('Demo closed')}{th('Live closed')}{th('Open now (demo / live)')}</tr></thead>
      <tbody>{"".join(_row(_td(sec), _sector_cells(sector_pnl['demo'], sec), _sector_cells(sector_pnl['live'], sec),
                           _td(f"{sector_pnl['demo_open'].get(sec, 0)} / {sector_pnl['live_open'].get(sec, 0)}"))
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
CLOSED TRADES BY SECTOR (n per sector; a description, not evidence of an edge)
{_plain_sectors(sector_pnl)}
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
                  f"L1={bp.get('lock1_threshold')} TP={bp.get('take_profit_pct', 0)*100:.0f}% SL={bp.get('stop_loss_pct', 0)*100:.0f}% "
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
        subject, html, plain = build_report(recal_changes=recal_changes if flagged else None)
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
