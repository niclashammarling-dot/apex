"""
Shared state and helpers for APEX mechanical checks.

Imported by all domain check modules. findings/triggered are module-level
globals — all domain modules run in the same process and share this state.
"""
from datetime import date, timedelta
from pathlib import Path

REPO    = Path(__file__).parent.parent
TODAY   = date.today().isoformat()
REPORT  = REPO / "audit" / f"nightly-report-{TODAY}.md"

findings: list  = []   # (check_num, check_name, sev, file_line, finding)
triggered: set  = set()
skipped: list   = []   # (check_num, check_name, file_line, reason) — check did NOT evaluate
SKIPPED         = "SKIPPED"


def flag(check_num: int, check_name: str, sev: str, file_line: str, finding: str) -> None:
    findings.append((check_num, check_name, sev, file_line, finding))
    triggered.add(check_num)


def require_data_file(check_num: int, check_name: str, path: Path, hint: str = "") -> bool:
    """
    Gate a check on a runtime data file (apex.db, data/*.json markers, …).

    Returns True if the file is present. If absent, records a SKIPPED row —
    a third report state, distinct from ✓ and from any finding — and returns
    False so the caller can `return` without evaluating.

    Why this exists (2026-09-11): the nightly audit runs on ubuntu-latest from
    a fresh checkout where apex.db and every app-written data file are absent.
    Seventeen checks answered that with a bare `if not db.exists(): return`,
    which the report rendered as ✓ — 69 committed CI reports showed green rows
    for checks that never read anything. A SKIPPED row says "this check could
    not run" instead of "this check ran and found nothing"; those are different
    facts and must not share a label. SKIPPED is neither triggered (no
    retirement-clock reset) nor clean (no last_clean advance) in CHECKS.md.

    Use this at every runtime-data site — never hand-write the exists/return
    branch. A new check inherits the correct behaviour by calling this.
    """
    if path.exists():
        return True
    try:
        rel = str(path.relative_to(REPO))
    except ValueError:
        rel = str(path)
    reason = f"{rel} not present — check did not evaluate"
    if hint:
        reason += f" ({hint})"
    skipped.append((check_num, check_name, rel, reason))
    return False


def _most_recent_trading_day(ref: date | None = None) -> date:
    """Return the most recent Mon–Fri on or before ref (default: today).

    Used for staleness checks that run at ~4 AM before market open — a
    today-only check would always flag because today's data doesn't exist yet.
    """
    d = ref or date.today()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d
