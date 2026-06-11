#!/usr/bin/env python3
"""
APEX Mechanical Checks — orchestrator.

Imports domain check modules and runs all checks. Writes findings to
audit/nightly-report-YYYY-MM-DD.md. LLM checks 1, 2, 7, 8 are appended
separately by llm_checks.py.

Domain modules:
  checks_config  — CHECKs 4, 9, 11, 13, 21, 37, 40  (config parity, drift, coverage)
  checks_gate    — CHECKs 24, 25, 26, 28, 29, 38      (wiring, strings, silence)
  checks_data    — CHECKs 14, 15, 17, 22, 33, 35, 39, 44  (DB state, freshness, peak price, regime weight validation)
  checks_sector  — CHECKs 5, 27, 36, 41, 42, 43        (GICS, sub-check rates, expansion, addition completeness)
  checks_code    — CHECKs 3, 6, 10, 12, 16, 30, 31, 45  (broker, tests, yfinance, wiring, static analysis)
  (CHECK 32 git sync lives directly here — it needs subprocess and is orchestrator-level)
"""
import subprocess
from datetime import date, timedelta

from audit._audit_core import REPO, TODAY, REPORT, findings, triggered, flag
from audit import checks_config, checks_gate, checks_data, checks_sector, checks_code


# ── CHECK 32 — Git sync divergence ───────────────────────────────────────────

def check32():
    """
    Verify local master is not behind origin/master and working tree is clean.

    Two assertions:
      1. Local master not behind origin/master — nightly agent commits to origin;
         unpushed dev commits cause stale audit/ and wrong frontend last-test date.
         CRITICAL if >3 behind, WARNING if 1–3.
      2. No uncommitted files in working tree — nightly agent may write files without
         committing; those changes silently persist across sessions and are invisible
         to the audit history.

    Prevented by: 2026-05-09. 26 nightly commits accumulated on origin over 25 days
    while 19 dev commits sat unpushed. Extended 2026-05-12: nightly agent wrote 14
    files (frontend + backend) on May 9 without committing.
    """
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD..origin/master"],
            cwd=REPO, capture_output=True, text=True, timeout=15,
        )
        behind = int(result.stdout.strip()) if result.returncode == 0 else None
    except Exception:
        behind = None

    if behind is None:
        flag(32, "Git sync divergence", "WARNING", ".git/",
             "Could not determine commits behind origin/master — fetch may have failed")
        return

    if behind > 3:
        flag(32, "Git sync divergence", "CRITICAL", ".git/",
             f"Local master is {behind} commits behind origin/master — dev commits were not pushed; "
             f"audit/ is stale, frontend will show outdated last-test date")
    elif behind > 0:
        flag(32, "Git sync divergence", "WARNING", ".git/",
             f"Local master is {behind} commit(s) behind origin/master — push dev commits after session close")

    try:
        dirty = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=REPO, capture_output=True, text=True, timeout=15,
        )
        uncommitted = [l for l in dirty.stdout.splitlines() if l.strip()]
        count = len(uncommitted)
    except Exception:
        count = None

    if count is None:
        flag(32, "Git sync divergence", "WARNING", ".git/",
             "Could not count uncommitted changes — git diff HEAD failed")
    elif count > 0:
        flag(32, "Git sync divergence", "WARNING", ".git/",
             f"{count} uncommitted file(s) in working tree — changes will be lost or skipped on next nightly run")


# ── Check registry updater ────────────────────────────────────────────────────

def update_registry():
    checks_path = REPO / "audit/CHECKS.md"
    if not checks_path.exists():
        return

    today            = date.today().isoformat()
    retirement_days  = 90
    lines            = checks_path.read_text().splitlines()
    new_lines        = []
    retirement_candidates = []

    for line in lines:
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 9 and parts[1].isdigit():
            num  = int(parts[1])
            name, added, prompted, files = parts[2], parts[3], parts[4], parts[5]
            lt   = today if num in triggered else parts[6]
            lc   = parts[7] if num in triggered else today
            try:
                days_since = (date.today() - date.fromisoformat(lt)).days
                if days_since >= retirement_days:
                    since = (date.today() - timedelta(days=retirement_days)).isoformat()
                    if any(
                        subprocess.run(
                            ["git", "log", "--oneline", f"--since={since}", "--", f],
                            cwd=REPO, capture_output=True, text=True
                        ).stdout.strip()
                        for f in files.split(",")
                    ):
                        retirement_candidates.append((num, name, days_since))
            except Exception:
                pass
            new_lines.append(f"| {num} | {name} | {added} | {prompted} | {files} | {lt} | {lc} |")
        else:
            new_lines.append(line)

    checks_path.write_text("\n".join(new_lines) + "\n")
    return retirement_candidates


# ── Report writer ─────────────────────────────────────────────────────────────

# Complete set of mechanical checks — used for clean-row generation.
_ALL_CHECKS = {
    3, 4, 5, 6, 9, 10, 11, 12, 13, 14, 15, 16, 17,
    21, 22, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33,
    35, 36, 37, 38, 39, 40, 41, 42, 45,
}

_CHECK_NAMES = {
    3:  "Fractional qty",
    4:  "Config parity",
    5:  "Sector name strings",
    6:  "Test DB isolation",
    9:  "Config value drift",
    10: "Ticker data coverage",
    11: "NaN/null pipeline",
    12: "Lock3 context parity",
    13: "Undisclosed config change",
    14: "EOD regime freshness",
    15: "Calibration freshness",
    16: "yfinance scalar extraction",
    17: "Sentiment cache freshness",
    21: "Overflow increment range",
    22: "Yahoo data pipeline health",
    24: "Chain-runner wiring",
    25: "gate_decision string parity",
    26: "L1/L2 threshold-source parity",
    27: "GICS classification parity",
    28: "EXCLUDED_SECTORS gate wiring",
    29: "Live sector exposure cap wiring",
    30: "Startup live regime exit reconciliation",
    31: "Live bracket TIF and exit reconciliation",
    32: "Git sync divergence",
    33: "Bayesian multiplier health",
    35: "PCR collection freshness",
    36: "L4 sub-check pass rates",
    37: "Promote exclusion integrity",
    38: "Live entry absence-of-activity",
    39: "Live peak_price integrity",
    40: "Config coverage audit",
    41: "New-sector integrity",
    42: "New-sector integrity",
    45: "Static code analysis",
}


def write_report(retirement_candidates: list) -> None:
    REPORT.parent.mkdir(exist_ok=True)

    rows = []
    for num in sorted(_ALL_CHECKS):
        if num not in triggered:
            rows.append(f"| {num} {_CHECK_NAMES[num]} | ✓ | — | — | — |")

    for num, name, sev, file_line, finding in sorted(findings, key=lambda x: x[0]):
        rows.append(f"| {num} {name} | ⚠ | {sev} | {file_line} | {finding} |")

    retire_section = "None"
    if retirement_candidates:
        retire_section = "\n".join(
            f"CHECK {n} ({name}) — {days} days since last triggered"
            for n, name, days in retirement_candidates
        )

    n_crit = sum(1 for _, _, s, _, _ in findings if s == "CRITICAL")
    n_warn = sum(1 for _, _, s, _, _ in findings if s == "WARNING")
    n_info = sum(1 for _, _, s, _, _ in findings if s == "INFO")

    content = f"""# Batman's Report — {TODAY}
{len(findings)} issues: {n_crit} critical, {n_warn} warnings, {n_info} info
*(LLM checks 1, 2, 7, 8 appended below by llm_checks.py)*

| Check | Status | Sev | File:line | Finding |
|-------|--------|-----|-----------|---------|
""" + "\n".join(rows) + f"""

## Retirement Candidates
{retire_section}
"""
    REPORT.write_text(content)
    print(f"Mechanical checks done. {len(findings)} finding(s). Report: {REPORT.name}")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    checks_config.run()
    checks_gate.run()
    checks_data.run()
    checks_sector.run()
    checks_code.run()
    check32()
    retirement_candidates = update_registry()
    write_report(retirement_candidates or [])
