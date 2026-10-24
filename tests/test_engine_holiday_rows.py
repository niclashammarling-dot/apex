"""A row where only ^VIX has a bar (a US holiday: 2026-05-25, 2026-09-07 in the yfinance
union index) is not a session (2026-10-17). Before the fix both engines simulated it
as a trading day and kept it in every ticker's indicator window: the slow engine's
rolling ATR went NaN → 0 for 14 sessions (Kelly size unscaled by volatility), the fast
engine's asof() froze ATR at its pre-holiday value. Found by CHECK 76 layer 3 at live
cooloffs (MRVL 05-26: slow 744 vs fast 293)."""
from datetime import date

import numpy as np
import pandas as pd

import backend.backtest.engine as se
import backend.backtest.engine_fast as ef
from tests.test_engine_flag_off_parity import UNIVERSE, synthetic_raw

HOLIDAY = pd.Timestamp("2025-05-26")     # Memorial Day 2025, not in the bdate index… yet


def _with_holiday_row(raw: pd.DataFrame) -> pd.DataFrame:
    row = pd.DataFrame({c: [np.nan] for c in raw.columns}, index=[HOLIDAY])
    row[("^VIX", "Close")] = 18.0
    row.columns = raw.columns
    return pd.concat([raw.drop(index=HOLIDAY, errors="ignore"), row]).sort_index()


def _caches(raw):
    ts = {t: s for s, (tk, _, _) in UNIVERSE.items() for t in tk}
    se_ = {s: e for s, (_, e, _) in UNIVERSE.items()}
    days = ef._trading_days(date(2025, 5, 1), date(2025, 6, 27), raw)
    spy = ef._build_spy_cache(raw, days); etf = ef._build_etf_cache(raw, list(se_.values()), days)
    return days, ef._build_signal_cache(raw, ts, se_, spy, etf, days)


def test_holiday_row_is_not_a_session_in_either_engine():
    raw = _with_holiday_row(synthetic_raw())
    assert HOLIDAY in raw.index                                            # control: the row is there
    for mod in (ef, se):
        days = mod._trading_days(date(2025, 5, 1), date(2025, 6, 27), raw)
        assert HOLIDAY.date() not in days, mod.__name__


def test_holiday_row_leaves_every_cached_signal_unchanged():
    clean = synthetic_raw().drop(index=HOLIDAY, errors="ignore")
    d0, c0 = _caches(clean)
    d1, c1 = _caches(_with_holiday_row(clean))
    assert d0 == d1
    assert c0 == c1


def test_slow_engine_history_slice_has_no_holiday_row():
    raw = _with_holiday_row(synthetic_raw())
    df = se._slice_history(raw, "AAA", date(2025, 6, 2), lookback_days=90)
    assert HOLIDAY not in df.index and len(df) == 90 and df["Close"].notna().all()
