"""
CHECK 74 reviewed-file exceptions (2026-10-10): a review covers the exact text it
read. Positive control: the three real files reviewed that day are flagged when the
exception list is empty, so the detector still fires on this tree.
"""
import pytest

FILES = ("tests/test_live_exit_lock.py", "tests/test_live_reconciliation.py",
         "tests/test_time_stop_fresh_reread.py")


@pytest.fixture
def run74(monkeypatch):
    import audit.checks_code as cc
    real = dict(cc._C74_REVIEWED)
    found = []
    monkeypatch.setattr(cc, "flag", lambda num, name, sev, where, msg: found.append((where, msg)))

    def run(reviewed):
        monkeypatch.setattr(cc, "_C74_REVIEWED", reviewed)
        found.clear()
        cc.check74()
        return list(found)
    return run, real


def test_detector_still_fires_without_the_list(run74):
    run, _ = run74
    flagged = {w for w, _ in run({})}
    assert set(FILES) <= flagged


def test_reviewed_files_are_clean(run74):
    run, real = run74
    assert not [w for w, _ in run(real) if w in FILES]


def test_changed_file_is_flagged_again(run74):
    run, real = run74
    stale = {k: (r, e, "0" * 16) for k, (r, e, _) in real.items()}
    f = run(stale)
    assert {w for w, m in f if "edited since its review" in m} >= set(FILES)


def test_expired_review_is_flagged_again(run74):
    run, real = run74
    expired = {k: (r, "2000-01-01", h) for k, (r, _, h) in real.items()}
    f = run(expired)
    assert {w for w, m in f if "review expired" in m} >= set(FILES)
