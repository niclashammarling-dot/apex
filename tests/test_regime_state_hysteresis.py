"""
market_regime_state() bull-boundary hysteresis (built 2026-09-17).

Enter bull at ≥ 0.75; stay bull down to _REGIME_BULL_EXIT (0.70) when the previous
bucket was bull; bear has no band. The previous bucket rides in the result cache
(regime_state / regime_prev_state) and load_regime_state() reads it back; a cache
written before the field existed falls back to the stateless read.
"""
import json

import backend.regime.regime_bayes as rb
from backend.regime.regime_bayes import RegimeResult, SectorEntry, market_regime_state


def _entry(p: float, i: int = 0) -> SectorEntry:
    return SectorEntry(sector=f"S{i}", rank=i + 1, aggregate_score=0.5,
                       posterior=p, adjusted_score=0.5, allocation=0.0)


def _result(*posteriors: float, date: str = "2026-09-17") -> RegimeResult:
    return RegimeResult(date=date, leaderboard=[_entry(p, i) for i, p in enumerate(posteriors)],
                        allocation={}, leader="S0", qualifiers=[])


class TestBucket:
    def test_stateless_unchanged_without_prev(self):
        assert market_regime_state(_result(0.80, 0.76, 0.75)) == "bull"
        assert market_regime_state(_result(0.74, 0.72, 0.70)) == "neutral"
        assert market_regime_state(_result(0.59, 0.55, 0.50)) == "bear"

    def test_bull_holds_inside_exit_band(self):
        inside = _result(0.72, 0.71, 0.70)   # mean 0.71 — stateless says neutral
        assert market_regime_state(inside) == "neutral"
        assert market_regime_state(inside, "bull") == "bull"
        assert market_regime_state(inside, "neutral") == "neutral"

    def test_bull_releases_below_exit(self):
        assert market_regime_state(_result(0.70, 0.69, 0.69), "bull") == "neutral"

    def test_exit_boundary_inclusive(self):
        # (0.70,)*3 sums to 0.6999…98 in float — pick a triple whose mean is 0.70 from above
        assert market_regime_state(_result(0.75, 0.70, 0.65), "bull") == "bull"
        assert market_regime_state(_result(0.70, 0.70, 0.699), "bull") == "neutral"

    def test_bear_has_no_band(self):
        assert market_regime_state(_result(0.59, 0.59, 0.59), "bull") == "bear"

    def test_entry_still_needs_threshold(self):
        assert market_regime_state(_result(0.74, 0.74, 0.74), "neutral") == "neutral"

    def test_short_leaderboard_neutral(self):
        assert market_regime_state(_result(0.9, 0.9), "bull") == "neutral"


class TestPersistence:
    def _write_cache(self, extra: dict) -> None:
        payload = {
            "date": "2026-09-16", "leader": "S0", "qualifiers": [], "allocation": {},
            "leaderboard": [{"sector": f"S{i}", "rank": i + 1, "aggregate_score": 0.5,
                             "posterior": 0.71, "adjusted_score": 0.5, "allocation": 0.0}
                            for i in range(3)],
        }
        payload.update(extra)
        rb.RESULT_CACHE_PATH.write_text(json.dumps(payload))

    def test_load_reads_persisted_bucket(self):
        self._write_cache({"regime_state": "bull", "regime_prev_state": "bull"})
        assert rb.load_regime_state() == "bull"      # mean 0.71 would be neutral stateless

    def test_legacy_cache_falls_back_to_stateless(self):
        self._write_cache({})
        assert rb.load_regime_state() == "neutral"

    def test_save_and_reload_roundtrip(self):
        eng = rb.RegimeBayes.__new__(rb.RegimeBayes)
        res = _result(0.72, 0.71, 0.70)
        res.regime_state, res.regime_prev_state = "bull", "bull"
        eng._save_result(res)
        back = eng._load_result()
        assert (back.regime_state, back.regime_prev_state) == ("bull", "bull")
        assert rb.load_regime_state() == "bull"
