"""
gate/lock_codes.py — the one table of gate locks and gate_decision codes.

Lock labels phase 1 (vault raw/notes/2026-10/2026-10-24-apex-lock-labels-scope.md; chain approved by
Niclas 2026-10-24: "Kedjan ser bra ut. Godkänd."). Before this table five vocabularies named the same
locks: chain.py's exit_lock (the module numbers: exit_lock 2 = lock2_quant), the stored gate_decision
codes (one behind those: FILTERED_L1 = quant), the DB columns, the config keys, and the frontend labels
(CHECK 54, firing since 06-11). Every writer and reader of a code now imports it from here; the frontend
reads it from /api/gate/locks.

Two numberings until phase 3. "Lock 1".."Lock 5" in code and comments outside this file are the module
numbers (lock1_eligibility .. lock5_claude, chain.py's lock_id and exit_lock); "L0".."L6" and the names
below are the chain. Module N → chain: 1 → L1 ELIGIBILITY (+ L2's earnings rules), 2 → L3 SCORE,
3 → L4 SENTIMENT, 4 → L5 POSITIONING, 5 → L6 REVIEW (MODULE_TO_CHAIN). Phase 2 re-codes the stored
history to the chain, phase 3 renames the modules and keys (lock labels review R9, 2026-10-26: module
numbering is rename material, not a miss), after which one numbering remains.

Phase 1 changes no stored string. `code` is what the three gate tables hold today; phase 2 rewrites
history and this column in one migration. `lock` and `label` are the approved chain:

    L0 PORTFOLIO → L1 ELIGIBILITY → L2 RISK → L3 SCORE → L4 SENTIMENT → L5 POSITIONING → L6 REVIEW

What each lock owns is the approved chain's table (2026-10-24), amended by Niclas 2026-10-26 ("flytta
makro till l1": the macro calendar gives the same answer for every ticker, market state like VIX):
L1 market and sector state, VIX and the macro calendar; L2 ETF, earnings and concentration. Where the
code runs a lock's check in a different place than the chain names (phase 4 moves them), STAGES keeps
the evaluation order, so funnel "reached" counts stay true, and the stored code is the place it ran,
not the lock that owns it:
  - L1's CPI/NFP/FOMC blackout records FILTERED_ELIGIBILITY (its own lock); L1's pre-event penalty is
    applied in chain.py and bites at the score re-test, recorded FILTERED_L1;
  - L2 RISK's earnings hard block runs in lock1_eligibility.py (FILTERED_ELIGIBILITY) and its near-term
    penalty bites at the score re-test (FILTERED_L1); neither has fired in live (the earnings date never
    arrives, PRODUCTION_GAP rows 8-9); concentration is the sector cap (execution, and L6 REVIEW's
    context);
  - the ETF penalty is L2 RISK by ruling but runs after L5 POSITIONING (chain.py re-test);
  - the chain's 20 % drawdown guard records exit_lock 5, i.e. FILTERED_L3, the same code as an
    L6 REVIEW rejection. It is L0 PORTFOLIO's by ruling (Niclas 2026-10-26: "dd är före allt annat"):
    its key is labelled L0, phase 2 re-codes its rows, phase 4 moves the check before L1;
  - both runners defer L6 REVIEW to the serial execution phase (stop_after_lock=4), where the
    overflow check runs first: an overflow row never reached L6;
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
    1: ("ELIGIBILITY", "market and sector state, macro calendar",         "backend/gate/lock1_eligibility.py, chain.py"),
    2: ("RISK",        "ETF, earnings, concentration, overflow",          "backend/gate/lock1_eligibility.py, chain.py, gate_runner*.py"),
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
    # L2 RISK by ruling (Niclas 2026-10-26: "den hör till risk"): one more position beyond max_positions.
    # It runs in the execution phase, so it is not a CHAIN column; lock_states marks L2 failed for it.
    Stage(OVERFLOW, "execution", "L2 · Risk (overflow)", lock=2, config_keys=("overflow_quant_increment",)),
    Stage("FILTERED_L3", "filter", "L6 · Review", lock=6, exit_lock=5, cooloff=True,
          config_keys=("lock3_confidence_min", "max_drawdown_pct")),
    Stage("TRADE_EXECUTED", "trade", "Executed"),
    Stage("TRADE_REJECTED", "trade", "Rejected"),
    Stage("TRADE_FAILED", "trade", "Order failed"),
    Stage("FILTERED_MACRO", "legacy", "Macro (before 2026-04-10)"),
)

# Config keys a lock owns that no stored code carries (they act inside another stage's row, see above).
# The macro_earnings_* keys are earnings rules (L2) despite their prefix; macro_* without it are L1's calendar.
LOCK_KEYS: dict[int, tuple[str, ...]] = {
    0: ("max_drawdown_pct",),
    1: ("vix_threshold", "macro_event_blackout_days", "macro_pre_event_penalty"),
    2: ("macro_earnings_blackout_days", "macro_earnings_near_days", "macro_earnings_near_penalty",
        "max_sector_exposure"),
}

BY_CODE: dict[str, Stage] = {s.code: s for s in STAGES}
_INDEX: dict[str, int] = {s.code: i for i, s in enumerate(STAGES)}
OUTCOME_BY_EXIT_LOCK: dict[int, str] = {s.exit_lock: s.code for s in STAGES if s.exit_lock is not None}
# Module lock_id (lockN_*.py, chain.py exit_lock) → chain lock, until the phase 3 rename.
MODULE_TO_CHAIN: dict[int, int] = {s.exit_lock: s.lock for s in STAGES if s.exit_lock is not None}
COOLOFF_CODES: tuple[str, ...] = tuple(s.code for s in STAGES if s.cooloff)
SKIP_CODES: tuple[str, ...] = tuple(s.code for s in STAGES if s.kind == "skip")
REVIEW_REJECT = OUTCOME_BY_EXIT_LOCK[5]
REVIEW_LABEL = BY_CODE[REVIEW_REJECT].label


def outcome_for(exit_lock: int | None) -> str:
    """The code a chain rejection at `exit_lock` records (exit_label overrides live in chain.py)."""
    return OUTCOME_BY_EXIT_LOCK.get(exit_lock, UNKNOWN)


def sql_in(codes: tuple[str, ...]) -> str:
    """`'A', 'B'` for an IN (...) clause; codes are module constants, never user input."""
    return ", ".join(f"'{c}'" for c in codes)


# The per-candidate filters in the order a candidate meets them; the Terminal's lock columns and
# the weekly report's chained funnel read this. L6 REVIEW is last: the overflow check (deferred path,
# both runners) runs before it, so the reports split what follows L2 RISK instead of chaining it.
CHAIN: tuple[Stage, ...] = tuple(s for s in STAGES if s.kind == "filter")
CHAINED: tuple[Stage, ...] = tuple(s for s in CHAIN if s.lock != 6)

_BADGE = {SKIPPED_BLOCKLIST: "BLOCKED", SKIPPED_OPEN: "HELD", SKIPPED_COOLOFF: "COOLOFF",
          OVERFLOW: "L2 OVERFLOW", "TRADE_EXECUTED": "EXECUTED", "TRADE_REJECTED": "REJECTED",
          "TRADE_FAILED": "FAILED", "FILTERED_MACRO": "MACRO (OLD)"}


def badge(s: Stage) -> str:
    return f"L{s.lock} FAIL" if s.kind == "filter" else _BADGE[s.code]


def lock_states(code: str) -> list[bool | None]:
    """One entry per CHAIN column: passed (True), failed here (False), not reached or not known (None).
    From the evaluation order: every filter before the code's stage passed, its own failed, the rest
    were not reached (overflow stops before L6 REVIEW). An execution-phase rule that a lock owns fails
    that lock's column (overflow: L2 RISK). Skips reach no lock; a legacy or unknown code's path is not
    known."""
    s = BY_CODE.get(code)
    if s is None or s.kind in ("skip", "legacy"):
        return [None for _ in CHAIN]
    i = _INDEX[code]
    out = [True if _INDEX[c.code] < i else False if c.code == code else None for c in CHAIN]
    if s not in CHAIN and s.lock is not None:      # an execution-phase rule of a lock (overflow, L2)
        out = [False if c.lock == s.lock else v for c, v in zip(CHAIN, out)]
    return out


def row_states(code: str, lock3_pass: int | None) -> list[bool | None]:
    """lock_states for one stored row. A trade row passed L6 REVIEW only if Claude approved it
    (lock3_pass = 1): the pre-review "position already open" reject records TRADE_REJECTED with
    lock3_pass 0, and its L6 column is not reached."""
    out = lock_states(code)
    s = BY_CODE.get(code)
    if s is not None and s.kind == "trade" and lock3_pass != 1:
        out = [None if c.lock == 6 else v for c, v in zip(CHAIN, out)]
    return out


def reason_label(code: str) -> str | None:
    """Whose reason the row's lock3_reasoning column holds: the writers put the deciding stage's reason
    there for every filter code (gate_runner._chain_to_gate_result), and Claude's for a trade row. An
    overflow row stores none (0 of 22 since 2026-09-01: it is set in the execution phase, before L6), and
    skips have none, so both get no label."""
    s = BY_CODE.get(code)
    if s is None:
        return None
    if s.kind == "filter":
        return s.label
    if s.kind == "trade":
        return REVIEW_LABEL
    return None


def decorate_rows(rows: list[dict]) -> list[dict]:
    """Per-row lock states and reason label for the gate history endpoints (Terminal)."""
    for r in rows:
        code = r.get("gate_decision")
        r["lock_states"] = row_states(code, r.get("lock3_pass"))
        r["reason_label"] = reason_label(code)
    return rows


def lock_code(n: int) -> str:
    """"FILTERED_L2_RISK": the gate_decision code of chain lock n from phase 2 on (ruling B, 2026-10-26)."""
    return f"FILTERED_L{n}_{LOCKS[n][0]}"


def lock_label(n: int) -> str:
    """"L2 · Risk": the chain name of lock n, in the stage labels' form."""
    return f"L{n} · {LOCKS[n][0].capitalize()}"


def config_label(key: str) -> str | None:
    """The lock label that owns a config key ("L4 · Sentiment"), for Settings and Promote."""
    return config_labels().get(key)


def config_labels() -> dict[str, str]:
    out = {k: s.label for s in STAGES if s.lock is not None for k in s.config_keys}
    out.update({k: lock_label(n) for n, keys in LOCK_KEYS.items() for k in keys})
    return out


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
        "config_labels": config_labels(),
        "detail_labels": {"macro_reason": BY_CODE["FILTERED_ELIGIBILITY"].label,
                          "l2_summary": BY_CODE["FILTERED_L2"].label,
                          "lock_leading_checks": BY_CODE["FILTERED_LEADING"].label},
    }
