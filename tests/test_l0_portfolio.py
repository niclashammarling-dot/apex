"""L0 Portfolio (M4 stop rules, ruled 2026-10-24): each rule fires on its own condition and only then,
the order of precedence, and the live runner stops before L1 with the rule's reason."""
from contextlib import ExitStack
from unittest.mock import patch

from backend.config import (
    M4_DAILY_LOSS_CAP_PCT,
    M4_DRAWDOWN_PCT,
    M4_EXPECTANCY_N,
    M4_EXPECTANCY_REF,
)
from backend.gate import l0_portfolio as l0

ACCT = {"equity": 10_000.0, "day_pnl": 0.0}
AFTER = "2026-11-01T15:00:00+00:00"


def _t(pnl, exited_at="2026-09-01T15:00:00+00:00", cls="in_session", notional=1000.0):
    return {"pnl": pnl, "exit_price": 1.0, "exited_at": exited_at, "signal_class": cls, "notional": notional}


def test_realised_drawdown_is_from_the_peak_in_exit_order():
    rows = [_t(500, "2026-09-02"), _t(-300, "2026-09-03"), _t(-400, "2026-09-04"), _t(100, "2026-09-01")]
    d = l0.realised_drawdown(rows, 10_000)
    # 10,100 -> 10,600 (peak) -> 10,300 -> 9,900
    assert d["peak"] == 10_600 and d["equity"] == 9_900
    assert abs(d["drawdown"] - 700 / 10_600) < 1e-12


def test_open_trades_do_not_count():
    rows = [_t(-5000), {"pnl": None, "exit_price": None, "exited_at": None}]
    assert l0.realised_drawdown(rows[1:], 10_000)["drawdown"] == 0.0


def test_drawdown_rule_fires_at_its_threshold_and_not_below():
    below = [_t(-(M4_DRAWDOWN_PCT * 10_000) + 1)]
    at = [_t(-(M4_DRAWDOWN_PCT * 10_000))]
    assert l0.evaluate(ACCT, below, 10_000)["reason"] is None
    assert l0.evaluate(ACCT, at, 10_000)["reason"] == l0.HALT_DRAWDOWN


def test_daily_cap_is_a_share_of_current_equity():
    cap = M4_DAILY_LOSS_CAP_PCT * 20_000
    assert l0.evaluate({"equity": 20_000, "day_pnl": -(cap - 1)}, [], 10_000)["reason"] is None
    v = l0.evaluate({"equity": 20_000, "day_pnl": -cap}, [], 10_000)
    assert v["reason"] == l0.HALT_LOSS_CAP and v["daily_loss_cap"] == cap
    assert l0.evaluate({"equity": 20_000, "day_pnl": +5_000}, [], 10_000)["reason"] is None


def test_expectancy_waits_for_n_trades_after_the_freeze_then_compares_to_the_bound():
    bound = l0.expectancy([])["lower_bound"]
    assert abs(bound - (M4_EXPECTANCY_REF["mean"] - 1.645 * M4_EXPECTANCY_REF["sd"] / M4_EXPECTANCY_REF["n"] ** 0.5)) < 1e-12
    bad = [_t(-10.0, AFTER) for _ in range(M4_EXPECTANCY_N - 1)]
    assert l0.expectancy(bad)["breached"] is False                       # n not reached
    bad.append(_t(-10.0, AFTER))
    assert l0.expectancy(bad)["breached"] is True
    good = [_t(50.0, AFTER) for _ in range(M4_EXPECTANCY_N)]             # 5 % on notional > bound
    assert l0.expectancy(good)["breached"] is False
    before = [_t(-10.0, "2026-10-01T15:00:00+00:00") for _ in range(M4_EXPECTANCY_N)]
    assert l0.expectancy(before)["n"] == 0                               # the reference sample is not re-counted
    other = [_t(-10.0, AFTER, cls="earlier_day") for _ in range(M4_EXPECTANCY_N)]
    assert l0.expectancy(other)["n"] == 0                                # in-session only


def test_drawdown_takes_precedence_over_the_daily_cap():
    rows = [_t(-(M4_DRAWDOWN_PCT * 10_000))]
    v = l0.evaluate({"equity": 9_000, "day_pnl": -10_000}, rows, 10_000)
    assert v["reason"] == l0.HALT_DRAWDOWN


def test_live_runner_stops_before_l1_on_an_l0_halt():
    from backend.gate import gate_runner_live
    from backend.gate.cycle import GateCycle
    with ExitStack() as s:
        s.enter_context(patch("backend.gate.gate_runner_live.LIVE_ENABLED", True))
        s.enter_context(patch("backend.gate.gate_runner_live.get_unreconciled_live_trades", return_value=[]))
        s.enter_context(patch("backend.brokers.alpaca.get_account", return_value={
            "trading_blocked": False, "account_blocked": False, "day_pnl": 0.0, "equity": 10_000.0}))
        s.enter_context(patch("backend.brokers.alpaca.get_positions", return_value=[]))
        s.enter_context(patch("backend.gate.gate_runner_live._compute_apex_day_pnl", return_value=(0.0, [], {})))
        s.enter_context(patch("backend.live_config.get_live_config",
                              return_value={"daily_loss_cap": 500.0, "starting_balance": 10_000.0}))
        s.enter_context(patch("backend.db.get_ticker_thresholds", return_value={}))
        s.enter_context(patch("backend.db.live_sample_rows", return_value=[_t(-(M4_DRAWDOWN_PCT * 10_000))]))
        s.enter_context(patch("backend.gate.gate_runner_live.set_alert_latch", return_value=True))
        blocked = s.enter_context(patch("backend.alerts.alert_gate_blocked"))
        cands = s.enter_context(patch("backend.gate.gate_runner_live.get_lock1_candidates"))
        cycle = GateCycle.unrecorded()
        assert gate_runner_live.run(cycle) == []
    assert cycle.reason == l0.HALT_DRAWDOWN
    cands.assert_not_called()                                            # L1 never starts
    assert blocked.call_count == 1
