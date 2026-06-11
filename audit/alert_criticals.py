#!/usr/bin/env python3
"""
APEX Audit Critical Alert — runs after the nightly audit.
Scans today's report for CRITICAL findings and sends an email if any exist.

Environment variables (same as ALERT_* in .env, passed as GitHub secrets):
  ALERT_EMAIL_TO, ALERT_EMAIL_FROM
  ALERT_SMTP_USER, ALERT_SMTP_PASS
  ALERT_SMTP_HOST  (default: smtp.gmail.com)
  ALERT_SMTP_PORT  (default: 587)
"""
import os
import re
import smtplib
import sys
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

REPO   = Path(__file__).parent.parent
TODAY  = date.today().isoformat()
REPORT = REPO / "audit" / f"nightly-report-{TODAY}.md"


def parse_criticals(report: Path) -> list[dict]:
    findings = []
    for line in report.read_text().splitlines():
        if "| CRITICAL |" in line or "| CRITICAL" in line:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 6:
                findings.append({
                    "check":    parts[1],
                    "file":     parts[4],
                    "finding":  parts[5],
                })
    return findings


def send_alert(criticals: list[dict]) -> bool:
    email_to   = os.getenv("ALERT_EMAIL_TO", "")
    email_from = os.getenv("ALERT_EMAIL_FROM", "")
    smtp_user  = os.getenv("ALERT_SMTP_USER", "")
    smtp_pass  = os.getenv("ALERT_SMTP_PASS", "")
    smtp_host  = os.getenv("ALERT_SMTP_HOST", "smtp.gmail.com")
    smtp_port  = int(os.getenv("ALERT_SMTP_PORT", "587"))

    if not all([email_to, email_from, smtp_user, smtp_pass]):
        print("Email not configured — printing to stdout instead:")
        for c in criticals:
            print(f"  CRITICAL [{c['check']}] {c['file']}: {c['finding']}")
        return False

    subject = f"[APEX] ⚠ {len(criticals)} CRITICAL finding(s) in Batman's report {TODAY}"

    rows = "\n".join(
        f"<tr>"
        f"<td style='padding:6px 12px;color:#ef4444;font-weight:600;'>{c['check']}</td>"
        f"<td style='padding:6px 12px;color:#9ca3af;'>{c['file']}</td>"
        f"<td style='padding:6px 12px;color:#e5e7eb;'>{c['finding']}</td>"
        f"</tr>"
        for c in criticals
    )

    html = f"""<!DOCTYPE html>
<html>
<body style='background:#111827;font-family:system-ui,sans-serif;padding:24px;'>
  <div style='max-width:680px;margin:0 auto;'>
    <h2 style='color:#ef4444;margin:0 0 4px 0;'>APEX — Critical Audit Findings</h2>
    <p style='color:#6b7280;font-size:13px;margin:0 0 24px 0;'>{TODAY}</p>
    <p style='color:#e5e7eb;font-size:13px;'>
      Batman's report found <strong style='color:#ef4444;'>{len(criticals)} CRITICAL finding(s)</strong>
      that require immediate attention. These indicate incorrect trades or silent wrong state right now.
    </p>
    <table style='border-collapse:collapse;width:100%;font-size:13px;margin-top:16px;'>
      <thead>
        <tr style='background:#1f2937;'>
          <th style='padding:6px 12px;text-align:left;color:#9ca3af;'>Check</th>
          <th style='padding:6px 12px;text-align:left;color:#9ca3af;'>File</th>
          <th style='padding:6px 12px;text-align:left;color:#9ca3af;'>Finding</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    <p style='color:#6b7280;font-size:12px;margin-top:24px;'>
      Full report: audit/nightly-report-{TODAY}.md
    </p>
  </div>
</body>
</html>"""

    plain = f"APEX — {len(criticals)} CRITICAL audit finding(s) — {TODAY}\n\n"
    for c in criticals:
        plain += f"  [{c['check']}] {c['file']}\n  {c['finding']}\n\n"
    plain += f"Full report: audit/nightly-report-{TODAY}.md"

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = email_from
        msg["To"]      = email_to
        msg.attach(MIMEText(plain, "plain"))
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(email_from, email_to, msg.as_string())

        print(f"Critical alert sent to {email_to}")
        return True
    except Exception as e:
        print(f"Failed to send critical alert: {e}", file=sys.stderr)
        return False


if __name__ == "__main__":
    if not REPORT.exists():
        print(f"No report found for {TODAY} — skipping")
        sys.exit(0)

    criticals = parse_criticals(REPORT)
    if not criticals:
        print(f"No CRITICAL findings in {REPORT.name} — no alert sent")
        sys.exit(0)

    print(f"{len(criticals)} CRITICAL finding(s) found — sending alert")
    sent = send_alert(criticals)
    sys.exit(0 if sent else 1)
