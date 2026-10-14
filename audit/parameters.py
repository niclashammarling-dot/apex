"""
Parameter provenance registry (2026-10-14).

Niclas 2026-10-14: every parameter and threshold has an origin of one of four kinds,
and "vi behöver ju ha motor och commit när ett värde räknas eller testas. Så att vi
kan härleda det på något sätt". On where it lives: "Ta en separat fil och gör ett
gemensamt register" — one registry for code constants and both JSON configs, kept
beside them, so live_config.json keeps its runtime-edited format (Promote writes it,
live_config.py:138) and none of its readers has to be ported.

Registry: audit/PARAMETERS.json, keyed
    <path>::<NAME>           module-level constant in backend/ or audit/
    <data/x_config.json>:<key>   numeric key in live_config.json / demo_config.json
Each entry records the value its provenance was written for. A value that differs from
the applied one is stale provenance: an edit or a Promote needs new provenance, it does
not inherit the old. tests/test_parameter_registry.py holds the schema, the value pin and
the ratchet on unregistered parameters.

Kinds: measured (sweep/backtest/replay: engine, commit, date, sample), derived (from
another registered key), structural (given from outside: source), chosen (a judgment:
allowed only as listed debt with a review date).
Tiers (ruled 2026-10-14): money = compared against or multiplying something in the
entry, size or exit chain, and any audit threshold whose action changes configuration;
ops = timing or freshness only.

Population, read without importing any module (AST literals, JSON): the same rule as
the 2026-10-14 joint inventory — module-level UPPER_CASE names bound to numbers or
containers of numbers.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REGISTRY = REPO / "audit" / "PARAMETERS.json"
CODE_ROOTS = ("backend", "audit")
CONFIGS = ("data/live_config.json", "data/demo_config.json")

KINDS = {
    "measured":   ("source", "engine", "commit", "date", "sample"),
    "derived":    ("source", "from"),
    "structural": ("source",),
    "chosen":     ("source", "review"),
}
TIERS = ("money", "ops")
_NAME = re.compile(r"_?[A-Z][A-Z0-9_]+")


def _numeric(v: ast.AST) -> bool:
    if isinstance(v, ast.Constant):
        return isinstance(v.value, (int, float)) and not isinstance(v.value, bool)
    if isinstance(v, ast.UnaryOp):
        return _numeric(v.operand)
    if isinstance(v, ast.BinOp):
        return _numeric(v.left) and _numeric(v.right)
    if isinstance(v, (ast.Tuple, ast.List, ast.Set)):
        return bool(v.elts) and all(_numeric(e) for e in v.elts)
    if isinstance(v, ast.Dict):
        return bool(v.values) and all(_numeric(e) for e in v.values)
    return False


def _literal(v: ast.AST):
    """Evaluate a numeric literal expression (BinOp included) without importing the module."""
    if isinstance(v, ast.BinOp):
        return eval(compile(ast.Expression(v), "<param>", "eval"), {"__builtins__": {}})   # numbers only (_numeric)
    value = ast.literal_eval(v)
    return sorted(value) if isinstance(value, (set, frozenset)) else value


def code_parameters(repo: Path = REPO) -> dict[str, object]:
    out: dict[str, object] = {}
    for root in CODE_ROOTS:
        for p in sorted((repo / root).rglob("*.py")):
            if "tests" in p.parts or "__pycache__" in p.parts:
                continue
            tree = ast.parse(p.read_text(errors="ignore"))
            for n in tree.body:
                if isinstance(n, ast.Assign) and len(n.targets) == 1:
                    tgt = n.targets[0]
                elif isinstance(n, ast.AnnAssign):
                    tgt = n.target
                else:
                    continue
                if isinstance(tgt, ast.Name) and _NAME.fullmatch(tgt.id) and n.value is not None \
                        and _numeric(n.value):
                    out[f"{p.relative_to(repo)}::{tgt.id}"] = _literal(n.value)
    return out


def config_parameters(repo: Path = REPO) -> dict[str, object]:
    out: dict[str, object] = {}
    for rel in CONFIGS:
        f = repo / rel
        if not f.exists():
            continue
        for k, v in json.loads(f.read_text()).items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out[f"{rel}:{k}"] = v
    return out


def applied(repo: Path = REPO) -> dict[str, object]:
    return {**code_parameters(repo), **config_parameters(repo)}


def load_registry(path: Path = REGISTRY) -> dict[str, dict]:
    return json.loads(path.read_text())["parameters"]


def problems(registry: dict[str, dict], values: dict[str, object]) -> list[str]:
    """Every way an entry fails: unknown key, stale value, bad kind/tier, missing field."""
    out = []
    for key, e in registry.items():
        if key not in values:
            out.append(f"{key}: not an applied parameter (renamed or removed?)")
            continue
        if e.get("value") != values[key]:
            out.append(f"{key}: provenance written for {e.get('value')!r}, applied value is "
                       f"{values[key]!r} — stale; record the new value's provenance")
        kind = e.get("kind")
        if kind not in KINDS:
            out.append(f"{key}: kind {kind!r} not one of {sorted(KINDS)}")
        else:
            missing = [f for f in KINDS[kind] if not e.get(f)]
            if missing:
                out.append(f"{key}: kind {kind} needs {missing}")
            if kind == "derived" and e.get("from") not in registry:
                out.append(f"{key}: derived from {e.get('from')!r}, which has no registry entry")
        if e.get("tier") not in TIERS:
            out.append(f"{key}: tier {e.get('tier')!r} not one of {list(TIERS)}")
    return out


def unregistered(registry: dict[str, dict], values: dict[str, object]) -> list[str]:
    return sorted(k for k in values if k not in registry)


if __name__ == "__main__":
    reg, vals = load_registry(), applied()
    for line in problems(reg, vals):
        print("PROBLEM", line)
    print(f"PARAMETERS: {len(vals)} applied, {len(reg)} registered, "
          f"{len(unregistered(reg, vals))} without provenance")
