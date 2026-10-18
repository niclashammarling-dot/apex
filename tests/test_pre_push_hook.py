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


def test_hook_passes_on_a_green_tree_and_records_the_run(tmp_path):
    # 2026-10-18: each run appends one history line; the path is redirected so the test never
    # writes the production logs/ (feedback_apex_test_isolation)
    hist = tmp_path / "hist.log"
    env = dict(os.environ, APEX_PRE_PUSH_HISTORY=str(hist))
    for _ in range(2):
        r = subprocess.run([str(HOOK)], cwd=HOOK.parent, capture_output=True, text=True, timeout=120, env=env)
        assert r.returncode == 0, r.stderr
    lines = hist.read_text().splitlines()
    assert len(lines) == 2                              # appended, not overwritten
    assert all(" | rc=0 | " in l and "passed" in l for l in lines), lines


def test_a_failing_ratchet_is_recorded_as_blocked(tmp_path):
    # the induced failure: a python that exits 1 stands in for a red ratchet
    fake = tmp_path / "repo"
    (fake / ".venv" / "bin").mkdir(parents=True)
    py = fake / ".venv" / "bin" / "python"
    py.write_text("#!/bin/sh\necho '1 failed in 0.1s'\nexit 1\n")
    py.chmod(0o755)
    subprocess.run(["git", "init", "-q", str(fake)], check=True)
    hist = tmp_path / "hist.log"
    env = dict(os.environ, APEX_PRE_PUSH_HISTORY=str(hist))
    r = subprocess.run(["bash", str(HOOK)], cwd=fake, capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 1 and "push blocked" in r.stderr
    assert " | rc=1 | " in hist.read_text() and "1 failed" in hist.read_text()
