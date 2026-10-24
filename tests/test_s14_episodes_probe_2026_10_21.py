"""S14 episode probe: runs of level closes, a one-session gap joins, a two-session gap splits."""
from backend.backtest.s14_episodes_probe_2026_10_21 import episodes


def test_one_session_gap_joins_two_splits():
    lv = [False, True, True, False, True, False, False, True, False]
    days = [f"d{i}" for i in range(len(lv))]
    assert episodes(lv, days) == [("d1", "d4", 3), ("d7", "d7", 1)]
