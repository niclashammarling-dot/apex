"""
S13 FOMC_PRE split probe (2026-10-21, exploratory; no verdict rests on it). Why: L0's FOMC_PRE
pooled two kinds of close, and the note read its CI as excluding the Lucca & Moench drift. Only
the session just before the FOMC day has a one-day return spanning the pre-announcement window
(vault raw/notes/2026-10/2026-10-21-apex-preregistration-class-by-effect-window.md, defect D7).
How: L0's own windows, pool series and Newey-West regression, with FOMC_PRE replaced by
PRE_NEXT_IS_FOMC and PRE_EARLIER. What: the h = 1 coefficient and 95 % CI of each half, pool and
SPY, IS and OOS, printed. The split is pre-registered for the R1 card, not here.

Usage: python -m backend.backtest.s13_fomc_pre_split_2026_10_21
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.backtest import s13_macro_l0_2026_10_21 as l0

SPLIT = ("PRE_NEXT_IS_FOMC", "PRE_EARLIER", "FOMC_DAY", "RELEASE", "EVE")


def split_pre(classes: pd.Series) -> pd.Series:
    """FOMC_PRE becomes PRE_NEXT_IS_FOMC when the next session is the FOMC day, else PRE_EARLIER."""
    nxt = classes.shift(-1)
    out = classes.copy()
    pre = classes == "FOMC_PRE"
    out[pre & (nxt == "FOMC_DAY")] = "PRE_NEXT_IS_FOMC"
    out[pre & (nxt != "FOMC_DAY")] = "PRE_EARLIER"
    return out


def main() -> int:
    for w in l0.WINDOWS:
        closes, spy, classes = l0.window_data(w)
        split = split_pre(classes)
        for name, ser in (("pool", closes), ("spy", spy)):
            y = l0.forward_returns(ser, 1)
            c = split.loc[y.index]
            X = np.column_stack([np.ones(len(y))] + [(c == k).to_numpy(float) for k in SPLIT])
            b, se = l0.nw_ols(y.to_numpy(float), X, 1)
            print(w, name, "clear %.3f" % b[0], " ".join(
                f"{k} {b[i]:+.3f} [{b[i] - 1.96 * se[i]:+.2f},{b[i] + 1.96 * se[i]:+.2f}] n{int((c == k).sum())}"
                for i, k in enumerate(SPLIT, 1) if k.startswith("PRE")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
