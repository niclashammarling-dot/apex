"""
The writer of Rule.since is forced (R070 a, Niclas 2026-11-14: "Skrivaren ska vara tvingad … Fas 4 flyttar åtta
regler, och det är där fältet annars glöms"). tests/fixtures/rule_placements.json holds each rule's placements; the
last one must equal the register, so a change to a rule's owner, source or split_from cannot land without a new
placement in the same commit, and that placement must carry a later since or a written no_stop_change.

`source` stands in for the place: phase 4's moves keep every owner (M2 earnings is L2's already, M4 (ii)'s caps
are L2's), so an owner-only snapshot would not see one of them. 22 of the 29 rules are written through a variable
(`lock_codes.put(states, ident, …)`), so their call site cannot be read statically (measured 2026-11-14).
"""
import json
import re
from dataclasses import replace
from pathlib import Path

from backend.gate import lock_codes

SNAPSHOT = Path(__file__).parent / "fixtures" / "rule_placements.json"
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}$")
_COUPLING = ("owner", "source", "split_from")


def _placement(r) -> dict:
    return {"owner": r.owner, "source": r.source, "since": r.since, "split_from": r.split_from}


def _placement_errors(rules, snapshot: dict) -> list[str]:
    errors = []
    current = {r.name: r for r in rules}
    for name, placements in snapshot.items():
        last = placements[-1]
        if name not in current:
            if not _DATE.match(str(last.get("retired", ""))):
                errors.append(f"{name}: left RULES without a retired placement")
            continue
        if {k: last.get(k) for k in ("owner", "source", "since", "split_from")} != _placement(current[name]):
            errors.append(f"{name}: RULES differs from its last placement; append one with a later since or a "
                          f"no_stop_change")
        for a, b in zip(placements, placements[1:]):
            if "retired" in b:
                continue
            if any(a.get(k) != b.get(k) for k in _COUPLING) and not (b["since"] > a["since"] or b.get("no_stop_change")):
                errors.append(f"{name}: {', '.join(k for k in _COUPLING if a.get(k) != b.get(k))} changed without "
                              f"a later since or a no_stop_change")
        if len(placements) == 1 and not last.get("initial") and not (last["since"] or last.get("no_stop_change")):
            errors.append(f"{name}: a new rule adds stops, so it needs a since")
    for name in current.keys() - snapshot.keys():
        errors.append(f"{name}: in RULES with no placement")
    for r in rules:
        if r.since and not _DATE.match(r.since):
            errors.append(f"{r.name}: since {r.since!r} is not a date")
        if r.split_from:
            parent = current.get(r.split_from) or (snapshot.get(r.split_from) or [{}])[0]
            owner = parent.owner if hasattr(parent, "owner") else parent.get("owner")
            if owner != r.owner:
                errors.append(f"{r.name}: split_from {r.split_from!r} is not a rule of the same lock")
            if not r.since:
                errors.append(f"{r.name}: a split needs its since")
    return errors


def _snapshot() -> dict:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))["rules"]


def test_the_register_matches_its_placements():
    assert _placement_errors(lock_codes.RULES, _snapshot()) == []


def test_a_rule_moved_to_another_lock_without_since_fails():
    moved = tuple(replace(r, owner=r.owner + 1) if r.name == "sector_cap" else r for r in lock_codes.RULES)
    snap = _snapshot()
    last = snap["sector_cap"][-1]
    snap["sector_cap"] = snap["sector_cap"] + [{**last, "owner": last["owner"] + 1, "initial": False}]
    assert [e for e in _placement_errors(moved, snap) if e.startswith("sector_cap")] == \
        ["sector_cap: owner changed without a later since or a no_stop_change"]


def test_a_place_change_in_the_register_alone_fails():
    # Phase 4's shape: the owner stays, the place (source) changes, and nobody touches the snapshot.
    moved = tuple(replace(r, source="L2 RISK before L4") if r.name == "sector_cap" else r for r in lock_codes.RULES)
    assert any(e.startswith("sector_cap: RULES differs") for e in _placement_errors(moved, _snapshot()))


def test_a_later_since_or_a_written_reason_passes():
    snap = _snapshot()
    last = snap["sector_cap"][-1]
    dated = tuple(replace(r, source="L2 RISK before L4", since="2026-11-30") if r.name == "sector_cap" else r
                  for r in lock_codes.RULES)
    snap["sector_cap"] = [last, {**last, "source": "L2 RISK before L4", "since": "2026-11-30", "initial": False}]
    assert _placement_errors(dated, snap) == []
    worded = tuple(replace(r, source="reworded") if r.name == "sector_cap" else r for r in lock_codes.RULES)
    snap["sector_cap"] = [last, {**last, "source": "reworded", "no_stop_change": "wording only", "initial": False}]
    assert _placement_errors(worded, snap) == []


def test_a_new_rule_needs_a_since_and_a_split_needs_its_parent_in_the_lock():
    snap = _snapshot()
    added = lock_codes.RULES + (lock_codes.Rule(2, "new_cap"),)
    snap["new_cap"] = [{"owner": 2, "source": "", "since": "", "split_from": ""}]
    assert "new_cap: a new rule adds stops, so it needs a since" in _placement_errors(added, snap)
    stray = tuple(replace(r, split_from="sector_cap") if r.name == "exit_cooloff_win" else r for r in lock_codes.RULES)
    assert any("not a rule of the same lock" in e for e in _placement_errors(stray, _snapshot()))


def test_a_retired_rule_carries_its_date():
    snap = _snapshot()
    kept = tuple(r for r in lock_codes.RULES if r.name != "drawdown_guard")
    assert "drawdown_guard: left RULES without a retired placement" in _placement_errors(kept, snap)
    snap["drawdown_guard"] = snap["drawdown_guard"] + [{"retired": "2026-12-05"}]
    assert _placement_errors(kept, snap) == []
