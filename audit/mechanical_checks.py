#!/usr/bin/env python3
"""
APEX Mechanical Checks — orchestrator.

Imports domain check modules and runs all checks. Writes findings to
audit/nightly-report-YYYY-MM-DD.md. LLM checks 1, 2, 7, 8 are appended
separately by llm_checks.py.

Domain modules:
  checks_config  — CHECKs 4, 9, 11, 13, 21, 37, 40  (config parity, drift, coverage)
  checks_gate    — CHECKs 24, 25, 26, 28, 29, 38      (wiring, strings, silence)
  checks_data    — CHECKs 14, 15, 17, 22, 33, 35, 39, 44, 46, 47, 55, 56  (DB state, freshness, peak price, regime weight validation, profit-lock ratchet, IPO sentiment)
  checks_sector  — CHECKs 5, 27, 36, 41, 42, 43, 57    (GICS, sub-check rates, expansion, addition completeness, SIC/SECTORS parity)
  checks_code    — CHECKs 3, 6, 10, 12, 16, 30, 31, 45  (broker, tests, yfinance, wiring, static analysis)
  (CHECK 32 git sync lives directly here — it needs subprocess and is orchestrator-level)
"""
import ast
import json
import re
import subprocess
from pathlib import Path
from datetime import date, timedelta

from audit._audit_core import REPO, TODAY, REPORT, findings, triggered, skipped, SKIPPED, flag
from audit import checks_config, checks_gate, checks_data, checks_sector, checks_code


# ── CHECK 32 — Git sync divergence ───────────────────────────────────────────

def check32():
    """
    Verify local master is neither behind nor ahead of origin/master, and the
    working tree is clean.

    Three assertions:
      1. Local master not behind origin/master — nightly agent commits to origin;
         unpushed dev commits cause stale audit/ and wrong frontend last-test date.
         CRITICAL if >3 behind, WARNING if 1–3.
      2. Local master not ahead of origin/master — dev commits sitting unpushed are
         invisible to anyone reading origin (including this check, in its other
         direction). CRITICAL if >3 ahead, WARNING if 1–3.
      3. No uncommitted files in working tree — nightly agent may write files without
         committing; those changes silently persist across sessions and are invisible
         to the audit history.

    Prevented by: 2026-05-09. 26 nightly commits accumulated on origin over 25 days
    while 19 dev commits sat unpushed. Extended 2026-05-12: nightly agent wrote 14
    files (frontend + backend) on May 9 without committing.

    Note on deployment: assertion 2 (ahead-count) is only meaningful when this
    check runs against a local dev checkout — in CI (.github/workflows/nightly-audit.yml),
    `actions/checkout` clones origin/master itself, so HEAD==origin/master by
    construction and ahead is always 0 there. CI can never observe a developer's
    unpushed local commits; this assertion only does its job when invoked from
    the local machine that might be holding them (2026-06-23: 15 local commits
    sat unpushed for ≥12 days, undetected, because the original check only ever
    measured the behind direction and only ever ran in CI).
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
        result = subprocess.run(
            ["git", "rev-list", "--count", "origin/master..HEAD"],
            cwd=REPO, capture_output=True, text=True, timeout=15,
        )
        ahead = int(result.stdout.strip()) if result.returncode == 0 else None
    except Exception:
        ahead = None

    if ahead is None:
        flag(32, "Git sync divergence", "WARNING", ".git/",
             "Could not determine commits ahead of origin/master — fetch may have failed")
    elif ahead > 3:
        flag(32, "Git sync divergence", "CRITICAL", ".git/",
             f"Local master is {ahead} commits ahead of origin/master — unpushed dev work; "
             f"push before it accumulates further (only detectable when this check runs locally, not in CI)")
    elif ahead > 0:
        flag(32, "Git sync divergence", "WARNING", ".git/",
             f"Local master is {ahead} commit(s) ahead of origin/master — push dev commits after session close")

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
    skipped_nums = {s[0] for s in skipped}

    for line in lines:
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 9 and parts[1].isdigit():
            num  = int(parts[1])
            name, added, prompted, files = parts[2], parts[3], parts[4], parts[5]
            if num in skipped_nums:
                lt, lc = parts[6], parts[7]      # did not evaluate: neither triggered nor clean
            else:
                lt = today if num in triggered else parts[6]
                lc = parts[7] if num in triggered else today
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

# Clean-row generation. _ALL_CHECKS is DERIVED from what each module's run()
# actually calls (2026-09-11) — the hand-maintained set below had drifted 26
# checks behind execution (43, 44, 46–56, 58–69 ran but rendered no ✓ row when
# clean, so a clean check was indistinguishable from an unregistered one).
# Names come from CHECKS.md's table, with _CHECK_NAMES as the fallback.
def _executed_checks() -> set:
    nums = set()
    for mod in (checks_config, checks_gate, checks_data, checks_sector, checks_code):
        tree = ast.parse(Path(mod.__file__).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "run":
                for call in ast.walk(node):
                    if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
                        m = re.fullmatch(r"check(\d+)", call.func.id)
                        if m:
                            nums.add(int(m.group(1)))
    nums.add(32)  # check32 lives in this module
    if _state_merged:
        nums.add(73)  # evaluated only in merge_state()
    return nums


def _registry_names() -> dict:
    names = {}
    path = REPO / "audit/CHECKS.md"
    if path.exists():
        for line in path.read_text().splitlines():
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 4 and parts[1].isdigit():
                names.setdefault(int(parts[1]), parts[2])
    return names


_ALL_CHECKS_LEGACY = {
    3, 4, 5, 6, 9, 10, 11, 12, 13, 14, 15, 16, 17,
    21, 22, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33,
    35, 36, 37, 38, 39, 40, 41, 42, 45, 57, 74,
}

_CHECK_NAMES_LEGACY = {
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
    57: "SIC_TO_SECTOR/SECTORS parity",
    74: "Decaying date-comparison test shape",
}


def write_report(retirement_candidates: list) -> None:
    REPORT.parent.mkdir(exist_ok=True)
    _ALL_CHECKS = _executed_checks() | _ALL_CHECKS_LEGACY
    _CHECK_NAMES = {**_registry_names(), **_CHECK_NAMES_LEGACY}

    rows = []
    skipped_nums = {s[0] for s in skipped}
    for num in sorted(_ALL_CHECKS):
        if num not in triggered and num not in skipped_nums:
            rows.append(f"| {num} {_CHECK_NAMES.get(num, f'CHECK {num}')} | ✓ | — | — | — |")

    for num, name, sev, file_line, finding in sorted(findings, key=lambda x: x[0]):
        rows.append(f"| {num} {name} | ⚠ | {sev} | {file_line} | {finding} |")

    # SKIPPED: the check could not evaluate (runtime data file absent in this
    # environment). Not a finding, not a pass — kept out of the issue count.
    for num, name, file_line, reason in sorted(skipped, key=lambda x: x[0]):
        rows.append(f"| {num} {name} | ○ | {SKIPPED} | {file_line} | {reason} |")

    retire_section = "None"
    if retirement_candidates:
        retire_section = "\n".join(
            f"CHECK {n} ({name}) — {days} days since last triggered"
            for n, name, days in retirement_candidates
        )

    n_crit = sum(1 for _, _, s, _, _ in findings if s == "CRITICAL")
    n_warn = sum(1 for _, _, s, _, _ in findings if s == "WARNING")
    n_info = sum(1 for _, _, s, _, _ in findings if s == "INFO")
    n_skip = len({s[0] for s in skipped})

    content = f"""# Batman's Report — {TODAY}
{len(findings)} issues: {n_crit} critical, {n_warn} warnings, {n_info} info — {n_skip} check(s) SKIPPED (could not evaluate: runtime data absent in this environment)
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

# ── State-report split (2026-09-11) ──────────────────────────────────────────
# The nightly workflow runs on ubuntu-latest where apex.db and the app-written
# data files don't exist, so every state-reading check is SKIPPED there. The
# split: the app's host runs the same checks nightly with the data present and
# publishes findings as JSON (`--emit-state`, pushed to the orphan branch
# `audit-state` by audit/publish_state.py); CI fetches that file and merges it
# (`--merge-state`) so the report carries real state findings. CI stays the
# side that runs regardless of the app — if the state file is older than
# STATE_MAX_AGE_HOURS, CHECK 73 fires CRITICAL from *outside* the app's failure
# domain. That is the audit-runner liveness watchdog, as a property of the
# split rather than a separate thing to keep alive.

STATE_MAX_AGE_HOURS = 36
_state_merged = False


def emit_state(path: Path) -> None:
    from datetime import datetime, timezone
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "host_commit": subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
                                      capture_output=True, text=True).stdout.strip(),
        "findings": [list(f) for f in findings],
        "skipped":  [list(s) for s in skipped],
        "evaluated": sorted(_executed_checks() - {s[0] for s in skipped}),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1))
    print(f"State emitted: {len(findings)} finding(s), {len(skipped)} skipped, "
          f"{len(payload['evaluated'])} evaluated → {path}")


def merge_state(path: Path) -> None:
    """Fold the host's state findings into this run; CHECK 73 on staleness."""
    from datetime import datetime, timezone
    global _state_merged
    _state_merged = True
    if not path.exists():
        flag(73, "Audit-runner liveness watchdog", "CRITICAL", str(path),
             "no state report fetched — the app host has never published one, or the "
             "audit-state branch fetch failed. State-reading checks did not evaluate.")
        return
    try:
        payload = json.loads(path.read_text())
        generated = datetime.fromisoformat(payload["generated_at"])
    except Exception as e:
        flag(73, "Audit-runner liveness watchdog", "CRITICAL", str(path),
             f"state report unreadable: {e}")
        return
    age_h = (datetime.now(timezone.utc) - generated).total_seconds() / 3600
    if age_h > STATE_MAX_AGE_HOURS:
        flag(73, "Audit-runner liveness watchdog", "CRITICAL", str(path),
             f"state report is {age_h:.0f}h old (generated {payload['generated_at']}, "
             f"host commit {payload.get('host_commit', '?')}) — the app host's nightly "
             f"publish has not run for >{STATE_MAX_AGE_HOURS}h. App down, scheduler job "
             f"dead, or push failing. State-reading checks did not evaluate.")
        return
    # Fresh: replace only this run's SKIPPED rows with the host's results for
    # those same checks. CI stays authoritative for everything it could
    # evaluate itself (code-shape checks run in both places; merging both
    # would double every finding).
    take = {s[0] for s in skipped} & set(payload.get("evaluated", []))
    skipped[:] = [s for s in skipped if s[0] not in take]
    for num, name, sev, file_line, finding in payload.get("findings", []):
        if num in take:
            flag(num, name, sev, file_line, f"{finding} [host, {payload['generated_at'][:16]}]")
    print(f"State merged: {age_h:.1f}h old, {len(take)} check(s) taken from host")


def main(argv: list[str] | None = None) -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit-state", type=Path, metavar="PATH",
                    help="write findings/skipped as JSON and exit — no report, no registry write")
    ap.add_argument("--merge-state", type=Path, metavar="PATH",
                    help="fold a host-published state JSON into this run before reporting")
    args = ap.parse_args(argv)

    checks_config.run()
    checks_gate.run()
    checks_data.run()
    checks_sector.run()
    checks_code.run()
    check32()
    if args.emit_state:
        emit_state(args.emit_state)
        return
    if args.merge_state:
        merge_state(args.merge_state)
    retirement_candidates = update_registry()
    write_report(retirement_candidates or [])


if __name__ == "__main__":
    main()
