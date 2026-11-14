"""
The demo gate records data_unreachable when its data host refuses the pre-flight connection (2026-11-14, Saturday
order item 7; found 2026-10-24). On 10-22 the ISP outage left Yahoo fetches at 0 rows (DNS failures) while every demo
cycle recorded `evaluated`; live recorded broker_unreachable. The cycle now stops before reading candidates, and the
reason is not actionable for the coverage readers.
"""
import socket

import backend.gate.gate_runner as gr
from audit.gate_cycles import NOT_ACTIONABLE
from backend.gate.cycle import GateCycle


def _dns_failure(monkeypatch):
    def refuse(address, *a, **k):
        raise socket.gaierror(-3, "Temporary failure in name resolution")      # the 10-22 shape
    monkeypatch.setattr(socket, "create_connection", refuse)


def test_unreachable_data_host_stops_the_demo_cycle_before_candidates(monkeypatch):
    _dns_failure(monkeypatch)
    read = []
    monkeypatch.setattr(gr, "get_l3_score_candidates", lambda *a, **k: read.append(1) or [])
    cycle = GateCycle.unrecorded()
    assert gr.run(cycle) == [] and cycle.reason == "data_unreachable" and read == []


def test_reachable_data_host_goes_on_to_the_candidates(monkeypatch):
    # Control: the conftest fake answers for Yahoo, so the cycle reads candidates and ends as before.
    read = []
    monkeypatch.setattr(gr, "get_l3_score_candidates", lambda *a, **k: read.append(1) or [])
    cycle = GateCycle.unrecorded()
    gr.run(cycle)
    assert read == [1] and cycle.reason == "no_candidates"


def test_the_probe_reads_any_os_error_as_unreachable(monkeypatch):
    _dns_failure(monkeypatch)
    assert gr._data_reachable() is False


def test_data_unreachable_is_not_actionable_for_coverage():
    assert "data_unreachable" in NOT_ACTIONABLE and "broker_unreachable" in NOT_ACTIONABLE
