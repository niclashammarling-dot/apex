"""options_probe_compare (scripts/options_probe_compare.py): the kept summary of probe runs, on a two-ticker
synthetic run whose every figure is known."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "options_probe_compare", Path(__file__).resolve().parents[1] / "scripts" / "options_probe_compare.py")
cmp_ = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cmp_)


def _side(pcr, pcr_pass, ucv_pass, call_vol, put_vol, oi, error=False):
    ucv = {"pass": ucv_pass, "call_vol": call_vol, "put_vol": put_vol}
    if error:
        ucv = {"pass": False, "error": True, "reason": "no lastTradeDate"}
    return {"pcr": {"pc_ratio": pcr, "pass": pcr_pass}, "ucv": ucv, "oi_by_symbol": oi}


RUN = {"run_utc": "2026-11-20T15:03:24+00:00", "producer": {"commit": "b3f6016aaaa"}, "results": [
    {"ticker": "A", "yf": _side(1.0, False, True, 30, 10, {"A1": 5, "A2": 7, "A3": None}),
     "alpaca": {**_side(1.3, False, False, 40, 30, {"A1": 5, "A2": 8, "A3": 2}), "oi_dates": ["2026-11-18"]},
     "volume_match": {"both": 3, "alpaca_only": 2, "yf_only": 0, "vol_yf_both": 40, "vol_alpaca_both": 44}},
    {"ticker": "B", "yf": _side(0.8, True, False, 0, 0, {"B1": 1}),
     "alpaca": {**_side(0.8, True, True, 4, 0, {"B1": 1}), "oi_dates": ["2026-11-18"]},
     "volume_match": {"both": 0, "alpaca_only": 1, "yf_only": 0, "vol_yf_both": 0, "vol_alpaca_both": 0}},
    {"ticker": "NVR", "yf_error": "no listed expiries"},
]}


def test_summary_figures_on_a_known_run():
    s = cmp_.summarise(RUN)
    assert s["missing_side"] == ["NVR"] and s["oi_dates"] == {"2026-11-18": 2}
    assert s["oi_identical"] == (2, 3)                 # A1, B1 equal; A2 differs; A3 None on yf is not shared
    assert s["pcr_far"] == ["A"] and s["pcr_flips"] == []
    assert abs(s["pcr_rel_median"] - 0.15) < 1e-9      # |1.3-1| = 0.30 and 0
    assert s["volume_match"]["alpaca_only"] == 3
    assert s["ucv_flips"] == ["A", "B"]
    assert s["ucv_small_passes"] == {"alpaca": ["B"], "yf": []}   # B: 4 calls, 0 puts on Alpaca


def test_an_errored_side_is_counted_and_never_a_flip():
    run = {**RUN, "results": [{**RUN["results"][0],
                                "alpaca": {**_side(1.3, False, False, 0, 0, {}, error=True), "oi_dates": []}}]}
    s = cmp_.summarise(run)
    assert s["ucv"]["alpaca_error"] == 1 and s["ucv_flips"] == []
