"""
Function ratchet and change gate (tests/coverage/ratchet.py; vault scope 2026-11-02 § 3, tests (a)-(c) as scoped).
"""
from __future__ import annotations

import ast

from tests.coverage import ratchet as r

TRACKER = "backend/live_trades_tracker.py"
ENGINE = "backend/backtest/engine.py"
RULES = r.load_tiers()


def _funcs(path: str, source: str) -> dict:
    return {(path, q): n for q, n in r.functions_in(source).items()}


def test_a_deleting_the_test_of_a_money_function_fails_and_the_parent_passes():
    funcs = _funcs(TRACKER, "def ratchet_stop():\n    return 1\n")
    k = (TRACKER, "ratchet_stop")
    assert not r.failing(r.evaluate(funcs, {k}, {}, RULES))                  # parent: the test ran it
    res = r.evaluate(funcs, set(), {}, RULES)                                # its test deleted
    assert res["new_money"] == [k] and r.failing(res)


def test_b_editing_an_unexercised_baseline_function_fails_the_change_gate():
    head = _funcs(TRACKER, "def peak():\n    '''Peak.'''\n    return 2\n")
    k = (TRACKER, "peak")
    base = {TRACKER: "def peak():\n    '''Peak.'''\n    return 1\n"}
    res = r.evaluate(head, set(), {k: "money"}, RULES, base.get)
    assert res["edited_untested"] == [k] and r.failing(res)
    doc_only = {TRACKER: "def peak():\n    '''Old wording.'''\n    return 2\n"}
    assert not r.failing(r.evaluate(head, set(), {k: "money"}, RULES, doc_only.get))   # a docstring edit passes


def test_c_a_new_untested_function_fails_on_the_money_path_and_is_reported_in_backtest():
    money = r.evaluate(_funcs(TRACKER, "def new_rule():\n    pass\n"), set(), {}, RULES)
    assert money["new_money"] == [(TRACKER, "new_rule")] and r.failing(money)
    research = r.evaluate(_funcs(ENGINE, "def new_rule():\n    pass\n"), set(), {}, RULES)
    assert research["new_other"] == [(ENGINE, "new_rule")] and not r.failing(research)


def test_the_baseline_only_shrinks():
    funcs = _funcs(TRACKER, "def a():\n    pass\n")
    k_a, k_gone = (TRACKER, "a"), (TRACKER, "removed")
    res = r.evaluate(funcs, {k_a}, {k_a: "money", k_gone: "money"}, RULES)
    assert res["now_exercised"] == [k_a] and res["gone"] == [k_gone] and r.failing(res)
    assert not r.failing(r.evaluate(funcs, set(), {k_a: "money"}, RULES))   # still unexercised, unchanged: passes


def test_qualnames_are_spelled_as_the_recorder_sees_them():
    src = ("class C:\n    def m(self):\n        def inner():\n            pass\n        return inner\n"
           "def outer():\n    class K:\n        def km(self):\n            pass\n    return K\n"
           "def m():\n    pass\n")
    seen = set()

    def collect(code):
        seen.add(code.co_qualname)
        for c in code.co_consts:
            if hasattr(c, "co_qualname"):
                collect(c)

    collect(compile(src, "x.py", "exec"))
    funcs = set(r.functions_in(src))
    assert funcs == {"C.m", "C.m.<locals>.inner", "outer", "outer.<locals>.K.km", "m"}
    assert funcs <= seen                                     # every def is keyed as its code object names it
    assert seen - funcs == {"<module>", "C", "outer.<locals>.K"}   # module and class bodies: recorded, never defs


def test_tiers_fail_closed():
    assert r.tier_of((TRACKER, "x"), RULES) == "money"
    assert r.tier_of((ENGINE, "x"), RULES) == "other"
    assert r.tier_of(("scripts/b3_cap_probe.py", "x"), RULES) == "other"
    assert r.tier_of(("backend/a_module_nobody_named.py", "x"), RULES) == "money"


def test_body_hash_ignores_positions():
    a = ast.parse("def f():\n    return 1\n").body[0]
    b = ast.parse("\n\n\ndef f():\n    return 1\n").body[0]
    assert r.body_hash(a) == r.body_hash(b)


def test_a_nested_pytest_session_does_not_write_over_the_outer_recording(tmp_path):
    """Found 2026-11-07: a test that runs pytest in a subprocess inherited APEX_CALLMON_OUT and overwrote the outer
    run's file with its own 93 code objects. Only the session that set APEX_CALLMON_OWNER writes."""
    import os
    import subprocess
    import sys
    from pathlib import Path
    root = Path(r.__file__).resolve().parents[2]
    (tmp_path / "test_x.py").write_text("def test_x():\n    assert True\n")
    out = tmp_path / "seen.json"
    env = {**os.environ, "APEX_CALLMON_OUT": str(out), "APEX_CALLMON_OWNER": "1", "PYTHONPATH": str(root)}
    plugin = f"import importlib.util as u; s=u.spec_from_file_location('m', r'{root / 'tests/coverage/callmon.py'}'); "
    code = plugin + "m=u.module_from_spec(s); s.loader.exec_module(m); import pytest; " \
                    f"raise SystemExit(pytest.main(['-q', '-p', 'no:cacheprovider', r'{tmp_path / 'test_x.py'}'], plugins=[m]))"
    subprocess.run([sys.executable, "-c", code], env=env, cwd=tmp_path, check=True, capture_output=True)
    assert not out.exists()                              # owner pid 1 is not this process: nothing written
    env["APEX_CALLMON_OWNER"] = ""
    del env["APEX_CALLMON_OWNER"]
    subprocess.run([sys.executable, "-c", code], env=env, cwd=tmp_path, check=True, capture_output=True)
    assert out.exists()                                  # the outermost session writes
