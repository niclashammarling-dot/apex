"""CHECK 71 — per-sector posterior-ceiling entry-floor sweep (pure core)."""
from audit.checks_sector import _entry_floor_sweep

ENTRY, CEIL = 0.37, 0.95


def _row(d, sector, agg):
    return {"date": d, "sector": sector, "aggregate_score": agg}


def test_dark_is_ceiling_adjusted_not_current_posterior():
    # 0.38 × 0.95 = 0.361 < 0.37 → dark even though a bare composite of 0.38 > 0.37
    res = _entry_floor_sweep([_row("2026-09-15", "Healthcare", 0.38),
                              _row("2026-09-15", "Energy", 0.70)], ENTRY, CEIL)
    assert res["dark_today"] == ["Healthcare"]
    assert res["enterable"] == ["Energy"]
    assert res["min_agg"] == round(ENTRY / CEIL, 4)


def test_same_day_rerun_collapses_to_last_row():
    # First write dark, re-run same day enterable — only the last row counts, once.
    res = _entry_floor_sweep([_row("2026-09-15", "Tech", 0.30),
                              _row("2026-09-15", "Tech", 0.50)], ENTRY, CEIL)
    st = res["sectors"]["Tech"]
    assert res["dark_today"] == [] and st["days"] == 1 and st["dark_days"] == 0


def test_persistence_counts_dark_days_in_window():
    rows = [_row(f"2026-09-{d:02d}", "Transportation", 0.27) for d in range(1, 11)]
    rows[-1]["aggregate_score"] = 0.27
    res = _entry_floor_sweep(rows, ENTRY, CEIL)
    st = res["sectors"]["Transportation"]
    assert (st["dark_days"], st["days"], st["persistent"]) == (10, 10, True)


def test_persistence_below_share_is_not_persistent():
    rows = [_row(f"2026-09-{d:02d}", "S", 0.27 if d % 2 else 0.60) for d in range(1, 11)]
    res = _entry_floor_sweep(rows, ENTRY, CEIL)
    st = res["sectors"]["S"]
    assert st["dark_days"] == 5 and st["persistent"] is False


def test_window_drops_old_dates():
    rows = [_row(f"2026-08-{d:02d}", "S", 0.27) for d in range(1, 30)]
    rows += [_row("2026-09-01", "S", 0.60)]
    res = _entry_floor_sweep(rows, ENTRY, CEIL, window=1)
    assert res["n_dates"] == 1 and res["sectors"]["S"]["days"] == 1


def test_empty_or_unparseable_yields_no_latest_date():
    assert _entry_floor_sweep([], ENTRY, CEIL)["latest_date"] is None
    assert _entry_floor_sweep([{"date": "2026-09-15", "sector": "S", "aggregate_score": None}],
                              ENTRY, CEIL)["latest_date"] is None


def test_sector_absent_today_is_neither_dark_nor_enterable():
    rows = [_row("2026-09-14", "Old", 0.20), _row("2026-09-15", "New", 0.60)]
    res = _entry_floor_sweep(rows, ENTRY, CEIL)
    assert res["dark_today"] == [] and res["enterable"] == ["New"]
    assert res["sectors"]["Old"]["dark_days"] == 1


def test_series_is_per_date_enterable_count_in_date_order():
    rows = [_row("2026-09-14", "A", 0.60), _row("2026-09-14", "B", 0.60),
            _row("2026-09-15", "A", 0.60), _row("2026-09-15", "B", 0.20)]
    res = _entry_floor_sweep(rows, ENTRY, CEIL)
    assert res["series"] == [("2026-09-14", 2), ("2026-09-15", 1)]
