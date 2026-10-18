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
