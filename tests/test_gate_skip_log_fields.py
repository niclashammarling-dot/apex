"""The skip line names what it counts: held candidates, and the book size separately."""
from loguru import logger

from tests.test_gate_runners import _run_demo, _run_live, _signal


def _lines(run, **kw):
    seen = []
    sink = logger.add(lambda m: seen.append(m.record["message"]))
    try:
        run(**kw)
    finally:
        logger.remove(sink)
    return [m for m in seen if "skipped" in m and "candidate(s)" in m]


def test_live_skip_line_separates_held_candidates_from_book():
    lines = _lines(_run_live, candidates=[_signal("NVDA", sid=1), _signal("AAPL", sid=2)],
                   open_tickers={"NVDA", "MSFT", "TMO"})
    assert lines and "held=1" in lines[0] and "book holds 3" in lines[0] and "open=" not in lines[0]


def test_demo_skip_line_separates_held_candidates_from_book():
    lines = _lines(_run_demo, candidates=[_signal("NVDA", sid=1), _signal("AAPL", sid=2)],
                   open_tickers={"NVDA", "MSFT"})
    assert lines and "held=1" in lines[0] and "book holds 2" in lines[0]
