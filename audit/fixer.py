#!/usr/bin/env python3
"""
APEX Fixer Agent — applies safe, approved fixes to INFO findings from the nightly audit.

Rules:
- Only fixes INFO-severity rows. Skips CRITICAL and WARNING entirely.
- Only applies Pattern A: bare except with no logging.
- Never changes logic, return values, or control flow. Only adds a log line.
- Creates a branch and PR — never pushes to master.
- If nothing is auto-fixable, exits cleanly with no branch or PR.
"""

import re
import sys
import subprocess
from pathlib import Path
from datetime import date

REPO_ROOT = Path(__file__).parent.parent
AUDIT_DIR = Path(__file__).parent

# Keywords in function names that warrant logger.warning vs logger.debug
WARNING_KEYWORDS = {"loss", "cap", "block", "guard", "limit", "reject", "exceeded", "fail"}

RISK_FUNCTION_RE = re.compile("|".join(WARNING_KEYWORDS), re.IGNORECASE)


def find_today_report() -> Path | None:
    today = date.today().isoformat()
    path = AUDIT_DIR / f"nightly-report-{today}.md"
    return path if path.exists() else None


def parse_info_findings(report: Path) -> list[dict]:
    """Extract INFO rows from the findings table."""
    findings = []
    in_table = False
    for line in report.read_text().splitlines():
        if line.startswith("| Check") or line.startswith("|----"):
            in_table = True
            continue
        if in_table and line.startswith("|"):
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 6:
                continue
            # cols: '', check, status, sev, file:line, finding, ''
            sev = parts[3] if len(parts) > 3 else ""
            if sev != "INFO":
                continue
            file_line = parts[4] if len(parts) > 4 else ""
            finding = parts[5] if len(parts) > 5 else ""
            if file_line and file_line != "—":
                findings.append({"file_line": file_line, "finding": finding})
        elif in_table and not line.startswith("|"):
            in_table = False
    return findings


def is_pattern_a(finding: str) -> bool:
    """Bare except with no logging."""
    f = finding.lower()
    return "bare except" in f and any(w in f for w in ("silently", "no log", "swallows"))


def apply_pattern_a(file_line: str) -> tuple[bool, str]:
    """
    Add a logger call to a bare except block at the given file:line.
    Returns (success, reason).
    """
    if ":" not in file_line:
        return False, "no line number in file:line reference"

    parts = file_line.rsplit(":", 1)
    rel_path, lineno_str = parts[0].strip(), parts[1].strip()

    try:
        lineno = int(lineno_str)
    except ValueError:
        return False, f"could not parse line number from '{file_line}'"

    # Resolve path relative to repo root
    filepath = REPO_ROOT / rel_path
    if not filepath.exists():
        # Try stripping leading path components
        for part_count in range(1, 4):
            candidate = REPO_ROOT / Path(*Path(rel_path).parts[part_count:])
            if candidate.exists():
                filepath = candidate
                break
        else:
            return False, f"file not found: {rel_path}"

    lines = filepath.read_text().splitlines(keepends=True)

    # Find the except block at or near the given line (search ±5 lines)
    except_idx = None
    search_start = max(0, lineno - 6)
    search_end = min(len(lines), lineno + 4)
    for i in range(search_start, search_end):
        stripped = lines[i].strip()
        if re.match(r"except(\s+Exception)?(\s+as\s+\w+)?\s*:", stripped):
            except_idx = i
            break

    if except_idx is None:
        return False, f"no except block found near {rel_path}:{lineno}"

    except_line = lines[except_idx]

    # Check if already has a logger call inside the block
    indent = len(except_line) - len(except_line.lstrip())
    body_indent = indent + 4
    for j in range(except_idx + 1, min(except_idx + 6, len(lines))):
        body = lines[j]
        if body.strip() and len(body) - len(body.lstrip()) >= body_indent:
            if "logger." in body:
                return False, "already has a logger call — skipping"
        elif body.strip():
            break

    # Determine function name for the log message
    func_name = "unknown"
    for i in range(except_idx, -1, -1):
        m = re.match(r"\s*def (\w+)\s*\(", lines[i])
        if m:
            func_name = m.group(1)
            break

    # Ensure `as e` is on the except line
    new_except = re.sub(
        r"except(\s+Exception)?\s*:",
        "except Exception as e:",
        except_line.rstrip()
    )
    if new_except == except_line.rstrip():
        # Already has `as e` or is a bare except — normalise
        new_except = re.sub(r"except\s*:", "except Exception as e:", except_line.rstrip())

    # Choose log level
    log_level = "warning" if RISK_FUNCTION_RE.search(func_name) else "debug"
    log_indent = " " * body_indent
    log_line = f'{log_indent}logger.{log_level}("{func_name}: %s", e)\n'

    lines[except_idx] = new_except + "\n"
    lines.insert(except_idx + 1, log_line)

    filepath.write_text("".join(lines))
    return True, f"added logger.{log_level} to {rel_path}:{lineno} ({func_name})"


def git(args: list[str], check=True) -> subprocess.CompletedProcess:
    return subprocess.run(["git"] + args, cwd=REPO_ROOT, capture_output=True, text=True, check=check)


def gh(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["gh"] + args, cwd=REPO_ROOT, capture_output=True, text=True, check=True)


def main():
    report = find_today_report()
    if not report:
        print("No audit report for today — exiting.")
        return

    today = date.today().isoformat()
    findings = parse_info_findings(report)
    print(f"Found {len(findings)} INFO finding(s) in {report.name}")

    applied = []
    skipped = []

    for f in findings:
        file_line = f["file_line"]
        finding = f["finding"]

        if is_pattern_a(finding):
            ok, reason = apply_pattern_a(file_line)
            if ok:
                applied.append(f"{file_line} — {reason}")
                print(f"  FIXED: {file_line} — {reason}")
            else:
                skipped.append(f"{file_line} — {reason}")
                print(f"  SKIPPED: {file_line} — {reason}")
        else:
            skipped.append(f"{file_line} — no matching pattern")
            print(f"  SKIPPED: {file_line} — no matching pattern")

    if not applied:
        print("Nothing to fix — no branch or PR created.")
        return

    # Create branch and PR
    branch = f"fix/audit-{today}"
    git(["checkout", "-b", branch])

    changed_files = list({f.split(":")[0].strip() for f in applied})
    git(["add"] + [str(REPO_ROOT / p) for p in changed_files])

    fix_bullets = "\n".join(f"- {a}" for a in applied)
    skip_bullets = "\n".join(f"- {s}" for s in skipped) if skipped else "- none"

    commit_msg = (
        f"fix(audit): auto-fix INFO findings from {today} audit\n\n"
        + fix_bullets
        + "\n\nCo-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
    )
    git(["commit", "-m", commit_msg])
    git(["push", "-u", "origin", branch])

    pr_body = (
        f"Auto-fixes from nightly audit {today}. "
        f"INFO-severity only — additive logging, no logic changes.\n\n"
        f"**Fixed**\n{fix_bullets}\n\n"
        f"**Skipped**\n{skip_bullets}\n\n"
        f"CRITICAL and WARNING findings left for human review."
    )
    result = gh([
        "pr", "create",
        "--title", f"fix(audit): auto-fix INFO findings {today}",
        "--body", pr_body,
    ])
    print(f"\nPR created: {result.stdout.strip()}")


if __name__ == "__main__":
    main()
