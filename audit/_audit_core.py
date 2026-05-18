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


def flag(check_num: int, check_name: str, sev: str, file_line: str, finding: str) -> None:
    findings.append((check_num, check_name, sev, file_line, finding))
    triggered.add(check_num)


def _most_recent_trading_day(ref: date | None = None) -> date:
    """Return the most recent Mon–Fri on or before ref (default: today).

    Used for staleness checks that run at ~4 AM before market open — a
    today-only check would always flag because today's data doesn't exist yet.
    """
    d = ref or date.today()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d
