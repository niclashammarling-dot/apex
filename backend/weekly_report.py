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

_COMMENTARY_SYSTEM = """You are the analytics engine for APEX, an automated paper-trading signal system.
Each Friday you receive a week's performance data and write a concise analyst commentary included in the operator report email.

Rules:
- 3–5 sentences maximum
- Synthesise — do not restate numbers verbatim (they appear in the tables below)
- Lead with the most notable finding, positive or negative
- Flag anything actionable: high L3 filter rate, threshold upward drift (scores expanded above expected range), config/sweep divergence, significant drawdown, sector concentration risk
- If broad_market_compressed is true: low/zero L1 pass rate and no trades entered are the regime floor working correctly during a market selloff — do NOT flag this as a filtering concern, do NOT suggest recalibration. Threshold drift with low_flagged sectors is compression, not miscalibration. Only threshold.high_flagged sectors warrant recalibration comment
- If live trading had 0 trades this week, skip it entirely
- Tone: direct, data-driven, no filler phrases like "Overall" or "In summary"
"""

_SWEEP_PATH = Path(__file__).parent.parent / "data" / "sweep_results.json"


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


def _gate_funnel(since: str, until: str) -> dict:
    """Demo gate pass rates for the week."""
    from backend.db import get_db
    conn = get_db()
    try:
        row = conn.execute("""
            SELECT
                COUNT(*) AS evaluated,
                SUM(lock1_pass) AS l1_pass,
                SUM(CASE WHEN gate_decision='SKIPPED_OPEN'    THEN 1 ELSE 0 END) AS skipped_open,
                SUM(CASE WHEN gate_decision='SKIPPED_COOLOFF' THEN 1 ELSE 0 END) AS skipped_cooloff,
                SUM(CASE WHEN gate_decision='FILTERED_ELIGIBILITY'  AND lock1_pass=1 THEN 1 ELSE 0 END) AS filtered_eligibility,
                SUM(CASE WHEN gate_decision='FILTERED_L2'     THEN 1 ELSE 0 END) AS l2_fail,
                SUM(CASE WHEN lock2_pass=1 AND lock3_pass=0 THEN 1 ELSE 0 END) AS l3_fail,
                SUM(CASE WHEN lock3_pass=1 THEN 1 ELSE 0 END) AS traded
            FROM signals
            WHERE timestamp >= ? AND timestamp < ? AND gate_decision IS NOT NULL
        """, (since, until)).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def _live_gate_funnel(since: str, until: str) -> dict:
    from backend.db import get_db
    conn = get_db()
    try:
        row = conn.execute("""
            SELECT
                COUNT(*) AS evaluated,
                SUM(lock1_pass) AS l1_pass,
                SUM(CASE WHEN gate_decision='SKIPPED_OPEN'    THEN 1 ELSE 0 END) AS skipped_open,
                SUM(CASE WHEN gate_decision='SKIPPED_COOLOFF' THEN 1 ELSE 0 END) AS skipped_cooloff,
                SUM(CASE WHEN gate_decision='FILTERED_ELIGIBILITY'  AND lock1_pass=1 THEN 1 ELSE 0 END) AS filtered_eligibility,
                SUM(CASE WHEN gate_decision='FILTERED_L2'     THEN 1 ELSE 0 END) AS l2_fail,
                SUM(CASE WHEN lock2_pass=1 AND lock3_pass=0 THEN 1 ELSE 0 END) AS l3_fail,
                SUM(CASE WHEN lock3_pass=1 THEN 1 ELSE 0 END) AS traded
            FROM live_gate_history
            WHERE timestamp >= ? AND timestamp < ? AND gate_decision IS NOT NULL
        """, (since, until)).fetchone()
        return dict(row) if row else {}
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
    Per-sector Lock 1 threshold status.

    For each calibrated sector, computes this week's actual pass rate
    (% of ticker_history scores >= calibrated threshold). The calibrator
    targets p80, so expected pass rate is ~20%. Significant deviation
    suggests the distribution has shifted and recalibration is warranted:
      - pass rate >> 20%: scores expanded upward — threshold too loose
      - pass rate << 20%: scores compressed downward — threshold too tight
    """
    from backend.db import get_db, get_ticker_thresholds
    from backend.demo_config import get_demo_config
    from backend.ticker_config import get_sectors

    flat = get_demo_config().get("lock1_threshold", 0.45)
    calibrated = get_ticker_thresholds()
    all_sectors = sorted(get_sectors().keys())

    # Pull this week's ticker_history scores
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT sector, signal_score FROM ticker_history
            WHERE day >= DATE(?) AND day < DATE(?)
        """, (since[:10], until[:10])).fetchall()
    finally:
        conn.close()

    from collections import defaultdict
    week_scores: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        week_scores[r["sector"]].append(r["signal_score"])

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

        sectors_out.append({
            "sector":     sector,
            "threshold":  thresh,
            "pass_rate":  pass_rate,
            "n":          n,
            "flag":       flag,
            "fallback":   thresh is None,
        })

    return {
        "flat":    flat,
        "sectors": sectors_out,
    }


def _sweep_best() -> list[dict] | None:
    if not _SWEEP_PATH.exists():
        return None
    try:
        with open(_SWEEP_PATH) as f:
            data = json.load(f)
        return data.get("top_configs", [])[:3]
    except Exception:
        return None


# ── Format helpers ────────────────────────────────────────────────────────────

def _pct(v: float | None) -> str:
    return f"{v*100:.1f}%" if v is not None else "—"


def _pnl_color(v: float) -> str:
    return "#22c55e" if v >= 0 else "#ef4444"


def _funnel_rate(numer: int | None, denom: int | None) -> str:
    if not denom:
        return "—"
    return f"{(numer or 0) / denom * 100:.0f}%"


def _funnel_count(n: int | None) -> str:
    return "—" if n is None else str(n)


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
    top_sector: tuple | None,
    thresh_status: dict,
    sweep_top: list | None,
    recal_changes: dict | None,
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

        de = dfunnel.get("evaluated") or 0
        l1p = dfunnel.get("l1_pass") or 0
        l2f = dfunnel.get("l2_fail") or 0
        l3f = dfunnel.get("l3_fail") or 0

        sectors_data = thresh_status.get("sectors", [])
        high_flagged = [s["sector"] for s in sectors_data if s.get("flag") == "high"]
        low_flagged  = [s["sector"] for s in sectors_data if s.get("flag") == "low"]
        # ≥5 sectors simultaneously low = broad market compression, not threshold drift
        broad_compressed = len(low_flagged) >= 5

        payload = {
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
                "closed_trades": live["closed_total"],
                "wins":          live["wins"],
                "losses":        live["losses"],
                "win_rate":      round(live_wr, 3) if live_wr is not None else None,
                "realized_pnl":  live["realized_pnl"],
                "regime_exits":  live["regime_exits"],
                "regime_pnl":    live["regime_pnl"],
            },
            "gate_funnel_demo": {
                "evaluated":      de,
                "l1_pass_rate":   round(l1p / de, 3) if de else None,
                "l2_filter_rate": round(l2f / l1p, 3) if l1p else None,
                "l3_filter_rate": round(l3f / max(l1p - l2f, 1), 3) if l1p else None,
                "trades_entered": dfunnel.get("traded", 0),
            },
            "top_sector":    {"name": top_sector[0], "avg_score": top_sector[1]} if top_sector else None,
            "broad_market_compressed": broad_compressed,
            "threshold_drift": {
                "high_flagged":  high_flagged,
                "low_flagged":   low_flagged,
                "recalibrated":  list(recal_changes.keys()) if recal_changes else [],
            },
            "current_config": {
                "lock1_threshold":    cfg.get("lock1_threshold"),
                "take_profit_pct":    cfg.get("take_profit_pct"),
                "max_positions":      cfg.get("max_positions"),
                "vix_threshold":      cfg.get("vix_threshold"),
            },
            "sweep_best": sweep_top[0] if sweep_top else None,
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


# ── HTML builder ──────────────────────────────────────────────────────────────

def build_report(recal_changes: dict[str, tuple[float, float]] | None = None) -> tuple[str, str, str]:
    """Return (subject, html_body, plain_body)."""
    since  = _week_start_iso()
    until  = (datetime.fromisoformat(since) + timedelta(days=7)).isoformat()
    now    = datetime.now(timezone.utc)
    week_label = now.strftime("Week ending %B %d, %Y")

    demo  = _demo_stats(since, until)
    live  = _live_stats(since, until)
    dfunnel = _gate_funnel(since, until)
    lfunnel = _live_gate_funnel(since, until)
    top_sector = _top_sector(since, until)
    thresh_status = _threshold_status(since, until)
    sweep_top  = _sweep_best()

    commentary = _gpt4o_commentary(
        demo, live, dfunnel, lfunnel,
        top_sector, thresh_status, sweep_top, recal_changes,
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

    # Gate funnel table
    de = dfunnel.get("evaluated", 0) or 0
    le = lfunnel.get("evaluated", 0) or 0

    funnel_table = f"""
    <table style='border-collapse:collapse;width:100%;font-size:13px;color:#e5e7eb;'>
      <thead><tr>
        {th('Stage')}{th('Demo')}{th('Live')}
      </tr></thead>
      <tbody>
        {_row(_td('Evaluated'), _td(str(de)), _td(str(le)))}
        {_row(_td('L1 pass rate'), _td(_funnel_rate(dfunnel.get('l1_pass'), de)), _td(_funnel_rate(lfunnel.get('l1_pass'), le)))}
        {_row(_td('Skipped — open position'), _td(_funnel_count(dfunnel.get('skipped_open'))), _td(_funnel_count(lfunnel.get('skipped_open'))))}
        {_row(_td('Skipped — cooloff'), _td(_funnel_count(dfunnel.get('skipped_cooloff'))), _td(_funnel_count(lfunnel.get('skipped_cooloff'))))}
        {_row(_td('Filtered — eligibility'), _td(_funnel_count(dfunnel.get('filtered_eligibility'))), _td(_funnel_count(lfunnel.get('filtered_eligibility'))))}
        {_row(_td('L2 fail — threshold (of L1 pass)'), _td(_funnel_rate(dfunnel.get('l2_fail'), dfunnel.get('l1_pass'))), _td(_funnel_rate(lfunnel.get('l2_fail'), lfunnel.get('l1_pass'))))}
        {_row(_td('L3 fail (of L2 pass)'), _td(_funnel_rate(dfunnel.get('l3_fail'), (dfunnel.get('l1_pass') or 0) - (dfunnel.get('l2_fail') or 0))), _td(_funnel_rate(lfunnel.get('l3_fail'), (lfunnel.get('l1_pass') or 0) - (lfunnel.get('l2_fail') or 0))))}
        {_row(_td('Trades entered', bold=True), _td(str(dfunnel.get('traded', 0)), bold=True), _td(str(lfunnel.get('traded', 0)), bold=True))}
      </tbody>
    </table>"""

    # Top sector
    sector_html = ""
    if top_sector:
        sector_html = f"""
        <p style='color:#e5e7eb;font-size:13px;'>
          <strong>{top_sector[0]}</strong> — avg score {top_sector[1]:.3f}
        </p>"""
    else:
        sector_html = "<p style='color:#6b7280;font-size:13px;'>No snapshot data for this week.</p>"

    # Threshold status
    flat = thresh_status["flat"]
    sectors_info = thresh_status["sectors"]
    n_cal = sum(1 for s in sectors_info if not s["fallback"])

    high_flagged = [s["sector"] for s in sectors_info if s["flag"] == "high"]
    low_flagged  = [s["sector"] for s in sectors_info if s["flag"] == "low"]

    FLAG_COLOR  = {"high": "#f59e0b", "low": "#6366f1", "ok": "#22c55e", None: "#6b7280"}
    FLAG_LABEL  = {
        "high": "recalibrate — scores drifted up",
        "low":  "compressed — market selloff",
        "ok":   "ok",
        None:   "no data",
    }

    def _pass_rate_cell(s: dict) -> str:
        if s["fallback"]:
            return _td(f"fallback ({flat})", color="#6b7280")
        if s["pass_rate"] is None:
            return _td("< 5 scores", color="#6b7280")
        color = FLAG_COLOR[s["flag"]]
        pct = f"{s['pass_rate']*100:.0f}% of {s['n']}"
        return _td(pct, color=color)

    def _flag_cell(s: dict) -> str:
        label = FLAG_LABEL[s["flag"]] if not s["fallback"] else "fallback"
        color = FLAG_COLOR[s["flag"]] if not s["fallback"] else "#6b7280"
        return _td(label, color=color)

    thresh_rows_html = "".join(
        _row(_td(s["sector"]), _td(str(s["threshold"]) if s["threshold"] else "—"), _pass_rate_cell(s), _flag_cell(s))
        for s in sectors_info
    )

    notes = []
    if high_flagged:
        if recal_changes is not None and recal_changes:
            # Calibration ran and produced meaningful threshold changes
            change_parts = ", ".join(
                f"{s}: {recal_changes[s][0]} → {recal_changes[s][1]}"
                for s in high_flagged if s in recal_changes
            )
            unchanged = [s for s in high_flagged if s not in recal_changes]
            detail = change_parts
            if unchanged:
                detail += f"; unchanged (insufficient data): {', '.join(unchanged)}"
            notes.append(
                f"<p style='color:#f59e0b;font-size:13px;font-weight:600;margin:0 0 4px 0;'>"
                f"⚠ {len(high_flagged)} sector(s) recalibrated (upward drift): {detail}"
                f"</p>"
            )
        elif recal_changes is not None and not recal_changes:
            # Calibration ran but all changes were below minimum meaningful delta
            notes.append(
                f"<p style='color:#f59e0b;font-size:13px;margin:0 0 4px 0;'>"
                f"⚠ {len(high_flagged)} sector(s) upward drift detected — recalibration ran, no meaningful threshold change (&lt;0.5%): {', '.join(high_flagged)}"
                f"</p>"
            )
        else:
            # recal_changes is None — shouldn't happen for high_flagged, safety fallback
            notes.append(
                f"<p style='color:#f59e0b;font-size:13px;font-weight:600;margin:0 0 4px 0;'>"
                f"⚠ {len(high_flagged)} sector(s) showing upward score drift: {', '.join(high_flagged)}"
                f"</p>"
            )
    if low_flagged:
        notes.append(
            f"<p style='color:#6366f1;font-size:13px;margin:0 0 4px 0;'>"
            f"ℹ {len(low_flagged)} sector(s) compressed (broad market selloff) — regime floor working correctly, no recalibration: {', '.join(low_flagged)}"
            f"</p>"
        )
    if not high_flagged and not low_flagged:
        notes.append(
            "<p style='color:#22c55e;font-size:13px;margin:0 0 4px 0;'>"
            "All sectors within expected range — no recalibration needed."
            "</p>"
        )
    recal_note = "".join(notes)

    thresh_html = f"""
    {recal_note}
    <p style='color:#6b7280;font-size:12px;margin:0 0 6px 0;'>
      Expected pass rate ~20%. Amber = distribution shifted this week vs historical calibration.
      Flat fallback: {flat}.
    </p>
    <table style='border-collapse:collapse;width:100%;font-size:13px;color:#e5e7eb;'>
      <thead><tr>{th('Sector')}{th('Threshold')}{th('This week pass rate')}{th('Signal')}</tr></thead>
      <tbody>{thresh_rows_html}</tbody>
    </table>"""

    # Sweep top configs
    sweep_html = ""
    if sweep_top:
        sweep_rows_html = "".join(
            _row(
                _td(f"#{i+1}"),
                _td(str(r.get("lock1_threshold", "—"))),
                _td(f"{r.get('take_profit_pct', 0)*100:.0f}% / {r.get('stop_loss_pct', 0)*100:.0f}%"),
                _td(str(r.get("time_stop_days", "—"))),
                _td(f"{r.get('sharpe', 0):.2f}"),
                _td(_pct(r.get("total_return_pct")), color="#22c55e" if r.get("total_return_pct", 0) >= 0 else "#ef4444"),
                _td(_pct(r.get("win_rate"))),
            )
            for i, r in enumerate(sweep_top)
        )
        sweep_html = f"""
        <table style='border-collapse:collapse;width:100%;font-size:13px;color:#e5e7eb;'>
          <thead><tr>
            {th('#')}{th('L1 thresh')}{th('TP / SL')}{th('Hold days')}{th('Sharpe')}{th('Return')}{th('Win rate')}
          </tr></thead>
          <tbody>{sweep_rows_html}</tbody>
        </table>
        <p style='color:#6b7280;font-size:11px;margin-top:4px;'>
          Sweep covers last 90 trading days — L1-only backtest. Compare vs current demo config before promoting.
        </p>"""
    else:
        sweep_html = "<p style='color:#6b7280;font-size:13px;'>Sweep results not yet available (runs Saturday night).</p>"

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

    {commentary_html}

    <div style='{section_style}'>Performance</div>
    {perf_table}

    <div style='{section_style}'>Gate Funnel</div>
    {funnel_table}

    <div style='{section_style}'>Top Sector This Week</div>
    {sector_html}

    <div style='{section_style}'>Lock 1 Thresholds</div>
    {thresh_html}

    <div style='{section_style}'>Weekend Backtest Sweep — Best Configs</div>
    {sweep_html}

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
    if commentary:
        plain += f"\nAI ANALYSIS\n  {commentary}\n"

    plain += f"""
PERFORMANCE
  Demo: {demo['closed_total']} closed ({demo['wins']}W/{demo['losses']}L) | Regime exits: {demo['regime_exits']} ({plain_pnl(demo['regime_pnl'])}) | Win rate: {_pct(demo_wr)} | Realized P&L: {plain_pnl(demo['realized_pnl'])} | Unrealized: {plain_pnl(demo['unrealized_pnl'])} | Total equity: ${demo['balance']:,.2f} ({_pct(demo_return)})
  Live: {live['closed_total']} closed ({live['wins']}W/{live['losses']}L) | Regime exits: {live['regime_exits']} ({plain_pnl(live['regime_pnl'])}) | Win rate: {_pct(live_wr)} | P&L: {plain_pnl(live['realized_pnl'])}

GATE FUNNEL (Demo)
  Evaluated: {de} | L1 pass: {_funnel_rate(dfunnel.get('l1_pass'), de)} | Traded: {dfunnel.get('traded', 0)}

GATE FUNNEL (Live)
  Evaluated: {le} | L1 pass: {_funnel_rate(lfunnel.get('l1_pass'), le)} | Traded: {lfunnel.get('traded', 0)}

TOP SECTOR
  {f"{top_sector[0]} ({top_sector[1]:.3f})" if top_sector else "—"}

LOCK 1 THRESHOLDS ({n_cal} calibrated, flat fallback: {flat})
"""
    if high_flagged:
        if recal_changes is not None and recal_changes:
            change_parts = ", ".join(
                f"{s}: {recal_changes[s][0]}→{recal_changes[s][1]}"
                for s in high_flagged if s in recal_changes
            )
            unchanged = [s for s in high_flagged if s not in recal_changes]
            detail = change_parts
            if unchanged:
                detail += f" | unchanged (insufficient data): {', '.join(unchanged)}"
            plain += f"  ⚠ Recalibrated (upward drift): {detail}\n"
        elif recal_changes is not None and not recal_changes:
            plain += f"  ⚠ {len(high_flagged)} sector(s) upward drift — recalibration ran, no meaningful change (<0.5%): {', '.join(high_flagged)}\n"
        else:
            plain += f"  ⚠ {len(high_flagged)} sector(s) upward drift: {', '.join(high_flagged)}\n"
    if low_flagged:
        plain += f"  ℹ {len(low_flagged)} sector(s) compressed (market selloff — regime floor correct, no recalibration): {', '.join(low_flagged)}\n"
    if not high_flagged and not low_flagged:
        plain += "  All sectors within range — no recalibration needed.\n"
    for s in sectors_info:
        if s["fallback"]:
            plain += f"  {s['sector']:20s}  fallback ({flat})\n"
        elif s["pass_rate"] is None:
            plain += f"  {s['sector']:20s}  {s['threshold']}  no data\n"
        else:
            flag_str = {"high": " ← recalibrate (scores up)", "low": " ← compressed (market selloff)", "ok": ""}.get(s["flag"], "")
            plain += f"  {s['sector']:20s}  {s['threshold']}  pass={s['pass_rate']*100:.0f}% (n={s['n']}){flag_str}\n"

    if sweep_top:
        plain += "\nBEST BACKTEST CONFIGS (last 90d)\n"
        for i, r in enumerate(sweep_top):
            plain += (
                f"  #{i+1}: L1={r.get('lock1_threshold')} "
                f"TP={r.get('take_profit_pct',0)*100:.0f}% "
                f"SL={r.get('stop_loss_pct',0)*100:.0f}% "
                f"hold={r.get('time_stop_days')}d "
                f"Sharpe={r.get('sharpe',0):.2f} "
                f"return={_pct(r.get('total_return_pct'))}\n"
            )

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
