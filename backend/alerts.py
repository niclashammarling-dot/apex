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
