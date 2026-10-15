"""
The pre-push hook (2026-10-15, after CI run #139): runs the parameter-registry ratchet
before a push. Its source is tracked in scripts/git-hooks/pre-push; .git/hooks/pre-push
is a symlink on the host (untracked, like post-commit). The failing path was induced
once by hand at install (a probe constant blocked a dry-run push, named in the output);
this test pins that the tracked script runs the ratchet and passes on a green tree.
"""
import os
import subprocess
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "scripts" / "git-hooks" / "pre-push"


def test_hook_is_executable_and_runs_the_ratchet():
    assert os.access(HOOK, os.X_OK)
    assert "tests/test_parameter_registry.py" in HOOK.read_text()


def test_hook_passes_on_a_green_tree():
    r = subprocess.run([str(HOOK)], cwd=HOOK.parent, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
