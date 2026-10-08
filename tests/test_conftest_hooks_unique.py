"""
Every conftest.py defines each top-level function once.

Why this file exists (2026-10-08). A second `def pytest_configure` added at
the bottom of tests/conftest.py silently replaced the first, so the DB/data
redirect never ran, and four merge-window test runs wrote to and deleted from
production data/apex.db (restored from the 07:28 snapshot). `e01f792` added
`pytest_sessionstart`, which refuses the run when production paths are not
redirected. That guard can be switched off the same way: a second
`def pytest_sessionstart` further down the file replaces it without a word.
Python keeps the last definition of a name, and pytest only sees that one.

This reads the source with `ast`, so it needs nothing the hooks set up and
catches the shadowing before any hook would have to notice it. Scope is every
conftest.py under the repo (not venv/), every top-level `def`, not only
`pytest_*` names: a shadowed fixture is the same failure one level down.
"""
import ast
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKIP_DIRS = {"venv", ".venv", "node_modules", ".git"}


def _conftests():
    return sorted(p for p in REPO.rglob("conftest.py")
                  if not SKIP_DIRS.intersection(p.relative_to(REPO).parts))


def duplicate_defs(source: str) -> dict:
    """Top-level function names defined more than once, with their line numbers."""
    tree = ast.parse(source)
    defs = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    counts = Counter(n.name for n in defs)
    return {name: [n.lineno for n in defs if n.name == name]
            for name, c in counts.items() if c > 1}


def test_conftest_population_is_not_empty():
    # Positive control on the glob: the test below must have a file to read.
    paths = _conftests()
    assert REPO / "tests" / "conftest.py" in paths, paths


def test_no_conftest_redefines_a_function():
    found = {str(p.relative_to(REPO)): d for p in _conftests()
             if (d := duplicate_defs(p.read_text(encoding="utf-8")))}
    assert not found, f"conftest defines a function twice; the later one silently replaces the earlier: {found}"


def test_detector_catches_the_10_08_shape():
    src = "def pytest_configure(config):\n    pass\n\ndef x():\n    pass\n\ndef pytest_configure(config):\n    pass\n"
    assert duplicate_defs(src) == {"pytest_configure": [1, 7]}
