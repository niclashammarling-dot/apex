"""
The function ratchet's FAIL lines are also written as ::error:: annotations under GitHub
Actions (2026-11-14): CI 326 failed the ratchet and its job log needed a token the session
does not have, while check-run annotations are readable without one.
"""
from tests.coverage.ratchet import report

RES = {"new_money": [("backend/x.py", "f")], "now_exercised": [("backend/scheduler.py", "run_weekly_research")],
       "gone": [], "edited_untested": [], "new_other": [("scripts/p.py", "main")]}


def test_fail_lines_are_annotated_under_actions():
    lines = report(RES, "abc123", annotate=True)
    errors = [l for l in lines if l.startswith("::error title=function ratchet::")]
    assert len(errors) == 2 and any("run_weekly_research" in l for l in errors)
    assert not any(l.startswith("::error") and "scripts/p.py" in l for l in lines)   # REPORT is never an error


def test_no_annotations_outside_actions():
    lines = report(RES, "abc123", annotate=False)
    assert not [l for l in lines if l.startswith("::")] and len(lines) == 3
