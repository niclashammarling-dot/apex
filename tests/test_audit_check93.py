"""CHECK 93 — live stop-fill slippage against the engine's model (2026-10-20). Each outcome is
held: a drift WARNING, a clean INFO, the ratcheted leg measured at its own stop, and a broker
failure or an unreadable population reported as "did not evaluate"."""
from audit import _audit_core as core
from audit import checks_gate as cg


def _run(monkeypatch, trades, orders, raise_=None):
    monkeypatch.setattr(core, "findings", [])
    monkeypatch.setattr(cg, "flag", core.flag)

    def fetch_order(oid):
        if raise_:
            raise raise_
        return orders[oid]
    cg.check93(fetch=(trades, fetch_order))
    return [(f[2], f[4]) for f in core.findings if f[0] == 93]


def _order(stop, fill, at="2026-09-10T15:30:00+00:00", typ="stop"):      # 11:30 ET: intraday
    return {"legs": [{"order_type": "limit", "filled_avg_price": None},
                     {"order_type": typ, "stop_price": stop, "filled_avg_price": fill, "filled_at": at}]}


def _trades(n):
    return [{"alpaca_order_id": f"o{i}", "exit_reason": "SL"} for i in range(n)]


def test_drift_beyond_tolerance_warns(monkeypatch):
    orders = {f"o{i}": _order(100.0, 99.0) for i in range(6)}                # 1 % below the stop, model 0.02 %
    out = _run(monkeypatch, _trades(6), orders)
    warn = [f for f in out if f[0] == "WARNING"]
    assert len(warn) == 1 and "intraday" in warn[0][1] and "+1.00 %" in warn[0][1]


def test_fills_at_the_model_are_info_only(monkeypatch):
    orders = {f"o{i}": _order(100.0, 99.98) for i in range(6)}
    out = _run(monkeypatch, _trades(6), orders)
    assert [f[0] for f in out] == ["INFO"] and "n 6, median +0.02 %" in out[0][1]


def test_gap_class_by_fill_time_and_thin_class_not_judged(monkeypatch):
    orders = {f"o{i}": _order(100.0, 97.0, at="2026-09-10T13:32:00+00:00") for i in range(3)}  # 09:32 ET
    out = _run(monkeypatch, _trades(3), orders)
    assert [f[0] for f in out] == ["INFO"] and "gap: n 3, median +3.00 %" in out[0][1]


def test_ratcheted_leg_is_measured_at_its_own_stop(monkeypatch):
    # The DB's sl_price would be the original 94; the leg carries the ratcheted 104.
    orders = {"o0": _order(104.0, 103.98)}
    s = cg.stop_fill_slip(orders["o0"])
    assert abs(s[0] - (1 - 103.98 / 104.0)) < 1e-12


def test_broker_failure_and_unmeasurable_population_did_not_evaluate(monkeypatch):
    out = _run(monkeypatch, _trades(2), {}, raise_=RuntimeError("401"))
    assert out == [("WARNING", "did not evaluate: RuntimeError: 401")]
    out = _run(monkeypatch, _trades(2), {"o0": {"legs": []}, "o1": {"legs": []}})
    assert len(out) == 1 and out[0][0] == "WARNING" and "none of 2 stop exits" in out[0][1]
