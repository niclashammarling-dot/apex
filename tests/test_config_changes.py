"""
One config_changes row per runtime config write that changes a key (vault scope
raw/notes/2026-11/2026-11-06-apex-scopes-gate-decision-inputs-per-row-and-config-change-log.md, Job 2; built
2026-11-07), and the bundle's config diff names the commit or change row behind a diff (item 6: the 11-06 bundle
flagged the decided lock_leading_min_pass retirement, 819d590, as undecided; Niclas: "så att nästa pensionering inte
flaggas igen"). Config files are pointed at tmp paths: conftest redirects the DB, not the config files.
"""
import json
import subprocess

import pytest

import backend.db as db


@pytest.fixture
def book(tmp_path, monkeypatch):
    import backend.live_config as lc
    path = tmp_path / "live_config.json"
    monkeypatch.setattr(lc, "_CONFIG_PATH", path)
    conn = db.get_db()
    try:
        conn.execute("DELETE FROM config_changes")
        conn.commit()
    finally:
        conn.close()
    return lc, path


def _rows():
    return db.get_config_changes(limit=500)


def test_one_changed_key_gives_one_row_with_old_and_new(book):
    lc, path = book
    lc.ensure_config_exists()
    n0 = len(_rows())
    assert n0 > 0 and {r["source"] for r in _rows()} == {"ensure_default"}
    cur = lc.get_live_config()["l3_score_threshold_min"]
    lc.set_live_config({"l3_score_threshold_min": cur + 0.01})
    new = _rows()[: len(_rows()) - n0]
    assert len(new) == 1 and new[0]["key"] == "l3_score_threshold_min" and new[0]["source"] == "settings"
    assert json.loads(new[0]["old"]) == cur and json.loads(new[0]["new"]) == pytest.approx(cur + 0.01)


def test_a_write_with_the_same_values_records_nothing(book):
    lc, _ = book
    lc.ensure_config_exists()
    n0 = len(_rows())
    lc.set_live_config(dict(lc.get_live_config()))
    assert len(_rows()) == n0


def test_a_corrupt_file_records_a_corrupt_reset(book, monkeypatch):
    lc, path = book
    path.write_text("{ not json")
    monkeypatch.setattr("backend.alerts.alert_config_corrupted", lambda *a, **k: None)
    lc.get_live_config()
    rows = _rows()
    assert rows and {r["source"] for r in rows} == {"corrupt_reset"} and all(r["old"] is None for r in rows)


def test_promote_records_its_source_and_demo_records_too(book, tmp_path, monkeypatch):
    lc, _ = book
    lc.ensure_config_exists()
    cur = lc.get_live_config()["l3_score_threshold_min"]
    lc.set_live_config({"l3_score_threshold_min": cur + 0.02}, source="promote")
    assert _rows()[0]["source"] == "promote"
    import backend.demo_config as dc
    monkeypatch.setattr(dc, "_CONFIG_PATH", tmp_path / "demo_config.json")
    dc.ensure_config_exists()
    d = dc.get_demo_config()["l3_score_threshold_min"]
    dc.set_demo_config({"l3_score_threshold_min": d + 0.01})
    top = _rows()[0]
    assert (top["file"], top["key"], top["source"]) == ("demo_config.json", "l3_score_threshold_min", "settings")


def test_the_endpoint_returns_the_latest_rows(book):
    lc, _ = book
    lc.ensure_config_exists()
    from backend.routers.signals_router import get_config_change_log
    out = get_config_change_log(limit=3)
    assert len(out["rows"]) == 3 and out["as_of"]


SINCE = "2026-11-01T00:00:00+00:00"     # the previous bundle; the key was added before it


def _repo_with_removal(tmp_path, commit: bool):
    import os
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)

    def run(*a, date=None):
        env = {**os.environ, **({"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date} if date else {})}
        subprocess.run(a, cwd=repo, check=True, capture_output=True, env=env)
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    (repo / "data/live_config.json").write_text('{"zz_retired_key": 2, "kept": 1}')
    run("git", "add", "-A")
    run("git", "commit", "-q", "-m", "before", date="2026-10-01T12:00:00+00:00")
    if commit:
        (repo / "data/live_config.json").write_text('{"kept": 1}')
        run("git", "commit", "-q", "-am", "Retire zz_retired_key: no reader", date="2026-11-05T12:00:00+00:00")
    return repo


@pytest.mark.parametrize("commit", [True, False], ids=["with commit", "without"])
def test_the_bundle_names_the_commit_behind_a_diff_or_warns(tmp_path, monkeypatch, book, commit):
    from audit import bundle
    monkeypatch.setattr(bundle, "REPO", _repo_with_removal(tmp_path, commit))
    items = bundle.config_items({"live_config": {"zz_retired_key": 2, "kept": 1}}, {"live_config": {"kept": 1}},
                                since=SINCE)
    [it] = items
    if commit:
        assert it["severity"] == "INFO" and "Retire zz_retired_key" in it["text"]
    else:
        assert it["severity"] == "WARNING" and "no commit or change row" in it["text"]


def test_the_bundle_names_a_runtime_write_behind_a_diff(tmp_path, monkeypatch, book):
    from audit import bundle
    monkeypatch.setattr(bundle, "REPO", _repo_with_removal(tmp_path, commit=False))
    db.record_config_changes("live_config.json", "settings", {"zz_tuned": 0.6}, {"zz_tuned": 0.65})
    [it] = bundle.config_items({"live_config": {"zz_tuned": 0.6}}, {"live_config": {"zz_tuned": 0.65}},
                               since=SINCE)
    assert it["severity"] == "INFO" and "settings write" in it["text"]


def test_a_branch_commit_merged_after_the_previous_bundle_is_found_by_commit_range(tmp_path, monkeypatch, book):
    # 819d590 was dated 10-28 on its branch and merged 10-31: a date window misses it, the commit range does not.
    from audit import bundle
    repo = _repo_with_removal(tmp_path, commit=True)              # the removal is dated 11-05
    first = subprocess.run(["git", "rev-list", "--max-parents=0", "HEAD"], cwd=repo, capture_output=True,
                           text=True).stdout.strip()
    monkeypatch.setattr(bundle, "REPO", repo)
    [it] = bundle.config_items({"live_config": {"zz_retired_key": 2, "kept": 1}}, {"live_config": {"kept": 1}},
                               since="2026-12-01T00:00:00+00:00", since_commit=first)   # the date alone would miss it
    assert it["severity"] == "INFO" and "Retire zz_retired_key" in it["text"]


def test_promote_copies_demo_thresholds_into_live_and_records_promote(book, tmp_path, monkeypatch):
    # The Promote endpoint (edited 2026-11-07 to pass source="promote"; the function ratchet's change gate asked for
    # this test). Account-size keys stay out (_PROMOTE_EXCLUDE); every promoted key that changed gets a promote row.
    import backend.demo_config as dc
    from backend.routers import live_router
    lc, _ = book
    monkeypatch.setattr(dc, "_CONFIG_PATH", tmp_path / "demo_config.json")
    monkeypatch.setattr(live_router, "LIVE_ENABLED", True)
    lc.ensure_config_exists()
    dc.ensure_config_exists()
    live_before = lc.get_live_config()
    demo_thr = dc.get_demo_config()["l3_score_threshold_min"]
    dc.set_demo_config({"l3_score_threshold_min": demo_thr + 0.03})
    out = live_router.promote_demo_to_live()
    assert out["promoted"] and out["config"]["l3_score_threshold_min"] == pytest.approx(demo_thr + 0.03)
    assert out["config"]["starting_balance"] == live_before["starting_balance"]          # excluded from Promote
    promoted = [r for r in _rows() if r["source"] == "promote"]
    assert any(r["key"] == "l3_score_threshold_min" for r in promoted)
    assert all(r["key"] != "starting_balance" for r in promoted)


def test_promote_is_refused_when_live_is_disabled(monkeypatch):
    from fastapi import HTTPException

    from backend.routers import live_router
    monkeypatch.setattr(live_router, "LIVE_ENABLED", False)
    with pytest.raises(HTTPException):
        live_router.promote_demo_to_live()
