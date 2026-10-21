"""CHECK 93 — live stop-fill slippage against the engine's model (2026-10-20). Each outcome is
held: a drift WARNING, a clean INFO, the ratcheted leg measured at its own stop, R7's gap class
(opening minutes AND open at or below the stop, measured against the open), a missing open as a
WARNING, and a broker failure or an unreadable population reported as "did not evaluate"."""
from datetime import date

from audit import _audit_core as core
from audit import checks_gate as cg


def _run(monkeypatch, trades, orders, raise_=None, opens=None):
    monkeypatch.setattr(core, "findings", [])
    monkeypatch.setattr(cg, "flag", core.flag)

    def fetch_order(oid):
        if raise_:
            raise raise_
        return orders[oid]
    opens = opens or {}
    asked = []

    def fetch_open(ticker, day):
        asked.append((ticker, day))
        return opens.get((ticker, day))
    cg.check93(fetch=(trades, fetch_order, fetch_open))
    _run.asked = asked
    return [(f[2], f[4]) for f in core.findings if f[0] == 93]


def _order(stop, fill, at="2026-09-10T15:30:00+00:00", typ="stop"):      # 11:30 ET: intraday
    return {"legs": [{"order_type": "limit", "filled_avg_price": None},
                     {"order_type": typ, "stop_price": stop, "filled_avg_price": fill, "filled_at": at}]}


def _trades(n):
    return [{"alpaca_order_id": f"o{i}", "exit_reason": "SL", "ticker": "XYZ"} for i in range(n)]


def test_drift_beyond_tolerance_warns(monkeypatch):
    orders = {f"o{i}": _order(100.0, 99.0) for i in range(6)}                # 1 % below the stop, model 0.02 %
    out = _run(monkeypatch, _trades(6), orders)
    warn = [f for f in out if f[0] == "WARNING"]
    assert len(warn) == 1 and "intraday" in warn[0][1] and "+1.00 %" in warn[0][1]


def test_fills_at_the_model_are_info_only(monkeypatch):
    orders = {f"o{i}": _order(100.0, 99.98) for i in range(6)}
    out = _run(monkeypatch, _trades(6), orders)
    assert [f[0] for f in out] == ["INFO"] and "n 6, median +0.02 %" in out[0][1]


def test_gap_needs_the_open_below_the_stop_and_is_measured_against_the_open(monkeypatch):
    # 09:32 ET fills. o0-o2: open 98 at/below the stop 100, fill 97.51 -> gap, 1 - 97.51/98 = 0.50 %.
    # o3: open 101 above the stop, a stop triggered in the opening minutes -> intraday, against the stop.
    at = "2026-09-10T13:32:00+00:00"
    orders = {f"o{i}": _order(100.0, 97.51, at=at) for i in range(3)} | {"o3": _order(100.0, 99.98, at=at)}
    trades = _trades(3) + [{"alpaca_order_id": "o3", "exit_reason": "TSL", "ticker": "ABC"}]
    out = _run(monkeypatch, trades, orders, opens={("XYZ", date(2026, 9, 10)): 98.0, ("ABC", date(2026, 9, 10)): 101.0})
    assert [f[0] for f in out] == ["INFO"]
    assert "gap: n 3, median +0.50 %" in out[0][1] and "intraday: n 1, median +0.02 %" in out[0][1]


def test_open_read_only_for_opening_minutes_fills(monkeypatch):
    _run(monkeypatch, _trades(2), {f"o{i}": _order(100.0, 99.98) for i in range(2)})      # 11:30 ET
    assert _run.asked == []


def test_missing_open_is_unclassed_and_warns(monkeypatch):
    orders = {"o0": _order(100.0, 97.0, at="2026-09-10T13:31:00+00:00"), "o1": _order(100.0, 99.98)}
    out = _run(monkeypatch, _trades(2), orders, opens={})
    warn = [f for f in out if f[0] == "WARNING"]
    assert len(warn) == 1 and "1 opening-minutes stop fills unclassed" in warn[0][1] and "XYZ 2026-09-10" in warn[0][1]
    info = [f for f in out if f[0] == "INFO"][0][1]
    assert "gap: no fills" in info and "intraday: n 1" in info and "1 unclassed (no open)" in info


class _Bar:
    def __init__(self, ts, open_):
        from datetime import datetime, timezone
        self.timestamp, self.open = datetime(*ts, tzinfo=timezone.utc), open_


def test_get_daily_open_returns_the_bar_dated_that_day_else_none(monkeypatch):
    from backend.brokers import alpaca

    class _Bars:
        def __init__(self, rows):
            self.data = {"XYZ": rows}

        def __getitem__(self, k):
            return self.data[k]

    class _Client:
        rows = [_Bar((2026, 9, 10, 4, 0), 98.25)]

        def __init__(self, *a, **k):
            pass

        def get_stock_bars(self, req):
            return _Bars(self.rows)
    monkeypatch.setattr(alpaca, "StockHistoricalDataClient", _Client)
    assert alpaca.get_daily_open("XYZ", date(2026, 9, 10)) == 98.25
    assert alpaca.get_daily_open("XYZ", date(2026, 9, 11)) is None
    _Client.get_stock_bars = lambda self, req: (_ for _ in ()).throw(RuntimeError("403"))
    assert alpaca.get_daily_open("XYZ", date(2026, 9, 10)) is None


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
