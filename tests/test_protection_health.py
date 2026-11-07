"""CHECK 92 — live position protection health (2026-10-18). Each fault is held closed
by a synthetic fixture on which the check must raise; a broker failure must surface as
"did not evaluate", never as a clean row."""
from unittest.mock import patch

import pytest

from backend.live_trades_tracker import _protection_first_cycle as REAL_HOOK   # before the conftest stub
from backend.protection_health import evaluate

POS   = {"ticker": "AAA", "qty": 3.0, "side": "long", "current_price": 100.0}
TRADE = {"ticker": "AAA", "alpaca_order_id": "p1"}


def _order(stop=90.0, status="held"):
    return {"legs": [{"order_type": "limit", "status": "new", "stop_price": None},
                     {"order_type": "stop", "status": status, "stop_price": stop}]}


def test_protected_position_is_clean():
    assert evaluate([POS], [TRADE], lambda _id: _order()) == []


def test_stop_at_or_above_the_last_trade_raises():      # a split the legs were not adjusted for
    found = evaluate([{**POS, "current_price": 45.0}], [TRADE], lambda _id: _order(stop=90.0))
    assert [f["severity"] for f in found] == ["CRITICAL"] and "at or above" in found[0]["what"]


def test_no_resting_stop_raises():
    for order in ({"legs": [{"order_type": "limit", "status": "new"}]}, _order(status="filled"), _order(status="replaced")):
        found = evaluate([POS], [TRADE], lambda _id, o=order: o)
        assert [f["severity"] for f in found] == ["CRITICAL"] and "no resting STOP" in found[0]["what"]


def test_short_position_raises():                       # both bracket legs filled
    found = evaluate([{**POS, "qty": -3.0, "side": "short"}], [TRADE], lambda _id: _order())
    assert [f["severity"] for f in found] == ["CRITICAL"] and "short" in found[0]["what"]


def test_broker_failure_is_a_finding_not_silence():
    def boom(_id):
        raise RuntimeError("503")
    found = evaluate([POS], [TRADE], boom)
    assert [f["severity"] for f in found] == ["WARNING"] and found[0]["what"].startswith("did not evaluate")


def _run92(tmp_path, monkeypatch, fetch):
    from audit import _audit_core as core
    from audit import checks_gate as cg
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data/apex.db").write_text("")
    monkeypatch.setattr(cg, "REPO", tmp_path)
    monkeypatch.setattr(core, "REPO", tmp_path)
    core.findings.clear()
    cg.check92(fetch=fetch)
    return [(f[0], f[2]) for f in core.findings]


def test_check92_flags_each_fault(tmp_path, monkeypatch):
    fetch = ([{**POS, "current_price": 45.0}, {"ticker": "BBB", "qty": -1.0, "side": "short"}], [TRADE],
             lambda _id: _order(stop=90.0))
    assert _run92(tmp_path, monkeypatch, fetch) == [(92, "CRITICAL"), (92, "CRITICAL")]


def test_check92_clean_and_broken_evaluator(tmp_path, monkeypatch):
    assert _run92(tmp_path, monkeypatch, ([POS], [TRADE], lambda _id: _order())) == []
    with patch("backend.protection_health.evaluate", side_effect=KeyError("qty")):
        assert _run92(tmp_path, monkeypatch, ([POS], [TRADE], lambda _id: _order())) == [(92, "WARNING")]


class _Broker:
    def __init__(self, order):
        self.order, self.calls = order, 0

    def get_order_by_id(self, _id):
        self.calls += 1
        return self.order


@pytest.fixture
def hook(monkeypatch):
    import backend.live_trades_tracker as ltt
    monkeypatch.setattr(ltt, "_protection_checked_on", None)
    monkeypatch.setattr("backend.scheduler.is_market_open", lambda: True)
    return REAL_HOOK


def test_first_cycle_hook_alerts_once_per_session(hook):
    broker = _Broker(_order(stop=90.0))
    with patch("backend.alerts._dispatch") as dispatch:
        hook([{**POS, "current_price": 45.0}], [TRADE], broker)
        hook([{**POS, "current_price": 45.0}], [TRADE], broker)   # same session: not again
    assert dispatch.call_count == 1 and broker.calls == 1


def test_first_cycle_hook_retries_after_a_failed_evaluation(hook):
    class Flaky(_Broker):
        def get_order_by_id(self, _id):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("timeout")
            return self.order
    broker = Flaky(_order())
    hook([POS], [TRADE], broker)
    hook([POS], [TRADE], broker)
    hook([POS], [TRADE], broker)
    assert broker.calls == 2          # failed once, evaluated once, then done for the session


def test_hook_does_nothing_when_the_market_is_closed(hook, monkeypatch):
    monkeypatch.setattr("backend.scheduler.is_market_open", lambda: False)
    broker = _Broker(_order())
    hook([POS], [TRADE], broker)
    assert broker.calls == 0


# 2026-11-07, from OXY 11-04: the first cycle ran 54 s into the session, the price had crossed a correctly levelled
# stop inside the first minute, the leg filled four minutes later, and the mail said "unprotected".
LEVELLED = {"ticker": "AAA", "alpaca_order_id": "p1", "id": 94, "sl_price": 59.15}
CROSSED = {**POS, "current_price": 59.01}


def test_a_crossed_stop_at_the_trades_level_is_fill_pending_not_unprotected():
    found = evaluate([CROSSED], [LEVELLED], lambda _id: _order(stop=59.15))
    assert [(f["severity"], f.get("kind")) for f in found] == [("WARNING", "fill_pending")]
    assert "price through the stop, fill pending" in found[0]["what"]


def test_a_crossed_stop_at_neither_level_stays_critical():
    found = evaluate([CROSSED], [LEVELLED], lambda _id: _order(stop=61.00))
    assert [f["severity"] for f in found] == ["CRITICAL"] and "not the trade's stop" in found[0]["what"]


def test_the_ratchets_last_level_is_the_expected_stop():
    # the ratchet moved the leg to 60.10; the row's sl_price is the entry stop
    found = evaluate([{**POS, "current_price": 60.0}], [LEVELLED], lambda _id: _order(stop=60.10),
                     ratchet_levels={94: 60.10})
    assert [f["severity"] for f in found] == ["WARNING"]
    found = evaluate([{**POS, "current_price": 60.0}], [LEVELLED], lambda _id: _order(stop=60.10))
    assert [f["severity"] for f in found] == ["CRITICAL"]           # without the ratchet level it matches neither


def test_a_crossed_levelled_stop_still_resting_with_the_market_closed_is_critical():
    found = evaluate([CROSSED], [LEVELLED], lambda _id: _order(stop=59.15), market_open=False)
    assert [f["severity"] for f in found] == ["CRITICAL"] and "did not fill" in found[0]["what"]


def test_first_cycle_rereads_fill_pending_and_escalates_the_second_sighting(hook, monkeypatch):
    import backend.live_trades_tracker as ltt
    monkeypatch.setattr(ltt, "_fill_pending", {})
    broker = _Broker(_order(stop=59.15))
    with patch("backend.alerts._dispatch") as dispatch:
        hook([CROSSED], [LEVELLED], broker)              # first sighting: WARNING, no mail, check stays open
        assert dispatch.call_count == 0 and ltt._protection_checked_on is None
        hook([CROSSED], [LEVELLED], broker)              # next cycle, still crossed and resting: CRITICAL
        assert dispatch.call_count == 1 and "still unfilled at the next cycle" in dispatch.call_args[0][1]


def test_first_cycle_closes_once_the_stop_filled(hook, monkeypatch):
    import backend.live_trades_tracker as ltt
    monkeypatch.setattr(ltt, "_fill_pending", {})
    broker = _Broker(_order(stop=59.15))
    with patch("backend.alerts._dispatch") as dispatch:
        hook([CROSSED], [LEVELLED], broker)
        hook([], [LEVELLED], broker)                     # filled: the position is gone, nothing pending
    assert dispatch.call_count == 0 and ltt._protection_checked_on is not None and ltt._fill_pending == {}
