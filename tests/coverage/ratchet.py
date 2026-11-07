"""
Function ratchet and change gate (2026-11-07; vault raw/notes/2026-11/2026-11-02-apex-saturday-scopes-11-07.md § 3,
the every-object-tested rule, Niclas 2026-10-14: no rule, function or object exists untested).

Found 2026-11-05: two live stop-ratchet inputs (db.update_live_trade_peak_price, live_trades_tracker._current_price)
lost their test between 10-14 and 11-05 and nothing noticed. This makes the next one fail CI the day it happens.

Inputs: the functions defined in backend/, audit/ and scripts/ (AST, keyed file::qualname as co_qualname spells it),
the set the suite started (tests/coverage/callmon.py, APEX_CALLMON_OUT), the baseline of known-unexercised functions
(tests/coverage/unexercised.txt) and the tier patterns (tests/coverage/money_path.txt; unmatched is `money`, so a
new module fails closed).

check fails when
  - a `money` function is unexercised and not in the baseline (a new untested function, or a regression);
  - a `money` baseline function is exercised now, or no longer exists (the file only shrinks: drop the line);
  - change gate: a `money` baseline function's body differs from the base revision (AST, docstring and line
    numbers ignored) while it is still unexercised (an edit with no test).
`other` is reported, never failed.

CLI:  python -m tests.coverage.ratchet baseline --seen SEEN.json        (writes unexercised.txt)
      python -m tests.coverage.ratchet check --seen SEEN.json [--base REV]
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BASELINE = HERE / "unexercised.txt"
TIERS = HERE / "money_path.txt"
SCOPE = ("backend/", "audit/", "scripts/")
Key = tuple[str, str]


def functions_in(source: str) -> dict[str, ast.AST]:
    """qualname → def node, spelled as co_qualname (Class.method, outer.<locals>.inner)."""
    out: dict[str, ast.AST] = {}

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, prefix + [child.name])
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                q = ".".join(prefix + [child.name])
                out[q] = child
                walk(child, prefix + [child.name, "<locals>"])
            else:
                walk(child, prefix)

    walk(ast.parse(source), [])
    return out


def defined(root: Path = ROOT) -> dict[Key, ast.AST]:
    files = subprocess.run(["git", "-C", str(root), "ls-files", "*.py"], capture_output=True, text=True).stdout.split()
    out = {}
    for f in files:
        if f.startswith(SCOPE):
            for q, node in functions_in((root / f).read_text()).items():
                out[(f, q)] = node
    return out


def load_tiers(path: Path = TIERS) -> list[tuple[str, str]]:
    rules = []
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            tier, pattern = line.split(None, 1)
            assert tier in ("money", "other"), line
            rules.append((tier, pattern.strip()))
    return rules


def tier_of(key: Key, rules: list[tuple[str, str]]) -> str:
    for tier, pattern in rules:
        if fnmatch.fnmatch(key[0], pattern):
            return tier
    return "money"   # fail closed: a module no pattern names is on the money path


def load_baseline(path: Path = BASELINE) -> dict[Key, str]:
    out = {}
    for line in path.read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            name, tier = line.split("\t")
            f, q = name.split("::", 1)
            out[(f, q)] = tier
    return out


def body_hash(node: ast.AST) -> str:
    node = ast.parse(ast.unparse(node)).body[0]          # a copy, positions dropped
    body = node.body
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
            and isinstance(body[0].value.value, str):
        node.body = body[1:] or [ast.Pass()]              # a docstring edit is not a code change
    return hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()


def source_at(rev: str, path: str, root: Path = ROOT) -> str | None:
    r = subprocess.run(["git", "-C", str(root), "show", f"{rev}:{path}"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def evaluate(funcs: dict[Key, ast.AST], seen: set[Key], baseline: dict[Key, str], rules,
             base_source=None) -> dict[str, list]:
    """The check's verdicts. base_source(path) returns the file at the base revision (None: no change gate)."""
    unexercised = {k for k in funcs if k not in seen}
    res = {"new_money": [], "new_other": [], "now_exercised": [], "gone": [], "edited_untested": []}
    for k in sorted(unexercised - set(baseline)):
        res["new_money" if tier_of(k, rules) == "money" else "new_other"].append(k)
    for k, tier in sorted(baseline.items()):
        if tier != "money":
            continue
        if k not in funcs:
            res["gone"].append(k)
        elif k not in unexercised:
            res["now_exercised"].append(k)
    if base_source is not None:
        cache: dict[str, dict | None] = {}
        for k, tier in sorted(baseline.items()):
            if tier != "money" or k not in funcs or k not in unexercised:
                continue
            if k[0] not in cache:
                src = base_source(k[0])
                cache[k[0]] = functions_in(src) if src is not None else None
            old = (cache[k[0]] or {}).get(k[1])
            if old is not None and body_hash(old) != body_hash(funcs[k]):
                res["edited_untested"].append(k)
    return res


def failing(res: dict[str, list]) -> bool:
    return bool(res["new_money"] or res["now_exercised"] or res["gone"] or res["edited_untested"])


def _fmt(k: Key) -> str:
    return f"{k[0]}::{k[1]}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("baseline", "check"))
    ap.add_argument("--seen", required=True)
    ap.add_argument("--base")
    a = ap.parse_args(argv)
    seen = {tuple(x) for x in json.loads(Path(a.seen).read_text())}
    funcs, rules = defined(), load_tiers()
    if not seen:
        print("RATCHET: the recorder saw nothing — the suite did not run under tests/coverage/callmon.py")
        return 2
    if a.cmd == "baseline":
        rows = sorted((k, tier_of(k, rules)) for k in funcs if k not in seen)
        BASELINE.write_text("# Known-unexercised functions (tests/coverage/ratchet.py). Only shrinks: test one, drop its line.\n"
                            + "".join(f"{_fmt(k)}\t{t}\n" for k, t in rows))
        n_money = sum(1 for _, t in rows if t == "money")
        print(f"baseline: {len(rows)} unexercised of {len(funcs)} ({n_money} money)")
        return 0
    res = evaluate(funcs, seen, load_baseline(), rules,
                   (lambda p: source_at(a.base, p)) if a.base else None)
    labels = {"new_money": "FAIL new or regressed untested money-path function",
              "now_exercised": "FAIL exercised now: drop it from tests/coverage/unexercised.txt",
              "gone": "FAIL no longer defined: drop it from tests/coverage/unexercised.txt",
              "edited_untested": f"FAIL edited since {a.base} with no test (change gate)",
              "new_other": "REPORT untested, tier other"}
    for kind, label in labels.items():
        for k in res[kind]:
            print(f"{label}: {_fmt(k)}")
    print(f"RATCHET: {len(funcs)} functions, {len(funcs) - len([k for k in funcs if k not in seen])} exercised; "
          + ", ".join(f"{k} {len(v)}" for k, v in res.items()))
    return 1 if failing(res) else 0


if __name__ == "__main__":
    sys.exit(main())
