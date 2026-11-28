"""No test hands the code a config it built by hand; every config comes from tests/book_cfg.py (2026-11-28).

Vault: raw/notes/2026-11/2026-11-28-apex-test-config-builder-from-tracked-files-scope.md. On 2026-11-28, 159 tests failed
at once when the money-path readers began to subscript (R150 b): each had handed the chain, a runner, L0 or the exit pass
a dict with only the keys it cared about, and the readers' .get() fallbacks had filled the rest. This walks tests/ and
fails on a dict literal holding a runtime config key (or a name, or a local helper, that yields one) that reaches a
config getter stub or a function that reads a config. book_cfg's live_cfg()/demo_cfg() are the form.
"""
import ast
import json
from pathlib import Path

from backend import demo_config, live_config
from tests.book_cfg import demo_cfg, live_cfg

ROOT = Path(__file__).resolve().parents[1]
KEYS = set(live_config._KEYS) | set(demo_config._KEYS)
GETTERS = ("get_live_config", "get_demo_config")
# Functions that read a config handed to them (their cfg argument, by position or keyword).
READERS = {"evaluate_chain", "build_base_context", "_maybe_ratchet_bracket_sl", "earnings_lock", "check_earnings",
           "check_etf"}


def _is_cfg_dict(node: ast.AST) -> bool:
    return isinstance(node, ast.Dict) and any(isinstance(k, ast.Constant) and k.value in KEYS for k in node.keys)


def offences(src: str, name: str = "<src>") -> list[str]:
    """Each place in src where a hand-built config reaches a getter stub or a config reader."""
    tree = ast.parse(src)
    bound = {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign) and _is_cfg_dict(n.value)
             for t in n.targets if isinstance(t, ast.Name)}
    helpers = {f.name for f in ast.walk(tree) if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
               and any(isinstance(r, ast.Return) and _is_cfg_dict(r.value) for r in ast.walk(f))}

    def hand_built(v: ast.AST) -> bool:
        if _is_cfg_dict(v) or (isinstance(v, ast.Name) and v.id in bound):
            return True
        if isinstance(v, ast.Call):
            f = v.func
            return (isinstance(f, ast.Name) and f.id in helpers) or (isinstance(f, ast.Attribute) and f.attr in helpers)
        return False

    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        strs = [a.value for a in n.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        values = []
        if any(g in s for s in strs for g in GETTERS):
            values += [kw.value for kw in n.keywords if kw.arg == "return_value"]
            values += [a.body for a in n.args if isinstance(a, ast.Lambda)]
        f = n.func
        fname = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
        if fname in READERS:
            values += list(n.args) + [kw.value for kw in n.keywords]
        out += [f"{name}:{v.lineno}: {ast.get_source_segment(src, v)[:90]}" for v in values if hand_built(v)]
    return out


def test_no_test_hands_the_code_a_hand_built_config():
    found = []
    for f in sorted((ROOT / "tests").glob("*.py")):
        if f.name != "test_book_cfg.py":
            found += offences(f.read_text(), str(f.relative_to(ROOT)))
    assert found == [], "build these through tests/book_cfg.py (live_cfg / demo_cfg):\n  " + "\n  ".join(found)


def test_the_detector_sees_each_form_and_passes_the_builder():
    planted = '''
CFG = {"max_hold_days": 25}
def _cfg():
    return {"profit_lock_trigger_pct": 0.04}
def t():
    patch("backend.live_config.get_live_config", return_value={"max_hold_days": 25})
    monkeypatch.setattr(wallet, "get_demo_config", lambda: {"stop_loss_pct": 0.05})
    patch("backend.live_config.get_live_config", return_value=CFG)
    _maybe_ratchet_bracket_sl(trade, peak, self._cfg(), broker)
    evaluate_chain("A", "B", 0.9, {}, {"l3_score_threshold_min": 0.6})
'''
    assert len(offences(planted)) == 5
    clean = '''
def t():
    patch("backend.live_config.get_live_config", return_value=live_cfg(max_hold_days=25))
    evaluate_chain("A", "B", 0.9, {}, live_cfg(l3_score_threshold_min=0.6))
    evaluate_chain("A", "B", 0.9, {"risk_limits": {}}, live_cfg())
'''
    assert offences(clean) == []


def test_each_book_builds_exactly_its_runtime_keys_from_its_file():
    assert sorted(live_cfg()) == sorted(live_config._KEYS)
    assert sorted(demo_cfg()) == sorted(demo_config._KEYS)
    tracked = json.loads((ROOT / "data" / "live_config.json").read_text())
    assert live_cfg()["stop_loss_pct"] == tracked["stop_loss_pct"]
    assert live_cfg({"stop_loss_pct": 1}, stop_loss_pct=2)["stop_loss_pct"] == 2      # overrides win
