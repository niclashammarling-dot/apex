"""Lock labels phase 3 part c: no "Lock N" in code or docs outside history (Niclas 2026-10-31: "alla namn är
konsekvent efter kodschemat"). The module numbering ran in three generations (Lock 3 was Claude in one, Grok in the
next), so a line names its lock by the chain: L1 ELIGIBILITY … L6 REVIEW. What stays, and why:"""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK_N = re.compile(r"\bLock ?[1-5]\b|\bLOCK [1-5]\b|\bLock Leading\b")
HISTORY = (
    re.compile(r"^audit/nightly-report-.*\.md$"),               # dated nightly reports
    re.compile(r"^backend/backtest/.*_2026_\d\d.*\.py$"),       # dated archived producers
)
KEEP = {
    "audit/PARAMETERS.json": "provenance quotes the notes as written",
    "backend/backtest/SIGNAL_REGISTER.md": "S-rows are edited on master; renamed there after the 11-07 merge",
    "tests/test_audit_check54.py": "CHECK 54's positive control: a frontend label written with a lock number",
    "tests/test_weekly_render_stored.py": "a stored week's commentary, rendered as it was written",
    "tests/test_l6_review_prompt.py": "asserts the L6 prompt's text, which waits for the 11-07 review (P3-E)",
    "tests/test_lock_text.py": "this test",
}


def _prompt_lines() -> set[int]:
    lines = (ROOT / "backend/gate/l6_review.py").read_text().split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith("SYSTEM_PROMPT = "))
    end = next(i for i in range(start + 1, len(lines)) if '"""' in lines[i])
    return set(range(start + 1, end + 2))


def test_no_lock_number_outside_history_and_the_prompt():
    files = subprocess.run(["git", "ls-files", "*.py", "*.md", "*.js", "*.jsx", "*.json"], cwd=ROOT,
                           capture_output=True, text=True, check=True).stdout.split()
    assert "backend/gate/chain.py" in files                     # the listing read the repo
    prompt = _prompt_lines()
    hits = []
    for f in files:
        if f in KEEP or any(p.match(f) for p in HISTORY):
            continue
        for n, line in enumerate((ROOT / f).read_text(errors="ignore").split("\n"), 1):
            if not LOCK_N.search(line):
                continue
            if f == "backend/gate/l6_review.py" and (n in prompt or "while the prompt said Lock 1–4" in line):
                continue                                         # the prompt (P3-E) and a dated record of it
            hits.append(f"{f}:{n}: {line.strip()[:120]}")
    assert hits == []


def test_the_check_finds_a_lock_number(tmp_path):
    assert LOCK_N.search('logger.debug(f"Lock 2 [{ticker}] PASS")') and LOCK_N.search("| 12 | Lock3 context |")
    assert not LOCK_N.search("lock 4/1 % profit lock") and not LOCK_N.search("2026-09-17-apex-lock4-pcr-design")


def test_claude_reads_the_lock_results_under_the_names_and_order_it_read_before():
    # The module numbers retired (lock_results keyed by chain lock); what the L6 prompt reads did not change.
    from backend.gate.l6_review import _build_context_payload
    from backend.gate.types import LockResult
    results = {n: LockResult.pass_(lock_id=n, score=1.0, reason="ok", data={"x": n}) for n in (1, 3, 4, 5)}
    payload = _build_context_payload("AAPL", "Technology", results, {"k": 1})
    assert list(payload) == ["ticker", "sector", "lock_results", "k"]
    assert list(payload["lock_results"]) == ["l1_eligibility", "l3_score", "l4_sentiment", "l5_positioning"]
    assert [v["data"]["x"] for v in payload["lock_results"].values()] == [1, 3, 4, 5]
