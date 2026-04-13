#!/usr/bin/env python3
"""
APEX LLM Checks — CHECK 1, 2, 7, 8
Uses OpenAI GPT-4o for reasoning-heavy analysis.
Appends findings to the existing audit report written by mechanical_checks.py.
"""

import os
import sys
from datetime import date
from pathlib import Path

try:
    from openai import OpenAI
except ImportError:
    print("openai package not installed — skipping LLM checks")
    sys.exit(0)

REPO   = Path(__file__).parent.parent
TODAY  = date.today().isoformat()
REPORT = REPO / "audit" / f"nightly-report-{TODAY}.md"

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

SYSTEM_PROMPT = """You are a code auditor for APEX, a Python/FastAPI + React paper-trading system.
Analyze the code provided and return findings ONLY as a markdown table rows — no prose, no explanation.
Each finding is one row: | check_num check_name | ⚠ | SEV | file:line | one-line description |
Clean checks get one row: | check_num check_name | ✓ | — | — | — |
Sev values: CRITICAL / WARNING / INFO.
CRITICAL = incorrect trades or silent wrong state right now.
WARNING = fails under a plausible condition.
INFO = brittle pattern or missing logging, no current risk.
Be concise. No code quotes. File:line is mandatory for every non-clean row."""


def read(rel_path: str) -> str:
    p = REPO / rel_path
    return p.read_text(errors="ignore") if p.exists() else f"[file not found: {rel_path}]"


def ask(check_description: str, code: str) -> str:
    response = client.chat.completions.create(
        model="gpt-4o",
        temperature=0,
        max_tokens=600,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": f"{check_description}\n\n---\n{code}"},
        ],
    )
    return response.choices[0].message.content.strip()


def check1():
    context_doc = read("audit/check1_context.md")
    db = read("backend/db.py")
    inserts = "\n".join(
        line for line in db.splitlines()
        if any(k in line for k in ["insert_", "update_signal", "gate_decision", "outcome"])
    )
    code = (
        f"# DESIGN INTENT AND RESOLVED STATE\n{context_doc}\n\n"
        f"# gate_runner.py\n" + read("backend/gate/gate_runner.py")[-6000:] +
        f"\n\n# gate_runner_live.py\n" + read("backend/gate/gate_runner_live.py")[-3000:] +
        f"\n\n# db.py (insert functions)\n{inserts[:2000]}"
    )

    return ask(
        "CHECK 1 — Result-dict sync hazard: using the design intent document above, determine "
        "whether the current code contains the specific three-part hazard pattern described. "
        "The document shows the resolved state and what a real finding looks like. "
        "If the code matches the resolved pattern, return a clean row. "
        "Only flag if you observe all three conditions: dict built, outcome mutated post-construction, "
        "DB write reads the stale gate_decision.",
        code
    )


def check2():
    tests_dir = REPO / "tests"
    test_code = ""
    for f in tests_dir.rglob("*.py"):
        text = f.read_text(errors="ignore")
        if "side_effect" in text and "Exception" in text:
            test_code += f"\n# {f.name}\n" + text[:3000]

    if not test_code:
        return "| 2 Exception-catch | ✓ | — | — | no exception-mock tests found |"

    return ask(
        "CHECK 2 — Exception-catch coverage in tests: for every test using side_effect=Exception, "
        "verify it asserts BOTH the return value AND what was passed to the DB insert mock "
        "(e.g. insert_live_gate_result.call_args). Flag tests that only check the return value "
        "and skip the persistence call args — the bug hides in the persistence path.",
        test_code[:6000]
    )


def check7():
    demo = read("backend/gate/gate_runner.py")
    live = read("backend/gate/gate_runner_live.py")
    context_doc = read("audit/check7_context.md")
    code = (
        f"# DESIGN INTENT AND REASONING EXAMPLES\n{context_doc}\n\n"
        f"# gate_runner.py (demo)\n{demo[-5000:]}\n\n"
        f"# gate_runner_live.py\n{live[-5000:]}"
    )

    return ask(
        "CHECK 7 — Demo/live gate runner parity: compare the two runners using the design "
        "intent document above to reason about each divergence. The document gives examples "
        "of intentional differences and the principles behind them — use these to reason "
        "about any divergence you find, not just match against the list. Flag only divergences "
        "you cannot explain from the stated principles. For each finding, briefly state why "
        "the principle does not cover it.",
        code
    )


def check8():
    context_doc = read("audit/check8_context.md")
    files = {
        "gate_runner.py":      read("backend/gate/gate_runner.py")[-4000:],
        "gate_runner_live.py": read("backend/gate/gate_runner_live.py")[-4000:],
        "lock1_eligibility.py": read("backend/gate/lock1_eligibility.py"),
        "db.py":               read("backend/db.py")[-3000:],
    }
    code = (
        f"# ACCEPTED PATTERNS AND REASONING\n{context_doc}\n\n" +
        "\n\n".join(f"# {name}\n{text}" for name, text in files.items())
    )

    return ask(
        "CHECK 8 — General code health: using the accepted patterns document above as your "
        "calibration baseline, find: (a) bare except blocks missing logging — compare against "
        "the accepted patterns to judge severity; (b) TODO/FIXME/HACK comments; "
        "(c) functions returning inconsistent types where callers don't guard. "
        "Do not flag except blocks that match the accepted patterns unless the log level is wrong "
        "for the context (e.g. debug in a risk-enforcement path).",
        code[:9000]
    )


def append_to_report(results: list[tuple[str, str]]):
    if not REPORT.exists():
        print(f"Report not found: {REPORT} — mechanical_checks.py must run first")
        sys.exit(1)

    existing = REPORT.read_text()
    # Remove the placeholder note
    existing = existing.replace("*(LLM checks 1, 2, 7, 8 appended below by llm_checks.py)*\n", "")

    llm_rows = "\n".join(rows for _, rows in results)

    # Insert LLM rows into the table (before the Retirement Candidates section)
    if "## Retirement Candidates" in existing:
        existing = existing.replace(
            "## Retirement Candidates",
            llm_rows + "\n\n## Retirement Candidates"
        )
    else:
        existing = existing.rstrip() + "\n" + llm_rows + "\n"

    # Recount totals
    lines = existing.splitlines()
    n_crit = sum(1 for l in lines if "| CRITICAL |" in l or "| CRITICAL" in l)
    n_warn = sum(1 for l in lines if "| WARNING |" in l or "| WARNING" in l)
    n_info = sum(1 for l in lines if "| INFO |" in l or "| INFO" in l)
    total  = n_crit + n_warn + n_info

    # Update summary line
    import re
    existing = re.sub(
        r"\d+ issues: \d+ critical, \d+ warnings, \d+ info",
        f"{total} issues: {n_crit} critical, {n_warn} warnings, {n_info} info",
        existing
    )

    REPORT.write_text(existing)
    print(f"LLM checks appended. Updated totals: {total} issues ({n_crit}C/{n_warn}W/{n_info}I)")


if __name__ == "__main__":
    print("Running LLM checks via GPT-4o...")
    results = [
        ("check1", check1()),
        ("check2", check2()),
        ("check7", check7()),
        ("check8", check8()),
    ]
    append_to_report(results)
