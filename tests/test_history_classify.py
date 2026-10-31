"""
Lock labels phase 2, step 1: the history classifier (backend/gate/history_classify.py).

One test per class, each built from a real stored row of that class (fields copied from data/apex.db
2026-10-28: table, id, timestamp, ticker, code and the deciding fields; long texts cut). Plus: a row that
matches no class raises, and every stop_code starts with its code (ruling B). The class counts on all
three tables are the dry run's positive control (scope step 2), not these tests.

The rule register (lock_codes.RULES) is tested against the writers' own source: every fail_type and
outcome_reason they emit has exactly one rule, every rule's config key is labelled with the rule's lock, and
every stop_code parses back (Niclas 2026-10-28: "konsekvent och kan härledas överallt där det används").
"""
import json
import re
from pathlib import Path

import pytest

from backend.gate import lock_codes
from backend.gate.history_classify import (ELIGIBILITY_FAIL_TYPES, TABLES, CooloffIndex, UnclassifiedRow, classify,
                                          pass_flags)


class _GateCooloff:
    """The cooloff replay's answer for the real cooloff row below (CVX 2026-03-30: a gate stop, replayed on
    production 2026-10-29). The replay itself is tested on its own fixture (test_cooloff_index_*)."""
    def rule(self, table, row):
        return "cooloff", "inferred:gate cooloff at the row's time"


COOLOFF = _GateCooloff()

GATE = Path(__file__).resolve().parents[1] / "backend" / "gate"

LIVE, DEMO = "live_gate_history", "demo_gate_history"

# (table, row, stop_code, basis kind)
CASES = [
    (LIVE, {"id": 501, "timestamp": "2026-04-20T13:32:33.762794+00:00", "ticker": "UNH",
            "gate_decision": "FILTERED_ELIGIBILITY", "signal_score": 0.6824,
            "macro_reason": "Sector 'Healthcare' not found in regime leaderboard"},
     "FILTERED_L1_ELIGIBILITY_REGIME", "field"),
    (LIVE, {"id": 780, "timestamp": "2026-05-04T14:48:17.639096+00:00", "ticker": "AMZN",
            "gate_decision": "FILTERED_ELIGIBILITY", "signal_score": 0.6231,
            "macro_reason": "Sector 'ConsumerDisc' below leaderboard cutoff — only top 5 sectors by adjusted score "
                            "qualify (leaders: Technology, ConsumerStaples, Communication, Financials, RealEstate)"},
     "FILTERED_L1_ELIGIBILITY_REGIME", "field"),
    (LIVE, {"id": 971, "timestamp": "2026-05-15T17:48:11.796138+00:00", "ticker": "KO",
            "gate_decision": "FILTERED_ELIGIBILITY", "signal_score": 0.5851,
            "macro_reason": "Sector 'ConsumerStaples' excluded from entry: Edge only at low thresholds (0.55–0.60), "
                            "inverted vs gate direction; no viable operating-range threshold"},
     "FILTERED_L1_ELIGIBILITY_REGIME", "field"),
    (LIVE, {"id": 503, "timestamp": "2026-04-20T13:32:33.722558+00:00", "ticker": "AMZN",
            "gate_decision": "FILTERED_ELIGIBILITY", "signal_score": 0.6583,
            "macro_reason": "Sector 'ConsumerDisc' adjusted score 0.462 < allocation floor 0.50"},
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
     "FILTERED_L5_POSITIONING_LEADING", "field"),
    # Under the two-group rule a price check error decides the price group: a data error, not the signal.
    (LIVE, {"id": 8151, "timestamp": "2026-07-17T17:40:09.073808+00:00", "ticker": "TMO",
            "gate_decision": "FILTERED_LEADING", "signal_score": 0.7134, "lock_leading_pass": 0,
            "lock_leading_checks": json.dumps({
                "relative_strength": {"pass": False, "error": True, "reason": "insufficient price history for 5d RS"},
                "put_call_ratio": {"pass": False, "reason": "P/C 1.85 vs threshold 0.85 (neutral/bearish)"},
                "unusual_calls": {"pass": False, "reason": "call/put vol 0.4x (normal)"},
                "volume_accumulation": {"pass": True, "reason": "up/down vol ratio 2.17 over 20d (accumulation)"}})},
     "FILTERED_L5_POSITIONING_UNAVAILABLE", "field"),
    # Price group passed; the options fetch timed out, so the options group failed on a data error.
    (LIVE, {"id": 18410, "timestamp": "2026-10-12T13:54:56.106545+00:00", "ticker": "LLY",
            "gate_decision": "FILTERED_LEADING", "signal_score": 0.6231, "lock_leading_pass": 0,
            "lock3_reasoning": "options group failed — neither PCR nor unusual calls passed",
            "lock_leading_checks": json.dumps({
                "relative_strength": {"pass": True, "reason": "LLY +3.3% vs XLV +3.3% (5d)"},
                "put_call_ratio": {"pass": False, "reason": "no options data (timed out)"},
                "unusual_calls": {"pass": False, "reason": "no options data (timed out)"},
                "volume_accumulation": {"pass": True, "reason": "up/down vol ratio 1.86 over 20d (accumulation)"}})},
     "FILTERED_L5_POSITIONING_UNAVAILABLE", "field"),
    # The same day, before the restart that brought the two-group rule: 2 of 4, an error was one fewer pass.
    (LIVE, {"id": 2359, "timestamp": "2026-06-03T17:48:08.592928+00:00", "ticker": "LRCX",
            "gate_decision": "FILTERED_LEADING", "signal_score": 0.8364, "lock_leading_pass": 0,
            "lock_leading_checks": json.dumps({
                "relative_strength": {"pass": False, "reason": "LRCX +12.3% vs SOXX +14.4% (5d)"},
                "put_call_ratio": {"pass": False, "reason": "P/C 0.94 vs threshold 0.85 (neutral/bearish)"},
                "unusual_calls": {"pass": True, "reason": "call/put vol 3.0x (unusual)"},
                "volume_accumulation": {"pass": False, "error": True, "reason": "unexpected column format"}})},
     "FILTERED_L5_POSITIONING_LEADING", "field"),
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
     "FILTERED_UNKNOWN", "unattributable"),
    (LIVE, {"id": 1, "timestamp": "2026-03-30T15:02:41.541658+00:00", "ticker": "XOM",
            "gate_decision": "TRADE_EXECUTED", "signal_score": 0.6155},
     "TRADE_EXECUTED", "field"),
    (LIVE, {"id": 18661, "timestamp": "2026-10-12T19:34:13.679935+00:00", "ticker": "LLY",
            "gate_decision": "TRADE_FAILED", "signal_score": 0.6495,
            "outcome_reason": "broker_error: Computed qty=0 for LLY @ $1181.11 notional=$844.99"},
     "FILTERED_L1_ELIGIBILITY_NOTIONAL_TOO_SMALL", "field"),
    (LIVE, {"id": 18, "timestamp": "2026-04-06T14:44:20.956321+00:00", "ticker": "EQIX",
            "gate_decision": "SKIPPED_OPEN", "signal_score": 0.551},
     "FILTERED_L1_ELIGIBILITY_POSITION_OPEN", "field"),
    (LIVE, {"id": 6, "timestamp": "2026-03-30T16:38:44.491463+00:00", "ticker": "CVX",
            "gate_decision": "SKIPPED_COOLOFF", "signal_score": 0.6108},
     "FILTERED_L1_ELIGIBILITY_COOLOFF", "inferred"),
    (DEMO, {"id": 1276, "timestamp": "2026-01-01T00:00:00", "ticker": "_TEST",
            "gate_decision": "FILTERED_L1", "signal_score": 0.5},
     "REMOVE_TEST_ROW", "field"),
]

# Read by scripts/fixture_provenance.py: each row is compared field by field against the production row it
# names, read-only, before a migration (ruled 2026-10-29 after the TMO 13309 fixture carried invented flags).
PRODUCTION_ROWS = [(table, row) for table, row, *_ in CASES]


@pytest.mark.parametrize("table, row, stop_code, basis", CASES, ids=[f"{c[2]}-{c[3]}-{c[1]['id']}" for c in CASES])
def test_each_class_from_a_real_row(table, row, stop_code, basis):
    k = classify(table, row, COOLOFF)
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
            "outcome_reason": "a reason no rule knows"}),
    # A positioning row under the two-group rule must carry its checks: the rule is read from them.
    (LIVE, {"id": 8, "timestamp": "2026-10-01T15:00:00+00:00", "gate_decision": "FILTERED_LEADING",
            "lock_leading_pass": 0, "lock_leading_checks": None}),
    (LIVE, {"id": 9, "timestamp": "2026-10-01T15:00:00+00:00", "gate_decision": "FILTERED_ELIGIBILITY",
            "macro_reason": "Sector 'Energy' a wording after a lock1 change"}),
    (LIVE, {"id": 7, "timestamp": "2026-10-01T15:00:00+00:00", "gate_decision": None}),
])
def test_a_row_matching_no_class_raises(table, row):
    with pytest.raises(UnclassifiedRow):
        classify(table, row)


def test_only_the_three_gate_tables():
    assert TABLES == ("live_gate_history", "demo_gate_history", "signals")
    with pytest.raises(ValueError):
        classify("live_trades", {"gate_decision": "TRADE_EXECUTED"})


# ── The rule register ────────────────────────────────────────────────────────────────────────────────

def _emitted_literals(path: Path, pattern: str) -> set[str]:
    return set(re.findall(pattern, path.read_text()))


def test_every_fail_type_lock1_emits_has_one_rule_and_a_history_reading():
    emitted = _emitted_literals(GATE / "lock1_eligibility.py", r'"fail_type":\s*"(\w+)"')
    assert emitted >= {"vix", "macro_event", "earnings", "regime_floor"}       # the scan reads the source
    assert {lock_codes.rule_for(f).name for f in emitted} <= {r.name for r in lock_codes.RULES}
    assert {f for _, f in ELIGIBILITY_FAIL_TYPES} == emitted


def test_every_outcome_reason_live_emits_has_one_rule_owned_by_a_lock():
    src = (GATE / "gate_runner_live.py").read_text()
    pairs = re.findall(r'result\["outcome"\]\s*=\s*"(TRADE_\w+)"\s*\n\s*'
                       r'(?:#[^\n]*\n\s*)*result\["outcome_reason"\]\s*=\s*f?"(\w+)', src)
    # From 2026-10-29 every execution-phase check also records its verdict (rule_states): through _put in the live
    # runner, and through the shared affordability check (gate_runner._affordability), so all three are read.
    shared = (GATE / "gate_runner.py").read_text()
    checked = (set(re.findall(r'_put\(result, "(\w+)"', src))
               | set(re.findall(r'lock_codes\.put\(states, "(\w+)"', shared)) - {"overflow"})
    owners = {reason: lock_codes.rule_for(reason).owner for reason in {r for _, r in pairs} | checked}
    # Eighth round: open position, too small a notional and a missing price are L1's, the sector cap L2's,
    # the broker L6's.
    assert owners == {"position_open": 1, "notional_too_small": 1, "price_missing": 1, "sector_cap": 2,
                      "broker_error": 6}
    assert {r for _, r in pairs} <= checked                     # every outcome_reason is also a recorded verdict


def test_identifiers_and_names_are_unique_and_every_stop_code_parses_back():
    idents = [i for r in lock_codes.RULES for i in r.emitted_as]
    assert len(idents) == len(set(idents))
    keys = [(r.owner, r.name) for r in lock_codes.RULES]
    assert len(keys) == len(set(keys))
    for owner, name in keys:
        code = lock_codes.stop_code(owner, name)
        assert code.startswith(lock_codes.owner_code(owner) + "_")
        assert lock_codes.parse_stop_code(code) == (owner, name)
    with pytest.raises(KeyError):
        lock_codes.stop_code(5, "min_pass")             # a name outside the register is refused
    with pytest.raises(KeyError):
        lock_codes.parse_stop_code("FILTERED_L3_SCORE_GROK")


def test_every_rule_config_key_is_labelled_with_the_rule_lock():
    labels = lock_codes.config_labels()
    for r in lock_codes.RULES:
        for k in r.config_keys:
            assert labels.get(k, "").startswith(f"L{r.owner} "), (r.name, k, labels.get(k))


def test_every_outcome_is_owned_by_a_lock_or_is_unknown():
    for table, row, *_ in CASES:
        k = classify(table, row, COOLOFF)
        if k.code in ("TRADE_EXECUTED", "REMOVE_TEST_ROW", lock_codes.UNKNOWN):
            assert k.stop_code == k.code
            continue
        owner, rule = lock_codes.parse_stop_code(k.stop_code)
        assert k.code == lock_codes.lock_code(owner) and k.rule == rule


def test_cooloff_stops_are_the_rows_the_old_cooloff_codes_held():
    # Ruling 6 (keep cooloff as is): {FILTERED_L2, FILTERED_L3} before phase 2, the same rows after it.
    for table, row, *_ in CASES:
        k = classify(table, row, COOLOFF)
        old = row["gate_decision"] in ("FILTERED_L2", "FILTERED_L3") and k.code != "REMOVE_TEST_ROW"
        assert (k.stop_code in lock_codes.COOLOFF_STOPS) == old, row["id"]


# ── Ruling D: a flag is NULL where its lock did not run (run order before phase 4) ──────────────────

FLAGS = {"lock1_pass": 0, "lock2_pass": 0, "lock_leading_pass": 0, "lock3_pass": 0}


@pytest.mark.parametrize("code, extra, ran", [
    ("SKIPPED_COOLOFF", {"lock1_pass": 1}, ()),                         # skips write lock1_pass 1, ran nothing
    ("FILTERED_ELIGIBILITY", {"macro_reason": "VIX=31.0 >= threshold 30"}, ()),
    ("FILTERED_L1", {"timestamp": "2026-05-01T15:00:00+00:00"}, ("lock1_pass",)),
    ("FILTERED_MACRO", {"lock1_pass": 1, "macro_reason": "macro event: CPI on 2026-04-08 (Δ1d)"}, ("lock1_pass",)),
    ("FILTERED_L2", {"lock1_pass": 1, "lock3_reasoning": "bearish"}, ("lock1_pass", "lock2_pass")),
    ("FILTERED_ETF_PENALTY", {"lock1_pass": 1, "lock2_pass": 1, "lock_leading_pass": 1},
     ("lock1_pass", "lock2_pass", "lock_leading_pass")),
    ("FILTERED_OVERFLOW_QUANT", {"lock1_pass": 1, "lock2_pass": 1, "lock_leading_pass": 1},
     ("lock1_pass", "lock2_pass", "lock_leading_pass")),
    ("FILTERED_L3", {"lock3_reasoning": "drawdown 25.1% > max 20.0% (mechanical pre-L5 guard)"},
     ("lock1_pass", "lock2_pass", "lock_leading_pass")),
    ("FILTERED_L3", {"lock3_reasoning": "HOLD"}, ("lock1_pass", "lock2_pass", "lock_leading_pass", "lock3_pass")),
    ("TRADE_REJECTED", {"lock3_pass": 1}, ("lock1_pass", "lock2_pass", "lock_leading_pass", "lock3_pass")),
    ("TRADE_REJECTED", {"lock3_pass": 0, "outcome_reason": "position_open"},
     ("lock1_pass", "lock2_pass", "lock_leading_pass")),                 # the pre-review reject: L6 did not run
    ("TRADE_EXECUTED", {}, ("lock1_pass", "lock2_pass", "lock_leading_pass", "lock3_pass")),
])
def test_pass_flags_are_null_after_the_last_lock_that_ran(code, extra, ran):
    row = {"id": 1, "timestamp": "2026-10-01T15:00:00+00:00", "gate_decision": code, **FLAGS, **extra}
    flags = pass_flags(classify(LIVE, row, COOLOFF), row)
    assert {c for c, v in flags.items() if v is not None} == set(ran)
    assert all(flags[c] == row[c] for c in ran)


# ── Gate or exit cooloff (Niclas 2026-10-29: "Skipped cooloff och exit cooloff bör vara olika markörer") ──

def _cooloff_db(stops=(), exits=(), demo_skips=(), signals=()):
    import sqlite3
    c = sqlite3.connect(":memory:")
    for t in ("live_gate_history", "demo_gate_history"):
        c.execute(f"CREATE TABLE {t} (ticker TEXT, timestamp TEXT, gate_decision TEXT)")
    for t in ("live_trades", "trades"):
        c.execute(f"CREATE TABLE {t} (ticker TEXT, exited_at TEXT, outcome TEXT, exit_reason TEXT)")
    c.execute("CREATE TABLE signals (ticker TEXT, timestamp TEXT)")
    c.executemany("INSERT INTO live_gate_history VALUES (?, ?, ?)", stops)
    c.executemany("INSERT INTO demo_gate_history VALUES (?, ?, ?)",
                  [(t, ts, "SKIPPED_COOLOFF") for t, ts in demo_skips] + list(stops))
    c.executemany("INSERT INTO live_trades VALUES (?, ?, ?, ?)", exits)
    c.executemany("INSERT INTO trades VALUES (?, ?, ?, ?)", exits)
    c.executemany("INSERT INTO signals VALUES (?, ?)", signals)
    return CooloffIndex(c)


def _skip(ticker, ts):
    return {"id": 1, "ticker": ticker, "timestamp": ts, "gate_decision": "SKIPPED_COOLOFF"}


def test_cooloff_index_tells_gate_from_exit_and_gives_both_to_cooloff():
    idx = _cooloff_db(stops=[("AAA", "2026-07-01T14:00:00+00:00", "FILTERED_L3")],
                      exits=[("BBB", "2026-07-01T10:00:00+00:00", "LOSS", "SL"),
                             ("AAA", "2026-07-01T13:00:00+00:00", "LOSS", "SL"),
                             ("CCC", "2026-06-28T10:00:00+00:00", "WIN", "TP")])     # TP win: 168 h
    assert idx.rule(LIVE, _skip("AAA", "2026-07-01T16:00:00+00:00"))[0] == "cooloff"          # both: cooloff
    assert "exit cooloff held too" in idx.rule(LIVE, _skip("AAA", "2026-07-01T16:00:00+00:00"))[1]
    assert idx.rule(LIVE, _skip("BBB", "2026-07-02T09:00:00+00:00"))[0] == "exit_cooloff"     # 23 h after an SL
    assert idx.rule(LIVE, _skip("CCC", "2026-07-03T10:00:00+00:00"))[0] == "exit_cooloff"     # 120 h after a TP
    with pytest.raises(UnclassifiedRow):
        idx.rule(LIVE, _skip("BBB", "2026-07-02T11:00:00+00:00"))                             # 25 h: neither


def test_cooloff_index_reads_the_april_macro_code_until_it_left_the_set():
    # 6794433 (2026-05-12) took FILTERED_MACRO out of the gate cooloff set; the April skips were cooled by it.
    idx = _cooloff_db(stops=[("AAA", "2026-04-08T13:00:00+00:00", "FILTERED_MACRO"),
                             ("AAA", "2026-05-20T13:00:00+00:00", "FILTERED_MACRO")])
    assert idx.rule(LIVE, _skip("AAA", "2026-04-08T15:00:00+00:00"))[0] == "cooloff"
    with pytest.raises(UnclassifiedRow):
        idx.rule(LIVE, _skip("AAA", "2026-05-20T15:00:00+00:00"))


def test_cooloff_index_replays_a_signals_row_at_the_matched_demo_decision():
    # signals.timestamp is the signal's poll time; the decision came later (LLY 2026-10-22: the L2 stop at 19:03
    # cooled the 19:14 skip written onto the 17:20 signal row).
    idx = _cooloff_db(stops=[("LLY", "2026-10-22T19:03:54+00:00", "FILTERED_L2")],
                      demo_skips=[("LLY", "2026-10-22T19:14:18+00:00")],
                      signals=[("LLY", "2026-10-22T17:20:52+00:00"), ("LLY", "2026-10-22T20:05:52+00:00")])
    rule, basis = idx.rule("signals", _skip("LLY", "2026-10-22T17:20:52+00:00"))
    assert rule == "cooloff" and "matched demo_gate_history decision" in basis
    with pytest.raises(UnclassifiedRow):
        idx.rule("live_gate_history", _skip("LLY", "2026-10-22T17:20:52+00:00"))   # live never saw the demo stop


def test_a_cooloff_skip_without_the_index_is_refused():
    with pytest.raises(UnclassifiedRow):
        classify(LIVE, _skip("AAA", "2026-07-01T16:00:00+00:00"))
