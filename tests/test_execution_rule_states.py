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

    def execute(gr, price, **kw):
        if refuse:
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
    assert results[0]["outcome"] == "TRADE_REJECTED"
    assert mocks[8].call_args[0][0]["outcome_reason"] == "sector_cap"


def test_the_demo_runner_records_overflow_beyond_max_positions():
    # One position open (AAPL), max 1: NVDA needs an overflow slot at 0.5 × 1.05 = 0.525.
    results, mocks = _run_demo(score=0.51, open_tickers=("AAPL",), max_positions=1)
    assert results[0]["outcome"] == lock_codes.OVERFLOW
    assert results[0]["rule_states"][K(2, "overflow")] == FAIL
    results, _ = _run_demo(score=0.65, open_tickers=("AAPL",), max_positions=1)
    assert results[0]["outcome"] == "TRADE_EXECUTED" and results[0]["rule_states"][K(2, "overflow")] == PASS
    results, _ = _run_demo(score=0.65)                                  # a free slot: overflow does not run
    assert K(2, "overflow") not in results[0]["rule_states"]


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
