"""
Every signal_trace field the RegimeBayes card reads is a key regime_bayes writes (scope § 12, inventory A,
2026-11-07: vault raw/notes/2026-11/2026-11-02-apex-saturday-scopes-11-07.md).

0b4f32d (2026-05-09) renamed the trace key etf_consecutive_days to etf_signed_streak (a signed int: up days
positive, down days negative); the card kept reading the old name, so its "ETF consecutive days" row showed "—"
for six months. The card reads `entry.signal_trace` (RegimeBayes.jsx) from /api/sectors/regime-bayes, whose
entries carry regime_bayes's signal_trace dict, built from string keys in backend/regime/regime_bayes.py.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CARD = ROOT / "frontend/src/components/RegimeBayes.jsx"
WRITER = ROOT / "backend/regime/regime_bayes.py"


def _dict_keys(path: Path) -> set[str]:
    return {k.value for n in ast.walk(ast.parse(path.read_text())) if isinstance(n, ast.Dict)
            for k in n.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}


def test_every_trace_field_read_is_a_key_regime_bayes_writes():
    read = set(re.findall(r"\btrace\.([a-z_][a-z0-9_]*)", CARD.read_text()))
    assert read, "no trace field found in RegimeBayes.jsx: the pattern no longer matches the card"
    missing = read - _dict_keys(WRITER)
    assert not missing, f"RegimeBayes.jsx reads {sorted(missing)}, keys regime_bayes.py never writes"
