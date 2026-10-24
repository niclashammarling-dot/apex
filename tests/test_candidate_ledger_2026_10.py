"""Candidate ledger builder (2026-10-24): the class summary splits as stated and the macro band is
[threshold, threshold / (1 - p)) on pre-event days."""
import numpy as np
import pandas as pd

import backend.backtest.candidate_ledger_2026_10 as cl


def _df():
    rows = []
    for k in range(40):
        md = ["clear", "pre_event", "hard_block", "clear"][k % 4]
        rows.append({"date": f"2020-01-{k % 28 + 1:02d}", "ticker": f"T{k}", "sector": "S", "threshold": 0.65,
                     "score": 0.66 if k % 8 == 1 else 0.80, "macro_day": md, "vix": 35.0 if k < 10 else 20.0,
                     "etf_ret": -0.02 if k % 5 == 0 else 0.01, "earnings_near": None, "held": False, "cooloff": False,
                     "pnl_live": 0.01 * ((k % 3) - 1), "entered": k % 7 == 0})
    return pd.DataFrame(rows)


def test_ci_and_small_n():
    c = cl.ci(pd.Series([0.01, 0.02, 0.03]))
    assert np.isclose(c["mean"], 0.02) and c["lo"] < 0.02 < c["hi"] and c["win_rate"] == 1.0
    assert cl.ci(pd.Series([0.01]))["n"] == 1


def test_summary_classes():
    df = _df()
    s = cl.summary(df)
    assert s["rows"] == 40 and s["entered"] == int(df["entered"].sum())
    assert set(s["by_macro_day"]) == {"clear", "pre_event", "hard_block"}
    pre = df["macro_day"] == "pre_event"
    band = pre & (df["score"] < 0.65 / (1 - cl.MACRO_P))
    assert s["macro_band"]["in_band_pre_event"]["n"] == int(band.sum()) > 0
    assert s["macro_band"]["pre_event_above_band"]["n"] == int((pre & ~band).sum())
    assert s["vix_ge_30"]["n"] == 10 and s["etf_below_floor"]["n"] == 8
    assert s["earnings_near_known"] is False
