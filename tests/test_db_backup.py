"""Finding 4 (2026-10-31): the startup database backup and CHECK 95, which reads its age and integrity."""
import json
from datetime import datetime, timedelta, timezone

from backend import db, db_backup

NOW = datetime(2026, 11, 2, 13, 20, tzinfo=timezone.utc)


def _fresh_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(db_backup, "backup_dir", lambda: tmp_path / "auto")
    return tmp_path / "auto"


def test_one_backup_per_day_intact_and_pruned(tmp_path, monkeypatch):
    d = _fresh_dir(tmp_path, monkeypatch)
    db.init_db()
    e = db_backup.backup_once(NOW)
    assert e["integrity"] == "ok" and (d / e["file"]).stat().st_size == e["bytes"] > 0
    assert db_backup.backup_once(NOW + timedelta(hours=3)) is None          # once per day
    monkeypatch.setattr(db_backup, "BACKUP_KEEP", 2)
    for k in (1, 2, 3):
        db_backup.backup_once(NOW + timedelta(days=k))
    assert sorted(p.name for p in d.glob("apex_auto_*.db")) == ["apex_auto_2026-11-04.db", "apex_auto_2026-11-05.db"]
    assert len(json.loads((d / "MANIFEST.json").read_text())) == 4
    assert not list(d.glob("*.partial"))


def test_the_default_dir_is_beside_the_test_db():
    assert db_backup.backup_dir().parent.parent == db.DB_PATH.parent


def _check95(tmp_path, monkeypatch, entries, files=()):
    from audit import _audit_core as core
    from audit import checks_data as cd
    repo = tmp_path / "repo"
    (repo / "data/backups/auto").mkdir(parents=True)
    (repo / "data/apex.db").write_text("")
    for f in files:
        (repo / "data/backups/auto" / f).write_text("x")
    if entries is not None:
        (repo / "data/backups/auto/MANIFEST.json").write_text(json.dumps(entries))
    monkeypatch.setattr(cd, "REPO", repo)
    monkeypatch.setattr(core, "REPO", repo)
    monkeypatch.setattr(core, "findings", [])
    monkeypatch.setattr(cd, "flag", core.flag)
    cd.check95(NOW)
    return [(f[2], f[4]) for f in core.findings if f[0] == 95]


def _entry(days_old, integrity="ok"):
    return {"file": "apex_auto_x.db", "written_at": (NOW - timedelta(days=days_old)).isoformat(),
            "bytes": 110_000_000, "integrity": integrity}


def test_a_recent_intact_backup_is_info_only(tmp_path, monkeypatch):
    out = _check95(tmp_path, monkeypatch, [_entry(1)], files=["apex_auto_x.db"])
    assert [s for s, _ in out] == ["INFO"]


def test_an_old_backup_warns(tmp_path, monkeypatch):
    out = _check95(tmp_path, monkeypatch, [_entry(5)], files=["apex_auto_x.db"])
    assert any(s == "WARNING" and "5 days old" in m for s, m in out)


def test_missing_manifest_bad_integrity_and_missing_file_warn(tmp_path, monkeypatch):
    assert any("has ever been written" in m for _, m in _check95(tmp_path / "a", monkeypatch, None))
    out = _check95(tmp_path / "b", monkeypatch, [_entry(0, "row 3 missing from index")])
    msgs = [m for s, m in out if s == "WARNING"]
    assert any("integrity" in m for m in msgs) and any("not on disk" in m for m in msgs)
