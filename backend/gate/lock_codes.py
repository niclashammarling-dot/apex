"""
gate/lock_codes.py — the one table of gate locks and gate_decision codes.

Lock labels phase 1 (vault raw/notes/2026-10/2026-10-24-apex-lock-labels-scope.md; chain approved by
Niclas 2026-10-24: "Kedjan ser bra ut. Godkänd."). Before this table five vocabularies named the same
locks: chain.py's exit_lock (right), the stored gate_decision codes (one behind: FILTERED_L1 = quant),
the DB columns, the config keys, and the frontend labels (CHECK 54, firing since 06-11). Every writer
and reader of a code now imports it from here; the frontend reads it from /api/gate/locks.

Phase 1 changes no stored string. `code` is what the three gate tables hold today; phase 2 rewrites
history and this column in one migration. `lock` and `label` are the approved chain:

    L0 PORTFOLIO → L1 ELIGIBILITY → L2 RISK → L3 SCORE → L4 SENTIMENT → L5 POSITIONING → L6 REVIEW

Where the code runs a lock in a different place than the chain names (phase 4 moves them), STAGES
keeps the evaluation order, so funnel "reached" counts stay true:
  - the ETF penalty is L2 RISK by ruling but runs after L5 POSITIONING (chain.py re-test);
  - the chain's 20 % drawdown guard records exit_lock 5, i.e. FILTERED_L3, the same code as an
    L6 REVIEW rejection (its job moved to L0, portfolio_l0.py; the guard leaves in phase 4);
  - L0 PORTFOLIO halts the cycle and writes no gate row (gate_cycles reason), so it has no code;
  - the real quant threshold is the pre-filter (db.get_lock1_candidates), which writes no row either;
    FILTERED_L1 counts only penalty bites at the re-test (10-10 finding, ruling 5 after S1).

Cooloff (ruling 6, 2026-10-24: "behåll cool off nu"): the set is kept exactly as db.py selected it,
{FILTERED_L2, FILTERED_L3}: an L4 SENTIMENT or L6 REVIEW rejection (and the drawdown guard, which shares
FILTERED_L3) cools the ticker; score, positioning and ETF rejections do not. Pinned by a test.
"""
from __future__ import annotations

from dataclasses import dataclass

# The approved chain: number → (name, what it reads, the module that runs it today).
LOCKS: dict[int, tuple[str, str, str]] = {
    0: ("PORTFOLIO",   "portfolio stop rules (M4), cycle level",         "backend/gate/portfolio_l0.py"),
    1: ("ELIGIBILITY", "market and sector state, calendar",               "backend/gate/lock1_eligibility.py"),
    2: ("RISK",        "ticker and sector risk: ETF, earnings, macro",    "backend/gate/chain.py"),
    3: ("SCORE",       "price and volume score",                          "backend/gate/lock2_quant.py"),
    4: ("SENTIMENT",   "what is said: analysts, news, social",            "backend/gate/lock3_sentiment.py"),
    5: ("POSITIONING", "what is done with money: options, accumulation",  "backend/gate/lock4_leading.py"),
    6: ("REVIEW",      "judgement over the whole candidate",              "backend/gate/lock5_claude.py"),
}


@dataclass(frozen=True)
class Stage:
    code: str                         # gate_decision as stored
    kind: str                         # skip | filter | execution | trade | legacy
    label: str                        # display label (funnel, Terminal badge)
    lock: int | None = None           # number in the approved chain
    exit_lock: int | None = None      # chain.py exit_lock whose default code this is
    cooloff: bool = False
    config_keys: tuple[str, ...] = ()


ETF_PENALTY = "FILTERED_ETF_PENALTY"
OVERFLOW = "FILTERED_OVERFLOW_QUANT"
SKIPPED_BLOCKLIST = "SKIPPED_BLOCKLIST"
SKIPPED_OPEN = "SKIPPED_OPEN"
SKIPPED_COOLOFF = "SKIPPED_COOLOFF"
UNKNOWN = "FILTERED_UNKNOWN"

# Evaluation order, as the runners record it.
STAGES: tuple[Stage, ...] = (
    Stage(SKIPPED_BLOCKLIST, "skip", "Live blocklist"),
    Stage(SKIPPED_OPEN, "skip", "Position open"),
    Stage(SKIPPED_COOLOFF, "skip", "Cooloff", config_keys=("gate_cooloff_hours",)),
    Stage("FILTERED_ELIGIBILITY", "filter", "L1 · Eligibility", lock=1, exit_lock=1),
    Stage("FILTERED_L1", "filter", "L3 · Score", lock=3, exit_lock=2,
          config_keys=("lock1_threshold",)),
    Stage("FILTERED_L2", "filter", "L4 · Sentiment", lock=4, exit_lock=3, cooloff=True,
          config_keys=("lock2_sentiment_min",)),
    Stage("FILTERED_LEADING", "filter", "L5 · Positioning", lock=5, exit_lock=4,
          config_keys=("lock_leading_min_pass", "lock4_pcr_mode")),
    Stage(ETF_PENALTY, "filter", "L2 · Risk (ETF)", lock=2,
          config_keys=("etf_negative_penalty", "etf_negative_floor")),
    Stage("FILTERED_L3", "filter", "L6 · Review", lock=6, exit_lock=5, cooloff=True,
          config_keys=("lock3_confidence_min",)),
    Stage(OVERFLOW, "execution", "Overflow", config_keys=("overflow_quant_increment",)),
    Stage("TRADE_EXECUTED", "trade", "Executed"),
    Stage("TRADE_REJECTED", "trade", "Rejected"),
    Stage("TRADE_FAILED", "trade", "Order failed"),
    Stage("FILTERED_MACRO", "legacy", "Macro (before 2026-04-10)"),
)

BY_CODE: dict[str, Stage] = {s.code: s for s in STAGES}
OUTCOME_BY_EXIT_LOCK: dict[int, str] = {s.exit_lock: s.code for s in STAGES if s.exit_lock is not None}
COOLOFF_CODES: tuple[str, ...] = tuple(s.code for s in STAGES if s.cooloff)
SKIP_CODES: tuple[str, ...] = tuple(s.code for s in STAGES if s.kind == "skip")
REVIEW_REJECT = OUTCOME_BY_EXIT_LOCK[5]


def outcome_for(exit_lock: int | None) -> str:
    """The code a chain rejection at `exit_lock` records (exit_label overrides live in chain.py)."""
    return OUTCOME_BY_EXIT_LOCK.get(exit_lock, UNKNOWN)


def sql_in(codes: tuple[str, ...]) -> str:
    """`'A', 'B'` for an IN (...) clause; codes are module constants, never user input."""
    return ", ".join(f"'{c}'" for c in codes)


# The per-candidate filters in the order a candidate meets them; the Terminal's lock columns and
# the weekly report's chained funnel read this. L6 REVIEW is last: on the live deferred path the
# overflow check runs before it, so the reports split what follows L2 RISK instead of chaining it.
CHAIN: tuple[Stage, ...] = tuple(s for s in STAGES if s.kind == "filter")
CHAINED: tuple[Stage, ...] = tuple(s for s in CHAIN if s.lock != 6)

_BADGE = {SKIPPED_BLOCKLIST: "BLOCKED", SKIPPED_OPEN: "HELD", SKIPPED_COOLOFF: "COOLOFF",
          OVERFLOW: "OVERFLOW PASS", "TRADE_EXECUTED": "EXECUTED", "TRADE_REJECTED": "REJECTED",
          "TRADE_FAILED": "FAILED", "FILTERED_MACRO": "MACRO (OLD)"}


def badge(s: Stage) -> str:
    return f"L{s.lock} FAIL" if s.kind == "filter" else _BADGE[s.code]


def lock_states(code: str) -> list[bool | None]:
    """One entry per CHAIN column: passed (True), failed here (False), not reached (None).
    A code that is not a chain filter passed every column it reached (trades, overflow)."""
    out: list[bool | None] = []
    hit = False
    for s in CHAIN:
        if hit:
            out.append(None)
        elif s.code == code:
            out.append(False)
            hit = True
        else:
            out.append(True)
    return out


def config_label(key: str) -> str | None:
    """The lock label that owns a config key ("L4 · Sentiment"), for Settings and Promote."""
    for s in STAGES:
        if key in s.config_keys and s.lock is not None:
            return s.label
    return None


def funnel_stages(by_code: dict[str, int]) -> list[dict]:
    """The dashboard funnel, in evaluation order, counted per stored code (computed here, rendered
    as-is by the frontend). Legacy codes are left out; anything unknown is listed under its code."""
    rows = [{"code": s.code, "label": s.label, "kind": s.kind, "lock": s.lock, "count": by_code.get(s.code, 0)}
            for s in STAGES if s.kind != "legacy"]
    rows += [{"code": c, "label": c, "kind": "unknown", "lock": None, "count": n}
             for c, n in sorted((c, n) for c, n in by_code.items() if c is not None) if c not in BY_CODE]
    return rows


def as_api() -> dict:
    """Served at /api/gate/locks and inside both gate-history responses: the chain, the Terminal's
    lock columns, and every stage in evaluation order with its badge and lock states."""
    return {
        "locks": [{"lock": n, "name": name, "reads": reads, "module": module}
                  for n, (name, reads, module) in sorted(LOCKS.items())],
        "columns": [{"code": s.code, "lock": s.lock, "head": f"L{s.lock}", "label": s.label} for s in CHAIN],
        "stages": [{"code": s.code, "kind": s.kind, "label": s.label, "lock": s.lock, "badge": badge(s),
                    "states": lock_states(s.code), "exit_lock": s.exit_lock, "cooloff": s.cooloff,
                    "config_keys": list(s.config_keys)}
                   for s in STAGES],
        "config_labels": {k: s.label for s in STAGES if s.lock is not None for k in s.config_keys},
    }
