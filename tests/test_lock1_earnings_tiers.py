"""
Tests for the Lock 1 three-tier earnings gate and chain.py near-term penalty.

Tiers:
  imminent   (0 <= delta <= blackout_days)  -> hard fail
  near-term  (blackout < delta <= near_days) -> pass + earnings_data in l1.data
  clear      (delta > near_days)             -> pass, no earnings_data
  post-event (delta < 0)                     -> pass, never blocked
"""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

import backend.gate.lock1_eligibility as m
from backend.gate.lock1_eligibility import evaluate


# ── Shared regime mock ─────────────────────────────────────────────────────────

def _make_regime(sector: str = "Technology"):
    entry = MagicMock()
    entry.sector        = sector
    entry.adjusted_score = 0.60
    entry.allocation    = 0.25
    entry.rank          = 1

    result = MagicMock()
    result.leaderboard  = [entry]
    result.leader       = sector
    result.date         = date.today().isoformat()
    return result


def _cfg(blackout: int = 3, near_days: int = 14, near_penalty: float = 0.15) -> dict:
    return {
        "vix_threshold":                 99.0,   # VIX never blocks
        "macro_event_blackout_days":     0,       # macro events never block
        "macro_earnings_blackout_days":  blackout,
        "macro_earnings_near_days":      near_days,
        "macro_earnings_near_penalty":   near_penalty,
        "max_positions":                 4,
        "max_sector_exposure":           0.25,
        "max_position_size":             0.15,
        "daily_loss_cap":                100.0,
    }


def _evaluate_with_earnings(ticker: str, delta_days: int, **cfg_overrides) -> "LockResult":
    """Run Lock 1 evaluate() with a synthetic earnings date at today + delta_days."""
    today    = date.today()
    earnings = today + timedelta(days=delta_days)
    regime   = _make_regime()
    cfg      = _cfg(**cfg_overrides)

    with (
        patch.object(m, "_get_regime_result", return_value=regime),
        patch.object(m, "_get_vix",           return_value=10.0),
        patch.object(m, "_days_to_nearest_event", return_value=(False, None)),
        patch.object(m, "_get_earnings_date", return_value=earnings),
    ):
        return evaluate(ticker, "Technology", cfg)


# ── Imminent: hard block ───────────────────────────────────────────────────────

def test_earnings_imminent_day0_fails():
    r = _evaluate_with_earnings("AAPL", delta_days=0)
    assert not r.passed
    assert r.data.get("fail_type") == "earnings"
    assert r.data.get("days_to_earnings") == 0


def test_earnings_imminent_at_blackout_fails():
    r = _evaluate_with_earnings("AAPL", delta_days=3, blackout=3)
    assert not r.passed
    assert r.data.get("fail_type") == "earnings"
    assert r.data.get("days_to_earnings") == 3


def test_earnings_just_past_blackout_is_near_term():
    r = _evaluate_with_earnings("AAPL", delta_days=4, blackout=3, near_days=14)
    assert r.passed
    assert r.data.get("earnings_near") is True
    assert r.data.get("days_to_earnings") == 4
    assert r.data.get("earnings_near_penalty") == pytest.approx(0.15)


# ── Near-term: passes with penalty data ───────────────────────────────────────

def test_earnings_near_term_mid_window():
    r = _evaluate_with_earnings("MSFT", delta_days=8, blackout=3, near_days=14)
    assert r.passed
    assert r.data.get("earnings_near") is True
    assert r.data.get("days_to_earnings") == 8
    assert r.data.get("earnings_near_penalty") == pytest.approx(0.15)


def test_earnings_near_term_at_boundary():
    r = _evaluate_with_earnings("MSFT", delta_days=14, blackout=3, near_days=14)
    assert r.passed
    assert r.data.get("earnings_near") is True


# ── Clear: passes with no earnings penalty data ────────────────────────────────

def test_earnings_clear_no_penalty_data():
    r = _evaluate_with_earnings("NVDA", delta_days=20, blackout=3, near_days=14)
    assert r.passed
    assert not r.data.get("earnings_near")
    assert "earnings_near_penalty" not in r.data


# ── Post-event: never blocked ──────────────────────────────────────────────────

def test_earnings_post_event_not_blocked():
    r = _evaluate_with_earnings("AMD", delta_days=-1)
    assert r.passed
    assert not r.data.get("earnings_near")


def test_earnings_post_event_far_past_not_blocked():
    r = _evaluate_with_earnings("AMD", delta_days=-30)
    assert r.passed


# ── No earnings date: passes cleanly ──────────────────────────────────────────

def test_no_earnings_date_passes():
    regime = _make_regime()
    with (
        patch.object(m, "_get_regime_result", return_value=regime),
        patch.object(m, "_get_vix",           return_value=10.0),
        patch.object(m, "_days_to_nearest_event", return_value=(False, None)),
        patch.object(m, "_get_earnings_date", return_value=None),
    ):
        r = evaluate("SPY", "Technology", _cfg())
    assert r.passed
    assert not r.data.get("earnings_near")


# ── Chain: near-term penalty applied to signal_score ──────────────────────────

def test_chain_applies_near_term_penalty():
    """
    If L1 passes with earnings_near_penalty=0.15 and signal_score=0.80,
    L2 should receive 0.80 * 0.85 = 0.68.
    """
    from backend.gate.chain import evaluate_chain
    from backend.gate.types import LockResult

    l1_result = LockResult(
        passed=True, lock_id=1, score=1.0,
        reason="pass", data={
            "earnings_near":         True,
            "earnings_near_penalty": 0.15,
            "days_to_earnings":      8,
        }
    )

    captured = {}

    def fake_lock2(ticker, sector, score, **kwargs):
        captured["score"] = score
        return LockResult(passed=False, lock_id=2, score=score, reason="test stop", data={})

    with (
        patch("backend.gate.chain.lock1_evaluate", return_value=l1_result),
        patch("backend.gate.chain.lock2_evaluate", side_effect=fake_lock2),
    ):
        evaluate_chain("AAPL", "Technology", signal_score=0.80, context={}, cfg=_cfg())

    assert captured["score"] == pytest.approx(0.80 * 0.85, rel=1e-6)


def test_chain_no_penalty_when_clear():
    from backend.gate.chain import evaluate_chain
    from backend.gate.types import LockResult

    l1_result = LockResult(
        passed=True, lock_id=1, score=1.0,
        reason="pass", data={}
    )

    captured = {}

    def fake_lock2(ticker, sector, score, **kwargs):
        captured["score"] = score
        return LockResult(passed=False, lock_id=2, score=score, reason="test stop", data={})

    with (
        patch("backend.gate.chain.lock1_evaluate", return_value=l1_result),
        patch("backend.gate.chain.lock2_evaluate", side_effect=fake_lock2),
    ):
        evaluate_chain("NVDA", "Technology", signal_score=0.80, context={}, cfg=_cfg())

    assert captured["score"] == pytest.approx(0.80, rel=1e-6)
