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


def test_eod_cutoff_is_1615_new_york_across_the_dst_change():
    from datetime import date
    assert g.eod_cutoff_utc(date(2026, 9, 2)) == "2026-09-02T20:15:00+00:00"    # EDT
    assert g.eod_cutoff_utc(date(2026, 11, 20)) == "2026-11-20T21:15:00+00:00"  # EST


def test_live_trace_keeps_the_last_row_per_date_and_sector(tmp_path):
    import json
    f = tmp_path / "trace.jsonl"
    rows = [{"date": "2026-09-01", "sector": "Energy", "aggregate_score": 0.6821},
            {"date": "2026-09-01", "sector": "Energy", "aggregate_score": 0.7787},
            {"date": "2026-09-01", "sector": "Defense", "aggregate_score": 0.2}]
    f.write_text("\n".join(json.dumps(r) for r in rows) + "\n\n")
    t = g.live_trace(f)
    assert t["2026-09-01"]["Energy"]["aggregate_score"] == 0.7787
    assert set(t["2026-09-01"]) == {"Energy", "Defense"}


def test_live_daily_history_averages_per_utc_day_inside_the_bounds(tmp_path, monkeypatch):
    import sqlite3
    from datetime import date
    db = tmp_path / "apex.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE sector_snapshots (id INTEGER PRIMARY KEY, timestamp TEXT, sector TEXT, avg_score REAL,"
              " top_ticker TEXT, top_score REAL, ticker_count INTEGER)")
    c.executemany("INSERT INTO sector_snapshots (timestamp, sector, avg_score) VALUES (?,?,?)", [
        ("2026-09-01T14:00:00+00:00", "Energy", 0.4), ("2026-09-01T18:00:00+00:00", "Energy", 0.6),
        ("2026-09-02T14:00:00+00:00", "Energy", 0.5), ("2026-09-02T21:00:00+00:00", "Energy", 0.9),  # after cutoff
        ("2026-08-01T14:00:00+00:00", "Energy", 0.1)])                                             # before lo
    c.commit(); c.close()
    monkeypatch.setattr(g, "DB", db)
    rows = g.live_daily_history(date(2026, 8, 15), "2026-09-02T20:15:00+00:00")
    assert [(r["day"], r["avg_score"]) for r in rows] == [("2026-09-01", 0.5), ("2026-09-02", 0.5)]
    assert rows[0]["last_ts"] == "2026-09-01T18:00:00+00:00"


def test_main_writes_the_read_and_a_failed_control_means_no_read(tmp_path, monkeypatch):
    import json
    good = [day(S, S, {"A": 1.0}, {"A": 1.0}) for _ in range(3)]
    ok = {"c1_engine_reproduces_timeline": {"checked": 3, "failed": []},
          "c2_live_input_equals_logged": {"checked": 3, "failed": [], "n_failed": 0}}
    monkeypatch.setattr(g, "walk", lambda a, b: (good, ok))
    out = tmp_path / "r.json"
    assert g.main(["--start", "2026-09-02", "--end", "2026-09-04", "--out", str(out)]) == 0
    r = json.loads(out.read_text())
    assert r["controls_pass"] and r["summary"]["regime"]["allocation_set_identical"] == "3/3"
    bad = {**ok, "c2_live_input_equals_logged": {"checked": 3, "failed": [{"date": "2026-09-01"}], "n_failed": 1}}
    monkeypatch.setattr(g, "walk", lambda a, b: (good, bad))
    assert g.main(["--start", "2026-09-01", "--end", "2026-09-04", "--out", str(out)]) == 3
    r = json.loads(out.read_text())
    assert r["controls_pass"] is False and r["summary"] is None
