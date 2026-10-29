"""
gate/history_classify.py — what stopped each stored gate row, by the approved chain (lock labels phase 2).

Scope: vault raw/notes/2026-10/2026-10-26-apex-lock-labels-phase2-scope.md (§ What each stored code holds,
rulings A-G, probes P1-P4 answered 2026-10-28). Phase 2 rewrites history so that "rätt lås har rätt namn
och enbart sin egen historik" (Niclas 2026-10-26). This module only reads a row and names its class; it
writes nothing. The migration (a later step) applies it to a copy first, then to production on a weekend.

classify(table, row) → Class(code, stop_code, rule, basis, reached):
  - code       the new gate_decision: the owning lock's code (FILTERED_L1_ELIGIBILITY …), TRADE_EXECUTED
               unchanged, or UNKNOWN for an outcome no rule covers (ninth round, 2026-10-28);
  - stop_code  the code plus the rule (FILTERED_L1_ELIGIBILITY_MACRO_EVENT …); it starts with `code`
               on every row (ruling B: "kod per lås och underkoder"; a CHECK asserts the prefix);
  - rule       the rule within the lock, None for a trade or skip row with no rule;
  - basis      "field" when a field on the row decides the class, "inferred:<why>" or
               "unattributable:<why>" when it does not (ruling F: never presented as observed);
  - reached    the last chain lock with a pass flag that ran on the row, by the run order before phase 4
               (2 = none of L3..L6 ran). pass_flags() sets every flag after it to NULL (ruling D).

Every outcome is owned by a lock (eighth round): the skips are L1 rules, and the execution phase's
rejections are rules of their locks by the identifier the writer recorded. A rejection without one is
UNKNOWN: the probe of 2026-10-28 found no open position at any of their times, so no rule can be inferred.

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

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

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

# The pre-chain skips: the rows the runners wrote before any lock ran, now L1's rules. SKIPPED_COOLOFF held two
# checks (Niclas 2026-10-29: "Skipped cooloff och exit cooloff bör vara olika markörer"): CooloffIndex says which.
SKIPS = {"SKIPPED_BLOCKLIST": "blocklist", "SKIPPED_COOLOFF": "cooloff", "SKIPPED_OPEN": "position_open"}
# The gate cooloff set held FILTERED_MACRO, the April macro gate's code, until 6794433 (2026-05-12 16:33 CEST).
MACRO_COOLOFF_UNTIL = "2026-05-12T14:33:12+00:00"
# gate_cooloff_hours per book and the exit windows (exit_cooloff_hours after any exit, tp_cooloff_hours after a
# TP/TSL win), as the configs hold them since they are tracked (2026-06-26). Replayed with these and the set in
# force, every cooloff skip in the three tables is explained (probe 2026-10-29: live 1432 gate / 1869 exit / 3 both
# after the 133 April rows the macro code explains; demo 1846 / 2395 / 71).
GATE_COOLOFF_HOURS = {"live": 4, "demo": 5}
EXIT_COOLOFF_HOURS, TP_COOLOFF_HOURS = 24, 168
# Live's whole-share sizing failure was recorded as a broker error; the order could not buy one share
# (11 rows, LLY, 2026-10-12..10-16; PRODUCTION_GAP row 34). The text decides it, so the rule is L1 notional_too_small (eighth round).
QTY_ZERO = "broker_error: Computed qty=0"
# L5's two-group rule (d70ab53, committed 2026-06-03 17:59 UTC) took effect at the restart between the
# 18:08 and 19:28 UTC cycles that day: the first positioning stop with two of four checks passed, possible
# only under it, is live 19:28:19. Before it a check error was one fewer pass in 2 of 4, never the cause.
GROUP_RULE_LIVE = "2026-06-03T19:00:00"

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
    reached: int = 6


def _lock(n: int, rule: str, reached: int, basis: str = "field") -> Class:
    return Class(lock_code(n), lock_codes.stop_code(n, rule), rule, basis, reached)


def _emitted(table: str, row: dict, ident: str, reached: int, basis: str = "field") -> Class:
    """The class of a row whose writer's own identifier (fail_type, outcome_reason) is known."""
    try:
        r = lock_codes.rule_for(ident)
    except KeyError as e:
        raise UnclassifiedRow(f"{table} id {row.get('id')}: {e}") from None
    return Class(lock_codes.owner_code(r.owner), lock_codes.stop_code(r.owner, r.name), r.name, basis, reached)


def pass_flags(k: Class, row: dict) -> dict[str, int | None]:
    """The four pass flags after ruling D: as recorded up to the last lock that ran, NULL after it."""
    return {col: (row.get(col) if n <= k.reached else None) for n, col in lock_codes.FLAG_COLUMNS.items()}


# The first eligibility-module rows (signals 2026-04-19 22:00 UTC, live_gate_history 2026-04-20; weekly_report
# REFERENCE_START): before them a row's L1 regime and macro checks did not run in one module before the score.
ELIGIBILITY_LIVE = "2026-04-20T00:00:00+00:00"
_VERDICT_RULES = {"lock1_pass": (3, "threshold"), "lock2_pass": (4, "grok"), "lock_leading_pass": (5, "leading"),
                  "lock3_pass": (6, "claude")}


def history_rule_states(k: Class, row: dict, flags: dict) -> dict[str, str]:
    """The rule verdicts a migrated row's own fields prove (basis inferred; Niclas 2026-10-29: "Håller med om
    historiken här"), given its pass flags after ruling D (pass_flags): the stop's rule fails; a flag of 1 passes
    its lock's verdict rule; a row that reached L3 after the eligibility module went live passed its regime and
    macro-event checks, and its earnings check had no data (the date source never returned one, PRODUCTION_GAP
    rows 8-9). Nothing else is claimed: VIX (it may have had no reading), the penalties, L0 and the execution
    checks stay absent, so Terminal shows those locks partial or empty on history."""
    states: dict[str, str] = {}
    try:
        owner, rule = lock_codes.parse_stop_code(k.stop_code)
        states[lock_codes.rule_key(owner, rule)] = lock_codes.FAIL
    except KeyError:
        pass                                         # TRADE_EXECUTED, FILTERED_UNKNOWN, the test row
    for col, (o, r) in _VERDICT_RULES.items():
        if flags.get(col) == 1:
            states.setdefault(lock_codes.rule_key(o, r), lock_codes.PASS)
    if flags.get("lock1_pass") is not None and (row.get("timestamp") or "") >= ELIGIBILITY_LIVE:
        for o, r in ((1, "regime"), (1, "macro_event")):
            states.setdefault(lock_codes.rule_key(o, r), lock_codes.PASS)
        states.setdefault(lock_codes.rule_key(2, "earnings"), lock_codes.NO_DATA)
    return states


def _positioning(table: str, row: dict) -> Class:
    """leading, or unavailable when a data error decided the failing group (the writer's rule from phase 2,
    read back from lock_leading_checks, which every positioning row carries)."""
    if (row.get("timestamp") or "") >= GROUP_RULE_LIVE:
        try:
            checks = json.loads(row.get("lock_leading_checks") or "")
        except ValueError:
            raise UnclassifiedRow(f"{table} id {row.get('id')}: positioning row without readable checks") from None
        price = [checks.get(c) or {} for c in ("relative_strength", "volume_accumulation")]
        options = [checks.get(c) or {} for c in ("put_call_ratio", "unusual_calls")]
        price_error = any(c.get("error") for c in price)
        price_pass = not price_error and any(c.get("pass") for c in price)
        fetch_error = any((c.get("reason") or "").startswith("no options data (")
                          and "no listed expiries" not in c["reason"] for c in options)
        if (not price_pass and price_error) or (price_pass and fetch_error):
            return _lock(5, "unavailable", 5)
    return _lock(5, "leading", 5)


def _score_in_reason(text: str) -> float | None:
    m = re.search(r"[Ss]ignal score ([0-9.]+)", text or "")
    return float(m.group(1)) if m else None


def _at(ts: str) -> datetime:
    t = datetime.fromisoformat(ts)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


class CooloffIndex:
    """Which cooloff stopped each SKIPPED_COOLOFF row, replayed at the time it was decided: a gate stop (an L4/L6
    decision, and FILTERED_MACRO until MACRO_COOLOFF_UNTIL) within gate_cooloff_hours → `cooloff`; the ticker's
    own exit within its window → `exit_cooloff`; both → `cooloff`, first in the register (ruling 2026-10-29).
    Live reads live_gate_history and live_trades; demo reads demo_gate_history (the demo runner read the `signals`
    copy, whose row is overwritten by each cycle's decision, so a stop can be gone from it) and trades. A
    `signals` row's timestamp is the signal's poll time, not the decision's: it takes the time of the last demo
    cooloff skip of the ticker between its own time and the ticker's next signal, else its own time. A row neither
    cooloff explains raises: the migration stops rather than guess."""

    def __init__(self, conn) -> None:
        def rows(sql):
            return conn.execute(sql).fetchall()
        self.stops: dict[tuple[str, str], list] = defaultdict(list)
        self.exits: dict[tuple[str, str], list] = defaultdict(list)
        for book, table in (("live", "live_gate_history"), ("demo", "demo_gate_history")):
            for t, ts, code in rows(f"SELECT ticker, timestamp, gate_decision FROM {table} "
                                    "WHERE gate_decision IN ('FILTERED_L2', 'FILTERED_L3', 'FILTERED_MACRO')"):
                self.stops[(book, t)].append((_at(ts), code))
        for book, table in (("live", "live_trades"), ("demo", "trades")):
            for t, ex, out, why in rows(f"SELECT ticker, exited_at, outcome, exit_reason FROM {table} "
                                        "WHERE exited_at IS NOT NULL AND outcome IN ('WIN', 'LOSS', 'EXPIRED')"):
                hours = TP_COOLOFF_HOURS if (out == "WIN" and why in ("TP", "TSL")) else EXIT_COOLOFF_HOURS
                self.exits[(book, t)].append((_at(ex), hours))
        self.demo_skips: dict[str, list] = defaultdict(list)
        for t, ts in rows("SELECT ticker, timestamp FROM demo_gate_history WHERE gate_decision = 'SKIPPED_COOLOFF'"):
            self.demo_skips[t].append(_at(ts))
        self.signal_times: dict[str, list] = defaultdict(list)
        for t, ts in rows("SELECT ticker, timestamp FROM signals"):
            self.signal_times[t].append(_at(ts))
        for d in (self.demo_skips, self.signal_times):
            for v in d.values():
                v.sort()

    def rule(self, table: str, row: dict) -> tuple[str, str]:
        t, at = row["ticker"], _at(row["timestamp"])
        book = "live" if table == "live_gate_history" else "demo"
        when = "at the row's time"
        if table == "signals":
            times = self.signal_times[t]
            nxt = next((x for x in times if x > at), None)
            matched = [d for d in self.demo_skips[t] if at <= d and (nxt is None or d < nxt)]
            if matched:
                at, when = matched[-1], "at the matched demo_gate_history decision"
            else:
                when = "at the signal's time (no matched demo decision)"
        codes = ("FILTERED_L2", "FILTERED_L3") + (("FILTERED_MACRO",) if at < _at(MACRO_COOLOFF_UNTIL) else ())
        gate = any(0 < (at - s).total_seconds() < GATE_COOLOFF_HOURS[book] * 3600 and c in codes
                   for s, c in self.stops[(book, t)])
        exit_ = any(0 < (at - x).total_seconds() < h * 3600 for x, h in self.exits[(book, t)])
        if gate:
            return "cooloff", f"inferred:gate cooloff {when}" + ("; the exit cooloff held too" if exit_ else "")
        if exit_:
            return "exit_cooloff", f"inferred:exit cooloff {when}"
        raise UnclassifiedRow(f"{table} id {row.get('id')}: a cooloff skip neither cooloff explains {when}")


def classify(table: str, row: dict, cooloff: CooloffIndex | None = None) -> Class:
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

    if code == "TRADE_EXECUTED":
        return Class(code, code, None, "field")

    if code == "SKIPPED_COOLOFF":           # gate or exit cooloff, replayed (CooloffIndex)
        if cooloff is None:
            raise UnclassifiedRow(f"{table} id {row.get('id')}: a cooloff skip needs the CooloffIndex")
        rule, basis = cooloff.rule(table, row)
        return _lock(1, rule, 2, basis)

    if code in SKIPS:                       # written before any lock ran
        return _lock(1, SKIPS[code], 2)

    if code in ("TRADE_REJECTED", "TRADE_FAILED"):
        # After the chain: L3..L5 ran; L6 ran only if Claude approved (the pre-review "position already open"
        # reject records lock3_pass 0). outcome_reason exists on live only, from b3804fb (09-27).
        reached = 6 if row.get("lock3_pass") == 1 else 5
        outcome = row.get("outcome_reason")
        if outcome and outcome.startswith(QTY_ZERO):
            return _lock(1, "notional_too_small", reached)
        if outcome:
            return _emitted(table, row, outcome, reached)
        return Class(lock_codes.UNKNOWN, lock_codes.UNKNOWN, None,
                     "unattributable:reason not recorded (before b3804fb, or demo); no position was open", reached)

    if code == "FILTERED_ELIGIBILITY":
        for pattern, fail_type in ELIGIBILITY_FAIL_TYPES:
            if re.match(pattern, macro):
                return _emitted(table, row, fail_type, 2)
        raise UnclassifiedRow(f"{table} id {row.get('id')}: eligibility reason {macro[:60]!r}")

    if code == "FILTERED_MACRO":            # legacy, 2026-04-06..04-09: the CPI 04-08 block
        # That gate ran after the quant test: all 81 rows carry lock1_pass 1 (dry run 2026-10-28).
        if macro.lower().startswith("macro event"):
            return _lock(1, "macro_event", 3)
        raise UnclassifiedRow(f"{table} id {row.get('id')}: legacy macro reason {macro[:60]!r}")

    if code == "FILTERED_L1":
        if ts < MACRO_PENALTY_LIVE:
            return _lock(3, "threshold", 3)
        penalised = _score_in_reason(reason)
        if penalised is not None and row.get("signal_score"):
            if round(penalised / row["signal_score"], 3) == PRE_EVENT_RATIO:
                return _lock(1, "macro_pre_event", 3)
            raise UnclassifiedRow(f"{table} id {row.get('id')}: score rejection after {MACRO_PENALTY_LIVE} "
                                  f"with ratio {penalised / row['signal_score']:.3f}, not the pre-event penalty")
        if not reason and ts[:10] == PRE_EVENT_REASONLESS_DAY:
            return _lock(1, "macro_pre_event", 3, "inferred:pre-event day under the calendar at 2a88a10 (P3)")
        raise UnclassifiedRow(f"{table} id {row.get('id')}: reasonless score rejection on {ts[:10]}")

    if code == "FILTERED_L2":
        if "Grok API unavailable" in reason or "Grok API unavailable" in (row.get("l2_summary") or ""):
            return _lock(4, "unavailable", 4)
        if not reason and not row.get("l2_summary"):
            return _lock(4, "unavailable_inferred", 4,
                         "inferred:no summary and no reason (the fail path sets the summary to None)")
        return _lock(4, "grok", 4)

    if code == "FILTERED_LEADING":
        if row.get("lock_leading_pass") == 0:
            return _positioning(table, row)
        if row.get("lock_leading_pass") == 1:      # 07-13..07-16: the ETF re-test before it had its code
            return _lock(2, "etf", 5)
        raise UnclassifiedRow(f"{table} id {row.get('id')}: positioning row with lock_leading_pass "
                              f"{row.get('lock_leading_pass')!r}")

    if code == "FILTERED_ETF_PENALTY":
        return _lock(2, "etf", 5)

    if code == "FILTERED_OVERFLOW_QUANT":     # ruling E: L2 RISK ("den hör till risk"); before L6
        return _lock(2, "overflow", 5)

    if code == "FILTERED_L3":
        if reason.startswith("anthropic_unavailable"):
            basis = ("inferred:backfilled 2026-09-18 from logs, marked in the text"
                     if "[backfilled" in reason else "field")
            return _lock(6, "unavailable", 6, basis)
        if "(mechanical pre-L5 guard)" in reason:     # ruling C: L0 ("dd är före allt annat"); before L6
            return _lock(0, "drawdown_guard", 5)
        if not reason:
            return _lock(6, "unavailable_inferred", 6,
                         "inferred:no reasoning before ba12aa2 (09-18); a Claude decision always carries one")
        return _lock(6, "claude", 6)

    raise UnclassifiedRow(f"{table} id {row.get('id')}: unknown code {code!r}")
