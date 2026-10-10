"""
The weekend bundle (2026-10-10): the reader for every APEX fault that needs a
decision. Found the same day: WARNING findings and TODO comments had no reader
since the CI audit stopped on 06-27; a 06-25 TODO ("remove Atlas entirely")
surfaced only on 10-10.
"""
import json
import sqlite3
from datetime import datetime, timezone

import pytest

import audit.bundle as bundle


def _tree(tmp_path, files: dict[str, str]):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return tmp_path


class TestTodoMarkers:
    def test_structured_and_unstructured(self, tmp_path, monkeypatch):
        root = _tree(tmp_path, {
            "backend/a.py": "x = 1  # TODO(bundle 2026-10-10): move knobs into Settings — Tweaks panel is dead\n",
            "frontend/src/B.jsx": "{/* TODO: remove Atlas entirely — Terminal is the only design going forward */}\n",
            "audit/c.py": 'PROMPT = "(b) TODO/FIXME/HACK comments; "\n',   # a string, not a marker
        })
        monkeypatch.setattr(bundle, "REPO", root)
        items = {i["where"]: i for i in bundle.todo_items()}
        s = items["backend/a.py:1"]
        assert s["structured"] and s["written"] == "2026-10-10" and "move knobs into Settings" in s["text"]
        u = items["frontend/src/B.jsx:1"]
        assert not u["structured"] and "unstructured" in u["text"] and "remove Atlas" in u["text"]
        assert not any(w.startswith("audit/c.py") for w in items)


class TestBuild:
    @pytest.fixture
    def env(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bundle, "STATE", tmp_path)
        monkeypatch.setattr(bundle, "BUNDLE", tmp_path / "bundle.json")
        self.items = []
        self.snap = {"SECTOR_THRESHOLD_FLOORS": {}}
        monkeypatch.setattr(bundle, "findings_items", lambda: list(self.items))
        monkeypatch.setattr(bundle, "todo_items", lambda: [])
        monkeypatch.setattr(bundle, "parity_items", lambda db=None: [])
        monkeypatch.setattr(bundle, "config_snapshot", lambda: dict(self.snap))
        return tmp_path

    W1 = datetime(2031, 3, 7, 21, 33, tzinfo=timezone.utc)
    W2 = datetime(2031, 3, 14, 20, 33, tzinfo=timezone.utc)

    def test_first_seen_carries_across_weeks(self, env):
        self.items = [bundle._item("finding:39:WARNING:x", "findings", "WARNING", "x", "ADI stale peak")]
        bundle.build(write=True, now=self.W1)
        b2 = bundle.build(write=True, now=self.W2)
        assert b2["items"][0]["first_seen"] == "2031-03-07"

    def test_config_change_is_an_item(self, env):
        bundle.build(write=True, now=self.W1)
        self.snap = {"SECTOR_THRESHOLD_FLOORS": {"ConsumerDisc": 0.75}}
        b = bundle.build(write=True, now=self.W2)
        cfg = [i for i in b["items"] if i["section"] == "config"]
        assert cfg and "ConsumerDisc" in cfg[0]["text"] and "0.75" in cfg[0]["text"]

    def test_active_exception_is_held_and_expired_one_returns(self, env, monkeypatch):
        self.items = [bundle._item("parity:column:t.a", "parity", "WARNING", "t", "a"),
                      bundle._item("parity:column:t.b", "parity", "WARNING", "t", "b")]
        monkeypatch.setattr(bundle, "EXCEPTIONS", {"parity:column:t.a": ("kept: history", "2031-12-31"),
                                                   "parity:column:t.b": ("kept: until", "2031-01-01")})
        b = bundle.build(now=self.W1)
        assert [e["key"] for e in b["excepted"]] == ["parity:column:t.a"]
        assert len(b["items"]) == 1 and "exception expired 2031-01-01" in b["items"][0]["text"]

    def test_read_only_without_write(self, env):
        bundle.build(now=self.W1)
        assert not (env / "bundle.json").exists()


class TestParityControls:
    def test_failed_control_is_an_item_not_an_empty_section(self, tmp_path, monkeypatch):
        root = _tree(tmp_path, {"backend/db.py": "", "backend/routers/r.py": "", "frontend/src/App.jsx": ""})
        dbf = root / "data" / "apex.db"
        dbf.parent.mkdir()
        sqlite3.connect(dbf).execute("CREATE TABLE t (x_col TEXT)").connection.commit()
        monkeypatch.setattr(bundle, "REPO", root)
        keys = {i["key"] for i in bundle.parity_items(dbf)}
        assert {"parity:scan_broken:columns", "parity:scan_broken:routes",
                "parity:scan_broken:components", "parity:scan_broken:functions"} <= keys

    def test_column_read_in_db_select_is_not_flagged(self, tmp_path, monkeypatch):
        root = _tree(tmp_path, {
            "backend/db.py": 'def f(c):\n    return c.execute("SELECT read_col FROM t")\n'
                             'def g(c, v):\n    c.execute("INSERT INTO t (written_col) VALUES (?)", (v,))\n',
        })
        dbf = root / "data" / "apex.db"
        dbf.parent.mkdir()
        sqlite3.connect(dbf).execute("CREATE TABLE t (read_col TEXT, written_col TEXT)").connection.commit()
        monkeypatch.setattr(bundle, "REPO", root)
        keys = {i["key"] for i in bundle.parity_items(dbf)}
        assert "parity:column:t.written_col" in keys and "parity:column:t.read_col" not in keys


class TestBundleDay:
    @pytest.mark.parametrize("ts,expected", [
        ("2026-10-09T20:00", True),    # Friday
        ("2026-10-08T20:00", False),   # Thursday, Friday is a session
        ("2026-04-02T20:00", True),    # Thursday before Good Friday
        ("2026-11-27T20:00", True),    # half-day Friday after Thanksgiving
        ("2026-10-10T15:00", False),   # Saturday
    ])
    def test_last_session_of_the_week(self, ts, expected):
        assert bundle.is_bundle_day(datetime.fromisoformat(ts + "+00:00")) is expected


class TestCheck88:
    @pytest.fixture
    def run88(self, tmp_path, monkeypatch):
        import audit.checks_gate as cg
        (tmp_path / "audit" / "state").mkdir(parents=True)
        monkeypatch.setattr(cg, "REPO", tmp_path)
        monkeypatch.setattr(cg, "_C88_FIRST_DUE", "2000-01-01")
        found = []
        monkeypatch.setattr(cg, "flag", lambda num, name, sev, where, msg: found.append((sev, msg)))

        def run(b=None):
            f = tmp_path / "audit" / "state" / "bundle.json"
            if b is not None:
                f.write_text(json.dumps(b))
            found.clear()
            cg.check88()
            return list(found)
        return run

    def test_missing_bundle_after_due_warns(self, run88):
        f = run88()
        assert f and f[0][0] == "WARNING" and "no bundle file" in f[0][1]

    def test_old_bundle_warns(self, run88):
        f = run88({"generated_at": "2001-01-05T21:33:00+00:00", "items": [], "counts": {}})
        assert any(s == "WARNING" and "is missing" in m for s, m in f)

    def test_fresh_bundle_is_info(self, run88):
        now = datetime.now(timezone.utc).isoformat()
        f = run88({"generated_at": now, "items": [], "counts": {"findings": 3}, "excepted": []})
        assert [s for s, _ in f] == ["INFO"] and "findings 3" in f[0][1]

    def test_broken_scan_warns(self, run88):
        now = datetime.now(timezone.utc).isoformat()
        f = run88({"generated_at": now, "counts": {}, "items": [
            {"key": "parity:scan_broken:routes", "text": "routes scan failed its known-present control"}]})
        assert any(s == "WARNING" and "routes scan" in m for s, m in f)
