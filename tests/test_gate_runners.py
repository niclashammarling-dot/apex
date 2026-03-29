"""
Gate runner tests — covers demo (gate_runner.py) and live (gate_runner_live.py)
orchestration logic:
  - Skip / cooloff guards
  - Full pipeline (L1 → Macro → L2 → Leading → L3)
  - Trade execution and rejection
  - gate_decision derived from outcome (not from lock internals)
  - Live-specific: LIVE_ENABLED flag, account blocked, daily loss cap,
    Alpaca pre-flight, max positions, notional floor

Patching strategy:
  - Module-level imports  → patch at "backend.gate.gate_runner[_live].symbol"
  - Lazy imports (inside run()) → patch at their source module path
"""
import json
from contextlib import ExitStack
from unittest.mock import MagicMock, patch, call

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Shared fixtures / helpers
# ─────────────────────────────────────────────────────────────────────────────

def _signal(ticker="NVDA", sector="Technology", score=0.65, price=200.0, sid=1):
    return {"id": sid, "ticker": ticker, "sector": sector,
            "signal_score": score, "price": price}


def _demo_cfg(**overrides):
    base = {
        "lock1_threshold":              0.5,
        "lock2_sentiment_min":          0.2,
        "lock3_confidence_min":         0.6,
        "lock_leading_min_pass":        2,
        "vix_threshold":                25.0,
        "macro_event_blackout_days":    1,
        "macro_earnings_blackout_days": 3,
        "gate_cooloff_hours":           4,
        "max_positions":                4,
        "max_sector_exposure":          0.25,
        "max_position_size":            0.15,
        "daily_loss_cap":               500.0,
        "take_profit_pct":              0.08,
        "stop_loss_pct":                0.04,
    }
    base.update(overrides)
    return base


def _live_cfg(**overrides):
    base = _demo_cfg()
    base.update({
        "daily_loss_cap":    500.0,
        "max_positions":     5,
        "max_position_size": 0.10,
    })
    base.update(overrides)
    return base


def _lock_pass():
    return {
        "passed": True, "score": 0.7, "threshold": 0.5, "reason": "pass",
        "decision": "BUY", "confidence": 0.85, "position_size_pct": 0.1,
        "reasoning": "Good setup.", "volume": "high", "key_themes": [],
        "summary": "Bullish.", "pass_count": 3, "min_pass": 2,
        "checks": {
            "relative_strength": {"pass": True,  "reason": "outperforms"},
            "put_call_ratio":    {"pass": True,  "reason": "P/C 0.5"},
            "unusual_calls":     {"pass": True,  "reason": "3x"},
            "insider_cluster":   {"pass": False, "reason": "1 filer"},
        },
    }


def _lock_fail(reason="fail"):
    return {
        "passed": False, "score": 0.3, "reason": reason,
        "decision": "HOLD", "confidence": 0.0, "position_size_pct": 0.0,
        "reasoning": None, "volume": "low", "key_themes": [], "summary": None,
        "pass_count": 0, "min_pass": 2,
        "checks": {
            "relative_strength": {"pass": False, "reason": "lag"},
            "put_call_ratio":    {"pass": False, "reason": "bearish"},
            "unusual_calls":     {"pass": False, "reason": "low vol"},
            "insider_cluster":   {"pass": False, "reason": "none"},
        },
    }


def _wallet_ctx():
    return {"balance": 10000, "open_positions": 0, "sector_exposure": {}}


# ─────────────────────────────────────────────────────────────────────────────
# Demo gate runner
# ─────────────────────────────────────────────────────────────────────────────

def _demo_patches(candidates, open_tickers=None, failed_tickers=None,
                  l1=None, lm=None, l2=None, l_leading=None, l3=None,
                  trade_result=True, cfg_overrides=None):
    cfg = _demo_cfg(**(cfg_overrides or {}))
    return [
        # Lazy imports inside run() → patch at source
        patch("backend.demo_config.get_demo_config",           return_value=cfg),
        patch("backend.db.get_ticker_thresholds",              return_value={}),
        patch("backend.sector_caps.compute_dynamic_caps",      return_value={}),
        patch("backend.sector_regime.compute_sector_regime",   return_value={"available": False}),
        # Module-level imports → patch at gate_runner scope
        patch("backend.gate.gate_runner.get_lock1_candidates",
              return_value=candidates),
        patch("backend.gate.gate_runner.get_open_tickers",
              return_value=open_tickers or set()),
        patch("backend.gate.gate_runner.get_recently_failed_tickers",
              return_value=failed_tickers or set()),
        patch("backend.gate.gate_runner.update_signal_gate"),
        patch("backend.gate.gate_runner.get_wallet_context",   return_value=_wallet_ctx()),
        patch("backend.gate.gate_runner.lock1_quant.evaluate", return_value=l1 or _lock_pass()),
        patch("backend.gate.gate_runner.lock_macro.evaluate",  return_value=lm or _lock_pass()),
        patch("backend.gate.gate_runner.lock2_sentiment.evaluate",
              return_value=l2 or _lock_pass()),
        patch("backend.gate.gate_runner.lock_leading.evaluate",
              return_value=l_leading or _lock_pass()),
        patch("backend.gate.gate_runner.lock3_claude.evaluate",
              return_value=l3 or _lock_pass()),
        patch("backend.gate.gate_runner.wallet.execute_trade", return_value=trade_result),
    ]


def _run_demo(**kwargs):
    from backend.gate import gate_runner
    patches = _demo_patches(**kwargs)
    with ExitStack() as stack:
        mocks = [stack.enter_context(p) for p in patches]
        return gate_runner.run(), mocks


class TestDemoGateRunner:

    def test_no_candidates_returns_empty(self):
        results, _ = _run_demo(candidates=[])
        assert results == []

    def test_all_candidates_skipped_open(self):
        results, mocks = _run_demo(candidates=[_signal("NVDA")],
                                   open_tickers={"NVDA"})
        assert results == []
        update_mock = mocks[7]  # update_signal_gate
        update_mock.assert_called_once()
        saved = update_mock.call_args[0][1]
        assert saved["gate_decision"] == "SKIPPED_OPEN"

    def test_all_candidates_skipped_cooloff(self):
        results, mocks = _run_demo(candidates=[_signal("NVDA")],
                                   failed_tickers={"NVDA"})
        assert results == []
        saved = mocks[7].call_args[0][1]
        assert saved["gate_decision"] == "SKIPPED_COOLOFF"

    def test_mixed_skip_and_evaluate(self):
        results, mocks = _run_demo(
            candidates=[_signal("NVDA", sid=1), _signal("AAPL", sid=2)],
            open_tickers={"NVDA"},
        )
        assert len(results) == 1
        assert results[0]["ticker"] == "AAPL"
        # Two DB writes: one SKIPPED_OPEN + one evaluation result
        assert mocks[7].call_count == 2

    def test_l1_fail_returns_filtered_l1(self):
        results, _ = _run_demo(candidates=[_signal()], l1=_lock_fail())
        assert results[0]["outcome"]      == "FILTERED_L1"
        assert results[0]["gate_decision"] == "FILTERED_L1"

    def test_macro_fail_returns_filtered_macro(self):
        results, _ = _run_demo(candidates=[_signal()], lm=_lock_fail())
        assert results[0]["outcome"]      == "FILTERED_MACRO"
        assert results[0]["gate_decision"] == "FILTERED_MACRO"

    def test_l2_fail_returns_filtered_l2(self):
        results, _ = _run_demo(candidates=[_signal()], l2=_lock_fail())
        assert results[0]["outcome"]      == "FILTERED_L2"
        assert results[0]["gate_decision"] == "FILTERED_L2"

    def test_leading_fail_returns_filtered_leading(self):
        results, _ = _run_demo(candidates=[_signal()], l_leading=_lock_fail())
        assert results[0]["outcome"]      == "FILTERED_LEADING"
        assert results[0]["gate_decision"] == "FILTERED_LEADING"

    def test_l3_fail_returns_filtered_l3(self):
        results, _ = _run_demo(candidates=[_signal()], l3=_lock_fail())
        assert results[0]["outcome"]      == "FILTERED_L3"
        assert results[0]["gate_decision"] == "FILTERED_L3"

    def test_all_pass_trade_executed(self):
        results, _ = _run_demo(candidates=[_signal()], trade_result=True)
        assert results[0]["outcome"]      == "TRADE_EXECUTED"
        assert results[0]["gate_decision"] == "TRADE_EXECUTED"

    def test_all_pass_wallet_rejects_trade(self):
        results, _ = _run_demo(candidates=[_signal()], trade_result=None)
        assert results[0]["outcome"]      == "TRADE_REJECTED"
        assert results[0]["gate_decision"] == "TRADE_REJECTED"

    def test_gate_decision_not_derived_from_lock_internals(self):
        # Regression: before the fix, l3["decision"]="BUY" was stored as gate_decision
        results, _ = _run_demo(candidates=[_signal()], trade_result=True)
        assert results[0]["gate_decision"] == "TRADE_EXECUTED"
        assert results[0]["gate_decision"] != "BUY"

    def test_all_lock_passes_written_to_db(self):
        results, mocks = _run_demo(candidates=[_signal()], trade_result=True)
        saved = mocks[7].call_args[0][1]  # update_signal_gate second arg
        assert saved["lock1_pass"]        == 1
        assert saved["lock2_pass"]        == 1
        assert saved["lock_leading_pass"] == 1
        assert saved["lock3_pass"]        == 1

    def test_leading_checks_json_written_to_db(self):
        results, mocks = _run_demo(candidates=[_signal()], trade_result=True)
        saved = mocks[7].call_args[0][1]
        assert saved["lock_leading_checks"] is not None
        parsed = json.loads(saved["lock_leading_checks"])
        assert "relative_strength" in parsed

    def test_sector_threshold_overrides_config_threshold(self):
        l1_spy = MagicMock(return_value=_lock_fail("below sector threshold"))
        patches = _demo_patches(candidates=[_signal(sector="Energy", score=0.55)])
        sector_thresh = patch("backend.db.get_ticker_thresholds",
                              return_value={"Energy": 0.60})
        l1_patch = patch("backend.gate.gate_runner.lock1_quant.evaluate", l1_spy)
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(sector_thresh)
            stack.enter_context(l1_patch)
            from backend.gate import gate_runner
            gate_runner.run()
        called_threshold = l1_spy.call_args[1].get("threshold") \
                           or l1_spy.call_args[0][1]
        assert called_threshold == 0.60

    def test_multiple_candidates_all_evaluated(self):
        sigs = [_signal("NVDA", sid=1), _signal("AAPL", sid=2),
                _signal("MSFT", sid=3)]
        results, _ = _run_demo(candidates=sigs, trade_result=True)
        assert len(results) == 3

    def test_evaluation_exception_does_not_crash_runner(self):
        patches = _demo_patches(candidates=[_signal()])
        boom = patch("backend.gate.gate_runner.lock1_quant.evaluate",
                     side_effect=RuntimeError("upstream exploded"))
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(boom)
            from backend.gate import gate_runner
            results = gate_runner.run()
        assert results == []


# ─────────────────────────────────────────────────────────────────────────────
# Live gate runner
# ─────────────────────────────────────────────────────────────────────────────

def _account(equity=10000.0, buying_power=10000.0, day_pnl=0.0,
             trading_blocked=False, account_blocked=False):
    return {
        "equity": equity, "buying_power": buying_power,
        "day_pnl": day_pnl, "day_pnl_pct": 0.0,
        "trading_blocked": trading_blocked,
        "account_blocked": account_blocked,
        "status": "ACTIVE",
    }


def _live_patches(candidates, open_tickers=None, failed_tickers=None,
                  l1=None, lm=None, l2=None, l_leading=None, l3=None,
                  account=None, positions=None, order_id="ord-123",
                  cfg_overrides=None, live_enabled=True):
    cfg      = _live_cfg(**(cfg_overrides or {}))
    acct     = account  or _account()
    positions = positions or []
    return [
        # Module-level constant
        patch("backend.gate.gate_runner_live.LIVE_ENABLED", live_enabled),
        # Lazy imports inside run() → patch at source
        patch("backend.live_config.get_live_config",             return_value=cfg),
        patch("backend.db.get_ticker_thresholds",                return_value={}),
        patch("backend.sector_regime.compute_sector_regime",     return_value={"available": False}),
        # Module-level imports → patch at gate_runner_live scope
        patch("backend.gate.gate_runner_live.get_lock1_candidates",
              return_value=candidates),
        patch("backend.gate.gate_runner_live.get_open_live_tickers",
              return_value=open_tickers or set()),
        patch("backend.gate.gate_runner_live.get_recently_failed_live_tickers",
              return_value=failed_tickers or set()),
        patch("backend.gate.gate_runner_live.insert_live_gate_result"),
        patch("backend.gate.gate_runner_live.insert_live_trade"),
        patch("backend.gate.gate_runner_live.lock1_quant.evaluate",
              return_value=l1 or _lock_pass()),
        patch("backend.gate.gate_runner_live.lock_macro.evaluate",
              return_value=lm or _lock_pass()),
        patch("backend.gate.gate_runner_live.lock2_sentiment.evaluate",
              return_value=l2 or _lock_pass()),
        patch("backend.gate.gate_runner_live.lock_leading.evaluate",
              return_value=l_leading or _lock_pass()),
        patch("backend.gate.gate_runner_live.lock3_claude.evaluate",
              return_value=l3 or _lock_pass()),
        # Broker — lazy import, patch at source
        patch("backend.brokers.alpaca.get_account",          return_value=acct),
        patch("backend.brokers.alpaca.get_positions",        return_value=positions),
        patch("backend.brokers.alpaca.place_bracket_order",  return_value=order_id),
        # Alerts
        patch("backend.alerts.alert_daily_loss_cap"),
        patch("backend.alerts.alert_trade_executed"),
    ]


def _run_live(**kwargs):
    from backend.gate import gate_runner_live
    patches = _live_patches(**kwargs)
    with ExitStack() as stack:
        mocks = [stack.enter_context(p) for p in patches]
        return gate_runner_live.run(), mocks


class TestLiveGateRunner:

    # ── Global guards ─────────────────────────────────────────────────────────

    def test_live_disabled_returns_empty(self):
        results, _ = _run_live(candidates=[_signal()], live_enabled=False)
        assert results == []

    def test_trading_blocked_returns_empty(self):
        results, _ = _run_live(candidates=[_signal()],
                               account=_account(trading_blocked=True))
        assert results == []

    def test_account_blocked_returns_empty(self):
        results, _ = _run_live(candidates=[_signal()],
                               account=_account(account_blocked=True))
        assert results == []

    def test_alpaca_unreachable_returns_empty(self):
        patches = _live_patches(candidates=[_signal()])
        boom = patch("backend.brokers.alpaca.get_account",
                     side_effect=Exception("connection refused"))
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(boom)
            from backend.gate import gate_runner_live
            results = gate_runner_live.run()
        assert results == []

    def test_daily_loss_cap_exceeded_returns_empty(self):
        results, _ = _run_live(candidates=[_signal()],
                               account=_account(day_pnl=-600.0),
                               cfg_overrides={"daily_loss_cap": 500.0})
        assert results == []

    def test_daily_loss_at_cap_returns_empty(self):
        # Exactly at cap → exceeded (>=)
        results, _ = _run_live(candidates=[_signal()],
                               account=_account(day_pnl=-500.0),
                               cfg_overrides={"daily_loss_cap": 500.0})
        assert results == []

    def test_daily_loss_below_cap_continues(self):
        results, _ = _run_live(candidates=[_signal()],
                               account=_account(day_pnl=-100.0),
                               cfg_overrides={"daily_loss_cap": 500.0})
        assert len(results) == 1

    def test_no_candidates_returns_empty(self):
        results, _ = _run_live(candidates=[])
        assert results == []

    # ── Skip guards ──────────────────────────────────────────────────────────

    def test_open_position_skipped(self):
        results, mocks = _run_live(candidates=[_signal("NVDA")],
                                   open_tickers={"NVDA"})
        assert results == []
        insert_mock = mocks[7]  # insert_live_gate_result
        insert_mock.assert_called_once()
        saved = insert_mock.call_args[0][0]
        assert saved["gate_decision"] == "SKIPPED_OPEN"

    def test_cooloff_ticker_skipped(self):
        results, mocks = _run_live(candidates=[_signal("NVDA")],
                                   failed_tickers={"NVDA"})
        assert results == []
        saved = mocks[7].call_args[0][0]
        assert saved["gate_decision"] == "SKIPPED_COOLOFF"

    def test_all_skipped_no_evaluations(self):
        results, mocks = _run_live(
            candidates=[_signal("NVDA", sid=1), _signal("AAPL", sid=2)],
            open_tickers={"NVDA"},
            failed_tickers={"AAPL"},
        )
        assert results == []
        assert mocks[7].call_count == 2

    # ── Pipeline outcomes ─────────────────────────────────────────────────────

    def test_l1_fail(self):
        results, _ = _run_live(candidates=[_signal()], l1=_lock_fail())
        assert results[0]["outcome"]      == "FILTERED_L1"
        assert results[0]["gate_decision"] == "FILTERED_L1"

    def test_macro_fail(self):
        results, _ = _run_live(candidates=[_signal()], lm=_lock_fail())
        assert results[0]["outcome"] == "FILTERED_MACRO"

    def test_l2_fail(self):
        results, _ = _run_live(candidates=[_signal()], l2=_lock_fail())
        assert results[0]["outcome"] == "FILTERED_L2"

    def test_leading_fail(self):
        results, _ = _run_live(candidates=[_signal()], l_leading=_lock_fail())
        assert results[0]["outcome"] == "FILTERED_LEADING"

    def test_l3_fail(self):
        results, _ = _run_live(candidates=[_signal()], l3=_lock_fail())
        assert results[0]["outcome"] == "FILTERED_L3"

    def test_all_pass_trade_executed(self):
        results, mocks = _run_live(candidates=[_signal()])
        assert results[0]["outcome"] == "TRADE_EXECUTED"
        mocks[16].assert_called_once()  # place_bracket_order
        mocks[8].assert_called_once()   # insert_live_trade

    def test_max_positions_reached_rejects_trade(self):
        positions = [{"ticker": f"T{i}"} for i in range(5)]
        results, mocks = _run_live(candidates=[_signal()], positions=positions,
                                   cfg_overrides={"max_positions": 5})
        assert results[0]["outcome"] == "TRADE_REJECTED"
        mocks[16].assert_not_called()

    def test_ticker_already_open_at_execution_rejected(self):
        # Race condition: position opened between evaluation and execution
        results, mocks = _run_live(candidates=[_signal("NVDA")],
                                   positions=[{"ticker": "NVDA"}])
        assert results[0]["outcome"] == "TRADE_REJECTED"
        mocks[16].assert_not_called()

    def test_notional_too_small_rejected(self):
        # equity=$1 → notional = 1 * 0.10 = $0.10, below $10 floor
        results, mocks = _run_live(candidates=[_signal()],
                                   account=_account(equity=1.0, buying_power=1.0),
                                   cfg_overrides={"max_position_size": 0.10})
        assert results[0]["outcome"] == "TRADE_REJECTED"
        mocks[16].assert_not_called()

    def test_broker_exception_sets_trade_failed(self):
        patches = _live_patches(candidates=[_signal()])
        boom = patch("backend.brokers.alpaca.place_bracket_order",
                     side_effect=Exception("order rejected"))
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(boom)
            from backend.gate import gate_runner_live
            results = gate_runner_live.run()
        assert results[0]["outcome"] == "TRADE_FAILED"

    def test_gate_decision_not_buy_for_executed_trade(self):
        results, _ = _run_live(candidates=[_signal()])
        assert results[0]["gate_decision"] == "TRADE_EXECUTED"
        assert results[0]["gate_decision"] != "BUY"

    def test_insert_live_gate_result_called_per_evaluated_ticker(self):
        results, mocks = _run_live(
            candidates=[_signal("NVDA", sid=1), _signal("AAPL", sid=2)])
        assert mocks[7].call_count == 2  # insert_live_gate_result

    def test_leading_checks_stored_in_db(self):
        results, mocks = _run_live(candidates=[_signal()])
        saved = mocks[7].call_args[0][0]  # insert_live_gate_result arg
        assert saved["lock_leading_pass"] == 1
        assert saved["lock_leading_checks"] is not None
        parsed = json.loads(saved["lock_leading_checks"])
        assert "relative_strength" in parsed

    # ── Demo / live parity ────────────────────────────────────────────────────

    @pytest.mark.parametrize("lock_to_fail,expected_outcome", [
        ("l1",        "FILTERED_L1"),
        ("lm",        "FILTERED_MACRO"),
        ("l2",        "FILTERED_L2"),
        ("l_leading", "FILTERED_LEADING"),
        ("l3",        "FILTERED_L3"),
    ])
    def test_demo_live_outcome_parity(self, lock_to_fail, expected_outcome):
        """Both runners produce identical outcome and gate_decision for each failure mode."""
        kwargs = {lock_to_fail: _lock_fail()}

        demo_results, _ = _run_demo(candidates=[_signal()], **kwargs)
        live_results, _ = _run_live(candidates=[_signal()], **kwargs)

        assert demo_results[0]["outcome"]       == expected_outcome
        assert live_results[0]["outcome"]       == expected_outcome
        assert demo_results[0]["gate_decision"] == expected_outcome
        assert live_results[0]["gate_decision"] == expected_outcome

    def test_both_runners_record_skipped_open(self):
        """Both runners write SKIPPED_OPEN to their respective DB tables."""
        sig = _signal("NVDA")

        demo_results, demo_mocks = _run_demo(candidates=[sig],
                                             open_tickers={"NVDA"})
        live_results, live_mocks = _run_live(candidates=[sig],
                                             open_tickers={"NVDA"})

        demo_saved = demo_mocks[7].call_args[0][1]  # update_signal_gate
        live_saved = live_mocks[7].call_args[0][0]  # insert_live_gate_result

        assert demo_saved["gate_decision"] == "SKIPPED_OPEN"
        assert live_saved["gate_decision"] == "SKIPPED_OPEN"
