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

Rule names come from lock_codes.RULES, never from this module: stop_code() refuses a name the register
does not hold. Where the writer emitted an identifier of its own (lock1_eligibility's fail_type, live's
outcome_reason), the row's text or field is read back to that identifier and the register gives the rule
(rule_for), so history and the writers name a check the same way (Niclas 2026-10-28: "konsekvent och kan
härledas överallt där det används").
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from backend.gate import lock_codes
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

UNCHANGED = ("TRADE_EXECUTED", "SKIPPED_OPEN", "SKIPPED_COOLOFF", "SKIPPED_BLOCKLIST")

# lock1_eligibility's reason texts → the fail_type it set with them (lock1_eligibility.py, `fail_type`), including
# the wordings before the hysteresis band ("< allocation floor") and before the leaderboard cutoff ("not found").
ELIGIBILITY_FAIL_TYPES: tuple[tuple[str, str], ...] = (
    (r"^VIX=", "vix"),
    (r"^Macro event blackout", "macro_event"),
    (r"^Earnings on", "earnings"),
    (r"^Sector '[^']*' excluded from entry", "excluded_sector"),
    (r"^Sector '[^']*' (below leaderboard cutoff|not found in regime leaderboard)", "below_leaderboard_cutoff"),
    (r"^Sector '[^']*' (has no allocation|adjusted score [0-9.]+ < allocation floor)", "regime_floor"),
    (r"^Regime module has not run", "regime_unavailable"),
)


class UnclassifiedRow(ValueError):
    pass


@dataclass(frozen=True)
class Class:
    code: str
    stop_code: str
    rule: str | None
    basis: str


def _lock(n: int, rule: str, basis: str = "field") -> Class:
    return Class(lock_code(n), lock_codes.stop_code(n, rule), rule, basis)


def _emitted(table: str, row: dict, ident: str, basis: str = "field") -> Class:
    """The class of a row whose writer's own identifier (fail_type, outcome_reason) is known."""
    try:
        r = lock_codes.rule_for(ident)
    except KeyError as e:
        raise UnclassifiedRow(f"{table} id {row.get('id')}: {e}") from None
    return Class(lock_codes.owner_code(r.owner), lock_codes.stop_code(r.owner, r.name), r.name, basis)


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

    if code in ("TRADE_REJECTED", "TRADE_FAILED"):
        # outcome_reason exists on live only, from b3804fb (09-27): every live TRADE_FAILED row carries it,
        # every TRADE_REJECTED row predates it, and demo has no such column.
        if row.get("outcome_reason"):
            k = _emitted(table, row, row["outcome_reason"])
            if k.code != code:
                raise UnclassifiedRow(f"{table} id {row.get('id')}: outcome_reason {row['outcome_reason']!r} "
                                      f"belongs under {k.code}, not {code}")
            return k
        return Class(code, lock_codes.stop_code(code, "reason_unrecorded"), "reason_unrecorded",
                     "unattributable:outcome_reason not recorded (before b3804fb, or demo), logs pruned")

    if code == "FILTERED_ELIGIBILITY":
        for pattern, fail_type in ELIGIBILITY_FAIL_TYPES:
            if re.match(pattern, macro):
                return _emitted(table, row, fail_type)
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
            return _lock(5, "leading")
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
