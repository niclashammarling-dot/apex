"""
CHECK 65 option A (Niclas 2026-10-31): the IPO term enters the posterior update at LR 1, its raw value stays in the
trace. Run over the regime-split golden window (09-16..10-15, production inputs), where the term was not neutral.
"""
import json

import backend.regime.regime_bayes as rbm
from tests.test_regime_split import GOLDEN, _run_window


def _entries(out):
    for day in out:
        for e in day["result"]["leaderboard"]:
            yield day["result"]["date"], e


def test_the_ipo_term_is_neutral_in_the_update_and_raw_in_the_trace():
    assert rbm.IPO_LR_NEUTRAL is True
    out = _run_window()
    raw = 0
    for d, e in _entries(out):
        t = e["signal_trace"]
        assert t["lr_ipo"] == 1.0, (d, e["sector"])
        assert t["after_ipo"] == t["after_rs"], (d, e["sector"])
        raw += t["lr_ipo_raw"] != 1.0
    assert raw > 0      # the window carried a non-neutral raw term, so the flag had something to remove


def test_positive_control_the_window_differs_from_the_recorded_ipo_path():
    golden = json.loads(GOLDEN.read_text())
    assert _run_window() != golden
