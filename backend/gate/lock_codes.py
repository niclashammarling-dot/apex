"""
gate/lock_codes.py — the one table of gate locks, their rules and the stored codes.

Lock labels (vault raw/notes/2026-10/2026-10-24-apex-lock-labels-scope.md and the phase 2 scope note; chain
approved by Niclas 2026-10-24: "Kedjan ser bra ut. Godkänd."):

    L0 PORTFOLIO → L1 ELIGIBILITY → L2 RISK → L3 SCORE → L4 SENTIMENT → L5 POSITIONING → L6 REVIEW

What each lock owns is the approved chain's table, amended 2026-10-26 ("flytta makro till l1": the macro
calendar is market state like VIX): L0 the portfolio (M4, the drawdown guard), L1 market and sector state, VIX,
the macro calendar and whether the candidate can be bought at all (the skips, affordability), L2 ETF, earnings
and concentration (overflow, max positions, the sector cap), L3 the score, L4 what is said, L5 what is done with
money, L6 the review and the order it releases.

Every outcome is owned by a lock (Niclas 2026-10-28: "alla utfall måste ägas av ett lås"). A row records every
rule that ran on it with its verdict (`rule_states`, keyed by `rule_key`), and its stop is the first failing
rule in chain order (`stop_of`). The stored codes are derived from that by `codes_for`:
  - gate_decision: the owner's lock code (`FILTERED_L1_ELIGIBILITY` …), TRADE_EXECUTED, or FILTERED_UNKNOWN;
  - stop_code: "FILTERED_" + the rule key (`FILTERED_L1_ELIGIBILITY_COOLOFF` …), the same for the other two.
FILTERED_UNKNOWN is the one outcome without an owner: no registered rule failed. It is mailed when written.

Until phase 4 a lock's rules run in several places (L2's earnings inside lock1_eligibility, its ETF re-test
after L5, overflow and the sector cap at execution; the guard after L5). So a code names the owner, and how far
the row got is the pass flags (FLAG_RULES; NULL where the lock did not run, ruling D) and `rule_states`.

Two numberings until phase 3: "Lock 1".."Lock 5" in code outside this file are the module numbers
(lock1_eligibility .. lock5_claude, chain.py's lock_id and exit_lock; MODULE_TO_CHAIN); "L0".."L6" and the
names here are the chain. The stored codes before the 2026-10-31 migration are LEGACY_CODES, read only by the
history classifier, the migration and archived producers (T7) through the migration record.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

# The approved chain: number → (name, what it reads, the modules that run its rules today).
LOCKS: dict[int, tuple[str, str, str]] = {
    0: ("PORTFOLIO",   "portfolio stop rules (M4), cycle level",         "backend/gate/portfolio_l0.py, chain.py"),
    1: ("ELIGIBILITY", "market and sector state, macro calendar",         "backend/gate/lock1_eligibility.py, chain.py, gate_runner*.py"),
    2: ("RISK",        "ETF, earnings, concentration, overflow",          "backend/gate/lock1_eligibility.py, chain.py, gate_runner*.py"),
    3: ("SCORE",       "price and volume score",                          "backend/gate/lock2_quant.py"),
    4: ("SENTIMENT",   "what is said: analysts, news, social",            "backend/gate/lock3_sentiment.py"),
    5: ("POSITIONING", "what is done with money: options, accumulation",  "backend/gate/lock4_leading.py"),
    6: ("REVIEW",      "judgement over the whole candidate",              "backend/gate/lock5_claude.py, gate_runner*.py"),
}

# Module lock_id (lockN_*.py, chain.py exit_lock) → chain lock, until the phase 3 rename: the first lock whose
# modules include lockN_ (lock1_eligibility also runs L2's earnings rules, so L1 is its lock).
MODULE_TO_CHAIN: dict[int, int] = {n: min(lock for lock, (_, _, modules) in LOCKS.items() if f"lock{n}_" in modules)
                                   for n in range(1, 6)}

TRADE_EXECUTED = "TRADE_EXECUTED"
UNKNOWN = "FILTERED_UNKNOWN"
BOOKS = ("live", "demo")

# The codes stored before phase 2's migration (2026-10-31). No writer emits them; a CHECK flags one on a new row,
# and a test refuses any reader outside the legacy readers that names one.
LEGACY_CODES: tuple[str, ...] = (
    "SKIPPED_BLOCKLIST", "SKIPPED_OPEN", "SKIPPED_COOLOFF", "FILTERED_ELIGIBILITY", "FILTERED_L1", "FILTERED_L2",
    "FILTERED_LEADING", "FILTERED_ETF_PENALTY", "FILTERED_OVERFLOW_QUANT", "FILTERED_L3", "TRADE_REJECTED",
    "TRADE_FAILED", "FILTERED_MACRO",
)


def lock_code(n: int) -> str:
    """"FILTERED_L2_RISK": the gate_decision code of chain lock n (ruling B, 2026-10-26)."""
    return f"FILTERED_L{n}_{LOCKS[n][0]}"


def lock_label(n: int) -> str:
    """"L2 · Risk": the chain name of lock n, as every surface shows it."""
    return f"L{n} · {LOCKS[n][0].capitalize()}"


def sql_in(codes: tuple[str, ...]) -> str:
    """`'A', 'B'` for an IN (...) clause; codes are module constants, never user input."""
    return ", ".join(f"'{c}'" for c in codes)


@dataclass(frozen=True)
class Rule:
    owner: int                        # the chain lock that owns the stop
    name: str
    emitted_as: tuple[str, ...] = ()  # the code's own identifiers for it (fail_type, outcome_reason)
    config_keys: tuple[str, ...] = ()
    source: str = ""
    cooloff: bool = False             # a stop by this rule cools the ticker (db's cooloff query)
    history_only: bool = False        # written only by the history migration; a CHECK refuses it on new rows
    books: tuple[str, ...] = BOOKS    # the books that run it
    check: bool = True                # a check that passes or fails; False for a failure mode (unavailable,
                                      # broker_error), which appears only as a fail
    optional: bool = False            # runs only on some rows of its book (demo's max_positions, not on an
                                      # overflow slot), so a lock can show ✓ without it


# The rules within each lock (phase 2, ruling B 2026-10-26: stop_code = the lock's code + "_" + rule).
# Every rule name exists here once; the writers, the readers, the history classifier and the CHECKs read it,
# so a stop_code parses back to its owner and rule (parse_stop_code) and a writer's identifier maps to
# one rule (rule_for) (Niclas 2026-10-28: "konsekvent och kan härledas överallt där det används").
# A name is the code's own word for the check that stopped the row: the identifier it already emits
# (lock1_eligibility's fail_type, the runners' outcome_reason) where there is one, else the check itself:
# the module whose check it is (leading, claude) or what it tests (threshold, grok's verdict).
# `unavailable` is a source that did not answer, failing closed; `_inferred` exists only in history (ruling F).
# Cooloff keeps the set db.py selected before phase 2 ({FILTERED_L2, FILTERED_L3}, ruling 6 2026-10-24):
# every L4 and L6 decision and the drawdown guard, which shared FILTERED_L3; not a broker error (TRADE_FAILED).
# Within a lock the rules are listed in the order they run, which stop_of reads when two fail on one row: M4's
# drawdown before its loss cap and expectancy (portfolio_l0.evaluate), the chain's guard after them; position open
# before cooloff (the runners' skip label), gate cooloff before exit cooloff (ruling 2026-10-29).
RULES: tuple[Rule, ...] = (
    Rule(0, "halt_m4_drawdown", ("halt_m4_drawdown",), books=("live",),
         source="M4 rule 1, portfolio_l0.HALT_DRAWDOWN: realised equity off its peak (live, cycle level)"),
    Rule(0, "loss_cap", ("loss_cap",), ("daily_loss_cap",),
         "M4 rule 2 in live (portfolio_l0.HALT_LOSS_CAP, cycle level); wallet.execute_trade's fixed-dollar cap in demo"),
    Rule(0, "halt_m4_expectancy", ("halt_m4_expectancy",), books=("live",),
         source="M4 rule 3, portfolio_l0.HALT_EXPECTANCY: the trailing sample's expectancy (live, cycle level)"),
    Rule(0, "drawdown_guard", config_keys=("max_drawdown_pct",), cooloff=True,
         source="chain.py mechanical pre-L5 guard (reason ends '(mechanical pre-L5 guard)')"),
    Rule(1, "blocklist", ("blocklist",), books=("live",),
         source="gate_runner_live: the live blocklist, before the chain"),
    Rule(1, "position_open", ("position_open",),
         source="both runners before the chain, and the execution re-check (gate_runner_live, wallet)"),
    Rule(1, "cooloff", ("cooloff",), ("gate_cooloff_hours",),
         "both runners, before the chain: a recent L4/L6 stop (the gate cooloff; before 2026-10-31 SKIPPED_COOLOFF "
         "also held the exit cooloff)"),
    Rule(1, "exit_cooloff", ("exit_cooloff",), ("exit_cooloff_hours", "tp_cooloff_hours"),
         "both runners, before the chain: the ticker's own recent exit (tp_cooloff_hours after a TP/TSL win, "
         "exit_cooloff_hours after any other exit; db.get_recently_exited_*tickers)"),
    Rule(1, "regime", ("regime", "excluded_sector", "regime_unavailable", "below_leaderboard_cutoff", "regime_floor"),
         source="lock1_eligibility fail_type: the regime module's verdict on the sector"),
    Rule(1, "vix", ("vix",), ("vix_threshold",), "lock1_eligibility fail_type"),
    Rule(1, "macro_event", ("macro_event",), ("macro_event_blackout_days",), "lock1_eligibility fail_type"),
    Rule(1, "macro_pre_event", ("macro_pre_event",), ("macro_pre_event_penalty",),
         "lock1_eligibility data key; the penalty bites at the score re-test (chain.py)"),
    Rule(1, "notional_too_small", ("notional_too_small",),
         source="the order cannot buy: before the chain on the largest possible notional, and at execution "
                "(live < $10 or no whole share, demo < $50)"),
    Rule(1, "price_missing", ("price_missing",), source="before the chain and at execution: no price"),
    Rule(2, "earnings", ("earnings",), ("macro_earnings_blackout_days",), "lock1_eligibility fail_type"),
    Rule(2, "earnings_near", ("earnings_near_penalty",), ("macro_earnings_near_days", "macro_earnings_near_penalty"),
         "lock1_eligibility data key; the penalty bites at the score re-test (chain.py); never fired"),
    Rule(2, "etf", config_keys=("etf_negative_penalty", "etf_negative_floor"),
         source="chain.py ETF re-test after L5 (FILTERED_ETF_PENALTY before 2026-10-31)"),
    Rule(2, "overflow", ("overflow",), ("overflow_quant_increment",),
         "gate_runner*.py execution phase: a position beyond max_positions needs the escalated score; a free slot "
         "passes"),
    Rule(2, "max_positions", ("max_positions",), ("max_positions",), books=("demo",), optional=True,
         source="wallet.execute_trade: demo max positions without an overflow slot"),
    Rule(2, "sector_cap", ("sector_cap",), ("max_sector_exposure",),
         "execution phase: gate_runner_live, wallet.execute_trade"),
    Rule(3, "threshold", config_keys=("lock1_threshold",), source="lock2_quant: score below the threshold"),
    Rule(4, "grok", config_keys=("lock2_sentiment_min",), cooloff=True,
         source="lock3_sentiment: Grok's verdict (score at or below the minimum, or low conviction)"),
    Rule(4, "unavailable", cooloff=True, check=False,
         source="lock3_sentiment 'Grok API unavailable — failing closed'"),
    Rule(4, "unavailable_inferred", cooloff=True, history_only=True, check=False,
         source="history: no summary and no reason"),
    Rule(5, "leading", config_keys=("lock4_pcr_mode",),
         source="lock4_leading: the leading-indicator checks (price and options groups; 2 of 4 before 2026-06-03)"),
    Rule(5, "unavailable", check=False,
         source="lock4_leading: a data error decided the failing group (price check error, or no options data "
                "with a fetch error)"),
    Rule(6, "claude", config_keys=("lock3_confidence_min",), cooloff=True,
         source="lock5_claude: Claude's decision (HOLD, or confidence below the minimum)"),
    Rule(6, "unavailable", cooloff=True, check=False, source="lock5_claude L5_UNAVAILABLE_PREFIX"),
    Rule(6, "unavailable_inferred", cooloff=True, history_only=True, check=False,
         source="history: no reasoning before ba12aa2 (2026-09-18)"),
    Rule(6, "broker_error", ("broker_error",), check=False,
         source="gate_runner_live: the broker refused the approved order"),
)

# The pass flag columns (names until phase 3 part a) and the rules each one summarises: 1 when they passed, 0 when
# one failed, NULL when none ran (ruling D). lock1_pass is the score test as run, so the two penalties that bite
# at it count; lock3_pass is Claude's decision, so a broker error after an approval leaves it 1. These are the
# values the columns held before phase 2, minus the 0s for locks that never ran.
FLAG_RULES: dict[str, tuple[tuple[int, str], ...]] = {
    "lock1_pass":        ((3, "threshold"), (1, "macro_pre_event"), (2, "earnings_near")),
    "lock2_pass":        ((4, "grok"), (4, "unavailable"), (4, "unavailable_inferred")),
    "lock_leading_pass": ((5, "leading"), (5, "unavailable")),
    "lock3_pass":        ((6, "claude"), (6, "unavailable"), (6, "unavailable_inferred")),
}
# The lock each flag column belongs to (the history migration's run-order check reads it).
FLAG_COLUMNS: dict[int, str] = {3: "lock1_pass", 4: "lock2_pass", 5: "lock_leading_pass", 6: "lock3_pass"}

# A row's rule_states: every rule that ran on it, by rule_key, with its verdict (Niclas 2026-10-29: "Kedjan
# anger ägare och regel för varje stopp, men även för varje pass"). A rule that did not run is absent.
# NO_DATA: the rule ran without its input and let the row through (VIX unreadable, no earnings date), which a
# pass would hide; Terminal shows it N/A (Niclas 2026-10-29: "ett tillfälligt tredje läge").
PASS, FAIL, NO_DATA = "pass", "fail", "no_data"
_ORDER: dict[tuple[int, str], int] = {(r.owner, r.name): i for i, r in enumerate(RULES)}
_BY_KEY: dict[tuple[int, str], Rule] = {(r.owner, r.name): r for r in RULES}


def owner_code(owner: int) -> str:
    return lock_code(owner)


def rule_key(owner: int, rule: str) -> str:
    """"L1_ELIGIBILITY_MACRO_EVENT": a registered rule's one name. rule_states keys are this, and a stop_code is
    "FILTERED_" + this (ruling B), so a rule is spelled once wherever it is used."""
    if (owner, rule) not in _ORDER:
        raise KeyError(f"no rule {rule!r} under {owner!r} in lock_codes.RULES")
    return f"L{owner}_{LOCKS[owner][0]}_{rule.upper()}"


def parse_rule_key(key: str) -> tuple[int, str]:
    """(owner, rule) of a rule_key: the inverse of rule_key, through the register."""
    for r in RULES:
        if key == f"L{r.owner}_{LOCKS[r.owner][0]}_{r.name.upper()}":
            return r.owner, r.name
    raise KeyError(f"not a registered rule_key: {key!r}")


def stop_code(owner: int, rule: str) -> str:
    """"FILTERED_L1_ELIGIBILITY_MACRO_EVENT": the owner's code + "_" + a registered rule (ruling B)."""
    return "FILTERED_" + rule_key(owner, rule)


def parse_stop_code(code: str) -> tuple[int, str]:
    """(owner, rule) of a stop_code: the inverse of stop_code, through the register. UNKNOWN has no owner."""
    if not code.startswith("FILTERED_"):
        raise KeyError(f"not a registered stop_code: {code!r}")
    try:
        return parse_rule_key(code[len("FILTERED_"):])
    except KeyError:
        raise KeyError(f"not a registered stop_code: {code!r}") from None


def rule_for(emitted: str) -> Rule:
    """The rule a writer's own identifier belongs to (fail_type, or outcome_reason before ':').
    Identifiers are unique across the register (pinned by a test), so the identifier alone gives the owner."""
    key = emitted.split(":", 1)[0]
    for r in RULES:
        if key in r.emitted_as:
            return r
    raise KeyError(f"{emitted!r} is not an identifier of any rule in lock_codes.RULES")


def put(states: dict[str, str], ident: str, verdict: str) -> bool:
    """Record a writer's identifier (fail_type, outcome_reason) with its verdict under its rule's key. False when
    the identifier is in no rule: nothing is recorded, so the row's stop is unknown rather than guessed."""
    try:
        r = rule_for(ident)
    except KeyError:
        return False
    states[rule_key(r.owner, r.name)] = verdict
    return True


def stop_of(states: dict[str, str]) -> tuple[int, str] | None:
    """The rule that owns a row's stop: its first FAIL in chain order, by lock and then register order (ruling
    2026-10-28: "macro slår till först, L1, så den regeln gäller först"; when gate and exit cooloff both hold,
    cooloff). None when nothing failed. Writers and readers both take the stop from here."""
    fails = [parse_rule_key(k) for k, v in states.items() if v == FAIL]
    return min(fails, key=lambda f: (f[0], _ORDER[f])) if fails else None


def codes_for(states: dict[str, str], executed: bool) -> tuple[str, str]:
    """(gate_decision, stop_code) of a row, the one derivation every writer uses: TRADE_EXECUTED for an order
    placed, else the stop's owner code and stop_code, else FILTERED_UNKNOWN (no registered rule failed)."""
    if executed:
        return TRADE_EXECUTED, TRADE_EXECUTED
    stop = stop_of(states)
    if stop is None:
        return UNKNOWN, UNKNOWN
    return lock_code(stop[0]), stop_code(*stop)


def flags(states: dict[str, str]) -> dict[str, int | None]:
    """The four pass flag columns from rule_states (FLAG_RULES)."""
    out: dict[str, int | None] = {}
    for col, rules in FLAG_RULES.items():
        seen = [states[rule_key(o, r)] for o, r in rules if rule_key(o, r) in states]
        out[col] = None if not seen else 0 if FAIL in seen else 1
    return out


COOLOFF_STOPS: tuple[str, ...] = tuple(stop_code(r.owner, r.name) for r in RULES if r.cooloff)
HISTORY_ONLY_STOPS: tuple[str, ...] = tuple(stop_code(r.owner, r.name) for r in RULES if r.history_only)
# The L1 rules a candidate can be stopped by before any chain lock runs: a stop by one of them with no pass flag
# set is a skip (it carries no judgement on the candidate; Claude's gate history leaves it out).
SKIP_RULES: tuple[str, ...] = tuple(stop_code(1, n) for n in
                                    ("blocklist", "position_open", "cooloff", "exit_cooloff",
                                     "notional_too_small", "price_missing"))


def is_skip(row: dict) -> bool:
    """A row stopped before any chain lock ran by one of SKIP_RULES."""
    return row.get("stop_code") in SKIP_RULES and all(row.get(c) is None for c in FLAG_RULES)


def skip_sql() -> str:
    """is_skip as an SQL predicate over a gate table's columns (the same constants)."""
    return f"(stop_code IN ({sql_in(SKIP_RULES)}) AND " + " AND ".join(f"{c} IS NULL" for c in FLAG_RULES) + ")"


def expected_checks(lock: int, book: str) -> tuple[str, ...]:
    """The rule keys a lock's ✓ needs on a row of `book`: its checks that the book runs on every row."""
    return tuple(rule_key(r.owner, r.name) for r in RULES
                 if r.owner == lock and r.check and not r.history_only and not r.optional and book in r.books)


def lock_marks(states: dict[str, str], book: str) -> list[str | None]:
    """One mark per lock L0..L6 for a row (Terminal, ruling 2026-10-28: one ✗ per stopped row, in its owner's
    column). "fail" in the stop owner's column; "n/a" when a rule of the lock ran without its input; "pass" when
    every check the book runs passed; "partial" when some ran and others not yet (until phase 4 a lock's rules run
    in several places) or another lock's stop ended the row; None when no rule of the lock ran."""
    stop = stop_of(states)
    out: list[str | None] = []
    for n in sorted(LOCKS):
        mine = {k: v for k, v in states.items() if parse_rule_key(k)[0] == n}
        if stop is not None and stop[0] == n:
            out.append("fail")
        elif not mine:
            out.append(None)
        elif NO_DATA in mine.values():
            out.append("n/a")
        elif all(mine.get(k) == PASS for k in expected_checks(n, book)) and FAIL not in mine.values():
            out.append("pass")
        else:
            out.append("partial")
    return out


def badge(row: dict) -> str:
    """The Terminal's outcome pill: the rule that stopped the row ("L1 COOLOFF"), EXECUTED or UNKNOWN."""
    code = row.get("stop_code") or row.get("gate_decision")
    if code == TRADE_EXECUTED:
        return "EXECUTED"
    try:
        owner, rule = parse_stop_code(code or "")
    except KeyError:
        return "UNKNOWN" if code == UNKNOWN else (code or "")
    return f"L{owner} {rule.upper().replace('_', ' ')}"


def badge_kind(row: dict) -> str:
    code = row.get("stop_code") or row.get("gate_decision")
    if code == TRADE_EXECUTED:
        return "executed"
    if code == UNKNOWN or not (code or "").startswith("FILTERED_L"):
        return "unknown"
    return "skip" if is_skip(row) else "stop"


def reason_label(row: dict) -> str | None:
    """Whose reason the row's lock3_reasoning column holds: Claude's once L6 ran (lock3_pass set), otherwise the
    chain lock that stopped the row (gate_runner._chain_to_gate_result stores the stopping lock's reason). A row
    with no reason (skips, overflow before L6, affordability) gets no label."""
    if not row.get("lock3_reasoning"):
        return None
    if row.get("lock3_pass") is not None:
        return lock_label(6)
    try:
        return lock_label(parse_stop_code(row.get("stop_code") or "")[0])
    except KeyError:
        return None


def decorate_rows(rows: list[dict], book: str) -> list[dict]:
    """Per-row lock marks, badge and reason label for the gate history endpoints (Terminal)."""
    for r in rows:
        try:
            states = json.loads(r.get("rule_states") or "{}")
        except (TypeError, ValueError):
            states = {}
        r["lock_states"] = lock_marks(states, book)
        r["badge"] = badge(r)
        r["badge_kind"] = badge_kind(r)
        r["reason_label"] = reason_label(r)
    return rows


def config_labels() -> dict[str, str]:
    """The lock label that owns each config key ("L4 · Sentiment"); Settings and Promote read it from as_api()."""
    return {k: lock_label(r.owner) for r in RULES for k in r.config_keys}


def funnel_stages(by_stop: dict[str, int]) -> list[dict]:
    """The dashboard funnel, per lock in chain order with each rule's stops, then executed and unknown (computed
    here, rendered as-is by the frontend). A code the register does not know is listed under its own name."""
    rows = []
    for n in sorted(LOCKS):
        rules = [{"rule": r.name, "stop_code": stop_code(n, r.name), "count": by_stop.get(stop_code(n, r.name), 0)}
                 for r in RULES if r.owner == n]
        rows.append({"code": lock_code(n), "label": lock_label(n), "kind": "lock", "lock": n,
                     "count": sum(x["count"] for x in rules), "rules": [x for x in rules if x["count"]]})
    rows.append({"code": TRADE_EXECUTED, "label": "Executed", "kind": "executed", "lock": None,
                 "count": by_stop.get(TRADE_EXECUTED, 0), "rules": []})
    rows.append({"code": UNKNOWN, "label": "Unknown (no rule)", "kind": "unknown", "lock": None,
                 "count": by_stop.get(UNKNOWN, 0), "rules": []})
    known = {stop_code(r.owner, r.name) for r in RULES} | {TRADE_EXECUTED, UNKNOWN}
    rows += [{"code": c, "label": c, "kind": "unregistered", "lock": None, "count": n, "rules": []}
             for c, n in sorted((c, n) for c, n in by_stop.items() if c is not None and c not in known)]
    return rows


MARKS = {"pass": "✓", "fail": "✗", "n/a": "N/A", "partial": "◐", "none": "·"}


def as_api() -> dict:
    """Served inside both gate-history responses (the funnel, Gate Activity, Settings and Promote render from
    it; the standalone /api/gate/locks had no caller and left 2026-10-31): the chain, the Terminal's lock
    columns L0–L6 and their marks, every rule with its stop code, and the config and detail labels."""
    return {
        "locks": [{"lock": n, "name": name, "reads": reads, "module": module}
                  for n, (name, reads, module) in sorted(LOCKS.items())],
        "columns": [{"lock": n, "head": f"L{n}", "label": lock_label(n), "code": lock_code(n)} for n in sorted(LOCKS)],
        "marks": MARKS,
        "rules": [{"lock": r.owner, "rule": r.name, "stop_code": stop_code(r.owner, r.name),
                   "label": f"{lock_label(r.owner)} · {r.name}", "cooloff": r.cooloff, "books": list(r.books),
                   "config_keys": list(r.config_keys)} for r in RULES],
        "config_labels": config_labels(),
        "detail_labels": {"macro_reason": lock_label(1), "l2_summary": lock_label(4),
                          "lock_leading_checks": lock_label(5)},
    }
