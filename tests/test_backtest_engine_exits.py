"""
Regression coverage for _check_exits' profit-lock ratchet semantics
(2026-08-17/18 finding): trailing_stop_pct previously replaced the fixed SL
branch entirely for the whole trade lifetime, not just after the profit-lock
trigger fired — every sweep run through this function (or engine_fast.py's
identical copy) evaluated an exit rule production stopped running on
2026-06-03. Fixed SL is now the unconditional floor until peak gain clears
profit_lock_trigger_pct, matching wallet.py's actual exit chain (see
2026-06-03-apex-trailing-stop-sl-floor-bypass).

The two synthetic cases here are the ones that discriminated the bug from
the fix before any sweep was trusted — see the session's own
verify_exit_logic.py derivation. Promoted to real tests per Niclas's
instruction so a regression here can't silently recur the way the original
defect sat unnoticed since June.
"""
from backend.backtest import engine, engine_fast


def _run_single_trade(entry_price: float, peak_price: float, current_price: float,
                       entry_date: str = "2024-01-01", today: str = "2024-01-05",
                       **exit_kwargs):
    trade = {
        "ticker": "TEST", "sector": "Test",
        "entry_date": entry_date, "entry_price": entry_price,
        "shares": 10, "amount": 1000.0, "signal_score": 0.8,
        "peak_price": peak_price,
    }
    orig_price_on = engine._price_on
    engine._price_on = lambda rd, ticker, d: current_price
    try:
        return engine._check_exits([trade], None, today, today, **exit_kwargs)
    finally:
        engine._price_on = orig_price_on


class TestProfitLockFixedSLFloor:
    """Fixed SL must stay live and unreplaced until the ratchet trigger fires."""

    def test_pre_trigger_breathing_stays_open(self):
        """
        Peak +3% (below a 4% profit-lock trigger), pulled back to -3.18% from
        entry. A real fixed-SL position (SL at -6%) would still be open —
        nowhere near the stop, and the ratchet hasn't engaged. The pre-fix
        code fired a premature TSL exit here because trailing_stop_pct alone
        was enough to replace the fixed-SL branch, regardless of whether the
        trigger had cleared.
        """
        closed = _run_single_trade(
            entry_price=100.0, peak_price=103.0, current_price=96.82,
            take_profit_pct=0.06, stop_loss_pct=0.06, time_stop_days=40,
            trailing_stop_pct=1.0,
            profit_lock_trigger_pct=0.04, profit_lock_trail_pct=0.01,
        )
        assert closed == []

    def test_straight_down_loser_still_hits_fixed_sl(self):
        """
        A position that never rises above entry (peak == entry) must still
        fire the fixed SL at exactly -6% — the fix must not disable the SL
        for losers while fixing the pre-trigger breathing case for winners.
        """
        closed = _run_single_trade(
            entry_price=100.0, peak_price=100.0, current_price=93.5,
            take_profit_pct=0.06, stop_loss_pct=0.06, time_stop_days=40,
            trailing_stop_pct=1.0,
            profit_lock_trigger_pct=0.04, profit_lock_trail_pct=0.01,
        )
        assert len(closed) == 1
        record = closed[0]["record"]
        assert record["exit_reason"] == "SL"
        assert record["outcome"] == "LOSS"
        assert round(record["pnl_pct"], 4) == -0.065

    def test_ratchet_fires_once_trigger_cleared(self):
        """Peak gain clears the 4% trigger, then pulls back past the 1% trail — ratchet should fire as TSL."""
        closed = _run_single_trade(
            entry_price=100.0, peak_price=105.0, current_price=103.9,  # 1.05% below peak
            take_profit_pct=0.06, stop_loss_pct=0.06, time_stop_days=40,
            trailing_stop_pct=1.0,
            profit_lock_trigger_pct=0.04, profit_lock_trail_pct=0.01,
        )
        assert len(closed) == 1
        assert closed[0]["record"]["exit_reason"] == "TSL"

    def test_days_held_populated(self):
        """
        days_held was computed but never written to TradeRecord until
        2026-08-18 — every sweep's avg_hold column silently read 0 for every
        trade, in every run, since this function was written.
        """
        closed = _run_single_trade(
            entry_price=100.0, peak_price=100.0, current_price=93.5,
            entry_date="2024-01-01", today="2024-01-10",
            take_profit_pct=0.06, stop_loss_pct=0.06, time_stop_days=40,
            trailing_stop_pct=1.0,
            profit_lock_trigger_pct=0.04, profit_lock_trail_pct=0.01,
        )
        assert closed[0]["record"]["days_held"] is not None
        assert closed[0]["record"]["days_held"] > 0


class TestMalformedProfitLockConfig:
    """One of trigger/trail set without the other previously silently no-op'd."""

    def test_trigger_without_trail_raises(self):
        import pytest
        with pytest.raises(ValueError, match="must both be set"):
            _run_single_trade(
                entry_price=100.0, peak_price=100.0, current_price=100.0,
                take_profit_pct=0.06, stop_loss_pct=0.06, time_stop_days=40,
                profit_lock_trigger_pct=0.04, profit_lock_trail_pct=None,
            )

    def test_trail_without_trigger_raises(self):
        import pytest
        with pytest.raises(ValueError, match="must both be set"):
            _run_single_trade(
                entry_price=100.0, peak_price=100.0, current_price=100.0,
                take_profit_pct=0.06, stop_loss_pct=0.06, time_stop_days=40,
                profit_lock_trigger_pct=None, profit_lock_trail_pct=0.01,
            )


def _run_single_trade_fast(entry_price: float, peak_price: float, current_price: float,
                            entry_date: str = "2024-01-01", today: str = "2024-01-05",
                            **exit_kwargs):
    trade = {
        "ticker": "TEST", "sector": "Test",
        "entry_date": entry_date, "entry_price": entry_price,
        "shares": 10, "amount": 1000.0, "signal_score": 0.8,
        "peak_price": peak_price,
    }
    price_cache = {("TEST", today): current_price}
    return engine_fast._check_exits_fast([trade], price_cache, today, today, **exit_kwargs)


class TestProfitLockFixedSLFloorFast:
    """engine_fast.py carried the identical defect as engine.py — same coverage."""

    def test_pre_trigger_breathing_stays_open(self):
        closed = _run_single_trade_fast(
            entry_price=100.0, peak_price=103.0, current_price=96.82,
            take_profit_pct=0.06, stop_loss_pct=0.06, time_stop_days=40,
            trailing_stop_pct=1.0,
            profit_lock_trigger_pct=0.04, profit_lock_trail_pct=0.01,
        )
        assert closed == []

    def test_straight_down_loser_still_hits_fixed_sl(self):
        closed = _run_single_trade_fast(
            entry_price=100.0, peak_price=100.0, current_price=93.5,
            take_profit_pct=0.06, stop_loss_pct=0.06, time_stop_days=40,
            trailing_stop_pct=1.0,
            profit_lock_trigger_pct=0.04, profit_lock_trail_pct=0.01,
        )
        assert len(closed) == 1
        record = closed[0]["record"]
        assert record["exit_reason"] == "SL"
        assert record["days_held"] is not None

    def test_malformed_config_raises(self):
        import pytest
        with pytest.raises(ValueError, match="must both be set"):
            _run_single_trade_fast(
                entry_price=100.0, peak_price=100.0, current_price=100.0,
                take_profit_pct=0.06, stop_loss_pct=0.06, time_stop_days=40,
                profit_lock_trigger_pct=0.04, profit_lock_trail_pct=None,
            )


class TestLegacyBareTrailingStop:
    """trailing_stop_pct without profit-lock config still works as a plain TSL (backward compat)."""

    def test_bare_tsl_fires_without_profit_lock(self):
        closed = _run_single_trade(
            entry_price=100.0, peak_price=110.0, current_price=98.9,  # 10.1% below peak
            take_profit_pct=0.06, stop_loss_pct=0.06, time_stop_days=40,
            trailing_stop_pct=0.10,
        )
        assert len(closed) == 1
        assert closed[0]["record"]["exit_reason"] == "TSL"
