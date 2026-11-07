"""
L3 SCORE's fallback is the book's l3_score_threshold_min, as the pre-filter's is (vault scope
raw/notes/2026-11/2026-11-01-apex-l3-threshold-fallback-scope.md; built 2026-11-07).

Until then l3_score._sector_threshold read DEMO_L3_SCORE_THRESHOLD_MIN (0.70) in both books, while the pre-filter
(db.get_l3_score_candidates) used the book's threshold (live 0.65): for a sector with no calibrated row, a live ticker
at 0.67 passed the pre-filter and was stopped at L3 by a value no Settings field governs. It bites only when a sector
is added or a calibration run fails (15 of 15 calibrated on 11-07).
"""
import pytest

from backend.gate import chain
from backend.gate.l3_score import _sector_threshold, evaluate

CALIBRATED = {"Technology": 0.62}


@pytest.mark.parametrize("book_threshold", [0.65, 0.60], ids=["live", "demo"])
def test_an_uncalibrated_sector_uses_the_books_threshold_at_l3(book_threshold):
    assert _sector_threshold("NewSector", CALIBRATED, book_threshold) == book_threshold
    assert evaluate("ZZZ", "NewSector", book_threshold + 0.02, sector_thresholds=CALIBRATED,
                    fallback=book_threshold).passed                     # what the pre-filter let through


def test_a_calibrated_sector_is_unchanged():
    assert _sector_threshold("Technology", CALIBRATED, 0.65) == 0.62


@pytest.mark.parametrize("book_threshold", [0.65, 0.60], ids=["live", "demo"])
def test_the_chain_decides_l3_on_the_books_threshold_for_an_uncalibrated_sector(book_threshold, monkeypatch):
    """Through evaluate_chain: L1 passes (stubbed), L3 runs on the real code, L4 is stubbed to stop the chain, so the
    row's L3 verdict is read. On the parent L3 used 0.70 and stopped a 0.67 (live) / 0.62 (demo) score."""
    from backend.gate.types import LockResult
    monkeypatch.setattr(chain, "l1_eligibility_evaluate",
                        lambda t, s, c: LockResult.pass_(lock_id=1, score=1.0, reason="stub", data={}))
    monkeypatch.setattr(chain, "l4_sentiment_evaluate",
                        lambda t, sentiment_min=None: LockResult.fail(lock_id=4, reason="stub stop", data={}))
    result = chain.evaluate_chain("ZZZ", "NewSector", book_threshold + 0.02, {}, {"l3_score_threshold_min": book_threshold},
                                  sector_thresholds=CALIBRATED)
    assert result.lock_results[3].passed
    assert result.lock_results[3].data["threshold"] == book_threshold


def test_without_a_fallback_the_constant_stays_for_callers_outside_the_chain():
    from backend.gate.l3_score import DEMO_L3_SCORE_THRESHOLD_MIN
    assert _sector_threshold("NewSector", CALIBRATED) == DEMO_L3_SCORE_THRESHOLD_MIN
