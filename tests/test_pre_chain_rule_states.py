"""
The rules that run before the chain name themselves too (lock labels phase 2, writer 5): L1's skips (the live
blocklist, an open position, gate cooloff, exit cooloff; Niclas 2026-10-29: "Skipped cooloff och exit cooloff bör
vara olika markörer") and L0's M4 rules, which run once per live cycle (portfolio_l0). Every evaluated row
carries their verdicts, so its L0 and L1 columns show what ran.
"""
from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import patch

from backend.gate import gate_runner, lock_codes, portfolio_l0
from backend.gate.lock_codes import FAIL, NO_DATA, PASS
from backend.gate.lock_codes import rule_key as K

SKIPS = {"blocklist": K(1, "blocklist"), "position_open": K(1, "position_open"),
         "cooloff": K(1, "cooloff"), "exit_cooloff": K(1, "exit_cooloff")}
M4 = {K(0, "halt_m4_drawdown"), K(0, "loss_cap"), K(0, "halt_m4_expectancy")}


def test_each_pre_chain_check_gives_its_own_verdict():
    checks = {"blocklist": set(), "position_open": {"MU"}, "cooloff": {"MU", "LLY"}, "exit_cooloff": {"LLY"}}
    mu = gate_runner._pre_chain_states("MU", checks)
    assert mu == {SKIPS["blocklist"]: PASS, SKIPS["position_open"]: FAIL, SKIPS["cooloff"]: FAIL,
                  SKIPS["exit_cooloff"]: PASS}
    # Open and cooloff together: position_open owns the skip, as the runners' SKIPPED_OPEN label did.
    assert lock_codes.stop_of(mu) == (1, "position_open")
    # Gate and exit cooloff together: cooloff (ruling 2026-10-29).
    assert lock_codes.stop_of(gate_runner._pre_chain_states("LLY", checks)) == (1, "cooloff")
    assert lock_codes.stop_of(gate_runner._pre_chain_states("NVDA", checks)) is None


def test_m4_verdicts_per_rule_and_expectancy_is_no_data_below_its_sample():
    v = {"drawdown": {"drawdown": 0.0}, "day_loss": 0.0, "daily_loss_cap": 500.0,
         "expectancy": {"n": 1, "needed": 40, "breached": False}}
    assert portfolio_l0.rule_verdicts(v) == {portfolio_l0.HALT_DRAWDOWN: PASS, portfolio_l0.HALT_LOSS_CAP: PASS,
                                             portfolio_l0.HALT_EXPECTANCY: NO_DATA}
    v = {"drawdown": {"drawdown": 0.07}, "day_loss": 600.0, "daily_loss_cap": 500.0,
         "expectancy": {"n": 40, "needed": 40, "breached": True}}
    states = {}
    for ident, verdict in portfolio_l0.rule_verdicts(v).items():
        lock_codes.put(states, ident, verdict)
    assert set(states.values()) == {FAIL}
    # All three fail: the drawdown halt first, as portfolio_l0.evaluate names it.
    assert lock_codes.stop_of(states) == (0, "halt_m4_drawdown")


def test_m4_verdicts_agree_with_the_halt_evaluate_names():
    # One rule fails at a time: rule_verdicts and evaluate name the same rule.
    for dd, loss, breached, reason in ((0.07, 0.0, False, portfolio_l0.HALT_DRAWDOWN),
                                       (0.0, 600.0, False, portfolio_l0.HALT_LOSS_CAP),
                                       (0.0, 0.0, True, portfolio_l0.HALT_EXPECTANCY)):
        v = {"drawdown": {"drawdown": dd}, "day_loss": loss, "daily_loss_cap": 500.0,
             "expectancy": {"n": 40, "needed": 40, "breached": breached}}
        assert [i for i, x in portfolio_l0.rule_verdicts(v).items() if x == FAIL] == [reason]


def test_a_live_row_carries_the_m4_and_pre_chain_verdicts():
    from tests.test_gate_runners import _live_patches, _signal
    with ExitStack() as st:
        [st.enter_context(p) for p in _live_patches(candidates=[_signal()])]
        from backend.gate import gate_runner_live
        results = gate_runner_live.run()
    states = results[0]["rule_states"]
    assert M4 <= set(states) and all(states[k] in (PASS, NO_DATA) for k in M4)
    assert all(states[k] == PASS for k in SKIPS.values())


def test_a_pre_rotation_row_shows_the_blocklist_was_not_checked():
    # Pre-rotation candidates skip the live blocklist (gate_runner_live; input to the watchlist and rotation work
    # after phase 4): their rows have no blocklist verdict instead of a pass they did not get.
    from tests.test_gate_runners import _live_patches, _signal
    main, pre = _signal("AAPL", sid=1), _signal("NVDA", sid=2)
    patches = _live_patches(candidates=[main])
    with ExitStack() as st:
        mocks = [st.enter_context(p) for p in patches]
        mocks[6].side_effect = [[main], [pre]]                     # get_lock1_candidates: main, then pre-rotation
        mocks[20].return_value = {"available": False, "watching": [{"sector": "Technology"}], "likely_next": []}
        from backend.gate import gate_runner_live
        results = gate_runner_live.run()
    by = {r["ticker"]: r["rule_states"] for r in results}
    assert SKIPS["blocklist"] in by["AAPL"] and SKIPS["blocklist"] not in by["NVDA"]
    assert by["NVDA"][SKIPS["exit_cooloff"]] == PASS


def test_a_demo_row_carries_the_pre_chain_verdicts_and_no_m4():
    from tests.test_gate_runners import _demo_patches, _signal
    with ExitStack() as st:
        [st.enter_context(p) for p in _demo_patches(candidates=[_signal()])]
        results = gate_runner.run()
    states = results[0]["rule_states"]
    assert {k: states.get(k) for k in SKIPS.values()} == {
        SKIPS["blocklist"]: None, SKIPS["position_open"]: PASS, SKIPS["cooloff"]: PASS, SKIPS["exit_cooloff"]: PASS}
    assert M4.isdisjoint(states)                                      # demo runs no M4 (phase 4 parity input)


def test_demo_refuses_an_unaffordable_cycle_before_the_chain():
    # Demo's largest notional is cash × max_position_size; below the wallet's $50 every candidate is refused at L1
    # before any Grok, options or Claude call (Niclas 2026-10-28: "Kan vi spara anrop så är det bra").
    from tests.test_gate_runners import _demo_patches, _signal
    patches = _demo_patches(candidates=[_signal()])
    with ExitStack() as st:
        mocks = [st.enter_context(p) for p in patches]
        st.enter_context(patch("backend.db.get_portfolio_summary", return_value={"cash": 300.0}))   # 15 % = $45
        results = gate_runner.run()
    assert results == []
    mocks[9].assert_not_called()                                       # evaluate_chain
    row = mocks[8].call_args[0][0]
    assert row["stop_code"] == "FILTERED_L1_ELIGIBILITY_NOTIONAL_TOO_SMALL" and row["lock1_pass"] is None


def test_a_live_unknown_outcome_is_mailed_once_per_cycle():
    # A chain stop that names no rule (a lock's identifier outside the register gives this): both rows are
    # FILTERED_UNKNOWN and the cycle sends one mail listing both.
    from tests.test_gate_runners import _chain_fail_at, _live_patches, _signal
    cr = _chain_fail_at(3)
    cr.rule_states = {}
    patches = _live_patches(candidates=[_signal("NVDA", sid=1), _signal("AAPL", sid=2)], chain_result=cr)
    with ExitStack() as st:
        mocks = [st.enter_context(p) for p in patches]
        alert = st.enter_context(patch("backend.alerts.alert_gate_unknown"))
        from backend.gate import gate_runner_live
        results = gate_runner_live.run()
    assert [r["gate_decision"] for r in results] == ["FILTERED_UNKNOWN", "FILTERED_UNKNOWN"]
    assert all(c.args[0]["stop_code"] == "FILTERED_UNKNOWN" for c in mocks[9].call_args_list)
    alert.assert_called_once()
    assert alert.call_args[0][0] == "live" and sorted(r["ticker"] for r in alert.call_args[0][1]) == ["AAPL", "NVDA"]


def test_alert_gate_unknown_mails_one_message_naming_each_row():
    from backend import alerts
    with patch.object(alerts, "_dispatch", return_value=True) as d:
        assert alerts.alert_gate_unknown("live", [
            {"ticker": "NVDA", "timestamp": "2026-11-03T14:00", "outcome_reason": None,
             "rule_states": {K(6, "claude"): PASS}},
            {"ticker": "AAPL", "timestamp": "2026-11-03T14:00", "outcome_reason": "new_check"}])
    d.assert_called_once()
    title, body = d.call_args[0]
    assert "2 row(s)" in title and "LIVE" in title
    assert "NVDA" in body and "L6_REVIEW_CLAUDE=pass" in body and "'new_check'" in body and "lock_codes.py" in body
