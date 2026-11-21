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


def _check95(tmp_path, monkeypatch, entries, files=(), offhost=None):
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
    od = tmp_path / "offhost"
    od.mkdir(parents=True, exist_ok=True)
    if offhost is not None:                               # (days_old, bytes on disk, bytes in meta)
        days, on_disk, in_meta = offhost
        (od / "apex_latest.db").write_bytes(b"x" * on_disk)
        (od / "apex_latest.json").write_text(json.dumps({
            "source": "apex_auto_x.db", "copied_at": (NOW - timedelta(days=days)).isoformat(), "bytes": in_meta}))
    monkeypatch.setattr(cd, "_c95_offhost_dir", lambda: od)
    cd.check95(NOW)
    return [(f[2], f[4]) for f in core.findings if f[0] == 95]


def _entry(days_old, integrity="ok"):
    return {"file": "apex_auto_x.db", "written_at": (NOW - timedelta(days=days_old)).isoformat(),
            "bytes": 110_000_000, "integrity": integrity}


def test_a_recent_intact_backup_is_info_only(tmp_path, monkeypatch):
    out = _check95(tmp_path, monkeypatch, [_entry(1)], files=["apex_auto_x.db"], offhost=(1, 3, 3))
    assert [s for s, _ in out] == ["INFO", "INFO"]
    assert "not prove the upload" in out[1][1]           # ruling condition 2: the limit is stated


def test_an_old_backup_warns(tmp_path, monkeypatch):
    out = _check95(tmp_path, monkeypatch, [_entry(5)], files=["apex_auto_x.db"])
    assert any(s == "WARNING" and "5 days old" in m for s, m in out)


def test_missing_manifest_bad_integrity_and_missing_file_warn(tmp_path, monkeypatch):
    assert any("has ever been written" in m for _, m in _check95(tmp_path / "a", monkeypatch, None))
    out = _check95(tmp_path / "b", monkeypatch, [_entry(0, "row 3 missing from index")])
    msgs = [m for s, m in out if s == "WARNING"]
    assert any("integrity" in m for m in msgs) and any("not on disk" in m for m in msgs)


# --- Off-host copy (R114 a, built 2026-11-21) ---------------------------------------------------------------

def _two_backups(tmp_path, monkeypatch, integrity_last="ok"):
    d = _fresh_dir(tmp_path, monkeypatch)
    db.init_db()
    db_backup.backup_once(NOW)
    db_backup.backup_once(NOW + timedelta(days=1))
    if integrity_last != "ok":
        m = json.loads((d / "MANIFEST.json").read_text())
        m[-1]["integrity"] = integrity_last
        (d / "MANIFEST.json").write_text(json.dumps(m))
    od = tmp_path / "offhost"
    od.mkdir()
    return d, od


def test_offhost_copy_writes_the_newest_backup_with_its_hashes(tmp_path, monkeypatch):
    d, od = _two_backups(tmp_path, monkeypatch)
    meta = db_backup.offhost_copy(NOW, dest_dir=od)
    assert meta["source"] == "apex_auto_2026-11-03.db"
    assert (od / "apex_latest.db").read_bytes() == (d / meta["source"]).read_bytes()
    assert (meta["sha256"], meta["md5"]) == db_backup._hashes(od / "apex_latest.db")
    assert json.loads((od / "apex_latest.json").read_text()) == meta
    assert sorted(p.name for p in od.iterdir()) == ["apex_latest.db", "apex_latest.json"]   # no partial inside
    assert (tmp_path / ".offhost-partial").is_dir()                                        # temp outside, beside
    assert db_backup.offhost_copy(NOW, dest_dir=od) is None                                 # same source: no copy


def test_offhost_copy_never_replaces_a_good_copy_with_a_bad_one(tmp_path, monkeypatch):
    d, od = _two_backups(tmp_path, monkeypatch)
    (od / "apex_latest.db").write_bytes(b"good")
    (od / "apex_latest.json").write_text(json.dumps({"source": "apex_auto_2026-11-02.db"}))
    m = json.loads((d / "MANIFEST.json").read_text())
    m[-1]["integrity"] = "row 3 missing from index"
    (d / "MANIFEST.json").write_text(json.dumps(m))
    assert db_backup.offhost_copy(NOW, dest_dir=od) is None
    assert (od / "apex_latest.db").read_bytes() == b"good"


def test_offhost_copy_skips_a_missing_folder_without_creating_it(tmp_path, monkeypatch):
    _fresh_dir(tmp_path, monkeypatch)
    db.init_db()
    db_backup.backup_once(NOW)
    assert db_backup.offhost_copy(NOW, dest_dir=tmp_path / "absent") is None
    assert not (tmp_path / "absent").exists()


def test_a_test_database_is_never_copied_off_host():
    assert db_backup.offhost_dir() is None              # conftest's DB is not production's
    assert db_backup.offhost_copy(NOW) is None


def test_check95_offhost_old_missing_or_short_copy_warns(tmp_path, monkeypatch):
    fresh = [_entry(1)]
    old = _check95(tmp_path / "a", monkeypatch, fresh, files=["apex_auto_x.db"], offhost=(5, 3, 3))
    assert any(s == "WARNING" and "off-host copy is 5 days old" in m for s, m in old)
    none = _check95(tmp_path / "b", monkeypatch, fresh, files=["apex_auto_x.db"])
    assert any(s == "WARNING" and "no off-host copy" in m for s, m in none)
    short = _check95(tmp_path / "c", monkeypatch, fresh, files=["apex_auto_x.db"], offhost=(0, 2, 3))
    assert any(s == "WARNING" and "not the 3 bytes" in m for s, m in short)
