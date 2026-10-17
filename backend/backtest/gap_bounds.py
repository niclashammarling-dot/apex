"""
Which production components a backtest result does NOT model (2026-10-17).

PRODUCTION_GAP.md is the list the engine work follows. Until now each result's
bound list was written by hand, and the 10-16 profit read's list named rows 9 and 10
but left out row 7 (macro blackout, "no" in engine_fast). Live blocks entries on
18 % of weekdays for that row, and every engine_fast run of 10-17 traded through
them. Every result now carries the open rows, read from the file at run time.

Open = status **no** or **partial**, plus the rows closed only under
`regime_layer=True` when the run has the flag off.
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


def open_rows(regime_layer: bool, path: Path = GAP_FILE) -> list[dict]:
    out = []
    for r in gap_rows(path):
        s = r["status"]
        if re.search(r"\*\*(no|partial)\b", s) or (not regime_layer and "closed under `regime_layer=True` only" in s):
            out.append({"row": r["row"], "component": r["component"][:120]})
    return out


def describe(regime_layer: bool) -> str:
    rows = open_rows(regime_layer)
    return f"{len(rows)} PRODUCTION_GAP rows not modelled: " + ", ".join(r["row"] for r in rows)
