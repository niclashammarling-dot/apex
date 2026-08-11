"""
Alert dispatcher — sends notifications when live trades execute or risk limits are hit.

Supported channels (configure in .env):
  Slack:  SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
  Email:  ALERT_EMAIL_TO=you@example.com
          ALERT_EMAIL_FROM=apex@example.com
          ALERT_SMTP_HOST=smtp.gmail.com
          ALERT_SMTP_PORT=587
          ALERT_SMTP_USER=you@gmail.com
          ALERT_SMTP_PASS=app_password

If neither is configured, alerts are logged only (no-op).
"""
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx
from loguru import logger

from backend.config import ALPACA_BASE_URL


def _is_paper() -> bool:
    return "paper-api" in ALPACA_BASE_URL


def _mode_label() -> str:
    return "PAPER" if _is_paper() else "LIVE"


# ── Config (lazy — read at send time so .env changes take effect) ─────────────

def _cfg():
    import os
    return {
        "slack_url":   os.getenv("SLACK_WEBHOOK_URL", ""),
        "email_to":    os.getenv("ALERT_EMAIL_TO", ""),
        "email_from":  os.getenv("ALERT_EMAIL_FROM", ""),
        "smtp_host":   os.getenv("ALERT_SMTP_HOST", "smtp.gmail.com"),
        "smtp_port":   int(os.getenv("ALERT_SMTP_PORT", "587")),
        "smtp_user":   os.getenv("ALERT_SMTP_USER", ""),
        "smtp_pass":   os.getenv("ALERT_SMTP_PASS", ""),
    }


# ── Public alert functions ────────────────────────────────────────────────────

def alert_trade_executed(ticker: str, sector: str, notional: float,
                         price: float, tp: float, sl: float,
                         order_id: str) -> None:
    mode   = _mode_label()
    title  = f"[APEX {mode}] Trade Executed — {ticker}"
    body   = (
        f"*{ticker}* ({sector})\n"
        f"Entry:  ${price:.2f}\n"
        f"Size:   ${notional:.2f}\n"
        f"TP:     ${tp:.2f}  (+{(tp/price-1)*100:.1f}%)\n"
        f"SL:     ${sl:.2f}  (-{(1-sl/price)*100:.1f}%)\n"
        f"Order:  {order_id}"
    )
    _dispatch(title, body)


def alert_daily_loss_cap(loss: float, cap: float) -> None:
    mode  = _mode_label()
    title = f"[APEX {mode}] Daily Loss Cap Hit"
    body  = (
        f"Daily realized loss ${loss:.2f} has reached the cap of ${cap:.2f}.\n"
        "Live gate trading suspended for today."
    )
    _dispatch(title, body)


def alert_config_corrupted(config_name: str, error: str) -> None:
    title = f"[APEX] Config File Corrupted — {config_name}"
    body  = (
        f"The {config_name} config file was unreadable and has been reset to defaults.\n"
        f"Error: {error}\n"
        "Check your saved settings and re-apply any customisations."
    )
    _dispatch(title, body)


def alert_regime_exits(closed: list[dict], mode: str = "DEMO") -> None:
    """Fire once per regime-exit batch with a summary of all positions closed."""
    if not closed:
        return
    total_pnl = sum(c["pnl"] for c in closed)
    title = f"[APEX {mode}] Regime Exit — {len(closed)} position(s) closed"
    lines = [
        title,
        "Trigger: sector dropped below leaderboard cutoff\n",
    ]
    for c in closed:
        pnl_sign  = "+" if c["pnl"] > 0 else ""
        avg_score = c.get("sector_avg_score")
        score_str = f"  sector avg_score={avg_score:.3f}" if avg_score is not None else ""
        held      = c.get("held_days")
        held_str  = f"{held}d" if held is not None else "?"
        notional  = c.get("notional")
        notional_str = f"${notional:,.0f}" if notional is not None else "?"
        entry     = c.get("entry_price")
        exit_p    = c.get("exit_price")
        price_str = (
            f"  entry ${entry:.2f} → exit ${exit_p:.2f}"
            if entry is not None and exit_p is not None else ""
        )
        lines.append(
            f"  {c['ticker']:<6} {c['sector']:<16} "
            f"held {held_str:<5} notional {notional_str}"
        )
        lines.append(
            f"         {price_str}"
        )
        lines.append(
            f"         P&L: {pnl_sign}${c['pnl']:.2f} ({pnl_sign}{c['pnl_pct']*100:.1f}%)  "
            f"{c['outcome']}{score_str}"
        )
        lines.append("")
    total_sign = "+" if total_pnl > 0 else ""
    lines.append(f"Net P&L: {total_sign}${total_pnl:.2f}")
    body = "\n".join(lines)
    _dispatch(title, body)


def alert_ticker_data_gap(symbol: str, sector: str, consecutive: int, rows: int) -> None:
    title = f"[APEX] Ticker Data Gap — {symbol}"
    body  = (
        f"*{symbol}* ({sector}) has returned insufficient history "
        f"for {consecutive} consecutive poll cycles.\n"
        f"Last fetch: {rows} rows (need 55).\n"
        "Possible causes: delisting, acquisition, yfinance outage, or ticker rename.\n"
        "Action: remove from tickers.json and checks_sector.py if delisted."
    )
    _dispatch(title, body)


def alert_position_unreconciled(ticker: str, entry_price: float, qty: float,
                                 detail: str) -> None:
    """
    A DB-open live position is gone from the broker with no fill, order, or
    account activity to explain it — the position value did not move through
    a sale. No exit price is fabricated; this requires manual resolution
    (broker support, dashboard inspection) before the trade record is closed.
    """
    mode  = _mode_label()
    title = f"[APEX {mode}] UNRECONCILED — {ticker} vanished with no trail"
    body  = (
        f"{ticker}: entry ${entry_price:.2f} x {qty:g} shares is no longer held "
        f"at the broker, but no fill, order, or account activity record explains "
        f"the close.\n\n"
        f"{detail}\n\n"
        "No exit price has been booked — the trade is frozen as UNRECONCILED, "
        "not closed. Do not resume live trading on this ticker until a human "
        "confirms what happened via broker support/dashboard and manually "
        "resolves the trade record."
    )
    _dispatch(title, body)


def alert_position_untracked(ticker: str) -> None:
    """
    A ticker is held at the broker but has no matching OPEN row in our own
    live_trades table — the mirror image of alert_position_unreconciled.
    Independent of the daily loss cap: this catches a broker/DB divergence
    at the moment it's observed rather than via the equity gap the next day.
    """
    mode  = _mode_label()
    title = f"[APEX {mode}] Position/DB mismatch — {ticker}"
    body  = (
        f"{ticker} is held at the broker but has no OPEN row in live_trades.\n"
        "Either an untracked order was placed outside the gate, or a DB write "
        "was lost. Investigate before the next live cycle."
    )
    _dispatch(title, body)


def alert_data_quality_divergence(broker_day_pnl: float, apex_day_pnl: float | None,
                                   missing_from_broker: list[str]) -> None:
    """
    Either broker-reported day P&L disagrees with APEX's own realized+
    unrealized reconstruction by more than LIVE_DATA_QUALITY_DIVERGENCE (same
    shape as the 2026-08-11 HON third snapshot-omission: $978 broker-side
    loss, $0 APEX-side), or the APEX-side reconstruction itself couldn't be
    computed (apex_day_pnl is None — broker.get_positions() or the DB read
    failed outright, not just disagreed). Both are "don't trust the broker
    number enough to size off it" — same halt, same alert. The label says
    which surface to distrust instead of implying a real trading loss.
    """
    mode  = _mode_label()
    title = f"[APEX {mode}] Data-Quality Halt (not a real loss)"
    missing_note = (
        f" Missing from broker position snapshot: {', '.join(missing_from_broker)}."
        if missing_from_broker else ""
    )
    if apex_day_pnl is None:
        comparison = (
            "APEX's own realized+unrealized reconstruction could not be computed this "
            "cycle (broker positions unreadable or a DB read failed) — broker day P&L "
            f"is unverified, not just unverified-and-disagreeing (${broker_day_pnl:.2f})."
        )
    else:
        comparison = (
            f"Broker day P&L (${broker_day_pnl:.2f}) diverges from APEX's own "
            f"realized+unrealized reconstruction (${apex_day_pnl:.2f}) by "
            f"${broker_day_pnl - apex_day_pnl:.2f}."
        )
    body  = (
        f"{comparison}{missing_note}\n\n"
        "Trading is halted with the same effect as a loss-cap trip, but this "
        "is not a booked trading loss — the broker's account/positions "
        "snapshot is the suspect surface, not the ledger. Do not resize or "
        "resume entries off the broker equity number until it reconciles "
        "with portfolio-history and the activity ledger."
    )
    _dispatch(title, body)


def alert_gate_blocked(reason: str) -> None:
    mode  = _mode_label()
    title = f"[APEX {mode}] Gate Blocked"
    body  = f"Live gate could not run: {reason}"
    _dispatch(title, body)


# ── Dispatch ──────────────────────────────────────────────────────────────────

def _dispatch(title: str, body: str) -> None:
    """Send to all configured channels. Failures are logged, never raised."""
    cfg = _cfg()
    sent = False

    if cfg["slack_url"]:
        sent |= _send_slack(cfg["slack_url"], title, body)

    if cfg["email_to"] and cfg["smtp_user"] and cfg["smtp_pass"]:
        sent |= _send_email(cfg, title, body)

    if not sent:
        logger.info(f"Alert (no channel configured) | {title} | {body}")


def _send_slack(webhook_url: str, title: str, body: str) -> bool:
    try:
        text = f"*{title}*\n{body}"
        resp = httpx.post(webhook_url, json={"text": text}, timeout=10)
        resp.raise_for_status()
        logger.info(f"Slack alert sent: {title}")
        return True
    except Exception as e:
        logger.warning(f"Slack alert failed: {e}")
        return False


def _send_email(cfg: dict, title: str, body: str, html_body: str | None = None) -> bool:
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = title
        msg["From"]    = cfg["email_from"]
        msg["To"]      = cfg["email_to"]
        msg.attach(MIMEText(body, "plain"))
        msg.attach(MIMEText(html_body if html_body else body.replace("\n", "<br>"), "html"))

        with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"]) as server:
            server.ehlo()
            server.starttls()
            server.login(cfg["smtp_user"], cfg["smtp_pass"])
            server.sendmail(cfg["email_from"], cfg["email_to"], msg.as_string())

        logger.info(f"Email alert sent: {title}")
        return True
    except Exception as e:
        logger.warning(f"Email alert failed: {e}")
        return False
