"""CHECK 45 — static code analysis: a planted finding of each sub-check fires (positive control, rule census
2026-11-07; until then the registry row had no control column, so its silence could not be told from a broken
check)."""
import importlib.util

import pytest


def _run45(tmp_path, monkeypatch, files: dict[str, str]):
    from audit import _audit_core as core
    from audit import checks_code as cc
    repo = tmp_path / "repo"
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    monkeypatch.setattr(core, "REPO", repo)
    monkeypatch.setattr(cc, "REPO", repo)
    monkeypatch.setattr(core, "findings", [])
    monkeypatch.setattr(core, "skipped", [])
    monkeypatch.setattr(cc, "flag", core.flag)
    cc.check45()
    return [(f[3], f[4]) for f in core.findings if f[0] == 45]


def test_a_leaking_connection_fires(tmp_path, monkeypatch):
    out = _run45(tmp_path, monkeypatch, {"backend/leaky.py": "def f():\n    with get_db() as conn:\n        pass\n"})
    assert any(loc == "backend/leaky.py:2" and "never closes" in msg for loc, msg in out), out


@pytest.mark.skipif(importlib.util.find_spec("ruff") is None, reason="ruff not installed in this environment "
                    "(requirements.txt pins it; CI installs it)")
def test_an_unused_import_fires_and_backtest_is_excluded(tmp_path, monkeypatch):
    out = _run45(tmp_path, monkeypatch, {"backend/unused.py": "import os\n",
                                         "backend/backtest/research.py": "import os\n"})
    assert any(loc == "backend/unused.py:1" and "F401" in msg for loc, msg in out), out
    assert not any("backtest" in loc for loc, _ in out)
