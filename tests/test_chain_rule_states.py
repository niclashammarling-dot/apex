"""
The chain names the rule of every stop and every pass (lock labels phase 2, Niclas 2026-10-29: "Kedjan anger
ägare och regel för varje stopp, men även för varje pass"): ChainResult.rule_states, keyed by
lock_codes.rule_key, and the stop is lock_codes.stop_of(rule_states), the function the readers use too.

Lock 1, Lock 2 and Lock 4 run for real (their I/O patched), so the identifiers and data keys are the
modules' own; Lock 3 and Lock 5 are LockResults in their modules' shapes. Each stop is also read back through
the history classifier from the row today's writer stores, so the writer and the migration name a row alike.
"""
from __future__ import annotations

import json
from contextlib import ExitStack
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import backend.gate.lock1_eligibility as l1m
import backend.gate.lock4_leading as l4m
from backend.gate import chain, gate_runner, lock_codes, lock5_claude
from backend.gate.history_classify import classify
from backend.gate.lock_codes import FAIL, NO_DATA, PASS
from backend.gate.lock_codes import rule_key as K
from backend.gate.types import LockResult

SECTOR = "Technology"
THRESHOLDS = {SECTOR: 0.65}
CFG = {
    "vix_threshold": 30.0, "macro_event_blackout_days": 1, "macro_pre_event_penalty": 0.05,
    "macro_earnings_blackout_days": 3, "macro_earnings_near_days": 14, "macro_earnings_near_penalty": 0.15,
    "etf_negative_penalty": 0.20, "etf_negative_floor": -1.0,
    "lock2_sentiment_min": 0.3, "lock3_confidence_min": 0.6, "lock4_pcr_mode": "pooled_scalar",
}
GUARD_OK = {"risk_limits": {"starting_balance": 10000, "max_drawdown_pct": 0.20}, "wallet_balance": 9500}
GUARD_HIT = {"risk_limits": {"starting_balance": 10000, "max_drawdown_pct": 0.20}, "wallet_balance": 7000}


def _regime():
    entry = MagicMock(sector=SECTOR, adjusted_score=0.60, allocation=0.25, rank=1)
    return MagicMock(leaderboard=[entry], leader=SECTOR, date=date.today().isoformat())


def _l1(vix=10.0, macro=("clear", None), earnings_in=None, regime=True) -> LockResult:
    earnings = date.today() + timedelta(days=earnings_in) if earnings_in is not None else None
    with ExitStack() as st:
        st.enter_context(patch.object(l1m, "_get_regime_result", return_value=_regime() if regime else None))
        st.enter_context(patch.object(l1m, "_get_vix", return_value=vix))
        st.enter_context(patch.object(l1m, "_macro_status", return_value=macro))
        st.enter_context(patch.object(l1m, "_get_earnings_date", return_value=earnings))
        return l1m.evaluate("AAPL", SECTOR, CFG)


def _grok(passed=True, unavailable=False) -> LockResult:
    if unavailable:     # lock3_sentiment's fail-closed shape
        return LockResult.fail(lock_id=3, reason="Grok API unavailable — failing closed",
                               data={"unavailable": True, "score": None, "conviction": None,
                                     "key_themes": [], "summary": None})
    data = {"score": 0.6 if passed else 0.1, "conviction": "high", "key_themes": [], "summary": "news"}
    if passed:
        return LockResult.pass_(lock_id=3, score=0.6, reason="pass", data=data)
    return LockResult.fail(lock_id=3, reason="score 0.10 <= 0.3", data=data)


def _chains(ok=True):
    calls = pd.DataFrame({"openInterest": [1000], "volume": [1000]})
    puts = pd.DataFrame({"openInterest": [100 if ok else 5000], "volume": [100 if ok else 5000]})
    return [(calls, puts)]


RS_OK = {"pass": True, "ticker_return": 2.0, "etf_return": 0.5, "etf": "XLK", "reason": "rs"}
VA_OK = {"pass": True, "reason": "va"}
RS_ERR = {"pass": False, "error": True, "reason": "empty download (429 or delisted)"}


def _l4(rs=RS_OK, va=VA_OK, chains=None, fetch_raises=None) -> LockResult:
    with ExitStack() as st:
        st.enter_context(patch.object(l4m, "_check_relative_strength", return_value=rs))
        st.enter_context(patch.object(l4m, "_check_volume_accumulation", return_value=va))
        if fetch_raises is not None:
            st.enter_context(patch.object(l4m, "_fetch_options_chains", side_effect=fetch_raises))
        else:
            st.enter_context(patch.object(l4m, "_fetch_options_chains",
                                          return_value=_chains() if chains is None else chains))
        return l4m.evaluate("AAPL", SECTOR)


def _claude(passed=False) -> LockResult:
    data = {"decision": "BUY" if passed else "HOLD", "confidence": 0.8 if passed else 0.4,
            "position_size_pct": 0.1, "reasoning": "Claude's reasoning", "model": "m"}
    if passed:
        return LockResult.pass_(lock_id=5, score=0.8, reason="approve", data=data)
    return LockResult.fail(lock_id=5, reason="HOLD", data=data)


def _run(l1=None, score=0.80, l3=None, l4=None, l5=None, context=None, stop_after=4):
    with ExitStack() as st:
        st.enter_context(patch("backend.gate.chain.lock1_evaluate", return_value=l1 or _l1()))
        st.enter_context(patch("backend.gate.chain.lock3_evaluate", return_value=l3 or _grok()))
        st.enter_context(patch("backend.gate.chain.lock4_evaluate", return_value=l4 or _l4()))
        st.enter_context(patch("backend.gate.chain.lock5_evaluate", return_value=l5 or _claude()))
        return chain.evaluate_chain("AAPL", SECTOR, score, context if context is not None else GUARD_OK, CFG,
                                    stop_after_lock=stop_after, sector_thresholds=THRESHOLDS)


L1_PASSED = {K(1, "regime"): PASS, K(1, "vix"): PASS, K(1, "macro_event"): PASS, K(2, "earnings"): NO_DATA}
SCORE_PASSED = {K(3, "threshold"): PASS, K(1, "macro_pre_event"): PASS, K(2, "earnings_near"): PASS}

# (id, chain kwargs, stop, rule_states that must hold, read back through the history classifier)
CASES = [
    ("pending", {}, None,
     {**L1_PASSED, **SCORE_PASSED, K(4, "grok"): PASS, K(5, "leading"): PASS, K(2, "etf"): PASS,
      K(0, "drawdown_guard"): PASS}, False),
    ("regime_unavailable", {"l1": _l1(regime=False)}, (1, "regime"), {K(1, "regime"): FAIL}, True),
    ("vix", {"l1": _l1(vix=35.0)}, (1, "vix"), {K(1, "regime"): PASS, K(1, "vix"): FAIL}, True),
    ("vix_no_data", {"l1": _l1(vix=None)}, None, {K(1, "vix"): NO_DATA}, False),
    ("macro_event", {"l1": _l1(macro=("hard_block", "CPI on 2026-11-10 (today)"))}, (1, "macro_event"),
     {K(1, "vix"): PASS, K(1, "macro_event"): FAIL}, True),
    ("earnings", {"l1": _l1(earnings_in=2)}, (2, "earnings"), {K(1, "macro_event"): PASS, K(2, "earnings"): FAIL},
     True),
    # The score test: the threshold owns the stop only when the unpenalised score fails it too.
    ("threshold", {"score": 0.50}, (3, "threshold"), {**SCORE_PASSED, K(3, "threshold"): FAIL}, False),
    ("macro_pre_event", {"l1": _l1(macro=("pre_event", "CPI on 2026-11-10 (in 1d)")), "score": 0.66},
     (1, "macro_pre_event"), {K(3, "threshold"): PASS, K(1, "macro_pre_event"): FAIL, K(2, "earnings_near"): PASS},
     True),
    ("earnings_near", {"l1": _l1(earnings_in=7), "score": 0.70}, (2, "earnings_near"),
     {K(2, "earnings"): PASS, K(3, "threshold"): PASS, K(1, "macro_pre_event"): PASS, K(2, "earnings_near"): FAIL},
     False),
    # Both penalties applied: the first in chain order (L1) owns the stop (ruling 2026-10-28).
    ("both_penalties", {"l1": _l1(macro=("pre_event", "CPI"), earnings_in=7), "score": 0.70}, (1, "macro_pre_event"),
     {K(1, "macro_pre_event"): FAIL, K(2, "earnings_near"): FAIL, K(3, "threshold"): PASS}, False),
    ("grok", {"l3": _grok(passed=False)}, (4, "grok"), {K(3, "threshold"): PASS, K(4, "grok"): FAIL}, True),
    ("grok_unavailable", {"l3": _grok(unavailable=True)}, (4, "unavailable"), {K(4, "unavailable"): FAIL}, True),
    ("leading", {"l4": _l4(chains=[])}, (5, "leading"), {K(4, "grok"): PASS, K(5, "leading"): FAIL}, True),
    ("leading_options_fetch_error", {"l4": _l4(fetch_raises=RuntimeError("Yahoo 429 rate limit"))},
     (5, "unavailable"), {K(5, "unavailable"): FAIL}, True),
    ("leading_price_error", {"l4": _l4(rs=RS_ERR)}, (5, "unavailable"), {K(5, "unavailable"): FAIL}, True),
    ("etf", {"l4": _l4(rs={**RS_OK, "etf_return": -2.0}), "score": 0.70}, (2, "etf"),
     {K(5, "leading"): PASS, K(2, "etf"): FAIL}, True),
    ("drawdown_guard", {"context": GUARD_HIT}, (0, "drawdown_guard"),
     {K(2, "etf"): PASS, K(0, "drawdown_guard"): FAIL}, True),
    ("claude", {"stop_after": None}, (6, "claude"), {K(0, "drawdown_guard"): PASS, K(6, "claude"): FAIL}, True),
    ("claude_unavailable", {"stop_after": None, "l5": lock5_claude._fail_closed("rate_limit")}, (6, "unavailable"),
     {K(6, "unavailable"): FAIL}, True),
    ("approved", {"stop_after": None, "l5": _claude(passed=True)}, None, {K(6, "claude"): PASS}, False),
]


@pytest.mark.parametrize("name, kwargs, stop, states, _", CASES, ids=[c[0] for c in CASES])
def test_every_stop_and_pass_is_named_by_its_rule(name, kwargs, stop, states, _):
    cr = _run(**kwargs)
    assert cr.stop == stop
    assert {k: cr.rule_states.get(k) for k in states} == states
    assert all(lock_codes.parse_rule_key(k) for k in cr.rule_states)     # every key is a registered rule


def test_a_stop_records_no_rule_after_it():
    # Ruling D for rules: a rule that did not run is absent, so a reader never sees a verdict it did not get.
    cr = _run(l1=_l1(vix=35.0))
    assert set(cr.rule_states) == {K(1, "regime"), K(1, "vix")}
    cr = _run(l3=_grok(passed=False))
    assert K(5, "leading") not in cr.rule_states and K(0, "drawdown_guard") not in cr.rule_states


def test_an_identifier_outside_the_register_is_an_unknown_stop_not_a_raise():
    l1 = LockResult.fail(lock_id=1, reason="new check", data={"fail_type": "brand_new",
                                                              "rule_states": {"regime": PASS, "brand_new": FAIL}})
    cr = _run(l1=l1)
    assert not cr.approved and cr.stop is None and cr.rule_states == {K(1, "regime"): PASS}


def test_a_failed_score_test_without_its_threshold_is_an_unknown_stop_not_a_guess():
    with patch("backend.gate.chain.lock2_evaluate", return_value=LockResult.fail(lock_id=2, reason="x", data={})):
        cr = _run()
    assert not cr.approved and cr.exit_lock == 2 and cr.stop is None


# The code the pre-phase-2 writer stored for a chain stop, frozen here (lock_codes.outcome_for and chain.py's
# exit_label until 2026-10-31): module exit_lock → code, the ETF re-test under its own code.
_LEGACY_CODE = {1: "FILTERED_ELIGIBILITY", 2: "FILTERED_L1", 3: "FILTERED_L2", 4: "FILTERED_LEADING", 5: "FILTERED_L3"}


@pytest.mark.parametrize("name, kwargs, stop, states, readable", [c for c in CASES if c[4]],
                         ids=[c[0] for c in CASES if c[4]])
def test_the_writer_and_the_history_classifier_name_the_same_stop(name, kwargs, stop, states, readable):
    # The row the pre-phase-2 writer would have stored for this stop, classified as the migration classifies
    # history: the same stop_code as the writer now derives (so migrated and new rows mean the same). Excluded:
    # classes history never held, which the classifier refuses (a genuine threshold stop after 06-16,
    # earnings_near, both penalties).
    cr = _run(**kwargs)
    signal = {"ticker": "AAPL", "sector": SECTOR, "id": 1, "signal_score": kwargs.get("score", 0.80)}
    res = gate_runner._chain_to_gate_result(signal, cr)
    legacy = "FILTERED_ETF_PENALTY" if cr.stop == (2, "etf") else _LEGACY_CODE[cr.exit_lock]
    row = {"id": 1, "timestamp": res["timestamp"], "gate_decision": legacy,
           "signal_score": signal["signal_score"], "lock3_reasoning": res["claude_reasoning"],
           "macro_reason": res["macro_reason"], "l2_summary": res["l2_summary"],
           "lock_leading_checks": res["lock_leading_checks"], **lock_codes.flags(cr.rule_states)}
    assert classify("live_gate_history", row).stop_code == lock_codes.stop_code(*stop)
    assert gate_runner._finalize({**res, "outcome": gate_runner.STOPPED})["stop_code"] == lock_codes.stop_code(*stop)


def test_the_deferred_review_adds_claude_rule_to_the_runner_result():
    for l5, expected in ((_claude(passed=False), {K(6, "claude"): FAIL}),
                         (lock5_claude._fail_closed("connection"), {K(6, "unavailable"): FAIL}),
                         (_claude(passed=True), {K(6, "claude"): PASS})):
        cr = _run()
        res = gate_runner._chain_to_gate_result({"ticker": "AAPL", "sector": SECTOR, "id": 1}, cr)
        res["_l5_lock_results"], res["_l5_context"] = cr.lock_results, {}
        with patch("backend.gate.lock5_claude.evaluate", return_value=l5):
            gate_runner._run_l5_for_result({"ticker": "AAPL", "sector": SECTOR}, res, CFG)
        assert {k: res["rule_states"].get(k) for k in expected} == expected
        assert lock_codes.stop_of(res["rule_states"]) == next(
            (lock_codes.parse_rule_key(k) for k, v in expected.items() if v == FAIL), None)


def test_options_fetch_error_is_a_data_error_and_no_expiries_is_not():
    err = _l4(fetch_raises=RuntimeError("curl: (35) TLS connect error"))
    assert not err.passed and err.data["unavailable"] and err.data["options_errors"] == 2
    assert err.reason.startswith("options group failed (2 error(s))")
    none = _l4(chains=[])
    assert not none.passed and not none.data["unavailable"] and none.data["options_errors"] == 0
    assert none.data["checks"]["put_call_ratio"]["reason"] == "no options data (no listed expiries)"


def test_the_options_error_flag_changes_no_group_verdict():
    # Phase 2 names rows; it moves no decision. A fetch error failed the options group before and still does,
    # and a passing options group with the price group failed still fails on price.
    assert not _l4(fetch_raises=RuntimeError("x")).data["options_group_pass"]
    ok = _l4()
    assert ok.passed and ok.data["options_group_pass"] and ok.data["price_group_pass"]
    assert not ok.data["unavailable"]


def test_claude_input_is_unchanged_by_the_bookkeeping():
    # The Lock 5 prompt approved 2026-10-26 reads the lock results' data; rule_states and the unavailable flags
    # name rows and stay out of it.
    lr = {1: _l1(), 3: _grok(), 4: _l4()}
    payload = lock5_claude._build_context_payload("AAPL", SECTOR, lr, {})
    seen = {k for r in payload["lock_results"].values() for k in r["data"]}
    assert seen.isdisjoint({"rule_states", "unavailable", "options_errors"})
    assert "checks" in payload["lock_results"]["l5_positioning"]["data"]      # the evidence still reaches Claude


def test_rule_key_is_the_stop_code_without_its_prefix_and_parses_back():
    for r in lock_codes.RULES:
        key = lock_codes.rule_key(r.owner, r.name)
        assert lock_codes.stop_code(r.owner, r.name) == "FILTERED_" + key
        assert lock_codes.parse_rule_key(key) == (r.owner, r.name)
    with pytest.raises(KeyError):
        lock_codes.rule_key(1, "earnings")                 # earnings is L2's
    with pytest.raises(KeyError):
        lock_codes.parse_rule_key("L1_ELIGIBILITY_EARNINGS")


def test_stop_of_takes_the_first_fail_in_chain_order():
    assert lock_codes.stop_of({}) is None
    assert lock_codes.stop_of({K(1, "regime"): PASS, K(1, "vix"): NO_DATA}) is None
    assert lock_codes.stop_of({K(2, "earnings_near"): FAIL, K(1, "macro_pre_event"): FAIL}) == (1, "macro_pre_event")
    # Gate and exit cooloff both holding: cooloff, first in the register (ruling 2026-10-29).
    assert lock_codes.stop_of({K(1, "exit_cooloff"): FAIL, K(1, "cooloff"): FAIL}) == (1, "cooloff")
    with pytest.raises(KeyError):
        lock_codes.stop_of({"L9_NOWHERE_X": FAIL})


def test_exit_cooloff_and_the_m4_rules_are_registered_under_their_locks():
    assert lock_codes.rule_for("exit_cooloff").owner == 1
    assert lock_codes.rule_for("halt_m4_drawdown").owner == 0
    assert lock_codes.rule_for("halt_m4_expectancy").owner == 0
    from backend.gate import portfolio_l0
    for ident in (portfolio_l0.HALT_DRAWDOWN, portfolio_l0.HALT_LOSS_CAP, portfolio_l0.HALT_EXPECTANCY):
        assert lock_codes.rule_for(ident).owner == 0       # M4's own identifiers, read from its module
    labels = lock_codes.config_labels()
    assert labels["exit_cooloff_hours"].startswith("L1 ") and labels["tp_cooloff_hours"].startswith("L1 ")
