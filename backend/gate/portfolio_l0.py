"""
gate/portfolio_l0.py — L0 Portfolio: the M4 stop rules, evaluated once per cycle before L1 starts.

Ruled by Niclas 2026-10-24 ("m4: då sätter vi de tre reglerna i L0 som hindrar L1 att ens starta.
Kör på live redan nu"; chain L0 PORTFOLIO → L1 ELIGIBILITY → L2 RISK → L3 SCORE → L4 SENTIMENT →
L5 POSITIONING → L6 REVIEW approved the same day). M4 itself was ruled 2026-10-24 (vault INDEX
go-criterion line; apex audit/QUESTIONS.md G7/M4). The three rules are portfolio conditions: the
answer is the same for every ticker, so they stop the cycle instead of filtering candidates.

  1. Drawdown: realised equity (starting balance + closed P&L of the canonical live sample) has
     fallen M4_DRAWDOWN_PCT or more from its peak  → "halt_m4_drawdown".
  2. Daily loss cap as a share of current equity (M4_DAILY_LOSS_CAP_PCT × broker equity); replaces
     the fixed-dollar cap  → "loss_cap" (the reason the dashboard and CHECKs already read).
  3. Expectancy: once M4_EXPECTANCY_N in-session trades have closed after the reference was frozen,
     their mean return on notional below the reference's one-sided 95 % lower bound
     → "halt_m4_expectancy".

A halt holds until the condition clears (rules 1 and 3 do not clear on their own: realised equity
and closed trades only change by trading) — resetting is a human decision, by design.
"""
from __future__ import annotations

import math

from backend.config import (
    M4_DAILY_LOSS_CAP_PCT,
    M4_DRAWDOWN_PCT,
    M4_EXPECTANCY_N,
    M4_EXPECTANCY_REF,
)

HALT_DRAWDOWN = "halt_m4_drawdown"
HALT_LOSS_CAP = "loss_cap"
HALT_EXPECTANCY = "halt_m4_expectancy"
REASONS = (HALT_DRAWDOWN, HALT_LOSS_CAP, HALT_EXPECTANCY)


def _closed(rows: list[dict]) -> list[dict]:
    return sorted((r for r in rows if r.get("exit_price") is not None and r.get("exited_at")),
                  key=lambda r: r["exited_at"])


def realised_drawdown(rows: list[dict], starting_balance: float) -> dict:
    """Peak-to-current drawdown of realised equity (closed trades in exit order)."""
    eq = peak = float(starting_balance)
    for r in _closed(rows):
        eq += float(r.get("pnl") or 0.0)
        peak = max(peak, eq)
    dd = (peak - eq) / peak if peak > 0 else 0.0
    return {"equity": round(eq, 2), "peak": round(peak, 2), "drawdown": dd}


def daily_loss_cap(equity: float) -> float:
    return float(equity) * M4_DAILY_LOSS_CAP_PCT


def expectancy(rows: list[dict]) -> dict:
    """In-session trades closed after the frozen reference, their mean return on notional, and the bound."""
    ref = M4_EXPECTANCY_REF
    after = [r for r in _closed(rows) if r.get("signal_class") == "in_session"
             and r["exited_at"] > ref["frozen_after_exit"] and r.get("notional")]
    x = [float(r["pnl"] or 0.0) / float(r["notional"]) for r in after]
    bound = ref["mean"] - 1.645 * ref["sd"] / math.sqrt(ref["n"])
    mean = sum(x) / len(x) if x else None
    return {"n": len(x), "needed": M4_EXPECTANCY_N, "mean": mean, "lower_bound": bound,
            "breached": len(x) >= M4_EXPECTANCY_N and mean is not None and mean < bound}


def evaluate(acct: dict, rows: list[dict], starting_balance: float) -> dict:
    """The L0 verdict for one cycle: reason (None = pass), the effective dollar cap, and the evidence."""
    dd = realised_drawdown(rows, starting_balance)
    cap = daily_loss_cap(acct["equity"])
    day_loss = abs(min(float(acct["day_pnl"]), 0.0))
    exp = expectancy(rows)
    reason = None
    if dd["drawdown"] >= M4_DRAWDOWN_PCT:
        reason = HALT_DRAWDOWN
    elif day_loss >= cap:
        reason = HALT_LOSS_CAP
    elif exp["breached"]:
        reason = HALT_EXPECTANCY
    return {"reason": reason, "daily_loss_cap": round(cap, 2), "day_loss": round(day_loss, 2),
            "drawdown": dd, "expectancy": exp}


def rule_verdicts(v: dict) -> dict[str, str]:
    """Each M4 rule's verdict for the cycle, by its identifier (lock_codes.put maps it to L0's rule): every rule
    runs every cycle; expectancy is no_data until its sample reaches M4_EXPECTANCY_N (lock labels phase 2)."""
    from backend.gate.lock_codes import FAIL, NO_DATA, PASS
    exp = v["expectancy"]
    return {
        HALT_DRAWDOWN:   FAIL if v["drawdown"]["drawdown"] >= M4_DRAWDOWN_PCT else PASS,
        HALT_LOSS_CAP:   FAIL if v["day_loss"] >= v["daily_loss_cap"] else PASS,
        HALT_EXPECTANCY: FAIL if exp["breached"] else NO_DATA if exp["n"] < exp["needed"] else PASS,
    }
