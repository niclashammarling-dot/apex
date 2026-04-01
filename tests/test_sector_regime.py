"""
Tests for sector_regime velocity, regime confidence, and transition matrix layers.
Run: cd ~/apex && PYTHONPATH=. .venv/bin/pytest tests/test_sector_regime.py -v
"""
import pytest
from backend.sector_regime import (
    _compute_velocity, _compute_regime,
    VELOCITY_THRESHOLD, CONFIRMED_MIN, CYCLICAL, DEFENSIVE,
)
from backend.sector_transitions import (
    _compute_regime_tagged_matrix, MIN_REGIME_TRANSITIONS,
    get_conditioned_transition_matrix, compute_ticker_rotation_scores,
    _VELOCITY_NORM_CENTER, _VELOCITY_NORM_WIDTH,
)


# ─────────────────────────────────────────────────────────────────────────────
# _compute_velocity
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeVelocity:

    def _scores(self, n=30, start=0.50, delta=0.0):
        """Generate a flat or uniformly-sloped score series."""
        return [round(start + delta * i, 4) for i in range(n)]

    # ── signal classification ──────────────────────────────────────────────

    def test_accelerating_when_5d_delta_above_threshold(self):
        # Scores rise by 0.02 per day → velocity_5d ≈ +0.10, well above 0.015
        scores = self._scores(30, start=0.40, delta=0.02)
        vel = _compute_velocity(scores)
        assert vel["velocity"] == "accelerating"

    def test_decelerating_when_5d_delta_below_negative_threshold(self):
        # Scores fall by 0.02 per day → velocity_5d ≈ -0.10
        scores = self._scores(30, start=0.80, delta=-0.02)
        vel = _compute_velocity(scores)
        assert vel["velocity"] == "decelerating"

    def test_flat_when_scores_unchanged(self):
        scores = self._scores(30, start=0.55, delta=0.0)
        vel = _compute_velocity(scores)
        assert vel["velocity"] == "flat"

    def test_flat_at_exactly_threshold_boundary(self):
        # velocity_5d == +VELOCITY_THRESHOLD should be "accelerating" (>=)
        # Build scores so last - 5d_ago == exactly VELOCITY_THRESHOLD
        base = [0.50] * 30
        base[-1] = 0.50 + VELOCITY_THRESHOLD  # velocity_5d == VELOCITY_THRESHOLD exactly
        vel = _compute_velocity(base)
        assert vel["velocity"] == "accelerating"

    def test_flat_just_inside_boundary(self):
        base = [0.50] * 30
        base[-1] = 0.50 + VELOCITY_THRESHOLD - 0.001  # just below threshold
        vel = _compute_velocity(base)
        assert vel["velocity"] == "flat"

    # ── numeric fields ─────────────────────────────────────────────────────

    def test_velocity_5d_is_correct(self):
        scores = [0.50] * 30
        scores[-1] = 0.56  # today
        # scores[-6] (5 days ago) is still 0.50
        vel = _compute_velocity(scores)
        assert vel["velocity_5d"] == pytest.approx(0.06, abs=1e-4)

    def test_velocity_20d_is_correct(self):
        scores = [0.40] * 30
        scores[-1] = 0.60
        # scores[max(0, 30-21)] = scores[9] = 0.40
        vel = _compute_velocity(scores)
        assert vel["velocity_20d"] == pytest.approx(0.20, abs=1e-4)

    def test_accel_positive_when_momentum_building(self):
        # Scores accelerate upward: slow rise then fast rise
        scores = [0.50 + i * 0.005 for i in range(20)] + \
                 [0.60 + i * 0.020 for i in range(10)]
        vel = _compute_velocity(scores)
        assert vel["accel"] > 0, "Accelerating trend should have positive accel"

    def test_accel_negative_when_momentum_fading(self):
        # Fast rise then slows down
        scores = [0.50 + i * 0.020 for i in range(20)] + \
                 [0.90 + i * 0.001 for i in range(10)]
        vel = _compute_velocity(scores)
        assert vel["accel"] < 0, "Fading trend should have negative accel"

    # ── short series edge cases ────────────────────────────────────────────

    def test_handles_single_row(self):
        vel = _compute_velocity([0.55])
        assert vel["velocity_5d"] == 0.0
        assert vel["velocity_20d"] == 0.0
        assert vel["accel"] == 0.0
        assert vel["velocity"] == "flat"

    def test_handles_exactly_six_rows(self):
        # 6 rows: index 0 is 5d ago, index 5 is today
        scores = [0.50, 0.51, 0.52, 0.53, 0.54, 0.60]
        vel = _compute_velocity(scores)
        assert vel["velocity_5d"] == pytest.approx(0.10, abs=1e-4)
        assert vel["velocity"] == "accelerating"

    def test_handles_fewer_than_six_rows(self):
        # Only 4 rows: score_5d_ago clamps to scores[0]
        scores = [0.50, 0.52, 0.54, 0.60]
        vel = _compute_velocity(scores)
        assert vel["velocity_5d"] == pytest.approx(0.10, abs=1e-4)

    def test_all_fields_present(self):
        scores = self._scores(30, start=0.5, delta=0.001)
        vel = _compute_velocity(scores)
        for key in ("velocity_5d", "velocity_20d", "accel", "velocity"):
            assert key in vel, f"Missing key: {key}"

    def test_velocity_values_are_rounded_to_4dp(self):
        scores = [0.123456789] * 30
        vel = _compute_velocity(scores)
        for key in ("velocity_5d", "velocity_20d", "accel"):
            assert vel[key] == round(vel[key], 4)


# ─────────────────────────────────────────────────────────────────────────────
# _compute_regime — confidence score
# ─────────────────────────────────────────────────────────────────────────────

def _make_sector_stats(cyclical_score: float, defensive_score: float,
                       above: bool = True, streak: int = 20) -> dict:
    """Build a minimal sector_stats dict for testing _compute_regime."""
    stats = {}
    for s in CYCLICAL:
        stats[s] = {"score": cyclical_score, "above": above if cyclical_score >= 0.50 else False,
                    "streak_days": streak}
    for s in DEFENSIVE:
        stats[s] = {"score": defensive_score, "above": above if defensive_score >= 0.50 else False,
                    "streak_days": streak}
    return stats


class TestComputeRegimeConfidence:

    # ── regime classification unchanged ───────────────────────────────────

    def test_risk_on_when_spread_positive(self):
        stats = _make_sector_stats(cyclical_score=0.65, defensive_score=0.50)
        result = _compute_regime(stats)
        assert result["regime"] == "risk_on"

    def test_risk_off_when_spread_negative(self):
        stats = _make_sector_stats(cyclical_score=0.45, defensive_score=0.60)
        result = _compute_regime(stats)
        assert result["regime"] == "risk_off"

    def test_neutral_when_spread_small(self):
        stats = _make_sector_stats(cyclical_score=0.52, defensive_score=0.50)
        result = _compute_regime(stats)
        assert result["regime"] == "neutral"

    # ── confidence = 0 for neutral ─────────────────────────────────────────

    def test_neutral_confidence_is_zero(self):
        stats = _make_sector_stats(cyclical_score=0.52, defensive_score=0.50)
        result = _compute_regime(stats)
        assert result["regime_confidence"] == 0.0

    # ── confidence increases with stronger signals ─────────────────────────

    def test_confidence_higher_with_larger_spread(self):
        weak  = _make_sector_stats(cyclical_score=0.56, defensive_score=0.50, streak=CONFIRMED_MIN)
        strong = _make_sector_stats(cyclical_score=0.75, defensive_score=0.50, streak=CONFIRMED_MIN)
        c_weak   = _compute_regime(weak)["regime_confidence"]
        c_strong = _compute_regime(strong)["regime_confidence"]
        assert c_strong > c_weak

    def test_confidence_higher_with_confirmed_streak(self):
        short_streak = _make_sector_stats(cyclical_score=0.65, defensive_score=0.50, streak=3)
        long_streak  = _make_sector_stats(cyclical_score=0.65, defensive_score=0.50, streak=CONFIRMED_MIN)
        c_short = _compute_regime(short_streak)["regime_confidence"]
        c_long  = _compute_regime(long_streak)["regime_confidence"]
        assert c_long > c_short

    def test_confidence_in_0_1_range(self):
        for cyc, defs in [(0.80, 0.40), (0.40, 0.80), (0.52, 0.51), (0.99, 0.01)]:
            stats = _make_sector_stats(cyclical_score=cyc, defensive_score=defs, streak=30)
            c = _compute_regime(stats)["regime_confidence"]
            assert 0.0 <= c <= 1.0, f"Confidence {c} out of range for cyc={cyc}, def={defs}"

    def test_confidence_rounded_to_4dp(self):
        stats = _make_sector_stats(cyclical_score=0.70, defensive_score=0.52, streak=18)
        c = _compute_regime(stats)["regime_confidence"]
        assert c == round(c, 4)

    # ── full spread saturation ─────────────────────────────────────────────

    def test_spread_component_saturates_at_max_spread(self):
        from backend.sector_regime import MAX_SPREAD
        # At MAX_SPREAD the spread_score saturates to 1.0; going higher shouldn't exceed 1.0
        extreme = _make_sector_stats(cyclical_score=0.50 + MAX_SPREAD + 0.10,
                                     defensive_score=0.50, streak=CONFIRMED_MIN)
        c = _compute_regime(extreme)["regime_confidence"]
        assert c <= 1.0

    # ── existing numeric fields still present ─────────────────────────────

    def test_all_fields_present(self):
        stats = _make_sector_stats(cyclical_score=0.65, defensive_score=0.50)
        result = _compute_regime(stats)
        for key in ("regime", "regime_confidence", "cyclical_avg", "defensive_avg", "spread"):
            assert key in result, f"Missing key: {key}"


# ─────────────────────────────────────────────────────────────────────────────
# _compute_regime_tagged_matrix
# ─────────────────────────────────────────────────────────────────────────────

def _make_raw_history(months: list[tuple]) -> list[dict]:
    """
    Build a minimal sector_snapshots-style list for the transition matrix tests.
    months: [(month_str, {sector: avg_score}), ...]
    """
    rows = []
    for month, scores in months:
        for sector, score in scores.items():
            rows.append({"timestamp": f"{month}-15T00:00:00", "sector": sector, "avg_score": score})
    return rows


# Representative sector sets (minimal — just enough for regime detection)
_RISK_ON_SCORES  = {s: 0.70 for s in CYCLICAL}  | {s: 0.45 for s in DEFENSIVE}
_RISK_OFF_SCORES = {s: 0.40 for s in CYCLICAL}  | {s: 0.65 for s in DEFENSIVE}
_NEUTRAL_SCORES  = {s: 0.52 for s in CYCLICAL}  | {s: 0.50 for s in DEFENSIVE}


class TestRegimeTaggedMatrix:

    def _run(self, months_data, monkeypatch):
        """Patch get_sector_history and run _compute_regime_tagged_matrix."""
        import backend.sector_transitions as mod
        raw = _make_raw_history(months_data)
        monkeypatch.setattr("backend.db.get_sector_history", lambda days: raw)
        # Force cache miss so the patched DB is used
        mod._regime_matrix_cache    = {}
        mod._regime_matrix_cache_ts = 0
        return _compute_regime_tagged_matrix()

    # ── structure ─────────────────────────────────────────────────────────

    def test_returns_three_regime_keys(self, monkeypatch):
        months = [(f"2024-{i:02d}", _RISK_ON_SCORES) for i in range(1, 6)]
        result = self._run(months, monkeypatch)
        assert set(result.keys()) == {"risk_on", "risk_off", "neutral"}

    def test_empty_when_fewer_than_3_months(self, monkeypatch):
        months = [("2024-01", _RISK_ON_SCORES), ("2024-02", _RISK_ON_SCORES)]
        result = self._run(months, monkeypatch)
        assert result == {}

    def test_empty_when_no_data(self, monkeypatch):
        import backend.sector_transitions as mod
        monkeypatch.setattr("backend.db.get_sector_history", lambda days: [])
        mod._regime_matrix_cache    = {}
        mod._regime_matrix_cache_ts = 0
        result = _compute_regime_tagged_matrix()
        assert result == {}

    # ── transitions land in correct regime bucket ─────────────────────────

    def test_risk_on_transitions_in_risk_on_bucket(self, monkeypatch):
        # Three consecutive risk_on months; any top-2 churn lands in risk_on bucket
        tech_dominant   = {**_RISK_ON_SCORES, "Technology": 0.90, "Financials": 0.85}
        energy_dominant = {**_RISK_ON_SCORES, "Energy": 0.90, "Financials": 0.85}
        months = [
            ("2024-01", tech_dominant),
            ("2024-02", tech_dominant),
            ("2024-03", energy_dominant),   # Energy replaces Technology in top-2
            ("2024-04", energy_dominant),
            ("2024-05", energy_dominant),
        ]
        result = self._run(months, monkeypatch)
        # Some transition should appear in risk_on (not risk_off or neutral)
        risk_on_total = sum(
            sum(v.values()) for v in result["risk_on"].values()
        )
        risk_off_total = sum(
            sum(v.values()) for v in result["risk_off"].values()
        )
        assert risk_on_total > 0
        assert risk_off_total == 0

    def test_risk_off_transitions_in_risk_off_bucket(self, monkeypatch):
        util_dominant  = {**_RISK_OFF_SCORES, "Utilities": 0.85, "Healthcare": 0.80}
        staples_enters = {**_RISK_OFF_SCORES, "ConsumerStaples": 0.85, "Healthcare": 0.80}
        months = [
            ("2024-01", util_dominant),
            ("2024-02", util_dominant),
            ("2024-03", staples_enters),
            ("2024-04", staples_enters),
            ("2024-05", staples_enters),
        ]
        result = self._run(months, monkeypatch)
        risk_off_total = sum(sum(v.values()) for v in result["risk_off"].values())
        risk_on_total  = sum(sum(v.values()) for v in result["risk_on"].values())
        assert risk_off_total > 0
        assert risk_on_total == 0

    # ── MIN_REGIME_TRANSITIONS constant ───────────────────────────────────

    def test_min_regime_transitions_is_positive_int(self):
        assert isinstance(MIN_REGIME_TRANSITIONS, int)
        assert MIN_REGIME_TRANSITIONS > 0

    # ── probabilities sum to ≤ 1 per sector per regime ────────────────────

    def test_conditioned_matrix_probabilities_sum_to_one(self, monkeypatch):
        from backend.sector_transitions import get_conditioned_transition_matrix
        tech_dom  = {**_RISK_ON_SCORES, "Technology": 0.90, "Financials": 0.85}
        energy_dom = {**_RISK_ON_SCORES, "Energy": 0.90, "Financials": 0.85}
        months = (
            [("2023-{:02d}".format(i), tech_dom)  for i in range(1, 7)] +
            [("2023-{:02d}".format(i), energy_dom) for i in range(7, 13)]
        )
        import backend.sector_transitions as mod
        raw = _make_raw_history(months)
        monkeypatch.setattr("backend.db.get_sector_history", lambda days: raw)
        mod._regime_matrix_cache    = {}
        mod._regime_matrix_cache_ts = 0
        mod._cache    = {}
        mod._cache_ts = 0
        matrix, _ = get_conditioned_transition_matrix("risk_on")
        for sector, succs in matrix.items():
            total = sum(succs.values())
            assert abs(total - 1.0) < 0.01, f"Probabilities for {sector} sum to {total}"


# ─────────────────────────────────────────────────────────────────────────────
# compute_ticker_rotation_scores
# ─────────────────────────────────────────────────────────────────────────────

def _patch_rotation_dependencies(monkeypatch,
                                  regime: str = "risk_on",
                                  next_probs: dict | None = None,
                                  ticker_signals: dict | None = None):
    """
    Patch compute_sector_regime, get_rotation_forecast, and compute_ticker_signals
    so compute_ticker_rotation_scores runs without DB or network calls.
    """
    from backend.sector_regime import CYCLICAL, DEFENSIVE

    if next_probs is None:
        next_probs = {}

    likely_next = [{"sector": s, "probability": p} for s, p in next_probs.items()]

    monkeypatch.setattr(
        "backend.sector_regime.compute_sector_regime",
        lambda: {"available": True, "regime": regime, "sectors": {}, "leader_streak": 0},
    )
    monkeypatch.setattr(
        "backend.sector_transitions.get_rotation_forecast",
        lambda: {"available": True, "likely_next": likely_next, "watching": []},
    )
    if ticker_signals is not None:
        monkeypatch.setattr(
            "backend.sector_regime.compute_ticker_signals",
            lambda: ticker_signals,
        )


class TestTickerRotationScores:

    # ── returns empty when data unavailable ───────────────────────────────

    def test_empty_when_regime_unavailable(self, monkeypatch):
        monkeypatch.setattr(
            "backend.sector_regime.compute_sector_regime",
            lambda: {"available": False},
        )
        assert compute_ticker_rotation_scores() == {}

    def test_empty_when_no_ticker_signals(self, monkeypatch):
        _patch_rotation_dependencies(monkeypatch, ticker_signals={})
        assert compute_ticker_rotation_scores() == {}

    # ── score range ───────────────────────────────────────────────────────

    def test_scores_in_0_1_range(self, monkeypatch):
        cyc_sector = next(iter(CYCLICAL))
        signals = {
            "AAPL": {"sector": cyc_sector, "velocity_5d": 0.05},
            "XOM":  {"sector": cyc_sector, "velocity_5d": -0.20},
        }
        _patch_rotation_dependencies(monkeypatch, regime="risk_on",
                                     next_probs={cyc_sector: 0.40},
                                     ticker_signals=signals)
        scores = compute_ticker_rotation_scores()
        for ticker, score in scores.items():
            assert 0.0 <= score <= 1.0, f"{ticker} score {score} out of range"

    def test_scores_rounded_to_4dp(self, monkeypatch):
        cyc_sector = next(iter(CYCLICAL))
        signals = {"AAPL": {"sector": cyc_sector, "velocity_5d": 0.03}}
        _patch_rotation_dependencies(monkeypatch, regime="risk_on",
                                     next_probs={cyc_sector: 0.35},
                                     ticker_signals=signals)
        scores = compute_ticker_rotation_scores()
        for score in scores.values():
            assert score == round(score, 4)

    # ── regime_alignment drives sector differentiation ────────────────────

    def test_higher_score_when_sector_is_cyclical_in_risk_on(self, monkeypatch):
        cyc_sector = next(iter(CYCLICAL))
        signals = {
            "CYC":  {"sector": cyc_sector, "velocity_5d": 0.0},
            "UNK":  {"sector": "UnknownSector", "velocity_5d": 0.0},
        }
        _patch_rotation_dependencies(monkeypatch, regime="risk_on",
                                     next_probs={}, ticker_signals=signals)
        scores = compute_ticker_rotation_scores()
        assert scores["CYC"] > scores["UNK"]

    def test_neutral_regime_same_score_across_sectors(self, monkeypatch):
        cyc_sector = next(iter(CYCLICAL))
        signals = {"AAPL": {"sector": cyc_sector, "velocity_5d": 0.0}}
        _patch_rotation_dependencies(monkeypatch, regime="neutral",
                                     next_probs={},  # irrelevant — not used in scoring
                                     ticker_signals=signals)
        scores = compute_ticker_rotation_scores()
        # velocity_score = 0.5, regime_alignment = 0.5 (neutral)
        expected = round(0.65 * 0.5 + 0.35 * 0.5, 4)
        assert scores["AAPL"] == pytest.approx(expected, abs=1e-4)

    # ── velocity_score component ──────────────────────────────────────────

    def test_velocity_score_is_05_when_flat(self, monkeypatch):
        cyc_sector = next(iter(CYCLICAL))
        signals = {"AAPL": {"sector": cyc_sector, "velocity_5d": 0.0}}
        _patch_rotation_dependencies(monkeypatch, regime="neutral",
                                     next_probs={}, ticker_signals=signals)
        scores = compute_ticker_rotation_scores()
        # velocity_score = (0.0 + 0.10) / 0.20 = 0.5, regime_alignment = 0.5 (neutral)
        # score = 0.65 * 0.5 + 0.35 * 0.5 = 0.50
        expected = round(0.65 * 0.5 + 0.35 * 0.5, 4)
        assert scores["AAPL"] == pytest.approx(expected, abs=1e-4)

    def test_velocity_score_clamps_to_1_for_large_positive(self, monkeypatch):
        cyc_sector = next(iter(CYCLICAL))
        signals = {"AAPL": {"sector": cyc_sector, "velocity_5d": 0.50}}  # >> norm_center
        _patch_rotation_dependencies(monkeypatch, regime="neutral",
                                     next_probs={}, ticker_signals=signals)
        scores = compute_ticker_rotation_scores()
        # velocity_score clamped to 1.0, regime_alignment = 0.5 (neutral)
        expected = round(0.65 * 1.0 + 0.35 * 0.5, 4)
        assert scores["AAPL"] == pytest.approx(expected, abs=1e-4)

    def test_velocity_score_clamps_to_0_for_large_negative(self, monkeypatch):
        cyc_sector = next(iter(CYCLICAL))
        signals = {"AAPL": {"sector": cyc_sector, "velocity_5d": -0.50}}  # << -norm_center
        _patch_rotation_dependencies(monkeypatch, regime="neutral",
                                     next_probs={}, ticker_signals=signals)
        scores = compute_ticker_rotation_scores()
        # velocity_score clamped to 0.0, regime_alignment = 0.5 (neutral)
        expected = round(0.65 * 0.0 + 0.35 * 0.5, 4)
        assert scores["AAPL"] == pytest.approx(expected, abs=1e-4)

    # ── regime_alignment component ────────────────────────────────────────

    def test_cyclical_scores_higher_in_risk_on(self, monkeypatch):
        cyc_sector = next(iter(CYCLICAL))
        def_sector = next(iter(DEFENSIVE))
        signals = {
            "CYC": {"sector": cyc_sector, "velocity_5d": 0.0},
            "DEF": {"sector": def_sector, "velocity_5d": 0.0},
        }
        _patch_rotation_dependencies(monkeypatch, regime="risk_on",
                                     next_probs={}, ticker_signals=signals)
        scores = compute_ticker_rotation_scores()
        assert scores["CYC"] > scores["DEF"]

    def test_defensive_scores_higher_in_risk_off(self, monkeypatch):
        cyc_sector = next(iter(CYCLICAL))
        def_sector = next(iter(DEFENSIVE))
        signals = {
            "CYC": {"sector": cyc_sector, "velocity_5d": 0.0},
            "DEF": {"sector": def_sector, "velocity_5d": 0.0},
        }
        _patch_rotation_dependencies(monkeypatch, regime="risk_off",
                                     next_probs={}, ticker_signals=signals)
        scores = compute_ticker_rotation_scores()
        assert scores["DEF"] > scores["CYC"]

    def test_regime_alignment_05_in_neutral(self, monkeypatch):
        cyc_sector = next(iter(CYCLICAL))
        def_sector = next(iter(DEFENSIVE))
        signals = {
            "CYC": {"sector": cyc_sector, "velocity_5d": 0.0},
            "DEF": {"sector": def_sector, "velocity_5d": 0.0},
        }
        _patch_rotation_dependencies(monkeypatch, regime="neutral",
                                     next_probs={}, ticker_signals=signals)
        scores = compute_ticker_rotation_scores()
        # Both should be equal — same velocity, same 0.5 alignment
        assert scores["CYC"] == pytest.approx(scores["DEF"], abs=1e-4)
