"""audit/question_map.py and audit/QUESTIONS.md: the pyramid is derived from the question map
(Niclas 2026-10-21). The real map must resolve against the real registers, every S-row and GAP row
must answer a question, and an unreadable register must raise, never roll up to zeros."""
from datetime import date

import pytest

from audit import question_map as qm

MAP = """
## Top

| id | question | answers | threshold | answered by |
|---|---|---|---|---|
| O3 | top? | — | — | G1, G2 |
| G1 | edge? | O3 | set | Q-A, M1 |
| G2 | ops? | O3 | not set | Q-B |

## Layers

| id | question | answers | answered by |
|---|---|---|---|
| Q-A | signals? | G1 | S1, S2, GAP-1 |
| Q-B | runs? | G2 | CHECK-*, PARAM-ops |

## Missing rows

| id | missing | needed by | where it would live |
|---|---|---|---|
| M1 | a verdict register | G1 | vault |
"""


def _derive(rows, params=None):
    return qm.derive(qm.parse_map(MAP), rows, ["CHECK-1", "CHECK-2"], params or {})


def test_rollup_answered_partial_open_and_missing():
    rows = {"S1": "adopted", "S2": "discarded", "GAP-1": "yes", "CHECK-1": "clean", "CHECK-2": "clean"}
    o = _derive(rows)["questions"]
    assert o["Q-A"]["state"] == "answered" and o["Q-B"]["state"] == "answered"
    assert o["G1"]["state"] == "partial" and o["G1"]["missing"] == ["M1"]      # a missing row caps it
    assert o["G2"]["scored"] is False and o["G1"]["scored"] is True
    rows.update(S1="open", S2="open", **{"GAP-1": "no"})
    o = _derive(rows)["questions"]
    assert o["Q-A"]["state"] == "open" and o["Q-A"]["counts"] == {"S:open": 2, "GAP:no": 1}


def test_chosen_parameter_past_review_is_open():
    params = qm.param_states({"a::X": {"kind": "chosen", "tier": "ops", "review": "2026-10-01"},
                              "a::Y": {"kind": "measured", "tier": "ops"}}, date(2026, 10, 21))
    rows = {"S1": "open", "S2": "open", "GAP-1": "no", "CHECK-1": "clean", "CHECK-2": "clean"}
    o = _derive(rows, params)["questions"]["Q-B"]
    assert o["state"] == "partial" and o["counts"]["PARAM:chosen, review passed"] == 1


def test_check_star_expands_to_checks_named_nowhere_else():
    m = qm.parse_map(MAP.replace("S1, S2, GAP-1", "S1, CHECK-2"))
    o = qm.derive(m, {"S1": "open", "CHECK-1": "clean", "CHECK-2": "warning"}, ["CHECK-1", "CHECK-2"], {})
    assert [r["ref"] for r in o["questions"]["Q-B"]["rows"]] == ["CHECK-1"]


def test_check_states_worst_severity_skipped_and_not_run():
    state = {"evaluated": [1, 2, 3], "skipped": [[4, "no db"]],
             "findings": [[2, "x", "INFO", "", ""], [3, "y", "WARNING", "", ""], [3, "y", "CRITICAL", "", ""]]}
    assert qm.check_states(state, [1, 2, 3, 4, 5]) == {
        "CHECK-1": "clean", "CHECK-2": "clean", "CHECK-3": "critical", "CHECK-4": "skipped", "CHECK-5": "not run"}


def test_unreadable_registers_raise_not_zero():
    for f in (qm.parse_map, qm.signal_states, qm.gap_states, qm.check_ids):
        with pytest.raises(qm.RegisterUnread):
            f("# nothing here\n")


def test_real_map_resolves_and_every_row_answers_a_question():
    out = qm.build(date(2026, 10, 21))
    # Only GAP rows may be unresolved on master: rows added on port-regime arrive with the merge (M6).
    assert all(r.startswith("GAP-") for r in out["unresolved"]), out["unresolved"]
    m = qm.parse_map(qm.MAP.read_text(encoding="utf-8"))
    refs = {r for q in m["questions"].values() for r in q["refs"]}
    s_rows = set(qm.signal_states(qm.SIGNALS.read_text(encoding="utf-8")))
    gap_rows = set(qm.gap_states(qm.GAPS.read_text(encoding="utf-8")))
    assert s_rows and s_rows <= refs, s_rows - refs                           # every S-row answers a question
    assert gap_rows <= refs, gap_rows - refs                                  # every GAP row answers a question
    q = out["questions"]
    for g in (f"G{i}" for i in range(1, 8)):                                  # every go group reaches a row
        stack, leaves = [g], 0
        while stack:
            x = q[stack.pop()]
            leaves += len(x["rows"])
            stack += x["children"]
        assert leaves, g
    assert set(m["missing"]) == {r for r in refs if r.startswith("M")}       # every M row is referenced, and only those


def test_endpoint_returns_the_derivation():
    from backend.routers.signals_router import get_pyramid
    out = get_pyramid()
    assert "O3" in out["questions"] and out["breaks"]
