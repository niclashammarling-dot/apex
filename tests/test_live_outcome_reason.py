"""
live_gate_history.outcome_reason / cap_check (2026-09-27).

TRADE_REJECTED covered three causes and TRADE_FAILED any broker error; only
the log said which, and logs are kept 14 days — the 13 rejections from
2026-05-25..08-10 can never be attributed. These tests assert the reason on
the row written to the DB (the insert mock's argument, not the return value),
and the observe-only cap evidence the two-caps decision reads.
"""
import json
from contextlib import ExitStack
from unittest.mock import patch

from backend import db
from tests.test_gate_runners import _account, _live_patches, _signal


def _run(caps=None, extra=(), **kwargs):
    patches = _live_patches(**kwargs)
    with ExitStack() as stack:
        mocks = [stack.enter_context(p) for p in patches]
        stack.enter_context(patch("backend.gate.gate_runner_live.compute_dynamic_caps",
                                  return_value=caps or {}))
        for p in extra:
            stack.enter_context(p)
        from backend.gate import gate_runner_live
        results = gate_runner_live.run()
    return results, mocks[9].call_args[0][0]


def test_position_open_reason():
    results, saved = _run(candidates=[_signal("NVDA")],
                          positions=[{"ticker": "NVDA", "cost_basis": 200.0}])
    assert saved["gate_decision"] == "TRADE_REJECTED"
    assert saved["outcome_reason"] == "position_open"
    assert saved["cap_check"] is None


def test_notional_too_small_reason():
    results, saved = _run(candidates=[_signal()], account=_account(equity=1.0, buying_power=1.0))
    assert saved["gate_decision"] == "TRADE_REJECTED"
    assert saved["outcome_reason"] == "notional_too_small"


def test_sector_cap_reason_and_evidence():
    # notional = 10000 × 0.10 = 1000 → projected 0.10 of a 10000 book; dynamic cap 0.05
    results, saved = _run(candidates=[_signal()], caps={"Technology": 0.05})
    assert saved["gate_decision"] == "TRADE_REJECTED"
    assert saved["outcome_reason"] == "sector_cap"
    cc = saved["cap_check"]
    assert cc["source"] == "dynamic" and cc["cap"] == 0.05
    assert cc["projected"] == 0.10 and cc["over_cap"] is True
    # the flat cap (live cfg default 0.30) would have allowed it — the disagreement is recorded
    assert cc["over_flat"] is False


def test_executed_trade_records_cap_check_with_flat_fallback():
    results, saved = _run(candidates=[_signal()], caps={})
    assert saved["gate_decision"] == "TRADE_EXECUTED"
    assert saved["outcome_reason"] is None
    assert saved["cap_check"]["source"] == "flat_fallback"
    assert saved["cap_check"]["over_cap"] is False


def test_broker_error_reason():
    boom = patch("backend.brokers.alpaca.place_bracket_order", side_effect=Exception("insufficient buying power"))
    results, saved = _run(candidates=[_signal()], extra=[boom])
    assert saved["gate_decision"] == "TRADE_FAILED"
    assert saved["outcome_reason"] == "broker_error: insufficient buying power"


def test_rejections_endpoint_round_trip():
    from datetime import datetime, timezone

    from backend.routers.signals_router import get_live_rejections

    ts = datetime.now(timezone.utc).isoformat()
    base = {"timestamp": ts, "sector": "Technology", "signal_score": 0.6,
            "lock1_pass": 1, "lock2_pass": 1, "lock3_pass": 1,
            "lock3_reasoning": None, "alpaca_order_id": None}
    cc = {"source": "dynamic", "cap": 0.05, "flat": 0.30, "before": 0.0,
          "projected": 0.10, "over_cap": True, "over_flat": False}
    db.insert_live_gate_result({**base, "ticker": "ZZA", "gate_decision": "TRADE_REJECTED",
                                "outcome_reason": "sector_cap", "cap_check": cc})
    # pre-2026-09-27 shape: no reason
    db.insert_live_gate_result({**base, "ticker": "ZZB", "gate_decision": "TRADE_REJECTED"})

    out = get_live_rejections(days=1)
    reasons = out["days"][0]["reasons"]
    assert reasons.get("sector_cap", 0) >= 1 and reasons.get("unattributed", 0) >= 1
    row = next(c for c in out["caps"] if c["ticker"] == "ZZA")
    assert row["over_cap"] is True and row["over_flat"] is False
    assert out["summary"]["dynamic_rejects_flat_allows"] >= 1
    stored = json.loads(db.get_db().execute(
        "SELECT cap_check FROM live_gate_history WHERE ticker='ZZA'").fetchone()[0])
    assert stored == cc
