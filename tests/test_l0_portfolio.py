"""L0 Portfolio (M4 stop rules, ruled 2026-10-24): each rule fires on its own condition and only then,
the order of precedence, and the live runner stops before L1 with the rule's reason."""
from contextlib import ExitStack
from unittest.mock import patch

from backend.config import (
    L0_PORTFOLIO_LOSS_CAP_MAX,
    L0_PORTFOLIO_HALT_M4_DRAWDOWN_MAX,
    L0_PORTFOLIO_HALT_M4_EXPECTANCY_COUNT,
    LIVE_L0_PORTFOLIO_HALT_M4_EXPECTANCY_REF,
)
from backend.gate import l0_portfolio as l0
from tests.book_cfg import live_cfg

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
    below = [_t(-(L0_PORTFOLIO_HALT_M4_DRAWDOWN_MAX * 10_000) + 1)]
    at = [_t(-(L0_PORTFOLIO_HALT_M4_DRAWDOWN_MAX * 10_000))]
    assert l0.evaluate(ACCT, below, 10_000)["reason"] is None
    assert l0.evaluate(ACCT, at, 10_000)["reason"] == l0.HALT_DRAWDOWN


def test_daily_cap_is_a_share_of_current_equity():
    cap = L0_PORTFOLIO_LOSS_CAP_MAX * 20_000
    assert l0.evaluate({"equity": 20_000, "day_pnl": -(cap - 1)}, [], 10_000)["reason"] is None
    v = l0.evaluate({"equity": 20_000, "day_pnl": -cap}, [], 10_000)
    assert v["reason"] == l0.HALT_LOSS_CAP and v["l0_portfolio_loss_cap_max"] == cap
    assert l0.evaluate({"equity": 20_000, "day_pnl": +5_000}, [], 10_000)["reason"] is None


def test_expectancy_waits_for_n_trades_after_the_freeze_then_compares_to_the_bound():
    bound = l0.expectancy([])["lower_bound"]
    assert abs(bound - (LIVE_L0_PORTFOLIO_HALT_M4_EXPECTANCY_REF["mean"] - 1.645 * LIVE_L0_PORTFOLIO_HALT_M4_EXPECTANCY_REF["sd"] / LIVE_L0_PORTFOLIO_HALT_M4_EXPECTANCY_REF["n"] ** 0.5)) < 1e-12
    bad = [_t(-10.0, AFTER) for _ in range(L0_PORTFOLIO_HALT_M4_EXPECTANCY_COUNT - 1)]
    assert l0.expectancy(bad)["breached"] is False                       # n not reached
    bad.append(_t(-10.0, AFTER))
    assert l0.expectancy(bad)["breached"] is True
    good = [_t(50.0, AFTER) for _ in range(L0_PORTFOLIO_HALT_M4_EXPECTANCY_COUNT)]             # 5 % on notional > bound
    assert l0.expectancy(good)["breached"] is False
    before = [_t(-10.0, "2026-10-01T15:00:00+00:00") for _ in range(L0_PORTFOLIO_HALT_M4_EXPECTANCY_COUNT)]
    assert l0.expectancy(before)["n"] == 0                               # the reference sample is not re-counted
    other = [_t(-10.0, AFTER, cls="earlier_day") for _ in range(L0_PORTFOLIO_HALT_M4_EXPECTANCY_COUNT)]
    assert l0.expectancy(other)["n"] == 0                                # in-session only


def test_drawdown_takes_precedence_over_the_daily_cap():
    rows = [_t(-(L0_PORTFOLIO_HALT_M4_DRAWDOWN_MAX * 10_000))]
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
                              return_value=live_cfg({"l0_portfolio_loss_cap_max": 500.0, "starting_balance": 10_000.0})))
        s.enter_context(patch("backend.db.get_ticker_thresholds", return_value={}))
        s.enter_context(patch("backend.db.live_sample_rows", return_value=[_t(-(L0_PORTFOLIO_HALT_M4_DRAWDOWN_MAX * 10_000))]))
        s.enter_context(patch("backend.gate.gate_runner_live.set_alert_latch", return_value=True))
        blocked = s.enter_context(patch("backend.alerts.alert_gate_blocked"))
        cands = s.enter_context(patch("backend.gate.gate_runner_live.get_l3_score_candidates"))
        cycle = GateCycle.unrecorded()
        assert gate_runner_live.run(cycle) == []
    assert cycle.reason == l0.HALT_DRAWDOWN
    cands.assert_not_called()                                            # L1 never starts
    assert blocked.call_count == 1


# ── The demo book (R067 a, 2026-11-14; built 2026-11-21; R127 a for its window and reference) ─────────────────

def test_demo_expectancy_counts_rows_without_a_signal_class_and_live_does_not():
    from backend.config import DEMO_L0_PORTFOLIO_HALT_M4_EXPECTANCY_REF as ref
    after = "2026-11-21T15:00:00+00:00"
    assert after > ref["frozen_after_exit"]
    rows = [{"pnl": -50.0, "exit_price": 1.0, "exited_at": after, "notional": 1000.0}] * L0_PORTFOLIO_HALT_M4_EXPECTANCY_COUNT
    demo, live = l0.expectancy(rows, l0.DEMO), l0.expectancy(rows, l0.LIVE)
    assert demo["n"] == L0_PORTFOLIO_HALT_M4_EXPECTANCY_COUNT and demo["breached"]      # -5 % < -0.47 %
    assert abs(demo["lower_bound"] - (ref["mean"] - 1.645 * ref["sd"] / ref["n"] ** 0.5)) < 1e-12
    assert live["n"] == 0 and not live["breached"]


def test_demo_reference_is_the_ruled_one():
    from backend.config import DEMO_L0_PORTFOLIO_HALT_M4_EXPECTANCY_REF as ref, DEMO_L0_PORTFOLIO_M4_SINCE
    assert DEMO_L0_PORTFOLIO_M4_SINCE == "2026-07-07"                    # live's reset, for drawdown and reference
    assert (ref["mean"], ref["sd"], ref["n"]) == (0.0071, 0.0605, 72)
    assert abs((ref["mean"] - 1.645 * ref["sd"] / ref["n"] ** 0.5) - (-0.0046)) < 0.0002   # R127: bound -0.47 %


def test_evaluate_demo_opens_the_window_at_its_equity_and_takes_the_share_of_realised_equity():
    rows = [_t(160, "2026-08-01", cls=None), _t(-100, "2026-08-02", cls=None)]
    with patch("backend.db.demo_m4_rows", return_value=(rows, 23.46, 109.19)) as m, \
         patch("backend.wallet._daily_realized_loss", return_value=30.0):
        v = l0.evaluate_demo(2000.0)
    assert m.call_args.args == ("2026-07-07",)
    # window opens at 2,023.46; peak 2,183.46; now 2,083.46 (4.6 % off, under the 6 % halt)
    assert v["drawdown"]["peak"] == 2183.46 and v["drawdown"]["equity"] == 2083.46
    assert v["l0_portfolio_loss_cap_max"] == round(2109.19 * L0_PORTFOLIO_LOSS_CAP_MAX, 2)
    assert v["day_loss"] == 30.0 and v["reason"] is None and v["book"] == "demo"


def test_evaluate_demo_halts_on_the_cap_from_todays_realised_losses():
    with patch("backend.db.demo_m4_rows", return_value=([], 0.0, 0.0)), \
         patch("backend.wallet._daily_realized_loss", return_value=2000.0 * L0_PORTFOLIO_LOSS_CAP_MAX):
        assert l0.evaluate_demo(2000.0)["reason"] == l0.HALT_LOSS_CAP


def test_demo_m4_rows_maps_the_trades_table_and_splits_the_window():
    from backend import db
    conn = db.get_db()
    try:
        conn.execute("DELETE FROM trades WHERE ticker LIKE 'M4D%'")
        for t, ts, ex, pnl, outcome in (("M4DA", "2026-07-01T15:00:00+00:00", 10.0, 23.0, "WIN"),
                                        ("M4DB", "2026-08-01T15:00:00+00:00", 11.0, -7.0, "LOSS"),
                                        ("M4DC", "2026-08-02T15:00:00+00:00", None, None, "OPEN")):
            conn.execute("INSERT INTO trades (timestamp, ticker, sector, action, price, shares, amount, outcome, pnl, "
                         "price_exit, exited_at) VALUES (?, ?, 'Technology', 'BUY', 10, 10, 100, ?, ?, ?, ?)",
                         (ts, t, outcome, pnl, ex, ts if ex else None))
        conn.commit()
        rows, before, total = db.demo_m4_rows("2026-07-07")
        mine = [r for r in rows if r["timestamp"] >= "2026-08-01" and r["notional"] == 100]
        assert {(r["exit_price"], r["pnl"]) for r in mine} >= {(11.0, -7.0), (None, None)}
        assert before >= 23.0 - 1e-9 and total - before >= -7.0 - 1e-9
    finally:
        conn.execute("DELETE FROM trades WHERE ticker LIKE 'M4D%'")
        conn.commit()
        conn.close()


def test_demo_runner_stops_before_l1_on_an_l0_halt_and_mails_no_one():
    from backend.gate import gate_runner
    from backend.gate.cycle import GateCycle
    from tests.test_gate_runners import _demo_patches, _signal
    halt = {"reason": l0.HALT_DRAWDOWN, "l0_portfolio_loss_cap_max": 100.0, "day_loss": 0.0,
            "drawdown": {"drawdown": 0.07, "peak": 2160.0, "equity": 2008.0}, "expectancy": {}, "book": "demo"}
    cycle = GateCycle.unrecorded()
    with ExitStack() as st:
        mocks = [st.enter_context(p) for p in _demo_patches(candidates=[_signal()])]
        st.enter_context(patch.object(l0, "evaluate_demo", return_value=halt))
        gate_mail = st.enter_context(patch("backend.alerts.alert_gate_blocked"))
        cap_mail = st.enter_context(patch("backend.alerts.alert_daily_loss_cap"))
        assert gate_runner.run(cycle) == []
    assert cycle.reason == l0.HALT_DRAWDOWN
    mocks[4].assert_not_called()                                        # no candidate read
    gate_mail.assert_not_called() and cap_mail.assert_not_called()


def test_demo_runner_puts_l0s_dollar_cap_on_cfg_for_l6s_risk_limits():
    from backend.gate import gate_runner
    from tests.test_gate_runners import _demo_patches, _signal
    with ExitStack() as st:
        mocks = [st.enter_context(p) for p in _demo_patches(candidates=[_signal()])]
        st.enter_context(patch("backend.db.demo_m4_rows", return_value=([], 0.0, 100.0)))
        st.enter_context(patch("backend.wallet._daily_realized_loss", return_value=0.0))
        gate_runner.run()
    cfg = mocks[9].call_args.args[4]                                    # evaluate_chain(ticker, sector, score, ctx, cfg)
    sb = mocks[0].return_value["starting_balance"]
    assert cfg["l0_portfolio_loss_cap_max"] == round((sb + 100.0) * L0_PORTFOLIO_LOSS_CAP_MAX, 2)


def test_no_config_key_is_read_only_to_be_overwritten():
    """A runner that assigns cfg[key] each cycle makes the stored value dead (R111 a: live's l0_portfolio_loss_cap_max
    was overwritten at gate_runner_live:424 while Settings could edit it). No key a runner assigns may be a config-file
    key of either book."""
    import re
    from pathlib import Path
    from backend import demo_config, live_config
    root = Path(__file__).resolve().parents[1]
    assigned = set()
    for f in (root / "backend" / "gate").glob("*.py"):
        assigned |= set(re.findall(r'\bcfg\["(\w+)"\]\s*=(?!=)', f.read_text()))
    assert "l0_portfolio_loss_cap_max" in assigned                     # positive control: both runners assign it
    assert not assigned & (set(live_config._KEYS) | set(demo_config._KEYS))
