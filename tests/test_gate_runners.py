"""
Gate runner tests — covers demo (gate_runner.py) and live (gate_runner_live.py)
orchestration logic:
  - Skip / cooloff guards
  - Full pipeline (L1 Eligibility → L2 Quant → L3 Sentiment → L4 Leading → L5 Claude)
  - Trade execution and rejection
  - gate_decision derived from outcome (not from lock internals)
  - Live-specific: LIVE_ENABLED flag, account blocked, daily loss cap,
    Alpaca pre-flight, max positions, notional floor

Patching strategy:
  - evaluate_chain   → patch at gate_runner / gate_runner_live scope
  - Module-level DB  → patch at gate_runner[_live] scope
  - Lazy imports     → patch at their source module path
"""
import json
from contextlib import ExitStack
from unittest.mock import MagicMock, patch, call

import pytest

from backend.gate.types import LockResult
from backend.gate.chain import ChainResult


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
        "starting_balance":             10000.0,
        "max_drawdown_pct":             0.20,
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
        "starting_balance":  10000.0,
    })
    base.update(overrides)
    return base


def _lr_pass(lock_id: int, score: float = 0.75, data: dict = None) -> LockResult:
    """Build a passing LockResult with realistic data for the given lock."""
    base: dict = {}
    if lock_id == 1:
        base = {"sector": "Technology", "adjusted_score": 0.8,
                "allocation": 0.3, "rank": 1, "entry_threshold": 0.37, "exit_threshold": 0.33,
                "regime_available": True}
    elif lock_id == 2:
        base = {"signal_score": score, "threshold": 0.5,
                "effective_threshold": 0.5, "on_watchlist": False,
                "watchlist_discount": 0.0, "sector": "Technology"}
    elif lock_id == 3:
        base = {"score": 0.5, "sentiment": "positive",
                "conviction": "high", "key_themes": [], "summary": "Bullish"}
    elif lock_id == 4:
        base = {
            "sub_checks": {
                "relative_strength": {"pass": True,  "reason": "outperforms"},
                "put_call_ratio":    {"pass": True,  "reason": "P/C 0.5"},
                "unusual_calls":     {"pass": True,  "reason": "3x"},
                "volume_accumulation": {"pass": False, "reason": "up/down vol ratio 0.88 over 20d (distribution/neutral)"},
            },
            "pass_count": 3, "min_pass": 2,
        }
    elif lock_id == 5:
        base = {"decision": "BUY", "confidence": 0.85,
                "position_size_pct": 0.10,
                "reasoning": "Good setup.", "model": "claude-opus-4-6"}
    if data:
        base.update(data)
    return LockResult.pass_(lock_id=lock_id, score=score, reason="pass", data=base)


def _lr_fail(lock_id: int, reason: str = "fail") -> LockResult:
    return LockResult.fail(lock_id=lock_id, reason=reason, data={})


def _chain_pass(ticker: str = "NVDA", sector: str = "Technology") -> ChainResult:
    """Return a fully-passing ChainResult (all 5 locks)."""
    lr = {i: _lr_pass(i) for i in range(1, 6)}
    return ChainResult(
        ticker=ticker, sector=sector, approved=True, exit_lock=None,
        lock_results=lr, final_score=0.85, summary="APPROVED",
    )


def _chain_fail_at(
    exit_lock: int,
    ticker: str = "NVDA",
    sector: str = "Technology",
    reason: str = "fail",
) -> ChainResult:
    """Return a ChainResult that failed at exit_lock (all prior locks passed)."""
    lr = {i: _lr_pass(i) for i in range(1, exit_lock)}
    lr[exit_lock] = _lr_fail(exit_lock, reason)
    return ChainResult(
        ticker=ticker, sector=sector, approved=False, exit_lock=exit_lock,
        lock_results=lr, final_score=0.3,
        summary=f"REJECTED at Lock {exit_lock} — {reason}",
    )


def _wallet_ctx():
    return {"balance": 10000, "open_positions": 0, "sector_exposure": {}}


# ─────────────────────────────────────────────────────────────────────────────
# Demo gate runner
# ─────────────────────────────────────────────────────────────────────────────

def _demo_patches(candidates, open_tickers=None, failed_tickers=None,
                  chain_result=None, trade_result=True, cfg_overrides=None):
    """
    Build the patch list for demo gate runner tests.

    Mock index reference:
      0  get_demo_config
      1  get_ticker_thresholds
      2  compute_dynamic_caps
      3  compute_sector_regime
      4  get_lock1_candidates
      5  get_open_tickers
      6  get_recently_failed_tickers
      7  update_signal_gate
      8  insert_demo_gate_result
      9  evaluate_chain
      10 wallet.execute_trade
      11 compute_ticker_rotation_scores
      12 get_rotation_forecast
      13 _get_regime_bayes
      14 get_wallet_context
      15 get_recently_exited_tickers
    """
    cfg = _demo_cfg(**(cfg_overrides or {}))
    # Default chain: all pass
    cr = chain_result if chain_result is not None else _chain_pass()
    wallet_ctx = {"balance": 2000.0, "open_positions": 0, "sector_exposure": {}}
    return [
        # Lazy imports inside run() → patch at source
        patch("backend.demo_config.get_demo_config",           return_value=cfg),        # 0
        patch("backend.db.get_ticker_thresholds",              return_value={}),          # 1
        patch("backend.sector_caps.compute_dynamic_caps",      return_value={}),          # 2
        patch("backend.sector_regime.compute_sector_regime",   return_value={"available": False}),  # 3
        # Module-level imports → patch at gate_runner scope
        patch("backend.gate.gate_runner.get_lock1_candidates",
              return_value=candidates),                                                    # 4
        patch("backend.gate.gate_runner.get_open_tickers",
              return_value=open_tickers or set()),                                         # 5
        patch("backend.gate.gate_runner.get_recently_failed_tickers",
              return_value=failed_tickers or set()),                                       # 6
        patch("backend.gate.gate_runner.update_signal_gate"),                             # 7
        patch("backend.gate.gate_runner.insert_demo_gate_result"),                        # 8
        # Chain evaluation — replaces individual lock patches
        patch("backend.gate.gate_runner.evaluate_chain", return_value=cr),               # 9
        patch("backend.gate.gate_runner.wallet.execute_trade", return_value=trade_result),  # 10
        # Lazy imports for rotation + Bayesian — patch at source
        patch("backend.sector_transitions.compute_ticker_rotation_scores", return_value={}),  # 11
        patch("backend.sector_transitions.get_rotation_forecast",
              return_value={"available": False, "watching": [], "likely_next": []}),      # 12
        patch("backend.scheduler._get_regime_bayes",
              return_value=MagicMock(last_result=MagicMock(return_value=None))),          # 13
        patch("backend.gate.gate_runner.get_wallet_context",
              return_value=wallet_ctx),                                                    # 14
        patch("backend.gate.gate_runner.get_recently_exited_tickers",
              return_value=set()),                                                          # 15
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

    def test_eligibility_fail_returns_filtered_eligibility(self):
        results, _ = _run_demo(candidates=[_signal()],
                               chain_result=_chain_fail_at(1))
        assert results[0]["outcome"]       == "FILTERED_ELIGIBILITY"
        assert results[0]["gate_decision"] == "FILTERED_ELIGIBILITY"

    def test_l1_fail_returns_filtered_l1(self):
        results, _ = _run_demo(candidates=[_signal()],
                               chain_result=_chain_fail_at(2))
        assert results[0]["outcome"]       == "FILTERED_L1"
        assert results[0]["gate_decision"] == "FILTERED_L1"

    def test_l2_fail_returns_filtered_l2(self):
        results, _ = _run_demo(candidates=[_signal()],
                               chain_result=_chain_fail_at(3))
        assert results[0]["outcome"]       == "FILTERED_L2"
        assert results[0]["gate_decision"] == "FILTERED_L2"

    def test_leading_fail_returns_filtered_leading(self):
        results, _ = _run_demo(candidates=[_signal()],
                               chain_result=_chain_fail_at(4))
        assert results[0]["outcome"]       == "FILTERED_LEADING"
        assert results[0]["gate_decision"] == "FILTERED_LEADING"

    def test_l3_fail_returns_filtered_l3(self):
        results, _ = _run_demo(candidates=[_signal()],
                               chain_result=_chain_fail_at(5))
        assert results[0]["outcome"]       == "FILTERED_L3"
        assert results[0]["gate_decision"] == "FILTERED_L3"

    def test_all_pass_trade_executed(self):
        results, _ = _run_demo(candidates=[_signal()], trade_result=True)
        assert results[0]["outcome"]       == "TRADE_EXECUTED"
        assert results[0]["gate_decision"] == "TRADE_EXECUTED"

    def test_all_pass_wallet_rejects_trade(self):
        results, _ = _run_demo(candidates=[_signal()], trade_result=None)
        assert results[0]["outcome"]       == "TRADE_REJECTED"
        assert results[0]["gate_decision"] == "TRADE_REJECTED"

    def test_gate_decision_not_derived_from_lock_internals(self):
        # Regression: gate_decision must never echo lock5's "BUY" decision
        results, _ = _run_demo(candidates=[_signal()], trade_result=True)
        assert results[0]["gate_decision"] == "TRADE_EXECUTED"
        assert results[0]["gate_decision"] != "BUY"

    def test_all_lock_passes_written_to_db(self):
        results, mocks = _run_demo(candidates=[_signal()], trade_result=True)
        saved = mocks[7].call_args[0][1]  # update_signal_gate second arg
        assert saved["lock1_pass"]        == 1  # quant (L2) → old lock1 column
        assert saved["lock2_pass"]        == 1  # sentiment (L3) → old lock2 column
        assert saved["lock_leading_pass"] == 1
        assert saved["lock3_pass"]        == 1  # claude (L5) → old lock3 column

    def test_leading_checks_json_written_to_db(self):
        results, mocks = _run_demo(candidates=[_signal()], trade_result=True)
        saved = mocks[7].call_args[0][1]
        assert saved["lock_leading_checks"] is not None
        parsed = json.loads(saved["lock_leading_checks"])
        assert "relative_strength" in parsed

    def test_multiple_candidates_all_evaluated(self):
        sigs = [_signal("NVDA", sid=1), _signal("AAPL", sid=2),
                _signal("MSFT", sid=3)]
        results, _ = _run_demo(candidates=sigs, trade_result=True)
        assert len(results) == 3

    def test_evaluation_exception_does_not_crash_runner(self):
        patches = _demo_patches(candidates=[_signal()])
        boom = patch("backend.gate.gate_runner.evaluate_chain",
                     side_effect=RuntimeError("upstream exploded"))
        with ExitStack() as stack:
            mocks = [stack.enter_context(p) for p in patches]
            stack.enter_context(boom)
            from backend.gate import gate_runner
            results = gate_runner.run()
        assert results == []
        # Exception during evaluation — nothing should be written to DB for that ticker
        insert_mock = mocks[8]  # insert_demo_gate_result
        insert_mock.assert_not_called()


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
                  chain_result=None, account=None, positions=None,
                  order_id="ord-123", cfg_overrides=None, live_enabled=True,
                  unreconciled=None, apex_day_pnl=None):
    """
    Build the patch list for live gate runner tests.

    Mock index reference:
      0  LIVE_ENABLED
      1  get_unreconciled_live_trades
      2  alert_gate_blocked
      3  get_live_config
      4  get_ticker_thresholds
      5  compute_sector_regime
      6  get_lock1_candidates
      7  get_open_live_tickers
      8  get_recently_failed_live_tickers
      9  insert_live_gate_result
      10 insert_live_trade
      11 evaluate_chain
      12 alpaca.get_account
      13 alpaca.get_positions
      14 alpaca.place_bracket_order
      15 alert_daily_loss_cap
      16 alert_trade_executed
      17 alert_data_quality_divergence
      18 _compute_apex_day_pnl
      19 compute_ticker_rotation_scores
      20 get_rotation_forecast
      21 _get_regime_bayes
      22 get_recently_exited_live_tickers
    """
    cfg       = _live_cfg(**(cfg_overrides or {}))
    acct      = account  or _account()
    positions = positions or []
    cr        = chain_result if chain_result is not None else _chain_pass()
    return [
        # Module-level constant
        patch("backend.gate.gate_runner_live.LIVE_ENABLED", live_enabled),              # 0
        # UNRECONCILED gate — checked before the broker is touched at all
        patch("backend.gate.gate_runner_live.get_unreconciled_live_trades",
              return_value=unreconciled or []),                                         # 1
        patch("backend.alerts.alert_gate_blocked"),                                     # 2
        # Lazy imports inside run() → patch at source
        patch("backend.live_config.get_live_config",           return_value=cfg),       # 3
        patch("backend.db.get_ticker_thresholds",              return_value={}),        # 4
        patch("backend.sector_regime.compute_sector_regime",   return_value={"available": False}),  # 5
        # Module-level imports → patch at gate_runner_live scope
        patch("backend.gate.gate_runner_live.get_lock1_candidates",
              return_value=candidates),                                                  # 6
        patch("backend.gate.gate_runner_live.get_open_live_tickers",
              return_value=open_tickers or set()),                                       # 7
        patch("backend.gate.gate_runner_live.get_recently_failed_live_tickers",
              return_value=failed_tickers or set()),                                     # 8
        patch("backend.gate.gate_runner_live.insert_live_gate_result"),                 # 9
        patch("backend.gate.gate_runner_live.insert_live_trade"),                       # 10
        # Chain evaluation — replaces individual lock patches
        patch("backend.gate.gate_runner_live.evaluate_chain", return_value=cr),        # 11
        # Broker — lazy import, patch at source
        patch("backend.brokers.alpaca.get_account",          return_value=acct),       # 12
        patch("backend.brokers.alpaca.get_positions",        return_value=positions),  # 13
        patch("backend.brokers.alpaca.place_bracket_order",  return_value=order_id),  # 14
        # Alerts
        patch("backend.alerts.alert_daily_loss_cap"),                                   # 15
        patch("backend.alerts.alert_trade_executed"),                                   # 16
        patch("backend.alerts.alert_data_quality_divergence"),
        # Dual P&L check (2026-08-11) — real DB calls otherwise; harness has
        # no reason to model live_trades rows. Default APEX-side figure
        # agrees with the broker's day_pnl (divergence 0) so tests unrelated
        # to the data-quality gate don't silently trip it — the gate now
        # runs every cycle, independent of the loss-cap check (2026-08-11
        # cap-independence fix), so any test whose broker day_pnl disagreed
        # with a hardcoded 0.0 by >= LIVE_DATA_QUALITY_DIVERGENCE would halt
        # via data-quality before ever reaching the logic it's testing.
        # Tests that specifically want a divergence (or the loss-cap path
        # in particular) pass apex_day_pnl explicitly.
        patch("backend.gate.gate_runner_live._compute_apex_day_pnl",
              return_value=(apex_day_pnl if apex_day_pnl is not None else acct["day_pnl"], [])),
        # Lazy imports for rotation + Bayesian — patch at source
        patch("backend.sector_transitions.compute_ticker_rotation_scores", return_value={}),  # 17
        patch("backend.sector_transitions.get_rotation_forecast",
              return_value={"available": False, "watching": [], "likely_next": []}),    # 18
        patch("backend.scheduler._get_regime_bayes",
              return_value=MagicMock(last_result=MagicMock(return_value=None))),        # 19
        patch("backend.gate.gate_runner_live.get_recently_exited_live_tickers",
              return_value=set()),                                                       # 20
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
            mocks = [stack.enter_context(p) for p in patches]
            stack.enter_context(boom)
            from backend.gate import gate_runner_live
            results = gate_runner_live.run()
        assert results == []
        # Pre-flight failure — no evaluation happened, nothing written to DB
        insert_mock = mocks[9]  # insert_live_gate_result
        insert_mock.assert_not_called()

    def test_daily_loss_cap_exceeded_returns_empty(self):
        """
        apex_day_pnl is set to agree with the broker (divergence 0) so this
        exercises the genuine loss-cap path specifically, not the
        data-quality gate — which now runs first, every cycle, and would
        otherwise halt on the very same day_pnl before this branch is ever
        reached (see TestDataQualityDivergence in test_live_reconciliation.py
        for that branch's own coverage).

        _ny_today is pinned and its latch cleared before/after — the
        loss_cap:<date> latch is one-shot per real day, and this test and
        test_daily_loss_at_cap_returns_empty would otherwise collide on
        today's real date and silently suppress the second test's alert.
        """
        from backend.db import clear_alert_latch
        clear_alert_latch("loss_cap:2026-08-11")
        with patch("backend.gate.gate_runner_live._ny_today", return_value="2026-08-11"):
            results, mocks = _run_live(candidates=[_signal()],
                                   account=_account(day_pnl=-600.0),
                                   apex_day_pnl=-600.0,
                                   cfg_overrides={"daily_loss_cap": 500.0})
        clear_alert_latch("loss_cap:2026-08-11")
        assert results == []
        mocks[15].assert_called_once()   # alert_daily_loss_cap
        mocks[17].assert_not_called()    # alert_data_quality_divergence

    def test_daily_loss_at_cap_returns_empty(self):
        # Exactly at cap → exceeded (>=). apex_day_pnl agrees, same reasoning
        # as above. Own latch key/date — no collision with the test above.
        from backend.db import clear_alert_latch
        clear_alert_latch("loss_cap:2026-08-12")
        with patch("backend.gate.gate_runner_live._ny_today", return_value="2026-08-12"):
            results, mocks = _run_live(candidates=[_signal()],
                                   account=_account(day_pnl=-500.0),
                                   apex_day_pnl=-500.0,
                                   cfg_overrides={"daily_loss_cap": 500.0})
        clear_alert_latch("loss_cap:2026-08-12")
        assert results == []
        mocks[15].assert_called_once()
        mocks[17].assert_not_called()

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
        insert_mock = mocks[9]  # insert_live_gate_result
        insert_mock.assert_called_once()
        saved = insert_mock.call_args[0][0]
        assert saved["gate_decision"] == "SKIPPED_OPEN"

    def test_cooloff_ticker_skipped(self):
        results, mocks = _run_live(candidates=[_signal("NVDA")],
                                   failed_tickers={"NVDA"})
        assert results == []
        saved = mocks[9].call_args[0][0]
        assert saved["gate_decision"] == "SKIPPED_COOLOFF"

    def test_all_skipped_no_evaluations(self):
        results, mocks = _run_live(
            candidates=[_signal("NVDA", sid=1), _signal("AAPL", sid=2)],
            open_tickers={"NVDA"},
            failed_tickers={"AAPL"},
        )
        assert results == []
        assert mocks[9].call_count == 2

    # ── Pipeline outcomes ─────────────────────────────────────────────────────

    def test_eligibility_fail(self):
        results, _ = _run_live(candidates=[_signal()],
                               chain_result=_chain_fail_at(1))
        assert results[0]["outcome"]       == "FILTERED_ELIGIBILITY"
        assert results[0]["gate_decision"] == "FILTERED_ELIGIBILITY"

    def test_l1_fail(self):
        results, _ = _run_live(candidates=[_signal()],
                               chain_result=_chain_fail_at(2))
        assert results[0]["outcome"]       == "FILTERED_L1"
        assert results[0]["gate_decision"] == "FILTERED_L1"

    def test_l2_fail(self):
        results, _ = _run_live(candidates=[_signal()],
                               chain_result=_chain_fail_at(3))
        assert results[0]["outcome"] == "FILTERED_L2"

    def test_leading_fail(self):
        results, _ = _run_live(candidates=[_signal()],
                               chain_result=_chain_fail_at(4))
        assert results[0]["outcome"] == "FILTERED_LEADING"

    def test_l3_fail(self):
        results, _ = _run_live(candidates=[_signal()],
                               chain_result=_chain_fail_at(5))
        assert results[0]["outcome"] == "FILTERED_L3"

    def test_all_pass_trade_executed(self):
        results, mocks = _run_live(candidates=[_signal()])
        assert results[0]["outcome"] == "TRADE_EXECUTED"
        mocks[14].assert_called_once()  # place_bracket_order
        mocks[10].assert_called_once()   # insert_live_trade

    def test_max_positions_reached_rejects_trade(self):
        positions = [{"ticker": f"T{i}"} for i in range(5)]
        # score=0.50 < overflow_threshold=0.525 (base 0.50 × 1.05) → overflow rejection
        results, mocks = _run_live(candidates=[_signal(score=0.50)], positions=positions,
                                   cfg_overrides={"max_positions": 5})
        assert results[0]["outcome"] == "FILTERED_OVERFLOW_QUANT"
        mocks[14].assert_not_called()

    def test_ticker_already_open_at_execution_rejected(self):
        # Race condition: position opened between evaluation and execution
        results, mocks = _run_live(candidates=[_signal("NVDA")],
                                   positions=[{"ticker": "NVDA", "cost_basis": 200.0}])
        assert results[0]["outcome"] == "TRADE_REJECTED"
        mocks[14].assert_not_called()

    def test_notional_too_small_rejected(self):
        # equity=$1 → notional = 1 * 0.10 = $0.10, below $10 floor
        results, mocks = _run_live(candidates=[_signal()],
                                   account=_account(equity=1.0, buying_power=1.0),
                                   cfg_overrides={"max_position_size": 0.10})
        assert results[0]["outcome"] == "TRADE_REJECTED"
        mocks[14].assert_not_called()

    def test_broker_exception_sets_trade_failed(self):
        patches = _live_patches(candidates=[_signal()])
        boom = patch("backend.brokers.alpaca.place_bracket_order",
                     side_effect=Exception("order rejected"))
        with ExitStack() as stack:
            mocks = [stack.enter_context(p) for p in patches]
            stack.enter_context(boom)
            from backend.gate import gate_runner_live
            results = gate_runner_live.run()
        assert results[0]["outcome"] == "TRADE_FAILED"
        # gate_decision written to DB must match the final outcome
        saved = mocks[9].call_args[0][0]  # insert_live_gate_result arg
        assert saved["gate_decision"] == "TRADE_FAILED"
        assert saved["alpaca_order_id"] is None

    def test_max_positions_gate_decision_in_db(self):
        positions = [{"ticker": f"T{i}"} for i in range(5)]
        # score=0.50 < overflow_threshold → FILTERED_OVERFLOW_QUANT, counted in overflow_fail funnel
        results, mocks = _run_live(candidates=[_signal(score=0.50)], positions=positions,
                                   cfg_overrides={"max_positions": 5})
        saved = mocks[9].call_args[0][0]
        assert saved["gate_decision"] == "FILTERED_OVERFLOW_QUANT"

    def test_gate_decision_not_buy_for_executed_trade(self):
        results, _ = _run_live(candidates=[_signal()])
        assert results[0]["gate_decision"] == "TRADE_EXECUTED"
        assert results[0]["gate_decision"] != "BUY"

    def test_insert_live_gate_result_called_per_evaluated_ticker(self):
        results, mocks = _run_live(
            candidates=[_signal("NVDA", sid=1), _signal("AAPL", sid=2)])
        assert mocks[9].call_count == 2  # insert_live_gate_result

    def test_leading_checks_stored_in_db(self):
        results, mocks = _run_live(candidates=[_signal()])
        saved = mocks[9].call_args[0][0]  # insert_live_gate_result arg
        assert saved["lock_leading_pass"] == 1
        assert saved["lock_leading_checks"] is not None
        parsed = json.loads(saved["lock_leading_checks"])
        assert "relative_strength" in parsed

    # ── Demo / live parity ────────────────────────────────────────────────────

    @pytest.mark.parametrize("exit_lock,expected_outcome", [
        (1, "FILTERED_ELIGIBILITY"),
        (2, "FILTERED_L1"),
        (3, "FILTERED_L2"),
        (4, "FILTERED_LEADING"),
        (5, "FILTERED_L3"),
    ])
    def test_demo_live_outcome_parity(self, exit_lock, expected_outcome):
        """Both runners produce identical outcome and gate_decision for each failure mode."""
        cr = _chain_fail_at(exit_lock)

        demo_results, _ = _run_demo(candidates=[_signal()], chain_result=cr)
        live_results, _ = _run_live(candidates=[_signal()], chain_result=cr)

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
        live_saved = live_mocks[9].call_args[0][0]  # insert_live_gate_result

        assert demo_saved["gate_decision"] == "SKIPPED_OPEN"
        assert live_saved["gate_decision"] == "SKIPPED_OPEN"


# ─────────────────────────────────────────────────────────────────────────────
# Mechanical pre-L5 drawdown guard (chain.py)
# ─────────────────────────────────────────────────────────────────────────────

class TestMechanicalDrawdownGuard:
    """
    The drawdown guard in evaluate_chain must reject before calling L5 when
    (starting_balance - wallet_balance) / starting_balance > max_drawdown_pct.

    This prevents the L5 failure mode observed on 2026-07-02 (BA 16:42):
    Claude returned 'REJECT on drawdown grounds' in its reasoning but 'BUY'
    in the JSON decision field — the system treated it as a pass and executed.
    """

    def _context(self, balance: float, starting: float = 2000.0, max_dd: float = 0.20):
        return {
            "wallet_balance": balance,
            "open_positions": 2,
            "sector_exposure": {},
            "risk_limits": {
                "starting_balance":    starting,
                "max_positions":       10,
                "max_sector_exposure": 0.30,
                "max_position_size":   0.10,
                "daily_loss_cap":      500.0,
                "max_drawdown_pct":    max_dd,
            },
        }

    def _run_chain(self, context: dict, cfg: dict | None = None):
        from backend.gate.chain import evaluate_chain
        return evaluate_chain(
            ticker="BA", sector="Industrials", signal_score=0.8,
            context=context, cfg=cfg or _demo_cfg(),
        )

    def _all_lock_patches(self, l5_return=None):
        """Patch all five locks at the chain module's bound names."""
        return [
            patch("backend.gate.chain.lock1_evaluate", return_value=_lr_pass(1)),
            patch("backend.gate.chain.lock2_evaluate", return_value=_lr_pass(2)),
            patch("backend.gate.chain.lock3_evaluate", return_value=_lr_pass(3)),
            patch("backend.gate.chain.lock4_evaluate", return_value=_lr_pass(4)),
            patch("backend.gate.chain.lock5_evaluate",
                  return_value=(l5_return if l5_return is not None else _lr_pass(5))),
        ]

    def test_drawdown_exceeded_rejects_before_l5(self):
        # 46.7% drawdown > 20% limit — must reject at lock 5 without calling Claude
        context = self._context(balance=1066.37, starting=2000.0, max_dd=0.20)
        patches = self._all_lock_patches()
        with ExitStack() as stack:
            mocks = [stack.enter_context(p) for p in patches]
            result = self._run_chain(context)

        assert result.approved is False
        assert result.exit_lock == 5
        assert "drawdown" in result.summary.lower()
        mocks[4].assert_not_called()  # lock5_evaluate must not be called

    def test_drawdown_at_limit_passes(self):
        # Exactly at 20% — should not be rejected by the guard (strictly greater than)
        context = self._context(balance=1600.0, starting=2000.0, max_dd=0.20)
        patches = self._all_lock_patches()
        with ExitStack() as stack:
            [stack.enter_context(p) for p in patches]
            result = self._run_chain(context)

        assert result.approved is True

    def test_no_risk_limits_in_context_skips_guard(self):
        # Contexts without risk_limits must not crash and must reach L5
        context = {}
        patches = self._all_lock_patches()
        with ExitStack() as stack:
            [stack.enter_context(p) for p in patches]
            result = self._run_chain(context)

        assert result.approved is True
