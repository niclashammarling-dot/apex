"""/api/ops/window audit cell: a session whose audit isn't due yet reads None, not False."""
from datetime import datetime

from backend.routers.signals_router import _audit_state
from backend.scheduler import NY


def _at(d, hh, mm):
    return datetime.fromisoformat(f"{d}T{hh:02d}:{mm:02d}").replace(tzinfo=NY)


def test_today_before_publish_is_not_due():
    assert _audit_state("2026-09-30", "2026-09-29", _at("2026-09-30", 10, 0)) is None


def test_today_after_publish_window_without_state_is_false():
    assert _audit_state("2026-09-30", "2026-09-29", _at("2026-09-30", 16, 45)) is False


def test_published_session_is_true():
    assert _audit_state("2026-09-29", "2026-09-29", _at("2026-09-30", 10, 0)) is True


def test_sessions_older_than_the_kept_state_are_unknown():
    assert _audit_state("2026-09-28", "2026-09-29", _at("2026-09-30", 10, 0)) is None


def test_no_state_at_all_and_past_due_is_false():
    assert _audit_state("2026-09-29", None, _at("2026-09-30", 10, 0)) is False
