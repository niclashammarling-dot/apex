"""
CHECK 77 sub-check (3) — live state must agree with the latest history row.

Positive control reproduces what run_eod_regime(overwrite=True) did before
apex dc33663: sector_posteriors and the result cache take a re-run's values,
history keeps the original (INSERT OR IGNORE).
"""
import json
import sqlite3

import pytest

from audit import _audit_core as core
import audit.checks_data as cd
from audit.checks_data import CHECK77_LIVE_STATE


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(cd, "REPO", tmp_path)
    db = tmp_path / "data" / "apex.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE sector_posterior_history (date TEXT, sector TEXT, posterior REAL, written_at TEXT, PRIMARY KEY (date, sector))")
    conn.execute("CREATE TABLE sector_posteriors (sector TEXT PRIMARY KEY, posterior REAL, updated_at TEXT)")
    for d, w in [("2026-09-23", "2026-09-24T12:30:00+00:00"), ("2026-09-24", "2026-09-25T12:30:00+00:00")]:
        conn.executemany("INSERT INTO sector_posterior_history VALUES (?, ?, ?, ?)",
                         [(d, "Technology", 0.86, w), (d, "Energy", 0.40, w)])
    conn.executemany("INSERT INTO sector_posteriors VALUES (?, ?, ?)",
                     [("Technology", 0.86, "x"), ("Energy", 0.40, "x")])
    conn.commit()
    conn.close()
    (tmp_path / "data" / "regime_result_cache.json").write_text(json.dumps({"date": "2026-09-24"}))
    yield tmp_path, db
    core.findings.clear()


def _live_state_findings():
    core.findings.clear()
    cd.check77()
    return [(sev, f) for _, _, sev, _, f in core.findings if f.startswith(CHECK77_LIVE_STATE)]


def test_agreement_is_quiet(repo):
    assert _live_state_findings() == []


def test_old_overwrite_shape_goes_red(repo):
    tmp, db = repo
    conn = sqlite3.connect(db)
    # what the removed overwrite=True wrote: upsert live, history INSERT OR IGNORE'd
    conn.execute("UPDATE sector_posteriors SET posterior = 0.93 WHERE sector = 'Technology'")
    conn.execute("INSERT OR IGNORE INTO sector_posterior_history VALUES ('2026-09-24', 'Technology', 0.93, 'y')")
    conn.commit()
    conn.close()
    out = _live_state_findings()
    assert [s for s, _ in out] == ["CRITICAL"]
    assert "Technology: live 0.9300 vs history 0.8600" in out[0][1]


def test_cache_dated_off_history_goes_red(repo):
    tmp, _ = repo
    (tmp / "data" / "regime_result_cache.json").write_text(json.dumps({"date": "2026-09-25"}))
    out = _live_state_findings()
    assert [s for s, _ in out] == ["CRITICAL"]
    assert "result cache dated 2026-09-25" in out[0][1]


def test_history_sector_without_live_row_goes_red(repo):
    _, db = repo
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM sector_posteriors WHERE sector = 'Energy'")
    conn.commit()
    conn.close()
    out = _live_state_findings()
    assert "Energy: history 0.4000, no live row" in out[0][1]
