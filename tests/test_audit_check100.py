"""CHECK 100 — split exit at Alpaca (R026, built 2026-11-21; replaces M8 under Q-EXE).

A take-profit that fills partly leaves the rest to the resized stop, and the tracker books the whole qty at one
price with no flag. Measured 2026-11-09: 0 of 77 brackets 07-07..11-05, so the positive control is synthetic.
"""
from datetime import date


def _run100(monkeypatch, fetch, today=date(2026, 11, 20)):
    from audit import _audit_core as core
    from audit import checks_gate as cg
    monkeypatch.setattr(core, "findings", [])
    monkeypatch.setattr(core, "skipped", [])
    monkeypatch.setattr(core, "triggered", set())
    monkeypatch.setattr(cg, "flag", core.flag)
    cg.check100(fetch=fetch, today=today)
    return [(f[2], f[4]) for f in core.findings if f[0] == 100]


def _bracket(oid, tp_filled, tp_qty=10.0, sl_filled=None):
    return {"id": oid, "ticker": "AMT", "legs": [
        {"side": "sell", "order_type": "limit", "qty": tp_qty, "filled_qty": tp_filled, "filled_avg_price": 210.5},
        {"side": "sell", "order_type": "stop", "qty": tp_qty - (tp_filled or 0), "filled_qty": sl_filled,
         "filled_avg_price": 198.0 if sl_filled else None}]}


def _trade(tid, oid, exited_at):
    return {"id": tid, "ticker": "AMT", "qty": 10, "alpaca_order_id": oid, "exit_price": 198.0,
            "exited_at": exited_at}


def test_split_recent_is_critical_and_names_both_fills(monkeypatch):
    """Positive control: TP filled 4 of 10, the stop sold the other 6, the trade booked whole at the stop."""
    out = _run100(monkeypatch, ([_trade(1, "o1", "2026-11-20T19:55:00+00:00")], [_bracket("o1", 4.0, sl_filled=6.0)]))
    sev = [s for s, _ in out]
    assert sev == ["CRITICAL", "INFO"]
    crit = out[0][1]
    assert "take-profit filled 4 of 10 at 210.5, booked whole at 198.0" in crit and "AMT trade 1" in crit


def test_full_exits_give_only_the_nightly_counts(monkeypatch):
    out = _run100(monkeypatch, ([_trade(1, "o1", "2026-11-19T15:00:00+00:00"), _trade(2, "o2", "2026-11-18T15:00:00+00:00")],
                                [_bracket("o1", 10.0), _bracket("o2", None, sl_filled=10.0)]))
    assert out == [("INFO", "last 30 days: 2 closed live trade(s), 2 bracket(s) found, 2 filled exit leg(s), 0 split(s)")]


def test_an_older_split_is_in_the_info_line_not_critical(monkeypatch):
    out = _run100(monkeypatch, ([_trade(1, "o1", "2026-11-10T15:00:00+00:00")], [_bracket("o1", 4.0, sl_filled=6.0)]))
    assert [s for s, _ in out] == ["INFO"] and "1 before the event window: 2026-11-10 AMT" in out[0][1]


def test_a_trade_without_its_bracket_is_stated_not_skipped(monkeypatch):
    out = _run100(monkeypatch, ([_trade(1, "o1", "2026-11-19T15:00:00+00:00")], []))
    assert "1 trade(s) whose bracket was not in the last 500 orders (not evaluated)" in out[0][1]


def test_a_broker_failure_is_a_warning(monkeypatch):
    from audit import checks_gate as cg

    class Boom:
        def __iter__(self):
            raise ConnectionError("alpaca unreachable")
    monkeypatch.setattr(cg, "split_exits", lambda t, o: (_ for _ in ()).throw(AssertionError("not reached")))
    out = _run100(monkeypatch, Boom())          # unpacking the fetch raises, as a broker call would
    assert out[0][0] == "WARNING" and "did not evaluate" in out[0][1]


def test_check100_runs_in_the_gate_module():
    import inspect
    from audit import checks_gate as cg
    assert "check100()" in inspect.getsource(cg.run)
