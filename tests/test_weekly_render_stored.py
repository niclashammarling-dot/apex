"""
Weekly report re-render from a stored snapshot (2026-10-31): the reader of db.get_weekly_snapshot. The stored
commentary is used verbatim with no second model call; a week without a snapshot raises.
"""
from unittest.mock import patch

import pytest

from backend import weekly_report as wr
from backend.db import init_db, insert_weekly_snapshot

init_db()

SNAP = {"week": "2031-W10", "since": "2031-03-03T00:00:00+00:00", "until": "2031-03-10T00:00:00+00:00",
        "code": {"commit": "abc", "dirty": False, "dirty_files": 0},
        "optimizer": {"state": "absent", "detail": "no file", "result": None},
        "sweep": {"state": "absent", "detail": "no file", "top": None},
        "thresholds": {"flat": 0.6, "sectors": []}, "recal_changes": {},
        "commentary": "STORED COMMENTARY: Lock 1 Eligibility filtered most candidates.",
        "commentary_min_n": 10, "reference_weeks": 8}


def test_a_stored_week_renders_its_commentary_without_a_model_call():
    insert_weekly_snapshot("2031-W10", SNAP)
    with patch.object(wr, "_gpt4o_commentary", side_effect=AssertionError("model called")):
        plain = wr.render_stored("2031-W10")
    assert "STORED COMMENTARY: Lock 1 Eligibility filtered most candidates." in plain
    assert "Week ending" in plain.splitlines()[0]


def test_a_week_without_a_snapshot_raises():
    with pytest.raises(LookupError):
        wr.render_stored("2031-W11")
