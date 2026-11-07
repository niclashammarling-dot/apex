"""
L5 options: a listed expiry whose chain is missing retries, then decides `unavailable` (2026-10-31).

Found 2026-10-29: the nearest expiry's chain came back None (EOG 9 of 12 cycles, TMO 6 of 12), the guard
skipped it without a log, and both options checks ran on the second expiry alone. Ruled 2026-10-31
(Niclas, option B): retry the fetch, and a chain still missing is a data error, so the options group
decides L5 `unavailable` instead of evaluating on half its input. The row stores OI per expiry.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

import backend.gate.l5_positioning as l4m

EXP = ("2026-10-30", "2026-11-06")
RS_OK = {"pass": True, "reason": "rs"}
VA_OK = {"pass": True, "reason": "va"}


def _chain(call_oi, put_oi):
    return SimpleNamespace(calls=pd.DataFrame({"openInterest": [call_oi], "volume": [call_oi]}),
                           puts=pd.DataFrame({"openInterest": [put_oi], "volume": [put_oi]}))


class _Ticker:
    """Fake yf.Ticker: the nearest expiry's chain is missing on the first `misses` fetches."""
    calls = 0

    def __init__(self, misses, how="none"):
        self.misses, self.how = misses, how

    def __call__(self, ticker):
        return self

    options = EXP

    def option_chain(self, exp):
        if exp == EXP[0]:
            type(self).calls += 1
            if type(self).calls <= self.misses:
                if self.how == "raise":
                    raise RuntimeError("curl: (35) TLS connect error")
                return SimpleNamespace(calls=None, puts=None)
            return _chain(1155, 1561)
        return _chain(829, 974)


@pytest.fixture(autouse=True)
def _no_sleep():
    _Ticker.calls = 0
    with patch.object(l4m.time, "sleep"):
        yield


def _evaluate(fake):
    with patch.object(l4m.yf, "Ticker", fake), \
         patch.object(l4m, "_check_relative_strength", return_value=RS_OK), \
         patch.object(l4m, "_check_volume_accumulation", return_value=VA_OK):
        return l4m.evaluate("EOG", "Energy")


@pytest.mark.parametrize("how", ["none", "raise"])
def test_a_chain_missing_on_every_attempt_is_unavailable_not_half_input(how):
    r = _evaluate(_Ticker(misses=99, how=how))
    pcr = r.data["checks"]["put_call_ratio"]
    assert pcr["error"] is True and "missing chain 2026-10-30" in pcr["reason"]
    assert r.data["unavailable"] is True and not r.passed
    assert "call_oi" not in pcr                      # never evaluated on the 11-06 expiry alone


def test_a_chain_missing_once_recovers_on_the_retry_with_both_expiries():
    r = _evaluate(_Ticker(misses=1))
    pcr = r.data["checks"]["put_call_ratio"]
    assert (pcr["call_oi"], pcr["put_oi"]) == (1155 + 829, 1561 + 974)   # the 10-29 full snapshot's sum
    assert pcr["oi_by_expiry"] == {EXP[0]: [1155, 1561], EXP[1]: [829, 974]}
    assert not r.data["unavailable"]


def test_two_tuple_chains_still_evaluate_without_the_expiry_field():
    calls, puts = pd.DataFrame({"openInterest": [100]}), pd.DataFrame({"openInterest": [80]})
    r = l4m._check_put_call_ratio([(calls, puts)], ticker="A", mode="pooled_scalar")
    assert r["pass"] is True and "oi_by_expiry" not in r
