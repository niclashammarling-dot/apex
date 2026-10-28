"""
gate/history_classify.py — what stopped each stored gate row, by the approved chain (lock labels phase 2).

Scope: vault raw/notes/2026-10/2026-10-26-apex-lock-labels-phase2-scope.md (§ What each stored code holds,
rulings A-G, probes P1-P4 answered 2026-10-28). Phase 2 rewrites history so that "rätt lås har rätt namn
och enbart sin egen historik" (Niclas 2026-10-26). This module only reads a row and names its class; it
writes nothing. The migration (a later step) applies it to a copy first, then to production on a weekend.

classify(table, row) → Class(code, stop_code, rule, basis):
  - code       the new gate_decision: the lock's code (FILTERED_L1_ELIGIBILITY …), or the unchanged
               trade/skip code;
  - stop_code  the code plus the rule (FILTERED_L1_ELIGIBILITY_MACRO_EVENT …); it starts with `code`
               on every row (ruling B: "kod per lås och underkoder"; a CHECK asserts the prefix);
  - rule       the rule within the lock, None for a trade or skip row with no rule;
  - basis      "field" when a field on the row decides the class, "inferred:<why>" or
               "unattributable:<why>" when it does not (ruling F: never presented as observed).

A row that matches no class raises UnclassifiedRow instead of defaulting: an unknown shape is a finding,
not a row to file under the nearest lock. Rows without a code (ungated `signals` rows, P1) stay NULL and
are never passed here.

Rule names: the scope's (regime, vix, macro_event, macro_pre_event, earnings, etf, overflow, claude,
unavailable, unavailable_inferred, drawdown_guard, reason_unrecorded), plus the deciding mechanism where
the scope left a lock's own judgement unnamed: L3 `threshold`, L4 `grok`, L5 `min_pass`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from backend.gate.lock_codes import lock_code

TABLES = ("live_gate_history", "demo_gate_history", "signals")

# Macro pre-event penalty went live 2026-06-16 (fdd1a7a); before it no penalty existed, so a score
# rejection was the threshold itself (the earnings penalty, 6d9f497 06-09, never fired: PRODUCTION_GAP 8-9).
MACRO_PENALTY_LIVE = "2026-06-16"
# The pre-event penalty multiplies the score by 1 - macro_pre_event_penalty (0.05 in both configs since it
# shipped): the reason's score ÷ signal_score is 0.95 on every reasoned row (172 of 172, probed 10-28).
PRE_EVENT_RATIO = 0.95
# P3 (2026-10-28): the only reasonless score rows after 06-16 are 2026-07-02, a pre-event day under the
# calendar in force (2a88a10: NFP = first Friday = 07-03).
PRE_EVENT_REASONLESS_DAY = "2026-07-02"
# The one test row that reached production (2026-01-01, demo_gate_history id 1276): removed with a dump.
TEST_ROW = ("demo_gate_history", 1276)
TEST_ROW_CODE = "REMOVE_TEST_ROW"

UNCHANGED = ("TRADE_EXECUTED", "TRADE_FAILED", "SKIPPED_OPEN", "SKIPPED_COOLOFF", "SKIPPED_BLOCKLIST")


class UnclassifiedRow(ValueError):
    pass


@dataclass(frozen=True)
class Class:
    code: str
    stop_code: str
    rule: str | None
    basis: str


def _lock(n: int, rule: str, basis: str = "field") -> Class:
    code = lock_code(n)
    return Class(code, f"{code}_{rule.upper()}", rule, basis)


def _score_in_reason(text: str) -> float | None:
    m = re.search(r"[Ss]ignal score ([0-9.]+)", text or "")
    return float(m.group(1)) if m else None


def classify(table: str, row: dict) -> Class:
    if table not in TABLES:
        raise ValueError(f"not a gate table: {table}")
    code = row.get("gate_decision")
    if code is None:
        raise UnclassifiedRow(f"{table} id {row.get('id')}: no gate code (ungated rows stay NULL, P1)")
    reason = row.get("lock3_reasoning") or ""
    macro = row.get("macro_reason") or ""
    ts = row.get("timestamp") or ""

    if (table, row.get("id")) == TEST_ROW:
        return Class(TEST_ROW_CODE, TEST_ROW_CODE, None, "field")

    if code in UNCHANGED:
        return Class(code, code, None, "field")

    if code == "TRADE_REJECTED":
        # outcome_reason exists on live only, from b3804fb (09-27); every TRADE_REJECTED row predates it.
        if row.get("outcome_reason"):
            raise UnclassifiedRow(f"{table} id {row.get('id')}: TRADE_REJECTED with an outcome_reason "
                                  f"({row['outcome_reason']!r}) — no such row existed at scoping; class it")
        return Class(code, f"{code}_REASON_UNRECORDED", "reason_unrecorded",
                     "unattributable:outcome_reason not recorded before b3804fb, logs pruned")

    if code == "FILTERED_ELIGIBILITY":
        if macro.startswith("VIX="):
            return _lock(1, "vix")
        if macro.startswith("Macro event blackout"):
            return _lock(1, "macro_event")
        if macro.startswith("Earnings on"):
            return _lock(2, "earnings")
        if macro.startswith("Sector"):
            return _lock(1, "regime")
        raise UnclassifiedRow(f"{table} id {row.get('id')}: eligibility reason {macro[:60]!r}")

    if code == "FILTERED_MACRO":            # legacy, 2026-04-06..04-09: the CPI 04-08 block
        if macro.lower().startswith("macro event"):
            return _lock(1, "macro_event")
        raise UnclassifiedRow(f"{table} id {row.get('id')}: legacy macro reason {macro[:60]!r}")

    if code == "FILTERED_L1":
        if ts < MACRO_PENALTY_LIVE:
            return _lock(3, "threshold")
        penalised = _score_in_reason(reason)
        if penalised is not None and row.get("signal_score"):
            if round(penalised / row["signal_score"], 3) == PRE_EVENT_RATIO:
                return _lock(1, "macro_pre_event")
            raise UnclassifiedRow(f"{table} id {row.get('id')}: score rejection after {MACRO_PENALTY_LIVE} "
                                  f"with ratio {penalised / row['signal_score']:.3f}, not the pre-event penalty")
        if not reason and ts[:10] == PRE_EVENT_REASONLESS_DAY:
            return _lock(1, "macro_pre_event", "inferred:pre-event day under the calendar at 2a88a10 (P3)")
        raise UnclassifiedRow(f"{table} id {row.get('id')}: reasonless score rejection on {ts[:10]}")

    if code == "FILTERED_L2":
        if "Grok API unavailable" in reason or "Grok API unavailable" in (row.get("l2_summary") or ""):
            return _lock(4, "unavailable")
        if not reason and not row.get("l2_summary"):
            return _lock(4, "unavailable_inferred",
                         "inferred:no summary and no reason (the fail path sets the summary to None)")
        return _lock(4, "grok")

    if code == "FILTERED_LEADING":
        if row.get("lock_leading_pass") == 0:
            return _lock(5, "min_pass")
        if row.get("lock_leading_pass") == 1:      # 07-13..07-16: the ETF re-test before it had its code
            return _lock(2, "etf")
        raise UnclassifiedRow(f"{table} id {row.get('id')}: positioning row with lock_leading_pass "
                              f"{row.get('lock_leading_pass')!r}")

    if code == "FILTERED_ETF_PENALTY":
        return _lock(2, "etf")

    if code == "FILTERED_OVERFLOW_QUANT":     # ruling E: L2 RISK ("den hör till risk")
        return _lock(2, "overflow")

    if code == "FILTERED_L3":
        if reason.startswith("anthropic_unavailable"):
            basis = ("inferred:backfilled 2026-09-18 from logs, marked in the text"
                     if "[backfilled" in reason else "field")
            return _lock(6, "unavailable", basis)
        if "(mechanical pre-L5 guard)" in reason:     # ruling C: L0 ("dd är före allt annat")
            return _lock(0, "drawdown_guard")
        if not reason:
            return _lock(6, "unavailable_inferred",
                         "inferred:no reasoning before ba12aa2 (09-18); a Claude decision always carries one")
        return _lock(6, "claude")

    raise UnclassifiedRow(f"{table} id {row.get('id')}: unknown code {code!r}")
