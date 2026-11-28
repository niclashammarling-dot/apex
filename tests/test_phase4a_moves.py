"""Lock labels phase 4a (2026-11-28; vault raw/notes/2026-11/2026-11-14-apex-lock-labels-phase4-scope.md, R068 a):
the moves where no entry can change. Each move is verified by scripts/identity_replay.py against the parent (0
differences, shadow mode on and off for M2); these tests pin each move's new place and the row it writes."""
from contextlib import ExitStack
from unittest.mock import patch

from backend.gate import chain, gate_runner, l2_risk, lock_codes
from backend.gate.lock_codes import FAIL, PASS
from backend.gate.types import LockResult

CFG = {"l3_score_threshold_min": 0.5, "l4_sentiment_grok_min": 0.0, "l5_positioning_leading_mode": "pooled_scalar",
       "l2_risk_etf_penalty": 0.0}


def _pass(lock_id, data=None):
    return LockResult.pass_(lock_id=lock_id, score=0.9, reason="pass", data=data or {})


def _run(l1, earnings, order=None):
    order = order if order is not None else []
    with ExitStack() as st:
        st.enter_context(patch("backend.gate.chain.l1_eligibility_evaluate",
                               side_effect=lambda *a, **k: (order.append("L1"), l1)[1]))
        st.enter_context(patch.object(l2_risk, "earnings_lock",
                                      side_effect=lambda *a, **k: (order.append("L2 earnings"), earnings)[1]))
        st.enter_context(patch("backend.gate.chain.l3_score_evaluate",
                               side_effect=lambda *a, **k: (order.append("L3"), LockResult.fail(lock_id=3, reason="stop here",
                                                                                                data={"effective_threshold": 0.5}))[1]))
        return chain.evaluate_chain("AAPL", "Technology", 0.40, {}, CFG)


# ── M2: the earnings rules are L2 RISK's own step, right after L1 ───────────────────────────────────────────────

def test_m2_earnings_step_runs_after_l1_and_before_l3():
    order: list = []
    _run(_pass(1, {"rule_states": {"regime": PASS}}), (_pass(2), PASS), order)
    assert order == ["L1", "L2 earnings", "L3"]


def test_m2_earnings_step_is_not_called_when_l1_stops():
    order: list = []
    l1 = LockResult.fail(lock_id=1, reason="VIX", data={"fail_type": "vix", "rule_states": {"regime": PASS, "vix": FAIL}})
    cr = _run(l1, (_pass(2), PASS), order)
    assert order == ["L1"] and cr.stop == (1, "vix")
    assert lock_codes.rule_key(2, "earnings") not in cr.rule_states      # recorded only when it ran, as before


def test_m2_an_earnings_stop_is_l2_risks_and_keeps_the_rows_eligibility_reason():
    stop = LockResult.fail(lock_id=2, reason="Earnings on 2026-11-29 (Δ1d)",
                           data={"fail_type": "earnings", "days_to_earnings": 1, "earnings_date": "2026-11-29"})
    order: list = []
    cr = _run(_pass(1, {"rule_states": {"regime": PASS}}), (stop, FAIL), order)
    assert order == ["L1", "L2 earnings"] and cr.stop == (2, "earnings")
    assert cr.lock_results[2] is stop
    row = gate_runner._chain_to_gate_result({"id": 1, "ticker": "AAPL", "signal_score": 0.4}, cr)
    # the row as when the rule ran inside L1: the reason in l1_eligibility_reason, no earnings fields on a stop
    assert row["l1_eligibility_reason"] == "Earnings on 2026-11-29 (Δ1d)"
    assert row["earnings_near"] is None and row["days_to_earnings"] is None


def test_m2_near_window_data_reaches_the_score_and_the_row():
    near = _pass(2, {"earnings_near": True, "earnings_near_penalty": 0.15, "days_to_earnings": 8})
    seen = {}
    with ExitStack() as st:
        st.enter_context(patch("backend.gate.chain.l1_eligibility_evaluate", return_value=_pass(1)))
        st.enter_context(patch.object(l2_risk, "earnings_lock", return_value=(near, PASS)))
        st.enter_context(patch("backend.gate.chain.l3_score_evaluate",
                               side_effect=lambda t, s, score, **k: (seen.setdefault("score", score),
                                                                      LockResult.fail(lock_id=3, reason="x",
                                                                                      data={"effective_threshold": 0.7}))[1]))
        cr = chain.evaluate_chain("AAPL", "Technology", 0.80, {}, CFG)
    assert abs(seen["score"] - 0.80 * 0.85) < 1e-9
    assert cr.stop == (2, "earnings_near")                                # the penalty decided the threshold stop
    row = gate_runner._chain_to_gate_result({"id": 1, "ticker": "AAPL", "signal_score": 0.8}, cr)
    assert row["earnings_near"] == 1 and row["days_to_earnings"] == 8


def test_m2_earnings_lock_wraps_check_earnings():
    with patch.object(l2_risk, "check_earnings",
                      return_value=({"reason": "Earnings soon", "fail_type": "earnings", "days_to_earnings": 0}, {}, FAIL)):
        r, state = l2_risk.earnings_lock("AAPL", {}, None)
    assert not r.passed and r.lock_id == 2 and r.reason == "Earnings soon" and state == FAIL
    assert "reason" not in r.data and r.data["fail_type"] == "earnings"
    with patch.object(l2_risk, "check_earnings", return_value=(None, {"earnings_shadow": {"days_to_earnings": 3}}, PASS)):
        r, state = l2_risk.earnings_lock("AAPL", {}, None)
    assert r.passed and r.data == {"earnings_shadow": {"days_to_earnings": 3}} and state == PASS
