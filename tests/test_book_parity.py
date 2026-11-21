"""
M9, book parity (R067 a, 2026-11-14; Niclas: "Det finns en parity check mellan demo och live. Varför har inte båda
m4?"). The port of CHECK 7 (the LLM runner-parity check with no runner since 2026-06-23), mechanical and in CI.

Founding: three parity defects in one week, one class (a change made in one runner): the earnings window 1 against 2
days, L2_RISK_MAX_POSITIONS in demo only, and M4 in live only (built 10-24 in gate_runner_live with no ruling).

For each book, the rule identifiers its runner can write are read from the source: every function reachable from the
runner's run() through the modules that write rule states (the two runners, the wallet, L0), with each string literal
and each module-level string constant it names that is a rule's identifier (lock_codes.RULES emitted_as). The chain
(evaluate_chain) is one module both runners call, so it cannot differ and is not read. A rule written by one book
only must be in DIFFERENCES with its reason, and must be declared so in its Rule's `books`; an entry nothing needs
fails too, so the list stays true.
"""
import ast
from pathlib import Path

import pytest

from backend.gate import lock_codes

ROOT = Path(__file__).resolve().parents[1]
MODULES = {"gate_runner": "backend/gate/gate_runner.py", "gate_runner_live": "backend/gate/gate_runner_live.py",
           "wallet": "backend/wallet.py", "l0_portfolio": "backend/gate/l0_portfolio.py"}
ENTRIES = {"demo": ("gate_runner", "run"), "live": ("gate_runner_live", "run")}

# identifier -> (the book that writes it, why the other does not; dated, with its ruling)
DIFFERENCES: dict[str, tuple[str, str]] = {
    "blocklist": ("live", "LIVE_TICKER_BLOCKLIST: broker-integrity holds on the live account, not signal quality "
                          "(backend/config.py); demo has no broker"),
    "broker_error": ("live", "an Alpaca order refusal; demo's wallet fills in the database and has no broker"),
    "broker_unknown": ("live", "submit and readback both failed at Alpaca (CHECK 89); demo has no broker"),
    "max_positions": ("demo", "demo's wallet refuses at execution past l2_risk_max_positions_count (optional: skipped "
                              "on an overflow slot); live counts slots in the serial phase and records overflow. "
                              "Listed 'to read' in the M9 scope (2026-11-14); recorded here as found 2026-11-21"),
}


def _parse(mod):
    return ast.parse((ROOT / MODULES[mod]).read_text())


def _index():
    defs, consts, imports = {}, {}, {}
    for mod in MODULES:
        tree = _parse(mod)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defs[(mod, node.name)] = node
            elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                    and isinstance(node.value.value, str):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        consts[(mod, t.id)] = node.value.value
        for node in ast.walk(tree):                      # names imported from another module in the set
            if isinstance(node, ast.ImportFrom) and node.module:
                src = node.module.rsplit(".", 1)[-1]
                if src in MODULES:
                    for a in node.names:
                        imports[(mod, a.asname or a.name)] = (src, a.name)
    return defs, consts, imports


def _resolve(mod, name, defs, consts, imports):
    if (mod, name) in defs or (mod, name) in consts:
        return (mod, name)
    return imports.get((mod, name))


def written(book: str) -> set[str]:
    defs, consts, imports = _index()
    idents = {i for r in lock_codes.RULES for i in r.emitted_as}
    seen, todo, out = set(), [ENTRIES[book]], set()
    while todo:
        key = todo.pop()
        if key in seen or key not in defs:
            continue
        seen.add(key)
        mod = key[0]
        for node in ast.walk(defs[key]):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in idents:
                out.add(node.value)
            elif isinstance(node, ast.Name):
                r = _resolve(mod, node.id, defs, consts, imports)
                if r in consts and consts[r] in idents:
                    out.add(consts[r])
                elif r in defs:
                    todo.append(r)
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in MODULES:
                tgt = (node.value.id, node.attr)
                if tgt in consts and consts[tgt] in idents:
                    out.add(consts[tgt])
                elif tgt in defs:
                    todo.append(tgt)
    return out


def test_each_rule_written_by_one_book_only_is_a_written_difference():
    demo, live = written("demo"), written("live")
    one_sided = {i: "demo" for i in demo - live} | {i: "live" for i in live - demo}
    unlisted = {i: b for i, b in one_sided.items() if DIFFERENCES.get(i, (None,))[0] != b}
    assert not unlisted, f"written by one book only, with no entry in DIFFERENCES: {unlisted}"
    stale = sorted(set(DIFFERENCES) - set(one_sided))
    assert not stale, f"DIFFERENCES entries no longer one-sided (remove them): {stale}"


def test_the_register_declares_each_difference():
    for ident, (book, _why) in DIFFERENCES.items():
        assert lock_codes.rule_for(ident).books == (book,), ident


def test_both_books_write_m4():
    # The founding defect (R067 a): M4 in live only from 10-24 to 11-21.
    m4 = {"halt_m4_drawdown", "loss_cap", "halt_m4_expectancy"}
    assert m4 <= written("demo") and m4 <= written("live")


def test_a_rule_added_to_one_runner_only_is_caught(monkeypatch):
    # Positive control: the parent's shape (live calls l0_portfolio.rule_verdicts, demo does not) fails.
    real = Path.read_text

    def without_demo_m4(self, *a, **k):
        text = real(self, *a, **k)
        if self.name == "gate_runner.py" and self.parent.name == "gate":
            text = text.replace("l0_portfolio.rule_verdicts(l0)", "{}").replace("l0_portfolio.evaluate_demo(", "dict(")
        return text
    monkeypatch.setattr(Path, "read_text", without_demo_m4)
    demo, live = written("demo"), written("live")
    assert {"halt_m4_drawdown", "halt_m4_expectancy"} <= live - demo
    with pytest.raises(AssertionError, match="no entry in DIFFERENCES"):
        test_each_rule_written_by_one_book_only_is_a_written_difference()
