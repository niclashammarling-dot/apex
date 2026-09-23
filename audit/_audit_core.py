"""
Shared state and helpers for APEX mechanical checks.

Imported by all domain check modules. findings/triggered are module-level
globals — all domain modules run in the same process and share this state.
"""
from datetime import date, timedelta
from pathlib import Path
import subprocess

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


#
# ── Trailing-window anchors: the convention ───────────────────────────────────
#
# THE RULE. A trailing-window check's end anchor must be justified by when its
# own data source becomes due — never inherited from a sibling check because
# the code looked similar. Two checks that read different producers on
# different schedules need different anchors even when both say "trailing 3".
#
# Why this is written here rather than in one check's docstring (2026-09-23):
# CHECKs 77 and 78 shipped in the same week with the identical anchor,
# `_most_recent_trading_day(today - 1)`. For 77 that is correct and its
# docstring says why — `eod_regime` writes a session's posterior at 08:30 ET
# the *next* morning, so today's row genuinely is not due when the audit runs.
# CHECK 78 copied the anchor without checking its own producer: `collect_pcr`
# runs at 16:30 ET the *same* day, four minutes before the audit's 16:33 ET
# slot. So 78 excluded a session that was already due and could not report a
# failed collection on the night it failed — it first fired the following
# night. Nothing was wrong with the arithmetic; the window was exactly three
# sessions. The justification was borrowed, and no docstring anywhere declared
# which convention a new check should follow, so the next author would copy
# whichever sibling they read first.
#
# Scope. This applies to windows over *sessions* keyed to a producer's
# schedule. CHECKs 81 and 83 use plain calendar windows (`today - N days`)
# over row timestamps and include today — that is a different question (how
# far back do I read rows that already exist) and is not governed by this
# rule. Do not "align" them to it.
#
def last_due_session(due_et: str, due_offset_sessions: int = 0, now_et=None) -> date | None:
    """
    Most recent NYSE session whose data is actually due by now.

    due_et               "HHMM" ET, the producer's own slot.
    due_offset_sessions  0 = session S is due at due_et on S itself
                         (CHECK 78: collect_pcr, 16:30 ET same day;
                          CHECK 82: the window's own close, 16:40 ET).
                         1 = S is due at due_et on the session *after* S
                         (CHECK 77: eod_regime, 08:30 ET next morning FOR the
                          previous session).

    Returns None if no session in the lookback is due yet (fresh series, or a
    long holiday run) — callers should treat that as "nothing to judge".

    now_et  injectable for tests and for replaying a past run; defaults to now.

    Half-day closes are not modelled: every current producer's slot is after
    the 13:00 ET early close as well as the 16:00 ET regular one, so the
    distinction does not bite. It would for a producer slotted intraday.
    """
    from datetime import datetime, timedelta as _td
    from zoneinfo import ZoneInfo
    import pandas_market_calendars as mcal

    now_et = now_et or datetime.now(ZoneInfo("America/New_York"))
    sched  = mcal.get_calendar("NYSE").schedule(
        start_date=now_et.date() - _td(days=40), end_date=now_et.date() + _td(days=10))
    sessions = list(sched.index.date)
    for i in range(len(sessions) - 1, -1, -1):
        j = i + due_offset_sessions
        if j >= len(sessions):
            continue                      # the session that would make S due hasn't happened
        due_day = sessions[j]
        if due_day < now_et.date() or (due_day == now_et.date()
                                       and now_et.strftime("%H%M") >= due_et):
            return sessions[i]
    return None


def run_tool(check_num: int, check_name: str, argv: list, *, ok_rc=(0,), timeout: int = 60,
             **kw):
    """
    Run an external tool for a check and refuse to read failure as "clean".

    Returns the CompletedProcess when the return code is in ok_rc; otherwise
    records a SKIPPED row ("<tool> did not run") and returns None so the caller
    can `return` without evaluating. A tool that failed to run produces the
    same empty stdout as a tool that ran and found nothing; every site that
    parsed stdout without looking at the return code rendered ✓ under both.

    Why this exists (2026-09-16/17): CHECK 45's ruff pass called `python3 -m
    ruff`, which resolved to the venv interpreter without ruff — rc 1, empty
    stdout, zero findings, green on every scheduled run for the life of the
    sub-check. The grep the next day found the same shape at eight more sites
    (git log/show/diff in CHECKs 9, 12, 32's dirty count, the retirement
    clock, host_commit). Third silent-skip class after file-absent
    (require_data_file) and zero-rows: tool-missing. Fixing one site leaves
    the next to be found the same way, so this is the class fix.

    ok_rc: return codes that mean "ran"; ruff uses 1 for "findings present",
    grep uses 1 for "no match" — pass (0, 1) for those.
    """
    kw.setdefault("cwd", REPO)
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    try:
        r = subprocess.run(argv, timeout=timeout, **kw)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        skipped.append((check_num, check_name, str(argv[0]),
                        f"{argv[0]} did not run: {e.__class__.__name__}: {e}"))
        return None
    if r.returncode not in ok_rc:
        err = (r.stderr or "").strip().splitlines()
        skipped.append((check_num, check_name, str(argv[0]),
                        f"{' '.join(map(str, argv[:3]))} exited {r.returncode}"
                        f"{' — ' + err[-1][:160] if err else ''}; nothing evaluated"))
        return None
    return r
