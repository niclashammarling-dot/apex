"""CHECK 67/68/69 (and the shared _dilution_monitor): a floor breach is attributed to the added
tickers only when the composite without them clears the floor (2026-10-21). On 10-20 all three
fired CRITICAL with "Action: remove …" while the original tickers alone sat below the same floor
(Industrials: added 0.307, original 0.302), and the market-wide suppression had lapsed because its
60-day peak window had rolled past the decline."""
import sqlite3
from datetime import datetime, timedelta, timezone

from audit import _audit_core as core
from audit import checks_sector as cs


def _db(tmp_path, avg, scores):
    (tmp_path / "data").mkdir()
    con = sqlite3.connect(tmp_path / "data" / "apex.db")
    con.execute("CREATE TABLE sector_snapshots (sector TEXT, avg_score REAL, timestamp TEXT)")
    con.execute("CREATE TABLE signals (ticker TEXT, sector TEXT, signal_score REAL, timestamp TEXT)")
    now = datetime.now(timezone.utc)
    con.execute("INSERT INTO sector_snapshots VALUES ('Industrials', ?, ?)", (avg, now.isoformat()))
    con.executemany("INSERT INTO signals VALUES (?, 'Industrials', ?, ?)",
                    [(t, s, (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")) for t, s in scores.items()])
    con.commit()
    con.close()


def _run(monkeypatch, tmp_path):
    monkeypatch.setattr(cs, "REPO", tmp_path)
    monkeypatch.setattr(core, "findings", [])
    monkeypatch.setattr(cs, "flag", core.flag)
    monkeypatch.setattr(cs, "_decline_cache", {"v": (False, "test: no market-wide decline")})
    cs.check67()
    return [(f[2], f[4]) for f in core.findings if f[0] == 67]


ORIG = {"CAT": 0.30, "DE": 0.31, "GE": 0.30, "HON": 0.30, "UNP": 0.30}


def test_level_breach_is_info_not_a_removal(monkeypatch, tmp_path):
    _db(tmp_path, 0.304, {**ORIG, "ETN": 0.31, "PWR": 0.30, "TT": 0.31})
    out = _run(monkeypatch, tmp_path)
    assert [s for s, _ in out] == ["INFO"] and "removing them would not clear it" in out[0][1]


def test_breach_caused_by_the_additions_stays_critical(monkeypatch, tmp_path):
    # Originals at 0.60, additions at 0.10: without them the composite clears 0.48.
    _db(tmp_path, 0.41, {t: 0.60 for t in ORIG} | {"ETN": 0.10, "PWR": 0.10, "TT": 0.10})
    out = _run(monkeypatch, tmp_path)
    assert [s for s, _ in out] == ["CRITICAL"] and "Without ETN/PWR/TT: 0.5975" in out[0][1]


def test_no_recent_signals_keeps_the_severity_and_says_so(monkeypatch, tmp_path):
    _db(tmp_path, 0.30, {})
    out = _run(monkeypatch, tmp_path)
    assert [s for s, _ in out] == ["CRITICAL"] and "not computable" in out[0][1]
