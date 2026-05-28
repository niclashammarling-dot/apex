#!/usr/bin/env python3
"""
APEX LLM Finding Verifier — runs after llm_checks.py.

For every finding row in today's report:
  1. Strip template placeholder rows (unfilled check_num/file:line/SEV artifacts)
  2. Resolve the file:line reference — mark [FILE NOT FOUND] if absent
  3. Verify (current: X) values — mark [VALUE UNVERIFIED] if X not in file
  4. Verify backtick-quoted identifiers:
       - If finding claims something IS present/wrong → verify the identifier exists
       - If finding claims something is MISSING → verify it is indeed absent
  5. Rewrite the report in place with verified/unverified tags

Mechanical check rows (3-6, 9-13) pass through untouched — they are correct by
construction. Only reasoning-heavy LLM rows (1, 2, 7, 8) are validated.
"""
import re
import sys
from datetime import date
from pathlib import Path

REPO   = Path(__file__).parent.parent
TODAY  = date.today().isoformat()
REPORT = REPO / "audit" / f"nightly-report-{TODAY}.md"

# Checks produced by the LLM — subject to verification
LLM_CHECK_PREFIXES = ("1 ", "2 ", "7 ", "8", "check_1", "check_2")

# Mechanical check prefixes — pass through untouched
MECHANICAL_PREFIXES = ("3 ", "4 ", "5 ", "6 ", "9 ", "10 ", "11 ", "12 ", "13 ")

# Template placeholder literals — rows containing these are artifacts
TEMPLATE_LITERALS = {"check_num check_name", "file:line", "one-line description", "SEV"}


# ── File resolution ───────────────────────────────────────────────────────────

def resolve_file(file_line_col: str) -> Path | None:
    """
    Given a 'file:line' column value, return the resolved Path or None.
    Tries the value as-is, then strips leading path components.
    Ignores placeholder values like '—', 'unknown', 'line', 'missing'.
    """
    if not file_line_col or file_line_col in ("—", "—", "-"):
        return None

    # Strip line number suffix
    base = file_line_col.split(":")[0].strip()

    # Reject obvious placeholders
    if base.lower() in ("file", "unknown", "missing", "present", "nn", ""):
        return None

    # Try direct path from repo root
    candidate = REPO / base
    if candidate.exists():
        return candidate

    # Try stripping leading components (e.g. 'backend/gate/gate_runner.py' vs 'gate_runner.py')
    parts = Path(base).parts
    for i in range(1, len(parts)):
        candidate = REPO / Path(*parts[i:])
        if candidate.exists():
            return candidate

    # Try searching common dirs
    for search_root in ["backend", "tests", "frontend/src", "audit"]:
        for found in (REPO / search_root).rglob(Path(base).name):
            return found

    return None


# ── Verification checks ───────────────────────────────────────────────────────

def verify_current_value(file_path: Path, finding: str) -> list[str]:
    """Check (current: X) patterns — the value must appear in the file."""
    issues = []
    for m in re.finditer(r'\(current:\s*([\d.]+)\)', finding):
        value = m.group(1)
        if value not in file_path.read_text(errors="ignore"):
            issues.append(f"VALUE UNVERIFIED: '{value}' not found in {file_path.name}")
    return issues


def verify_identifiers(file_path: Path, finding: str) -> list[str]:
    """
    Check backtick-quoted identifiers against the file.
    - If finding says 'Missing X' → confirm X is absent (flag if X is actually present)
    - Otherwise → confirm X is present (flag if X is absent)
    """
    issues = []
    text = file_path.read_text(errors="ignore")
    finding_lower = finding.lower()

    for m in re.finditer(r'`(\w+)`', finding):
        ident = m.group(1)
        if len(ident) < 4:  # skip short tokens — too many false positives
            continue
        present_in_file = ident in text

        # Context around this identifier in the finding
        start = max(0, m.start() - 30)
        context = finding_lower[start:m.start()].strip()
        claims_missing = any(w in context for w in ("missing", "absent", "not present", "not in"))

        if claims_missing and present_in_file:
            issues.append(f"CLAIM WRONG: '{ident}' claimed missing but found in {file_path.name}")
        elif not claims_missing and not present_in_file:
            issues.append(f"IDENTIFIER NOT FOUND: '{ident}' not in {file_path.name}")

    return issues


def is_template_row(parts: list[str]) -> bool:
    """Detect unfilled LLM template placeholder rows."""
    if len(parts) < 6:
        return False
    check   = parts[1].strip()
    sev     = parts[3].strip()
    file_lc = parts[4].strip()
    finding = parts[5].strip()

    # Separator rows — any cell that is all dashes (length ≥ 3)
    if all(re.fullmatch(r'-+', p.strip()) or p.strip() == "" for p in parts[1:6]):
        return True

    # Explicit template placeholders
    if check in TEMPLATE_LITERALS or file_lc in TEMPLATE_LITERALS:
        return True
    if finding in TEMPLATE_LITERALS or sev in TEMPLATE_LITERALS:
        return True

    return False


def is_llm_row(check_col: str) -> bool:
    check = check_col.strip()
    return any(check.startswith(p) for p in LLM_CHECK_PREFIXES)


def is_mechanical_row(check_col: str) -> bool:
    check = check_col.strip()
    return any(check.startswith(p) for p in MECHANICAL_PREFIXES)


# ── Main pass ─────────────────────────────────────────────────────────────────

def verify_report(report: Path) -> tuple[int, int, int]:
    """
    Rewrite the report in place.
    Returns (rows_checked, rows_unverified, templates_stripped).
    """
    lines = report.read_text().splitlines()
    out   = []
    rows_checked    = 0
    rows_unverified = 0
    templates_stripped = 0

    for line in lines:
        if not line.startswith("|"):
            out.append(line)
            continue

        parts = [p for p in line.split("|")]
        # parts[0] = '', parts[1] = check, parts[2] = status, parts[3] = sev,
        # parts[4] = file:line, parts[5] = finding, parts[6] = ''

        if len(parts) < 6:
            out.append(line)
            continue

        # Strip template artifacts
        if is_template_row(parts):
            templates_stripped += 1
            continue  # drop the row entirely

        check_col  = parts[1].strip()
        status_col = parts[2].strip()
        file_col   = parts[4].strip()
        finding    = parts[5].strip()

        # Only verify LLM rows with actual findings (not clean ✓ rows)
        if not is_llm_row(check_col) or status_col == "✓":
            out.append(line)
            continue

        rows_checked += 1
        issues = []

        # 1. File resolution
        resolved = resolve_file(file_col)
        if file_col and file_col not in ("—", "—") and resolved is None:
            issues.append(f"FILE NOT FOUND: {file_col.split(':')[0]}")
        elif resolved:
            # 2. (current: X) value verification
            issues.extend(verify_current_value(resolved, finding))
            # 3. Backtick identifier verification
            issues.extend(verify_identifiers(resolved, finding))

        if issues:
            rows_unverified += 1
            tag = " [UNVERIFIED: " + "; ".join(issues) + "]"
            parts[5] = " " + finding + tag + " "
            line = "|".join(parts)

        out.append(line)

    report.write_text("\n".join(out) + "\n")
    return rows_checked, rows_unverified, templates_stripped


if __name__ == "__main__":
    if not REPORT.exists():
        print(f"No report for {TODAY} — skipping")
        sys.exit(0)

    checked, unverified, stripped = verify_report(REPORT)
    print(
        f"LLM verification done — "
        f"{checked} LLM finding(s) checked, "
        f"{unverified} unverified, "
        f"{stripped} template artifact(s) stripped"
    )
