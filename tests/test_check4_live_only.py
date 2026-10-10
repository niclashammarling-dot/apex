"""CHECK 4 reads backend/maintenance.py _LIVE_ONLY_KEYS (2026-10-10): one exception list."""
import json

import pytest


@pytest.fixture
def run4(tmp_path, monkeypatch):
    import audit.checks_config as cc
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(cc, "REPO", tmp_path)
    found = []
    monkeypatch.setattr(cc, "flag", lambda num, name, sev, where, msg: found.append(msg))

    def run(demo, live):
        (tmp_path / "data/demo_config.json").write_text(json.dumps(demo))
        (tmp_path / "data/live_config.json").write_text(json.dumps(live))
        found.clear()
        cc.check4()
        return list(found)
    return run


def test_listed_live_only_key_is_clean(run4):
    assert run4({"a": 1}, {"a": 1, "live_account_since": "2026-07-07"}) == []


def test_unlisted_live_only_key_still_warns(run4):
    f = run4({"a": 1}, {"a": 1, "stray_key": 2})
    assert f and "stray_key" in f[0]


def test_demo_only_key_warns(run4):
    f = run4({"a": 1, "b": 2}, {"a": 1})
    assert f and "'b' in demo but missing from live" in f[0]
