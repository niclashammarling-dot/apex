"""
Interpreter guard (R143 a, 2026-11-24): tests/conftest.py refuses a run, before collection, when the interpreter lacks a
runtime package from requirements.txt. Founding cases: 10-23 (CHECK 65's holiday test) and 11-24 (111 failures in 23
files under /usr/bin/python3, all from the missing pandas-market-calendars). The induced firing below goes through the
real launcher (python -m pytest from the repo root); the guard's file override is the only way to make a complete
interpreter look incomplete in CI.
"""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("apex_conftest_guard", REPO / "tests" / "conftest.py")
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def test_requirements_split_into_missing_runtime_and_missing_dev(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text("# comment\n\nfastapi>=0.115.0\nuvicorn[standard]>=0.30.0  # extras\npandas_market_calendars>=4\n"
                   "-r other.txt\n--index-url https://example.invalid\nPyYAML>=6.0\npytest-mock>=3.15.0\nRuff\n"
                   "pytest>=9.0.0\n")
    have = {"fastapi", "uvicorn", "pyyaml", "pytest"}
    runtime, dev = guard.missing_requirements(req, installed=lambda n: guard._canon(n) in have)
    assert runtime == ["pandas_market_calendars"]                     # the package behind both founding cases
    assert dev == ["pytest-mock", "Ruff"]                             # named, never refused
    assert guard.missing_requirements(req, installed=lambda n: True) == ([], [])


def test_every_dev_tool_is_in_requirements_and_every_line_parses():
    """Parity: a dev-tool name that left requirements.txt would exempt nothing and hide the drift."""
    lines = [l.split("#", 1)[0].strip() for l in (REPO / "requirements.txt").read_text().splitlines()]
    lines = [l for l in lines if l and not l.startswith("-")]
    seen = []
    guard.missing_requirements(REPO / "requirements.txt", installed=lambda n: seen.append(n) or True)
    assert len(seen) == len(lines) > 0
    assert guard._DEV_TOOLS <= {guard._canon(n) for n in seen}


def _pytest(env_extra: dict) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("APEX_CALLMON_OUT", "APEX_CALLMON_OWNER",
                                                            "APEX_REQUIREMENTS_FILE")}
    env.update(env_extra)
    target = "tests/test_interpreter_guard.py::test_requirements_split_into_missing_runtime_and_missing_dev"
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", target], cwd=REPO,
                          env=env, capture_output=True, text=True, timeout=120, check=False)


def test_a_missing_runtime_package_refuses_the_run_before_collection(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text((REPO / "requirements.txt").read_text() + "apex-no-such-dist>=1.0\n")
    r = _pytest({"APEX_REQUIREMENTS_FILE": str(req)})
    assert r.returncode == 3, (r.stdout[-500:], r.stderr[-500:])
    assert "refused before collection (R143)" in r.stderr and "apex-no-such-dist" in r.stderr
    assert sys.executable in r.stderr and "venv/bin/python" in r.stderr
    assert "collected" not in r.stdout and "passed" not in r.stdout   # nothing ran
    # positive control: the same launch on the real file runs the test
    r = _pytest({})
    assert r.returncode == 0 and "1 passed" in r.stdout, (r.stdout[-500:], r.stderr[-500:])
