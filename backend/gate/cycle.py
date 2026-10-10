"""
One gate cycle, recorded whether or not it writes a gate row (2026-10-04).

Coverage counted cycles from gate rows, and a cycle with no Lock 1 candidates,
every candidate skipped, or a live halt writes none: quiet days read as
outages (CHECK 80, 85), a quiet second scheduler left no fingerprint (CHECK
83). Every cycle now goes through run_recorded(), from the scheduler and from
the manual /gate/run endpoints alike, and gets a gate_cycles row with its
trigger, so readers count scheduler cycles only.

The runner receives the GateCycle: its started_at is the cycle_started_at on
every gate row the cycle writes (one clock for both sources, so a cycle cannot
count twice), and the runner sets .reason at each exit. run()'s return value
is unchanged — the manual endpoints and tests consume it.

Reasons: no_candidates, no_fresh_candidates, all_skipped, all_excluded, evaluated,
evaluated_some_raised, all_raised; live adds disabled, halt_unreconciled,
broker_unreachable, account_blocked, halt_data_quality, loss_cap.
A runner that exits without setting one records None.

stale_excluded (Finding 1, 2026-10-10): distinct tickers refused by the session
bound this cycle (get_lock1_candidates stale_out), main and pre-rotation
candidates together. no_fresh_candidates = every would-be candidate was refused.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from loguru import logger


@dataclass
class GateCycle:
    job: str
    trigger: str
    started_at: str
    reason: str | None = None
    stale_excluded: int | None = None

    @classmethod
    def unrecorded(cls) -> "GateCycle":
        """A runner called directly (tests): same clock, no row."""
        return cls("unrecorded", "direct", datetime.now(timezone.utc).isoformat())


def evaluated_reason(n_candidates: int, n_evaluated: int) -> str:
    if n_evaluated == 0:
        return "all_raised"
    return "evaluated" if n_evaluated == n_candidates else "evaluated_some_raised"


def run_recorded(job: str, trigger: str, run: Callable[..., list]) -> list:
    """Run one gate cycle between gate_cycles stamps; returns run()'s results.

    A stamp failure is logged and never stops the cycle; the cycle's own
    exception is recorded as the outcome and re-raised.
    """
    from backend.db import finish_gate_cycle, insert_gate_cycle

    cycle = GateCycle(job, trigger, datetime.now(timezone.utc).isoformat())
    cycle_id = None
    try:
        cycle_id = insert_gate_cycle(job, trigger, cycle.started_at)
    except Exception as e:
        logger.warning(f"gate_cycles start failed for {job}: {e!r}")

    def _finish(outcome: str) -> None:
        if cycle_id is None:
            return
        try:
            finish_gate_cycle(cycle_id, outcome, cycle.reason, cycle.stale_excluded)
        except Exception as e:
            logger.warning(f"gate_cycles finish failed for {job}: {e!r}")

    try:
        results = run(cycle=cycle)
    except Exception as e:
        _finish(f"error: {e!r}"[:300])
        raise
    _finish("ok")
    return results


def record_stale(cycle: GateCycle, stale: list[dict], runner: str, start: int = 0) -> None:
    """Count the cycle's refused tickers and log stale[start:] with their reasons.
    A refusal for staleness logs its own label, never silence (Finding 1)."""
    cycle.stale_excluded = len({s["ticker"] for s in stale})
    new = stale[start:]
    if not new:
        return
    by_reason: dict[str, set[str]] = {}
    for s in new:
        by_reason.setdefault(s["reason"], set()).add(s["ticker"])
    detail = "; ".join(f"{r}: {', '.join(sorted(t))}" for r, t in sorted(by_reason.items()))
    logger.info(f"{runner}: skipped-for-staleness {len({s['ticker'] for s in new})} "
                f"candidate(s) — {detail}")
