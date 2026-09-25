"""CHECK 65 — posterior saturation monitor: pin duration, and divergence independence."""
import json

import pytest

from audit import _audit_core as core
import audit.checks_sector as cs
from audit.checks_sector import (
    CHECK65_PIN_STREAK_CRIT, _ceiling_pin_streak, _lr_decomposition,
)

CEIL_CUT = cs.CHECK65_FLOOR_CUTOFF


def _row(d, sector, pre, agg, binding=None, **lrs):
    r = {
        "date": d, "sector": sector, "pre_clamp_posterior": pre,
        "aggregate_score": agg, "decayed_prior": 0.8641,
        "clamp_binding": pre > 0.95 if binding is None else binding,
        "lr_ticker": 1.0, "lr_etf": 1.0, "lr_rs": 1.0, "lr_ipo": 1.0, "lr_rank": 1.0,
    }
    r.update(lrs)
    return r


@pytest.fixture
def run_check(tmp_path, monkeypatch):
    """Drive check65 against a synthetic trace; return its findings as (sev, detail)."""
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(cs, "REPO", tmp_path)

    def _run(rows):
        (tmp_path / "data" / "regime_signal_trace.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows))
        core.findings.clear()
        cs.check65()
        return [(sev, detail) for _, _, sev, _, detail in core.findings]

    yield _run
    core.findings.clear()


# ── pure cores ────────────────────────────────────────────────────────────────

def test_dominant_term_is_the_largest_lift_not_a_fixed_field():
    # The pre-2026-09-25 detail line named lr_ipo_raw unconditionally; on 2026-09-23
    # lr_ipo was 1.0 and lr_ticker 4.0 carried the pin.
    product, name, val = _lr_decomposition(
        _row("d", "S", 0.97, 0.6, lr_ticker=4.0, lr_etf=0.909, lr_rank=1.5))
    assert (name, val) == ("lr_ticker", 4.0)
    assert product == pytest.approx(4.0 * 0.909 * 1.5)


def test_dominant_term_is_none_when_nothing_lifts():
    product, name, val = _lr_decomposition(_row("d", "S", 0.2, 0.3, lr_ticker=0.5))
    assert name is None and val is None and product == pytest.approx(0.5)


def test_pin_streak_counts_consecutive_sessions_and_breaks_on_a_clean_day():
    rows = [_row("2026-08-18", "H", 0.967, 0.62), _row("2026-08-19", "H", 0.977, 0.65),
            _row("2026-08-20", "H", 0.910, 0.65), _row("2026-08-21", "H", 0.960, 0.65)]
    assert _ceiling_pin_streak(rows, "H", "2026-08-19", CEIL_CUT)["streak"] == 2
    assert _ceiling_pin_streak(rows, "H", "2026-08-21", CEIL_CUT)["streak"] == 1


def test_pin_streak_bridges_a_weekend():
    # 2026-08-21 Fri → 2026-08-24 Mon: no session between them, so the streak continues.
    rows = [_row("2026-08-21", "H", 0.96, 0.62), _row("2026-08-24", "H", 0.97, 0.62)]
    assert _ceiling_pin_streak(rows, "H", "2026-08-24", CEIL_CUT)["streak"] == 2


def test_pin_streak_stops_at_a_row_less_session_and_sizes_the_gap():
    # The live case: Healthcare 08-18 → 08-25 is six sessions but five trace rows; 08-20
    # and 08-21 have none. A trace-date walk reported 5; sessions say 2, plus a 2-session
    # hole. Under-reporting duration where the regime pipeline itself was down is the
    # failure mode this refuses.
    rows = [_row("2026-08-18", "H", 0.967, 0.62), _row("2026-08-19", "H", 0.977, 0.65),
            _row("2026-08-24", "H", 0.963, 0.60), _row("2026-08-25", "H", 0.968, 0.546)]
    st = _ceiling_pin_streak(rows, "H", "2026-08-25", CEIL_CUT)
    assert (st["streak"], st["unknown_before"]) == (2, 2)


def test_off_session_row_anchors_but_never_links():
    # 2026-08-23 is a Sunday catch-up run. It may be the day reported on, but it must not
    # bridge the two missing weekday sessions before it.
    rows = [_row("2026-08-19", "H", 0.977, 0.65), _row("2026-08-23", "H", 0.960, 0.67),
            _row("2026-08-24", "H", 0.963, 0.60)]
    anchored = _ceiling_pin_streak(rows, "H", "2026-08-23", CEIL_CUT)
    assert anchored["streak"] == 1 and anchored["unknown_before"] == 2
    bridged = _ceiling_pin_streak(rows, "H", "2026-08-24", CEIL_CUT)
    assert bridged["streak"] == 1 and bridged["off_session_rows"] == 1


def test_off_session_count_is_scoped_to_the_streak_span():
    # An August Sunday row must not decorate a September finding.
    rows = [_row("2026-08-23", "H", 0.96, 0.67), _row("2026-09-21", "H", 0.90, 0.60),
            _row("2026-09-22", "H", 0.98, 0.63), _row("2026-09-23", "H", 0.97, 0.61)]
    st = _ceiling_pin_streak(rows, "H", "2026-09-23", CEIL_CUT)
    assert (st["streak"], st["off_session_rows"]) == (2, 0)


def test_pin_streak_skips_a_market_holiday():
    # 2026-09-07 is Labor Day — not a session, so it is not a hole.
    rows = [_row("2026-09-04", "H", 0.96, 0.62), _row("2026-09-08", "H", 0.97, 0.62)]
    st = _ceiling_pin_streak(rows, "H", "2026-09-08", CEIL_CUT)
    assert (st["streak"], st["unknown_before"]) == (2, 0)
    assert st["basis"].startswith("NYSE")


def test_pin_streak_ignores_floor_clamps():
    # clamp_binding at the floor (0.05 on a depressed sector) is the gate working.
    rows = [_row("2026-08-18", "H", 0.04, 0.10, binding=True),
            _row("2026-08-19", "H", 0.972, 0.62)]
    assert _ceiling_pin_streak(rows, "H", "2026-08-19", CEIL_CUT)["streak"] == 1


# ── severity is duration ──────────────────────────────────────────────────────

def test_single_session_pin_is_a_warning(run_check):
    out = run_check([_row("2026-09-22", "Semiconductors", 0.9814, 0.6349, lr_ticker=4.0)])
    assert [sev for sev, _ in out] == ["WARNING"]
    assert "ceiling pin, session 1" in out[0][1]


def test_sustained_pin_escalates_to_critical(run_check):
    rows = [_row(f"2026-09-2{d}", "Semiconductors", 0.97, 0.6349, lr_ticker=4.0)
            for d in range(2, 2 + CHECK65_PIN_STREAK_CRIT)]
    sev, detail = run_check(rows)[0]
    assert sev == "CRITICAL"
    assert f"ceiling pin, session {CHECK65_PIN_STREAK_CRIT}" in detail
    assert "re-saturates" in detail


def test_floor_clamp_is_not_reported(run_check):
    assert run_check([_row("2026-09-11", "Defense", 0.04, 0.12, binding=True)]) == []


# ── the elif regression ───────────────────────────────────────────────────────

def test_divergence_fires_while_the_clamp_is_binding(run_check):
    # Healthcare 2026-09-01: pre_clamp 0.9501, aggregate 0.4779. Under the old `elif`
    # the divergence arm was unreachable whenever the pin arm matched — which is when
    # it matters — and this was reported as a pin with LR-calibration advice.
    out = run_check([_row("2026-09-01", "Healthcare", 0.9501, 0.4779, lr_ipo=2.4)])
    kinds = {("pin" if "ceiling pin" in d else "divergence"): sev for sev, d in out}
    assert kinds == {"pin": "WARNING", "divergence": "CRITICAL"}


def test_divergence_alone_is_a_warning(run_check):
    out = run_check([_row("2026-08-28", "Healthcare", 0.93, 0.52, binding=False)])
    assert [sev for sev, _ in out] == ["WARNING"]
    assert "divergence" in out[0][1]


def test_divergence_detail_routes_to_exposure_not_allocation(run_check):
    # Sector allocation cancels out of per-position sizing; the level is not exposure.
    _, detail = run_check([_row("2026-08-28", "Healthcare", 0.93, 0.52, binding=False)])[0]
    assert "cancels out of per-position sizing" in detail


def test_pin_detail_does_not_blame_a_formula_when_no_lr_is_near_the_cap(run_check):
    _, detail = run_check([_row("2026-09-23", "Semiconductors", 0.972, 0.6145,
                                lr_ticker=4.0, lr_rank=1.5)])[0]
    assert "the lift is the product, not one formula" in detail
    assert "escaped calibration" not in detail


def test_pin_detail_names_an_escaped_formula_when_one_is_past_the_guard(run_check):
    # The founding 2026-07-16 incident: ipo_share 1.0 → lr_ipo_raw 205.6.
    _, detail = run_check([_row("2026-07-16", "Technology", 0.999, 0.6, lr_ipo=205.6)])[0]
    assert "lr_ipo" in detail and "escaped calibration" in detail


def test_same_day_rerun_reports_once(run_check):
    rows = [_row("2026-09-23", "Semiconductors", 0.972, 0.6145, lr_ticker=4.0),
            _row("2026-09-23", "Semiconductors", 0.972, 0.6145, lr_ticker=4.0)]
    assert len(run_check(rows)) == 1


def test_gap_is_reported_in_the_finding_not_bridged(run_check):
    out = run_check([_row("2026-08-18", "Healthcare", 0.967, 0.62),
                     _row("2026-08-19", "Healthcare", 0.977, 0.65),
                     _row("2026-08-24", "Healthcare", 0.963, 0.60),
                     _row("2026-08-25", "Healthcare", 0.968, 0.546)])
    pin = next(d for _, d in out if "ceiling pin" in d)
    assert "ceiling pin, session 2" in pin
    assert "Streak is a floor, not the duration" in pin and "CHECK 77" in pin


def test_no_gap_note_when_every_session_has_a_row(run_check):
    _, detail = run_check([_row("2026-09-22", "Semiconductors", 0.98, 0.63, lr_ticker=4.0),
                           _row("2026-09-23", "Semiconductors", 0.97, 0.61, lr_ticker=4.0)])[0]
    assert "Streak is a floor" not in detail and "Session basis" not in detail
