"""
gate/types.py — Shared types for the apex gate lock chain.

All lock modules return a LockResult. The chain.py runner reads
passed, score, reason and data uniformly across all five locks.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LockResult:
    """
    Standardised return type for every lock in the gate chain.

    Attributes:
        passed  — True if the ticker cleared this lock.
        score   — Numeric confidence / strength (0.0–1.0).
                  Meaning is lock-specific:
                    Lock 1  — 1.0 (pass) / 0.0 (fail), binary
                    Lock 2  — signal_score from aggregator
                    Lock 3  — Grok sentiment score (-1 to 1, normalised)
                    Lock 4  — fraction of sub-checks passed (0–1)
                    Lock 5  — Claude confidence (0–1, extracted from response)
        reason  — Human-readable explanation, used in gate_history and
                  Lock 5 context building.
        data    — Lock-specific payload. Downstream locks and the
                  Claude context builder read from this dict.
                  Keys are lock-specific (see each lock module).
        lock_id — Lock number (1–5), set automatically by each module.
    """
    passed:  bool
    score:   float
    reason:  str
    data:    dict[str, Any] = field(default_factory=dict)
    lock_id: int = 0

    # ── Convenience ──────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Serialise to plain dict for DB writes and logging."""
        return {
            "lock_id": self.lock_id,
            "passed":  self.passed,
            "score":   round(self.score, 4),
            "reason":  self.reason,
            "data":    self.data,
        }

    @classmethod
    def fail(cls, lock_id: int, reason: str, data: dict | None = None) -> "LockResult":
        """Convenience constructor for a failed lock."""
        return cls(passed=False, score=0.0, reason=reason,
                   data=data or {}, lock_id=lock_id)

    @classmethod
    def pass_(cls, lock_id: int, score: float, reason: str,
              data: dict | None = None) -> "LockResult":
        """Convenience constructor for a passed lock."""
        return cls(passed=True, score=score, reason=reason,
                   data=data or {}, lock_id=lock_id)
