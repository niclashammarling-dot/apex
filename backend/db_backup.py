"""
Daily database backup at scheduler startup (Finding 4, 2026-10-07 review; built 2026-10-31).

The host is never up at midnight, so a cron backup would never run; the scheduler-owning process starts every
session day (scripts/market_window.sh, eod_window.sh), and that start takes the backup. Before this, the only
copies were the manual snapshots taken before a repair (data/backups/apex_<date>_pre-*.db).

Writer: backup_once() from main.lifespan, in the scheduler-owner branch only (a dev instance or a test never
writes one). sqlite3's online backup API gives a consistent copy while the WAL is live; the copy is
integrity-checked and recorded in MANIFEST. Reader: CHECK 95 (audit/checks_data.py), which warns when the newest
backup is old, missing, or failed its integrity check.

Off-host copy (R114 a, 2026-11-18; built 2026-11-21): every copy above sits on the same virtual disk as the database.
offhost_copy() puts the newest intact backup into a folder that Google Drive for desktop syncs
(C:\\Users\\Nix\\apex-offhost, under Computers in Drive), as one file overwritten each day, written under a temporary
name outside that folder and moved in, so Drive never uploads a half-written file. Why (Niclas at the ruling): the
account is Alpaca paper since 07-07, so a disk loss costs the evidence a go-live decision rests on (live_trades, the
gate histories, the scores), not money. CHECK 95 reads the folder copy's age; it sees the local folder, not the
cloud, so a file there does not prove the upload.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from backend import db

BACKUP_KEEP = 14            # daily copies kept; ~110 MB each on 2026-10-31
OFFHOST_DIR = Path("/mnt/c/Users/Nix/apex-offhost")   # R114 a; APEX_OFFHOST_DIR overrides
OFFHOST_DB = "apex_latest.db"
OFFHOST_META = "apex_latest.json"


def backup_dir() -> Path:
    """Beside the database, so a test (conftest's DB) never writes into production's backups."""
    return db.DB_PATH.parent / "backups" / "auto"


def backup_once(now: datetime | None = None) -> dict | None:
    """Back up the database once per UTC day; returns the manifest entry written, or None when today's exists."""
    now = now or datetime.now(timezone.utc)
    out_dir = backup_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"apex_auto_{now.date().isoformat()}.db"
    if dest.exists():
        return None
    tmp = dest.with_suffix(".db.partial")
    src = sqlite3.connect(str(db.DB_PATH), timeout=30)
    try:
        dst = sqlite3.connect(str(tmp))
        try:
            src.backup(dst)
            integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            dst.close()
    finally:
        src.close()
    tmp.rename(dest)
    entry = {"file": dest.name, "written_at": now.isoformat(), "bytes": dest.stat().st_size, "integrity": integrity}
    manifest = out_dir / "MANIFEST.json"
    entries = json.loads(manifest.read_text()) if manifest.exists() else []
    entries.append(entry)
    for old in sorted(out_dir.glob("apex_auto_*.db"))[:-BACKUP_KEEP]:
        old.unlink()
    manifest.write_text(json.dumps(entries[-200:], indent=1))
    logger.info(f"DB backup: {dest.name} ({entry['bytes'] / 1e6:.0f} MB, integrity {integrity})")
    return entry


def offhost_dir() -> Path | None:
    """The Drive-synced folder, or None when the database is not production's (a test DB is never copied off-host)."""
    production = Path(__file__).resolve().parents[1] / "data" / "apex.db"
    if Path(db.DB_PATH).resolve() != production:
        return None
    return Path(os.environ.get("APEX_OFFHOST_DIR", str(OFFHOST_DIR)))


def _hashes(path: Path) -> tuple[str, str]:
    sha, md5 = hashlib.sha256(), hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            sha.update(chunk)
            md5.update(chunk)
    return sha.hexdigest(), md5.hexdigest()


def offhost_copy(now: datetime | None = None, src_dir: Path | None = None,
                 dest_dir: Path | None = None) -> dict | None:
    """
    Copy the newest intact backup into the off-host folder as OFFHOST_DB, with OFFHOST_META beside it
    (source file, copied_at, bytes, sha256, md5: md5 is what Drive reports, for the hand check of the cloud copy).

    Returns the meta written, or None when nothing was copied: the folder already holds that backup, the
    folder is missing (logged WARNING: Drive for desktop not syncing it), the newest backup failed its integrity
    check (logged WARNING: a bad copy never replaces a good one), or no backup exists yet.
    """
    now = now or datetime.now(timezone.utc)
    src_dir = src_dir or backup_dir()
    dest_dir = dest_dir if dest_dir is not None else offhost_dir()
    if dest_dir is None:
        return None
    if not dest_dir.is_dir():
        logger.warning(f"Off-host backup: folder {dest_dir} missing — Drive for desktop not syncing it? (CHECK 95)")
        return None
    manifest = src_dir / "MANIFEST.json"
    entries = json.loads(manifest.read_text()) if manifest.exists() else []
    if not entries:
        logger.warning("Off-host backup: no local backup in the manifest yet")
        return None
    last = entries[-1]
    if last.get("integrity") != "ok":
        logger.warning(f"Off-host backup: newest backup {last['file']} failed its integrity check — not copied")
        return None
    meta_path = dest_dir / OFFHOST_META
    if meta_path.exists() and json.loads(meta_path.read_text()).get("source") == last["file"]:
        return None
    tmp_dir = dest_dir.parent / f".{dest_dir.name}-partial"     # outside the synced folder, same filesystem
    tmp_dir.mkdir(exist_ok=True)
    tmp_db = tmp_dir / OFFHOST_DB
    shutil.copyfile(src_dir / last["file"], tmp_db)
    sha, md5 = _hashes(tmp_db)                                  # hashes of the bytes written, not of the source
    meta = {"source": last["file"], "source_written_at": last["written_at"], "copied_at": now.isoformat(),
            "bytes": tmp_db.stat().st_size, "sha256": sha, "md5": md5}
    os.replace(tmp_db, dest_dir / OFFHOST_DB)
    tmp_meta = tmp_dir / OFFHOST_META
    tmp_meta.write_text(json.dumps(meta, indent=1))
    os.replace(tmp_meta, meta_path)
    logger.info(f"Off-host backup: {last['file']} → {dest_dir / OFFHOST_DB} ({meta['bytes'] / 1e6:.0f} MB)")
    return meta
