"""
Lock labels phase 2, step 1: the history classifier (backend/gate/history_classify.py).

One test per class, each built from a real stored row of that class (fields copied from data/apex.db
2026-10-28: table, id, timestamp, ticker, code and the deciding fields; long texts cut). Plus: a row that
matches no class raises, and every stop_code starts with its code (ruling B). The class counts on all
three tables are the dry run's positive control (scope step 2), not these tests.
"""
import pytest

from backend.gate import lock_codes
from backend.gate.history_classify import TABLES, UnclassifiedRow, classify

LIVE, DEMO = "live_gate_history", "demo_gate_history"

# (table, row, stop_code, basis kind)
CASES = [
    (LIVE, {"id": 501, "timestamp": "2026-04-20T13:32:33.762794+00:00", "ticker": "UNH",
            "gate_decision": "FILTERED_ELIGIBILITY", "signal_score": 0.6824,
            "macro_reason": "Sector 'Healthcare' not found in regime leaderboard"},
     "FILTERED_L1_ELIGIBILITY_REGIME", "field"),
    (LIVE, {"id": 22167, "timestamp": "2026-10-27T19:53:47.194580+00:00", "ticker": "SPOT",
            "gate_decision": "FILTERED_ELIGIBILITY", "signal_score": 0.5947,
            "macro_reason": "Sector 'Communication' has no allocation — adjusted score 0.017 below hysteresis "
                            "band (entry ≥ 0.37, exit < 0.33)"},
     "FILTERED_L1_ELIGIBILITY_REGIME", "field"),
    (LIVE, {"id": 1208, "timestamp": "2026-05-27T16:21:37.645237+00:00", "ticker": "MU",
            "gate_decision": "FILTERED_ELIGIBILITY", "signal_score": 0.67, "macro_reason": "VIX=318.9 >= threshold 30"},
     "FILTERED_L1_ELIGIBILITY_VIX", "field"),
    (LIVE, {"id": 2425, "timestamp": "2026-06-04T13:48:08.220631+00:00", "ticker": "NOW",
            "gate_decision": "FILTERED_ELIGIBILITY", "signal_score": 0.712,
            "macro_reason": "Macro event blackout: NFP on 2026-06-05 (in 1d)"},
     "FILTERED_L1_ELIGIBILITY_MACRO_EVENT", "field"),
    (LIVE, {"id": 40, "timestamp": "2026-04-07T18:34:56.095572+00:00", "ticker": "UNH",
            "gate_decision": "FILTERED_MACRO", "signal_score": 0.5896,
            "macro_reason": "macro event: CPI on 2026-04-08 (Δ1d)"},
     "FILTERED_L1_ELIGIBILITY_MACRO_EVENT", "field"),
    (LIVE, {"id": 13309, "timestamp": "2026-09-03T19:11:02.423910+00:00", "ticker": "TMO",
            "gate_decision": "FILTERED_L1", "signal_score": 0.6049,
            "lock3_reasoning": "Signal score 0.5747 below threshold 0.5892"},
     "FILTERED_L1_ELIGIBILITY_MACRO_PRE_EVENT", "field"),
    (LIVE, {"id": 5820, "timestamp": "2026-07-02T14:22:15.374010+00:00", "ticker": "BA",
            "gate_decision": "FILTERED_L1", "signal_score": 0.6245, "lock3_reasoning": None},
     "FILTERED_L1_ELIGIBILITY_MACRO_PRE_EVENT", "inferred"),
    (LIVE, {"id": 502, "timestamp": "2026-04-20T13:32:31.533555+00:00", "ticker": "MSFT",
            "gate_decision": "FILTERED_L1", "signal_score": 0.6608, "lock3_reasoning": None},
     "FILTERED_L3_SCORE_THRESHOLD", "field"),
    (LIVE, {"id": 15, "timestamp": "2026-04-01T13:39:46.047508+00:00", "ticker": "XOM",
            "gate_decision": "FILTERED_L2", "signal_score": 0.6023, "lock3_reasoning": None,
            "l2_summary": "Social sentiment on X/Twitter about $XOM is negative, driven by news of falling "},
     "FILTERED_L4_SENTIMENT_GROK", "field"),
    (DEMO, {"id": 18380, "timestamp": "2026-10-09T19:02:05.527206+00:00", "ticker": "MRNA",
            "gate_decision": "FILTERED_L2", "signal_score": 0.8061,
            "lock3_reasoning": "Grok API unavailable — failing closed", "l2_summary": None},
     "FILTERED_L4_SENTIMENT_UNAVAILABLE", "field"),
    (LIVE, {"id": 4, "timestamp": "2026-03-30T16:18:49.227141+00:00", "ticker": "CVX",
            "gate_decision": "FILTERED_L2", "signal_score": 0.6106, "lock3_reasoning": None, "l2_summary": None},
     "FILTERED_L4_SENTIMENT_UNAVAILABLE_INFERRED", "inferred"),
    (LIVE, {"id": 2, "timestamp": "2026-03-30T15:22:31.705322+00:00", "ticker": "XOM",
            "gate_decision": "FILTERED_LEADING", "signal_score": 0.6213, "lock_leading_pass": 0},
     "FILTERED_L5_POSITIONING_MIN_PASS", "field"),
    (LIVE, {"id": 7025, "timestamp": "2026-07-13T15:01:55.060627+00:00", "ticker": "AAPL",
            "gate_decision": "FILTERED_LEADING", "signal_score": 0.6505, "lock_leading_pass": 1},
     "FILTERED_L2_RISK_ETF", "field"),
    (LIVE, {"id": 7732, "timestamp": "2026-07-16T17:44:52.650492+00:00", "ticker": "JNJ",
            "gate_decision": "FILTERED_ETF_PENALTY", "signal_score": 0.5177, "lock_leading_pass": 1},
     "FILTERED_L2_RISK_ETF", "field"),
    (LIVE, {"id": 6495, "timestamp": "2026-07-07T13:42:33.982915+00:00", "ticker": "ISRG",
            "gate_decision": "FILTERED_OVERFLOW_QUANT", "signal_score": 0.6031, "lock3_reasoning": None},
     "FILTERED_L2_RISK_OVERFLOW", "field"),
    (LIVE, {"id": 249, "timestamp": "2026-04-10T14:16:03.697645+00:00", "ticker": "AMZN",
            "gate_decision": "FILTERED_L3", "signal_score": 0.6119,
            "lock3_reasoning": "Exceeded risk limits with 5 open positions against max of 4. Despite strong "
                               "sentiment, portfolio risk constraints take precedence."},
     "FILTERED_L6_REVIEW_CLAUDE", "field"),
    (LIVE, {"id": 14875, "timestamp": "2026-09-21T14:55:01.636553+00:00", "ticker": "ANET",
            "gate_decision": "FILTERED_L3", "signal_score": 0.6726,
            "lock3_reasoning": "anthropic_unavailable: credit_exhausted"},
     "FILTERED_L6_REVIEW_UNAVAILABLE", "field"),
    (LIVE, {"id": 14791, "timestamp": "2026-09-18T13:37:25.810210+00:00", "ticker": "ANET",
            "gate_decision": "FILTERED_L3", "signal_score": 0.669,
            "lock3_reasoning": "anthropic_unavailable: credit_exhausted [backfilled 2026-09-18 by inference from "
                               "logs/apex_2026-09-18.log req_011CfAzhg3AY6eDZp6Yry5FA / req_011CfB6oDf2jScnypmUDGWDM; "
                               "not observed under the new code path]"},
     "FILTERED_L6_REVIEW_UNAVAILABLE", "inferred"),
    (LIVE, {"id": 2392, "timestamp": "2026-06-03T19:28:17.084511+00:00", "ticker": "AMD",
            "gate_decision": "FILTERED_L3", "signal_score": 0.8543, "lock3_reasoning": None},
     "FILTERED_L6_REVIEW_UNAVAILABLE_INFERRED", "inferred"),
    (DEMO, {"id": 7439, "timestamp": "2026-07-17T13:50:12.156732+00:00", "ticker": "CVX",
            "gate_decision": "FILTERED_L3", "signal_score": 0.7684,
            "lock3_reasoning": "drawdown 25.1% > max 20.0% (mechanical pre-L5 guard)"},
     "FILTERED_L0_PORTFOLIO_DRAWDOWN_GUARD", "field"),
    (LIVE, {"id": 1038, "timestamp": "2026-05-25T14:54:17.161681+00:00", "ticker": "LLY",
            "gate_decision": "TRADE_REJECTED", "signal_score": 0.6855, "outcome_reason": None,
            "lock3_reasoning": "All four locks passed. Portfolio is empty with minimal drawdown (~0.95%)"},
     "TRADE_REJECTED_REASON_UNRECORDED", "unattributable"),
    (LIVE, {"id": 1, "timestamp": "2026-03-30T15:02:41.541658+00:00", "ticker": "XOM",
            "gate_decision": "TRADE_EXECUTED", "signal_score": 0.6155},
     "TRADE_EXECUTED", "field"),
    (LIVE, {"id": 18661, "timestamp": "2026-10-12T19:34:13.679935+00:00", "ticker": "LLY",
            "gate_decision": "TRADE_FAILED", "signal_score": 0.6495,
            "outcome_reason": "broker_error: Computed qty=0 for LLY @ $1181.11 notional=$844.99"},
     "TRADE_FAILED", "field"),
    (LIVE, {"id": 18, "timestamp": "2026-04-06T14:44:20.956321+00:00", "ticker": "EQIX",
            "gate_decision": "SKIPPED_OPEN", "signal_score": 0.551},
     "SKIPPED_OPEN", "field"),
    (LIVE, {"id": 6, "timestamp": "2026-03-30T16:38:44.491463+00:00", "ticker": "CVX",
            "gate_decision": "SKIPPED_COOLOFF", "signal_score": 0.6108},
     "SKIPPED_COOLOFF", "field"),
    (DEMO, {"id": 1276, "timestamp": "2026-01-01T00:00:00", "ticker": "_TEST",
            "gate_decision": "FILTERED_L1", "signal_score": 0.5},
     "REMOVE_TEST_ROW", "field"),
]


@pytest.mark.parametrize("table, row, stop_code, basis", CASES, ids=[f"{c[2]}-{c[3]}-{c[1]['id']}" for c in CASES])
def test_each_class_from_a_real_row(table, row, stop_code, basis):
    k = classify(table, row)
    assert (k.stop_code, k.basis.split(":")[0]) == (stop_code, basis)
    assert k.stop_code.startswith(k.code)                      # ruling B: the CHECK's invariant
    if k.basis != "field":
        assert ":" in k.basis and k.basis.split(":", 1)[1]     # ruling F: an inference names its why


def test_lock_codes_follow_the_chain_names():
    assert [lock_codes.lock_code(n) for n in range(7)] == [
        "FILTERED_L0_PORTFOLIO", "FILTERED_L1_ELIGIBILITY", "FILTERED_L2_RISK", "FILTERED_L3_SCORE",
        "FILTERED_L4_SENTIMENT", "FILTERED_L5_POSITIONING", "FILTERED_L6_REVIEW"]


@pytest.mark.parametrize("table, row", [
    (LIVE, {"id": 1, "timestamp": "2026-10-01T15:00:00+00:00", "gate_decision": "FILTERED_SOMETHING_NEW"}),
    (LIVE, {"id": 2, "timestamp": "2026-10-01T15:00:00+00:00", "gate_decision": "FILTERED_ELIGIBILITY",
            "macro_reason": "a wording no class knows"}),
    # A score rejection after the penalty shipped whose ratio is not the pre-event penalty (e.g. the earnings
    # penalty, which has never fired): not filed under macro by default.
    (LIVE, {"id": 3, "timestamp": "2026-10-01T15:00:00+00:00", "gate_decision": "FILTERED_L1",
            "signal_score": 0.70, "lock3_reasoning": "Signal score 0.5950 below threshold 0.6100"}),
    (LIVE, {"id": 4, "timestamp": "2026-10-01T15:00:00+00:00", "gate_decision": "FILTERED_L1",
            "signal_score": 0.70, "lock3_reasoning": None}),
    (LIVE, {"id": 5, "timestamp": "2026-10-01T15:00:00+00:00", "gate_decision": "FILTERED_LEADING",
            "lock_leading_pass": None}),
    (LIVE, {"id": 6, "timestamp": "2026-10-01T15:00:00+00:00", "gate_decision": "TRADE_REJECTED",
            "outcome_reason": "sector_cap"}),
    (LIVE, {"id": 7, "timestamp": "2026-10-01T15:00:00+00:00", "gate_decision": None}),
])
def test_a_row_matching_no_class_raises(table, row):
    with pytest.raises(UnclassifiedRow):
        classify(table, row)


def test_only_the_three_gate_tables():
    assert TABLES == ("live_gate_history", "demo_gate_history", "signals")
    with pytest.raises(ValueError):
        classify("live_trades", {"gate_decision": "TRADE_EXECUTED"})
