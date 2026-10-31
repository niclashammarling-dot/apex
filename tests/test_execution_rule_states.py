"""
The demo execution phase names its rules (lock labels phase 2, writer 3; 2026-10-28: "demo = live except
trading"): every check wallet.execute_trade runs is recorded in the gate result's rule_states, a refusal sets
outcome_reason to its rule's identifier as the live runner does, and the demo runner records overflow and writes
outcome_reason to demo_gate_history.
"""
from __future__ import annotations

import re
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

from backend import db, wallet
from backend.gate import lock_codes
from backend.gate.lock_codes import FAIL, PASS
from backend.gate.lock_codes import rule_key as K

ROOT = Path(__file__).resolve().parents[1]
CFG = {"max_sector_exposure": 0.30, "max_positions": 4, "daily_loss_cap": 100.0, "max_position_size": 0.15}
ORDER = ("max_positions", "sector_cap", "loss_cap", "position_open", "notional_too_small", "price_missing")
KEY = {i: K(lock_codes.rule_for(i).owner, i) for i in ORDER}


def _trade(open_count=0, exposure=0.0, loss=0.0, held=(), cash=2000.0, price=100.0, overflow=False):
    gr = {"ticker": "NVDA", "sector": "Technology", "lock3": {"position_size_pct": 0.10}, "lock1": {"score": 0.7},
          "rule_states": {K(3, "threshold"): PASS}}
    portfolio = {"open_position_count": open_count, "cash": cash, "sector_exposure": {"Technology": exposure}}
    with ExitStack() as st:
        st.enter_context(patch.object(wallet, "get_portfolio_summary", return_value=portfolio))
        st.enter_context(patch.object(wallet, "get_demo_config", return_value=CFG))
        st.enter_context(patch.object(wallet, "_daily_realized_loss", return_value=loss))
        st.enter_context(patch.object(wallet, "get_open_trades", return_value=[{"ticker": t} for t in held]))
        st.enter_context(patch.object(wallet, "insert_trade", return_value=7))
        out = wallet.execute_trade(gr, price, overflow=overflow)
    return out, gr


@pytest.mark.parametrize("ident, kwargs", [
    ("max_positions", {"open_count": 4}),
    ("sector_cap", {"exposure": 0.29}),
    ("loss_cap", {"loss": 150.0}),
    ("position_open", {"held": ("NVDA",)}),
    ("notional_too_small", {"cash": 400.0}),       # 10 % of $400 is below the $50 floor
    ("price_missing", {"price": 0.0}),
])
def test_each_wallet_refusal_names_its_rule_and_the_checks_before_it_passed(ident, kwargs):
    out, gr = _trade(**kwargs)
    assert out is None and gr["outcome_reason"] == ident
    i = ORDER.index(ident)
    assert {KEY[x]: gr["rule_states"].get(KEY[x]) for x in ORDER} == {
        **{KEY[x]: PASS for x in ORDER[:i]}, KEY[ident]: FAIL, **{KEY[x]: None for x in ORDER[i + 1:]}}
    assert lock_codes.stop_of(gr["rule_states"]) == lock_codes.parse_rule_key(KEY[ident])
    assert gr["rule_states"][K(3, "threshold")] == PASS                 # the chain's states are kept


def test_an_executed_trade_passed_every_wallet_rule_and_an_overflow_slot_skips_max_positions():
    out, gr = _trade()
    assert out["id"] == 7 and "outcome_reason" not in gr
    assert all(gr["rule_states"][KEY[x]] == PASS for x in ORDER)
    out, gr = _trade(open_count=4, overflow=True)
    assert out["id"] == 7 and KEY["max_positions"] not in gr["rule_states"]


def test_every_identifier_the_wallet_emits_has_one_rule_owned_by_a_lock():
    src = (ROOT / "backend" / "wallet.py").read_text()
    emitted = set(re.findall(r'_check\("(\w+)"', src))
    assert {i: lock_codes.rule_for(i).owner for i in emitted} == {
        "max_positions": 2, "sector_cap": 2, "loss_cap": 0,
        "position_open": 1, "notional_too_small": 1, "price_missing": 1}


# ── The demo runner ───────────────────────────────────────────────────────────────────────────────

def _run_demo(score=0.65, open_tickers=(), max_positions=4, refuse=None):
    from tests.test_gate_runners import _chain_pass, _demo_patches, _signal

    def execute(gr, price, **kw):              # the wallet's contract: a refusal records its rule and reason
        if refuse:
            lock_codes.put(gr.setdefault("rule_states", {}), refuse, FAIL)
            gr["outcome_reason"] = refuse
            return None
        return {"id": 1}
    patches = _demo_patches(candidates=[_signal(score=score)], open_tickers=set(open_tickers),
                            chain_result=_chain_pass(), cfg_overrides={"max_positions": max_positions})
    with ExitStack() as st:
        mocks = [st.enter_context(p) for p in patches]
        mocks[10].side_effect = execute
        from backend.gate import gate_runner
        return gate_runner.run(), mocks


def test_the_wallet_reason_reaches_demo_gate_history():
    results, mocks = _run_demo(refuse="sector_cap")
    row = mocks[8].call_args[0][0]
    assert results[0]["outcome"] == row["gate_decision"] == "FILTERED_L2_RISK"
    assert row["stop_code"] == "FILTERED_L2_RISK_SECTOR_CAP" and row["outcome_reason"] == "sector_cap"


def test_a_wallet_refusal_without_a_rule_is_unknown_and_mailed():
    # The wallet's contract broken (None with no reason): no rule failed, so the row is FILTERED_UNKNOWN and the
    # cycle mails it once (Niclas 2026-10-28: "som direkt flaggas och skickas till min mail").
    from tests.test_gate_runners import _chain_pass, _demo_patches, _signal
    patches = _demo_patches(candidates=[_signal()], chain_result=_chain_pass(), trade_result=None)
    with ExitStack() as st:
        mocks = [st.enter_context(p) for p in patches]
        alert = st.enter_context(patch("backend.alerts.alert_gate_unknown"))
        from backend.gate import gate_runner
        results = gate_runner.run()
    assert results[0]["gate_decision"] == lock_codes.UNKNOWN == mocks[8].call_args[0][0]["stop_code"]
    alert.assert_called_once()
    assert alert.call_args[0][0] == "demo" and [r["ticker"] for r in alert.call_args[0][1]] == ["NVDA"]


def test_the_demo_runner_records_overflow_beyond_max_positions():
    # One position open (AAPL), max 1: NVDA needs an overflow slot at 0.5 × 1.05 = 0.525.
    results, mocks = _run_demo(score=0.51, open_tickers=("AAPL",), max_positions=1)
    assert results[0]["stop_code"] == "FILTERED_L2_RISK_OVERFLOW"
    assert results[0]["rule_states"][K(2, "overflow")] == FAIL
    results, _ = _run_demo(score=0.65, open_tickers=("AAPL",), max_positions=1)
    assert results[0]["outcome"] == "TRADE_EXECUTED" and results[0]["rule_states"][K(2, "overflow")] == PASS
    results, _ = _run_demo(score=0.65)                                  # a free slot passes overflow
    assert results[0]["rule_states"][K(2, "overflow")] == PASS


def test_skip_rows_carry_outcome_reason_so_the_insert_guard_stays_quiet():
    results, mocks = _run_demo(open_tickers=("NVDA",))
    assert "outcome_reason" in mocks[8].call_args[0][0]


def test_demo_gate_history_stores_outcome_reason():
    row = {"timestamp": "2026-10-29T15:00:00+00:00", "ticker": "ZZOR", "sector": "Technology", "signal_score": 0.7,
           "lock1_pass": 1, "lock2_pass": 1, "lock_leading_pass": 1, "lock_leading_checks": None, "lock3_pass": 1,
           "gate_decision": "TRADE_REJECTED", "lock3_reasoning": None, "l2_summary": None,
           "lock3_sentiment_score": None, "lock3_conviction": None, "macro_reason": None, "ticker_signal": None,
           "earnings_near": None, "days_to_earnings": None, "overflow_slot": False, "cycle_started_at": None,
           "signal_ts": None, "bar_date": None, "outcome_reason": "loss_cap"}
    db.insert_demo_gate_result(row)
    conn = db.get_db()
    try:
        got = conn.execute("SELECT outcome_reason FROM demo_gate_history WHERE ticker = 'ZZOR'").fetchone()[0]
        conn.execute("DELETE FROM demo_gate_history WHERE ticker = 'ZZOR'")
        conn.commit()
    finally:
        conn.close()
    assert got == "loss_cap"


# ── The live execution phase (writer 4) ───────────────────────────────────────────────────────────

def _run_live(price=200.0, broker_raises=None, **kw):
    from tests.test_gate_runners import _live_patches, _signal
    patches = _live_patches(candidates=[_signal(price=price)], **kw)
    with ExitStack() as st:
        mocks = [st.enter_context(p) for p in patches]
        if broker_raises is not None:
            mocks[14].side_effect = broker_raises
        from backend.gate import gate_runner_live
        return gate_runner_live.run(), mocks


def _stored(mocks) -> dict:
    return mocks[9].call_args[0][0]                     # insert_live_gate_result's row


LIVE_ORDER = ("position_open", "price_missing", "notional_too_small", "sector_cap")


def test_an_executed_live_order_passed_every_execution_check():
    results, mocks = _run_live()
    assert results[0]["outcome"] == "TRADE_EXECUTED"
    assert all(results[0]["rule_states"][KEY.get(i) or K(lock_codes.rule_for(i).owner, i)] == PASS
               for i in LIVE_ORDER)
    assert lock_codes.stop_of(results[0]["rule_states"]) is None


def test_a_notional_below_one_share_is_refused_at_l1_before_the_chain_or_before_the_broker():
    # LLY 2026-10-12..10-16: $517-862 against a $1181-1272 share reached the broker as qty 0 (a "broker error"),
    # after a Claude call each time. Before the chain: the largest notional (10 % of $10,000) buys no $1,200 share.
    results, mocks = _run_live(price=1200.0)
    mocks[11].assert_not_called() and mocks[14].assert_not_called()
    assert results == [] and _stored(mocks)["stop_code"] == "FILTERED_L1_ELIGIBILITY_NOTIONAL_TOO_SMALL"
    # At execution: 15 % could buy a share, Claude's 10 % ($1,000) cannot; refused before the broker.
    results, mocks = _run_live(price=1200.0, cfg_overrides={"max_position_size": 0.15})
    mocks[14].assert_not_called()
    assert _stored(mocks)["outcome_reason"] == "notional_too_small"
    assert lock_codes.stop_of(results[0]["rule_states"]) == (1, "notional_too_small")
    assert results[0]["lock3_pass"] == 1                 # Claude approved; L1 refused the order after it
    results, mocks = _run_live(price=999.0)             # one share fits: the order goes
    assert results[0]["outcome"] == "TRADE_EXECUTED" and mocks[14].called


def test_a_missing_price_is_refused_at_l1_before_the_chain():
    # No price: refused before the chain (no Grok, options or Claude call) and before the broker.
    results, mocks = _run_live(price=None)
    mocks[11].assert_not_called()                       # evaluate_chain
    mocks[14].assert_not_called()
    row = _stored(mocks)
    assert results == [] and row["outcome_reason"] == "price_missing"
    assert row["stop_code"] == "FILTERED_L1_ELIGIBILITY_PRICE_MISSING" and row["lock1_pass"] is None


def test_a_broker_refusal_is_l6_broker_error_and_does_not_cool_the_ticker():
    results, mocks = _run_live(broker_raises=RuntimeError("insufficient buying power"))
    assert results[0]["outcome"] == "FILTERED_L6_REVIEW" and results[0]["lock3_pass"] == 1
    assert _stored(mocks)["outcome_reason"].startswith("broker_error: insufficient")
    stop = lock_codes.stop_of(results[0]["rule_states"])
    assert stop == (6, "broker_error") and lock_codes.stop_code(*stop) not in lock_codes.COOLOFF_STOPS


def test_the_live_sector_cap_and_overflow_are_recorded():
    results, _ = _run_live(cfg_overrides={"max_sector_exposure": 0.05})   # $1,000 of $10,000 is 10 % > 5 %
    assert results[0]["outcome"] == "FILTERED_L2_RISK"
    assert lock_codes.stop_of(results[0]["rule_states"]) == (2, "sector_cap")
    held = [{"ticker": "AAPL", "cost_basis": 100.0}]
    results, _ = _run_live(positions=held, cfg_overrides={"max_positions": 1})   # NVDA 0.65 ≥ 0.5 × 1.05
    assert results[0]["rule_states"][K(2, "overflow")] == PASS and results[0]["outcome"] == "TRADE_EXECUTED"
