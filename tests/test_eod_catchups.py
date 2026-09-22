"""Same-evening catch-ups for collect_pcr (16:30 ET) and publish_audit_state (16:33 ET)."""
import json
import sqlite3
from datetime import datetime, time

import backend.scheduler as sch
from backend.scheduler import NY


def _at(monkeypatch, d: str, hhmm: str):
    """Freeze scheduler.datetime.now(NY) at d hhmm ET and make d a session day."""
    fixed = datetime.combine(datetime.fromisoformat(d).date(), time.fromisoformat(hhmm), tzinfo=NY)

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz is None else fixed.astimezone(tz)
    monkeypatch.setattr(sch, "datetime", _DT)
    monkeypatch.setattr(sch, "_nyse_sessions_for_date", lambda s: 1)


def test_pcr_catchup_runs_same_evening_when_no_rows(monkeypatch):
    import backend.db as db
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute("DELETE FROM lock4_pcr_history"); conn.commit(); conn.close()
    _at(monkeypatch, "2026-09-21", "17:05")
    calls = []
    monkeypatch.setattr(sch, "collect_pcr_snapshot", lambda today=None: calls.append(today))
    sch._check_missed_pcr_collect()
    assert [str(c) for c in calls] == ["2026-09-21"]


def test_pcr_catchup_silent_before_slot_and_when_rows_exist(monkeypatch):
    import backend.db as db
    calls = []
    monkeypatch.setattr(sch, "collect_pcr_snapshot", lambda today=None: calls.append(today))
    _at(monkeypatch, "2026-09-21", "16:20")
    sch._check_missed_pcr_collect()
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute("INSERT INTO lock4_pcr_history (ticker, date, pcr) VALUES ('X', '2026-09-21', 0.5)")
    conn.commit(); conn.close()
    _at(monkeypatch, "2026-09-21", "17:05")
    sch._check_missed_pcr_collect()
    assert calls == []


def test_audit_catchup_keyed_on_generated_at_date(monkeypatch, tmp_path):
    state = tmp_path / "audit" / "state" / "latest.json"
    state.parent.mkdir(parents=True)
    calls = []
    monkeypatch.setattr(sch, "publish_audit_state", lambda: calls.append(1))
    monkeypatch.setattr(sch, "Path", lambda *_a, **_k: _FakePath(tmp_path / "backend" / "scheduler.py"))
    _at(monkeypatch, "2026-09-21", "17:05")
    state.write_text(json.dumps({"generated_at": "2026-09-18T20:34:58+00:00"}))
    sch._check_missed_audit_publish()
    assert calls == [1]
    state.write_text(json.dumps({"generated_at": "2026-09-21T20:40:00+00:00"}))
    sch._check_missed_audit_publish()
    assert calls == [1]
    _at(monkeypatch, "2026-09-21", "16:10")
    state.write_text(json.dumps({"generated_at": "2026-09-18T20:34:58+00:00"}))
    sch._check_missed_audit_publish()
    assert calls == [1]


class _FakePath:
    """Minimal stand-in so Path(__file__).resolve().parent.parent lands in tmp_path."""
    def __init__(self, p): self._p = p
    def resolve(self): return self
    @property
    def parent(self): return _FakePath(self._p.parent)
    def __truediv__(self, o): return self._p / o
