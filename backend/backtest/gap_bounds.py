"""
Which production components a backtest result does NOT model (2026-10-17).

PRODUCTION_GAP.md is the list the engine work follows. Until now each result's
bound list was written by hand, and the 10-16 profit read's list named rows 9 and 10
but left out row 7 (macro blackout, "no" in engine_fast). Live blocks entries on
18 % of weekdays for that row, and every engine_fast run of 10-17 traded through
them. Every result now carries the open rows, read from the file at run time.

Open = status **no** or **partial**, plus the rows closed only under
`regime_layer=True` when the run has the flag off, plus **off-both** (2026-10-18):
rows where the engines match live only because live's rule is dead (a live defect),
so a result still says the rule is not modelled.

Since 2026-11-23 also every other row the question map (audit/question_map.py) counts open: a status
whose first word is not yes, closed or n/a. Found by the Q-PAR read: **optional** (16), **different
default** (22b) and **engine-only** (25) were open in the pyramid but missing from every result's line,
because this reader matched only bold no/partial/off-both. They are listed under their own word, since
each is a setting where engine and live can differ rather than a component the engine lacks. A test
holds this reader's open set equal to the map's (vault
raw/notes/2026-11/2026-11-23-apex-q-par-read-three-gap-rows-outside-gap-bounds.md).
"""
from __future__ import annotations

import re
from pathlib import Path

GAP_FILE = Path(__file__).with_name("PRODUCTION_GAP.md")
_ROW = re.compile(r"^\|\s*(\d+[a-z]?)\s*\|([^|]*)\|[^|]*\|([^|]*)\|")


def gap_rows(path: Path = GAP_FILE) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _ROW.match(line)
        if m:
            status = m.group(3).strip()
            rows.append({"row": m.group(1), "component": m.group(2).strip(), "status": status})
    return rows


ANSWERED = {"yes", "closed", "n/a"}   # audit/question_map.GAP_ANSWERED; the parity test holds them equal


def first_word(status: str) -> str:
    """The status's first word as the question map reads it (markup skipped, lower case)."""
    m = re.match(r"[*_`\s]*([A-Za-z/-]+)", status)
    return m.group(1).lower() if m else ""


def open_rows(regime_layer: bool, path: Path = GAP_FILE) -> list[dict]:
    out = []
    for r in gap_rows(path):
        s = r["status"]
        w = first_word(s)
        if re.search(r"\*\*off-both\b", s):
            kind = "off-both"
        elif re.search(r"\*\*(no|partial)\b", s) or (not regime_layer and "closed under `regime_layer=True` only" in s):
            kind = "open"
        elif w and w not in ANSWERED:
            kind = w                     # optional, different, engine-only: engine and live can differ
        else:
            continue
        out.append({"row": r["row"], "component": r["component"][:120], "kind": kind})
    return out


def describe(regime_layer: bool) -> str:
    rows = open_rows(regime_layer)
    gap = [r["row"] for r in rows if r["kind"] == "open"]
    dead = [r["row"] for r in rows if r["kind"] == "off-both"]
    other = [f"{r['row']} ({r['kind']})" for r in rows if r["kind"] not in ("open", "off-both")]
    out = f"{len(rows)} PRODUCTION_GAP rows not modelled: " + ", ".join(gap)
    if dead:
        out += "; off in both engines and in live (live defect): " + ", ".join(dead)
    if other:
        out += "; engine and live can differ by setting: " + ", ".join(other)
    return out
