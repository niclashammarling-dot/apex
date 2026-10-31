"""
No reader is left on the codes stored before lock labels phase 2 (scope note 2026-10-29, the reader-writer ledger:
"a test that no file outside the legacy readers names an old code"). From the 2026-10-31 migration every stored
code is the one lock_codes derives; a code-literal of the old vocabulary outside the legacy readers is a reader
that would count, filter or label nothing. Comments and docstrings may record history; string literals in code
and quoted strings in the frontend are what is read.
"""
from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

from backend.gate.lock_codes import LEGACY_CODES

ROOT = Path(__file__).resolve().parents[1]
# The readers whose job is the old vocabulary: the classifier and migration that re-code it, the migration script,
# the table that lists it, and the archived T7 producer whose frozen mapping reads it from the migration record.
LEGACY_READERS = {"backend/gate/history_classify.py", "backend/gate/history_migrate.py",
                  "scripts/migrate_gate_codes.py", "backend/gate/lock_codes.py",
                  "backend/backtest/rejected_candidates_2026_10.py"}
_CODE = re.compile(r"\b(" + "|".join(sorted(LEGACY_CODES, key=len, reverse=True)) + r")\b")


def _docstrings(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                ids.add(id(first.value))
    return ids


def legacy_literals(source: str, suffix: str) -> list[str]:
    """The legacy codes a file reads: Python string literals outside docstrings; in JSX/JS any occurrence outside
    comments (a bare object key was the old badge map's form)."""
    if suffix == ".py":
        tree = ast.parse(source)
        skip = _docstrings(tree)
        return sorted({m.group(1) for node in ast.walk(tree)
                       if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip
                       for m in _CODE.finditer(node.value)})
    lines = (re.sub(r"\{/\*.*?\*/\}", "", re.sub(r"(^|\s)//.*$", r"\1", ln)) for ln in source.splitlines())
    return sorted({m.group(1) for ln in lines for m in _CODE.finditer(ln)})


def _tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files", "*.py", "*.jsx", "*.js"], cwd=ROOT, capture_output=True, text=True)
    return [f for f in out.stdout.split() if not f.startswith("tests/") and "node_modules" not in f]


def test_no_reader_outside_the_legacy_readers_names_an_old_code():
    found = {f: legacy_literals((ROOT / f).read_text(), Path(f).suffix) for f in _tracked()}
    readers = {f for f, codes in found.items() if codes}
    assert readers <= LEGACY_READERS, {f: found[f] for f in readers - LEGACY_READERS}


def test_the_finder_sees_a_reader_and_not_history():
    # Positive control: an SQL literal, a dict key and a JSX label are readers; a comment and a docstring are not.
    py = ('"""Until 2026-10-31 FILTERED_L3 held the guard."""\n'
          '# FILTERED_L2 was sentiment\n'
          'q = "SELECT 1 WHERE gate_decision IN (\'FILTERED_L2\', \'TRADE_REJECTED\')"\n'
          'm = {"SKIPPED_OPEN": 1}\n')
    assert legacy_literals(py, ".py") == ["FILTERED_L2", "SKIPPED_OPEN", "TRADE_REJECTED"]
    jsx = ('// FILTERED_L1 was the score\n'
           'const BADGE = { FILTERED_LEADING: "L5", x: "FILTERED_ETF_PENALTY" };\n')
    assert legacy_literals(jsx, ".jsx") == ["FILTERED_ETF_PENALTY", "FILTERED_LEADING"]
