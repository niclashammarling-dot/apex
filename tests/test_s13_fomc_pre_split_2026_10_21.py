"""S13 FOMC_PRE split: only the close whose next session is the FOMC day spans the window."""
import pandas as pd

from backend.backtest.s13_fomc_pre_split_2026_10_21 import split_pre


def test_split_pre_names_the_close_before_the_fomc_day():
    c = pd.Series(["CLEAR", "FOMC_PRE", "FOMC_PRE", "FOMC_DAY", "CLEAR", "FOMC_PRE"])
    assert list(split_pre(c)) == ["CLEAR", "PRE_EARLIER", "PRE_NEXT_IS_FOMC", "FOMC_DAY", "CLEAR", "PRE_EARLIER"]
