"""Edge probe 2026-10-20: the slot audit and the rank p that the vault note's numbers rest on."""
from backend.backtest.edge_probe_2026_10_20 import rank_p, slot_audit


def test_slot_audit_splits_short_days_by_cause():
    ref_entries = {"d1": 2, "d2": 3, "d3": 1}
    ref_open = {"d1": 9, "d2": 10, "d3": 5}
    mine = {"d1": 2, "d2": 1}                       # d1 full, d2 short by 2, d3 short by 1
    open_after = {"d1": 9, "d2": 10, "d3": 4}       # d2 at its cap (max(9, 10)), d3 below it
    out = slot_audit(ref_entries, ref_open, mine, open_after, max_pos=9)
    assert out == {"ref_entries": 6, "rnd_entries": 3, "short_days": 2, "missing_entries": 3,
                   "short_slot_bound": 1, "short_pool_bound": 1}


def test_slot_audit_cap_is_at_least_max_positions():
    # The reference held 5 after entries, but RND's roof is still max_positions (9): 7 open is not slot-bound.
    out = slot_audit({"d": 2}, {"d": 5}, {"d": 0}, {"d": 7}, max_pos=9)
    assert out["short_slot_bound"] == 0 and out["short_pool_bound"] == 1


def test_rank_p_matches_the_battery_rule():
    assert rank_p(5.0, [1, 2, 3, 4]) == 1 / 5           # nothing at or above: the floor 1/(n+1)
    assert rank_p(2.0, [1, 2, 3, 4]) == 4 / 5           # ties count against the reference
    assert rank_p(0.0, [1, 2, 3, 4]) == 1.0
