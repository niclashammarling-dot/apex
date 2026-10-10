"""Every audit CHECK names a control: a pytest node that makes it FIRE (2026-09-30).

"Tested" is an event, not a build-time property. A control written only to fail
against the parent proves the check worked the day it was built; living as a test,
CI re-runs it on every push, so a check that stops being able to fire is caught at
the next push. That is the "armed" state. It is not "evaluates the real population"
(the 09-16 fixture-vs-corpus lesson) — that is a separate column, not built yet.

Ratchet: the number of registry rows WITHOUT a control is pinned below. More → a new
check arrived without its control. Fewer → lower the baseline, so every improvement
locks in and the backlog cannot drift back up. The backlog is a list, not a blocker.
"""
import ast
from pathlib import Path

from audit.mechanical_checks import registry_cells

REPO = Path(__file__).resolve().parent.parent
UNCONTROLLED_BASELINE = 72


def _rows():
    lines = (REPO / "audit/CHECKS.md").read_text().splitlines()
    end = lines.index("## Pending Checks")
    return [registry_cells(l) for l in lines[:end]
            if l.startswith("| ") and registry_cells(l)[1].isdigit()]


def _node_exists(node: str) -> bool:
    path, *names = node.split("::")
    f = REPO / path
    if not f.is_file() or not names:
        return False
    scope = ast.parse(f.read_text()).body
    for name in names:
        match = next((n for n in scope if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name == name), None)
        if match is None:
            return False
        scope = getattr(match, "body", [])
    return names[-1].startswith("test")


def test_every_registry_row_has_the_control_column():
    bad = [r[1] for r in _rows() if len(r) != 10]
    assert not bad, f"registry rows without exactly 8 columns (a pipe in prose must be escaped as \\|): {bad}"


def test_named_controls_are_real_test_nodes():
    broken = [(r[1], r[8]) for r in _rows() if r[8] and not _node_exists(r[8])]
    assert not broken, f"control names a test that does not exist: {broken}"


def test_uncontrolled_count_is_pinned_both_ways():
    missing = [int(r[1]) for r in _rows() if not r[8]]
    n = len(missing)
    assert n <= UNCONTROLLED_BASELINE, (
        f"{n} checks without a control, baseline {UNCONTROLLED_BASELINE}: a new check needs a "
        f"test that makes it fire, named in its CHECKS.md control cell. Uncontrolled: {missing}")
    assert n >= UNCONTROLLED_BASELINE, (
        f"{n} checks without a control — lower UNCONTROLLED_BASELINE to {n} "
        f"in tests/test_check_registry_controls.py so the improvement locks in")
