"""
The pre-push hook (2026-10-15, after CI run #139): runs the parameter-registry ratchet
before a push. Its source is tracked in scripts/git-hooks/pre-push; .git/hooks/pre-push
is a symlink on the host (untracked, like post-commit). The failing path was induced
once by hand at install (a probe constant blocked a dry-run push, named in the output);
this test pins that the tracked script runs the ratchet and passes on a green tree.
"""
import json
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
        r = subprocess.run([str(HOOK)], cwd=HOOK.parent, capture_output=True, text=True, timeout=120, env=env,
                           input="")
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
    r = subprocess.run(["bash", str(HOOK)], cwd=fake, capture_output=True, text=True, timeout=60, env=env, input="")
    assert r.returncode == 1 and "push blocked" in r.stderr
    assert " | rc=1 | " in hist.read_text() and "1 failed" in hist.read_text()



# The CI guard (2026-11-07, after CI runs 291-294 stacked four commits on a red master).
RED, GREEN, RUNNING = "a" * 40, "b" * 40, "c" * 40


def _guard(tmp_path, tip: str, **extra):
    runs = tmp_path / "runs"
    runs.mkdir(exist_ok=True)
    for sha, concl, status, n in ((RED, "failure", "completed", 290), (GREEN, "success", "completed", 288),
                                  (RUNNING, None, "in_progress", 297)):
        (runs / f"{sha}.json").write_text(json.dumps({"workflow_runs": [
            {"name": "APEX Tests", "run_number": n, "conclusion": concl, "status": status},
            {"name": "other workflow", "run_number": n + 1, "conclusion": "success", "status": "completed"}]}))
    hist = tmp_path / "hist.log"
    env = dict(os.environ, APEX_PRE_PUSH_HISTORY=str(hist), APEX_CI_API=str(runs), **extra)
    env.pop("APEX_CI_RED_OK", None) if "APEX_CI_RED_OK" not in extra else None
    r = subprocess.run([str(HOOK)], cwd=HOOK.parent, capture_output=True, text=True, timeout=120, env=env,
                       input=f"refs/heads/master {'d' * 40} refs/heads/master {tip}\n")
    return r, hist.read_text() if hist.exists() else ""


def test_a_push_on_top_of_a_red_tip_is_blocked_and_named(tmp_path):
    r, hist = _guard(tmp_path, RED)
    assert r.returncode == 1 and "CI run 290" in r.stderr and "push blocked" in r.stderr
    assert "APEX_CI_RED_OK=290" in r.stderr and "CI guard: blocked on red run 290" in hist


def test_a_fix_on_top_of_red_passes_when_it_names_the_run(tmp_path):
    r, hist = _guard(tmp_path, RED, APEX_CI_RED_OK="290")
    assert r.returncode == 0, r.stderr
    assert "pushed on red run 290 (acknowledged)" in hist
    r, _ = _guard(tmp_path, RED, APEX_CI_RED_OK="289")           # naming another run does not unlock it
    assert r.returncode == 1


def test_a_green_tip_passes_silently_and_a_running_one_warns(tmp_path):
    r, _ = _guard(tmp_path, GREEN)
    assert r.returncode == 0 and "CI" not in r.stderr
    r, _ = _guard(tmp_path, RUNNING)
    assert r.returncode == 0 and "still running" in r.stderr


def test_an_unknown_ci_state_warns_and_does_not_block(tmp_path):
    r, _ = _guard(tmp_path, "e" * 40)                           # no fixture: the API answered nothing
    assert r.returncode == 0 and "CI state unknown" in r.stderr


def test_a_new_branch_has_no_remote_tip_to_read(tmp_path):
    r, _ = _guard(tmp_path, "0" * 40)
    assert r.returncode == 0 and "CI" not in r.stderr
