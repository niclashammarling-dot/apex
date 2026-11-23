"""gap29_snapshot_parity: the sealed report and its reading rule on synthetic walks (2026-11-23)."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "gap29_snapshot_parity", Path(__file__).resolve().parent.parent / "scripts" / "gap29_snapshot_parity.py")
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)


def day(snap_e, snap_l, alloc_e, alloc_l, caps_e=None, caps_l=None, live=None):
    caps_e = caps_e or {s: [1.0, 1.0, 1.0] for s in snap_e}
    caps_l = caps_l or {s: [1.0, 1.0, 1.0] for s in snap_l}
    return {"snap_e": snap_e, "snap_l": snap_l, "alloc_e": alloc_e, "alloc_l": alloc_l,
            "leader_e": max(alloc_e, key=alloc_e.get), "leader_l": max(alloc_l, key=alloc_l.get),
            "state_e": "neutral", "state_l": "neutral", "post_e": {"A": 0.3}, "post_l": {"A": 0.3},
            "caps_e": caps_e, "caps_l": caps_l, "alloc_live": live}


S = {"A": 0.5, "B": 0.4, "C": 0.3}


def test_identical_walks_are_not_material():
    days = [day(S, S, {"A": 0.6, "B": 0.4}, {"A": 0.6, "B": 0.4}) for _ in range(20)]
    r = g.summarise(days)
    assert r["regime"]["allocation_set_identical"] == "20/20"
    assert r["input"]["spearman_median"] == 1.0 and r["input"]["median_abs_delta"] == 0.0
    assert r["reading"] == "not material at both levels"


def test_two_of_twenty_set_changes_cross_the_five_percent_bound():
    same = [day(S, S, {"A": 0.6, "B": 0.4}, {"A": 0.6, "B": 0.4}) for _ in range(18)]
    moved = [day(S, {"A": 0.3, "B": 0.4, "C": 0.5}, {"A": 1.0}, {"C": 1.0}) for _ in range(2)]
    r = g.summarise(same + moved)
    assert r["regime"]["allocation_set_differs_share"] == 0.1
    assert r["input"]["same_top_sector"] == "18/20"
    assert r["reading"] == "material for allocation"


def test_one_of_twenty_is_at_the_bound_and_caps_are_read_separately():
    caps_l = {"A": [1.2, 1.0, 1.0], "B": [1.0, 1.0, 1.0], "C": [1.0, 1.0, 1.0]}
    days = [day(S, S, {"A": 1.0}, {"A": 1.0}, caps_l=caps_l) for _ in range(19)]
    days.append(day(S, S, {"A": 1.0}, {"B": 1.0}, live={"B": 1.0}))
    r = g.summarise(days)
    assert r["regime"]["allocation_set_differs_share"] == 0.05      # 5 % is not more than 5 %
    assert r["caps"]["score_w_differs_share"] == round(19 / 60, 4)
    assert r["reading"] == "not material for allocation; caps differ"
    assert r["reference_live_logged"] == {"trace_dates": 1, "engine_set_equals_live": "0/1",
                                          "live_input_set_equals_live": "1/1"}


def test_spearman_ties_and_short_input():
    assert abs(g.spearman({"A": 1, "B": 2, "C": 3}, {"A": 3, "B": 2, "C": 1}) + 1.0) < 1e-9
    assert g.spearman({"A": 1, "B": 1}, {"A": 1, "B": 2}) is None
    assert abs(g.spearman({"A": 1, "B": 1, "C": 2}, {"A": 1, "B": 2, "C": 3}) - 0.866) < 0.001
