"""
db.persist_session_posteriors — live state and history written in one
transaction (2026-09-26). Before, the upsert committed first and a failed
history insert left live state a session ahead of history, silently.
"""
from unittest.mock import patch

import pytest

from backend import db


def _clear():
    conn = db.get_db()
    conn.execute("DELETE FROM sector_posterior_history")
    conn.execute("DELETE FROM sector_posteriors")
    conn.commit()
    conn.close()


def _state():
    conn = db.get_db()
    live = dict(conn.execute("SELECT sector, posterior FROM sector_posteriors").fetchall())
    hist = conn.execute("SELECT date, sector, posterior FROM sector_posterior_history").fetchall()
    conn.close()
    return live, [tuple(r) for r in hist]


def test_writes_both_with_one_timestamp():
    _clear()
    db.persist_session_posteriors("2026-09-24", {"Technology": 0.8, "Energy": 0.4})
    live, hist = _state()
    assert live == {"Technology": 0.8, "Energy": 0.4}
    assert sorted(hist) == [("2026-09-24", "Energy", 0.4), ("2026-09-24", "Technology", 0.8)]
    conn = db.get_db()
    stamps = {r[0] for r in conn.execute("SELECT updated_at FROM sector_posteriors")} | \
             {r[0] for r in conn.execute("SELECT written_at FROM sector_posterior_history")}
    conn.close()
    assert len(stamps) == 1


def test_existing_history_row_rolls_back_live_state_too():
    """The positive for the old shape: upsert then INSERT OR IGNORE moved live
    state while history kept the old row."""
    _clear()
    db.persist_session_posteriors("2026-09-24", {"Technology": 0.8})
    with pytest.raises(Exception):
        db.persist_session_posteriors("2026-09-24", {"Technology": 0.9})
    live, hist = _state()
    assert live == {"Technology": 0.8}
    assert hist == [("2026-09-24", "Technology", 0.8)]


def test_rb_failure_restores_memory_and_reraises():
    from backend.regime.regime_bayes import RegimeBayes
    rb = RegimeBayes.__new__(RegimeBayes)
    rb._posteriors = {"Technology": 0.99}          # today's value, already in memory
    with patch("backend.db.persist_session_posteriors", side_effect=RuntimeError("disk")):
        with pytest.raises(RuntimeError):
            rb._save_session_posteriors("2026-09-24", {"Technology": 0.99},
                                        restore={"Technology": 0.6})
    assert rb._posteriors == {"Technology": 0.6}   # back on the previous session
