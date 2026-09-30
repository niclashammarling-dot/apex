"""Every row a gate cycle writes carries one cycle_started_at (2026-09-30).

CHECK 83 keys cycles on it: row timestamps are per-evaluation completions, and
one slow cycle (09-28, 1m40s under weekly_research) read as two schedulers.
"""
from tests.test_gate_runners import _run_demo, _run_live, _signal


def _stamps(insert_mock):
    return [c.args[0].get("cycle_started_at") for c in insert_mock.call_args_list]


def test_demo_cycle_skip_and_result_rows_share_one_stamp():
    _, mocks = _run_demo(candidates=[_signal("NVDA", sid=1), _signal("AAPL", sid=2)],
                         open_tickers={"NVDA"})
    stamps = _stamps(mocks[8])                   # insert_demo_gate_result
    assert len(stamps) == 2 and stamps[0] and len(set(stamps)) == 1


def test_live_cycle_skip_and_result_rows_share_one_stamp():
    _, mocks = _run_live(candidates=[_signal("NVDA", sid=1), _signal("AAPL", sid=2)],
                         open_tickers={"NVDA"})
    stamps = _stamps(mocks[9])                   # insert_live_gate_result
    assert len(stamps) == 2 and stamps[0] and len(set(stamps)) == 1
